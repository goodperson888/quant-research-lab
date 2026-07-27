from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd
import yaml

from quant_lab.application.ports import TrialEvaluationRequest, TrialEvaluationResult
from quant_lab.application.selective_reentry_smoke import SelectiveReentrySmokeBacktester
from quant_lab.domain.models import Job, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore


class DeterministicFixtureStrategyEvaluator:
    """Non-research fixture used only for wiring tests and explicitly labelled demos."""

    evaluator_id = "deterministic_fixture"

    def evaluate(self, request: TrialEvaluationRequest) -> TrialEvaluationResult:
        numeric = [
            float(value)
            for value in request.parameters.values()
            if isinstance(value, (int, float))
        ]
        score = sum(numeric)
        return TrialEvaluationResult(
            trial_id=request.trial_id,
            status="succeeded",
            metrics={
                "train_net_return": score / 1000,
                "validation_net_return": score / 2000,
                "validation_profit_factor": 1.0 + score / 100,
                "validation_expectancy": score / 10000,
                "validation_trade_count": 30.0,
                "validation_max_drawdown_abs": max(0.01, 0.2 - score / 1000),
                "incremental_net_return": score / 5000,
                "fixture_evidence": 1.0,
            },
            elapsed_seconds=0.001,
            peak_rss_mb=1.0,
            stop_reason=None,
        )


class StrategyEvaluatorRegistry:
    def __init__(self, evaluators=()) -> None:
        self._evaluators = {item.evaluator_id: item for item in evaluators}

    def register(self, evaluator) -> None:
        if evaluator.evaluator_id in self._evaluators:
            raise ValueError(f"strategy evaluator already registered: {evaluator.evaluator_id}")
        self._evaluators[evaluator.evaluator_id] = evaluator

    def get(self, evaluator_id: str):
        try:
            return self._evaluators[evaluator_id]
        except KeyError as exc:
            raise ValueError(
                f"strategy evaluator is unsupported: {evaluator_id}; "
                "no generic strategy execution is being fabricated"
            ) from exc


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_spec_id: str
    backtest_handlers: Mapping[str, Callable[[Job], Mapping[str, Any]]]
    evaluator_ids: tuple[str, ...]


class StrategyPluginRegistry:
    """Route reviewed strategy adapters without adding strategy conditionals to Worker."""

    def __init__(self, specs=(), *, fallback=None) -> None:
        self._fallback = fallback
        self._handlers: dict[str, Callable[[Job], Mapping[str, Any]]] = {}
        self._specs: dict[str, StrategySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: StrategySpec) -> None:
        if spec.strategy_spec_id in self._specs:
            raise ValueError(f"strategy spec already registered: {spec.strategy_spec_id}")
        overlap = set(spec.backtest_handlers) & set(self._handlers)
        if overlap:
            raise ValueError("backtest intent already registered: " + ", ".join(sorted(overlap)))
        self._specs[spec.strategy_spec_id] = spec
        self._handlers.update(spec.backtest_handlers)

    def route_backtest(self, job: Job) -> Mapping[str, Any]:
        intent = str(job.payload.get("intent", ""))
        handler = self._handlers.get(intent)
        if handler is not None:
            return handler(job)
        if self._fallback is not None:
            return self._fallback(job)
        raise ValueError(f"unsupported native backtest intent: {intent}")

    def list_specs(self) -> tuple[StrategySpec, ...]:
        return tuple(self._specs.values())


class SelectiveReentryComponentEvaluator:
    """Real Native evaluator for one-component diagnostic ablations."""

    evaluator_id = "selective_reentry_component_v1"

    def __init__(
        self,
        root: Path,
        repository: ProductRepository,
        *,
        config_artifact_key: str,
    ) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.config_artifact_key = validate_artifact_key(config_artifact_key)

    def evaluate(self, request: TrialEvaluationRequest) -> TrialEvaluationResult:
        started = time.monotonic()
        try:
            config = yaml.safe_load(self.artifacts.get(self.config_artifact_key))
            if not isinstance(config, dict):
                raise ValueError("selective reentry evaluator config must be a YAML mapping")
            if request.candidate_version_id != config["candidate_version_id"]:
                raise ValueError("Trial candidate does not match evaluator config")
            if "locked_test" in request.data_splits:
                raise ValueError("component Trial request must not receive locked-test data")
            self._validate_single_component(request.parameters)
            data_manifest = json.loads(
                self.artifacts.get(validate_artifact_key(config["data_manifest_key"]))
            )
            validation_end = config["time_splits"]["validation"]["end_utc_exclusive"]
            datasets = {
                "ohlcv_5m": self._load_dataset(
                    data_manifest, "futures_ohlcv", "5m", validation_end
                ),
                "ohlcv_15m": self._load_dataset(
                    data_manifest, "futures_ohlcv", "15m", validation_end
                ),
                "ohlcv_1h": self._load_dataset(
                    data_manifest, "futures_ohlcv", "1h", validation_end
                ),
                "funding": self._load_dataset(
                    data_manifest, "funding_rate", None, validation_end
                ),
                "mark_15m": self._load_dataset(
                    data_manifest, "mark_price", "15m", validation_end
                ),
            }
            overrides = self._overrides(request.parameters)
            backtester = SelectiveReentrySmokeBacktester(
                candidate_version_id=config["candidate_version_id"],
                baseline_version_id=config["baseline_version_id"],
                fee_per_side=float(request.cost_model["fee_per_side"]),
                slippage_bps_per_side=float(
                    request.cost_model["slippage_bps_per_side"]
                ),
                initial_equity=float(config["execution"]["initial_equity"]),
                tick_size=float(config["execution"]["tick_size"]),
                quantity_step=float(config["execution"]["quantity_step"]),
                minimum_notional=float(config["execution"]["minimum_notional"]),
                **overrides,
            )
            results = {
                label: backtester.run(
                    **datasets,
                    warmup_start_utc_inclusive=split[
                        "warmup_start_utc_inclusive"
                    ],
                    start_utc_inclusive=split["start_utc_inclusive"],
                    end_utc_exclusive=split["end_utc_exclusive"],
                )
                for label, split in (
                    ("train", config["time_splits"]["train"]),
                    ("validation", config["time_splits"]["validation"]),
                )
            }
            train = results["train"].metrics
            validation = results["validation"].metrics
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="succeeded",
                metrics={
                    "train_net_return": float(train["total_return"]),
                    "train_profit_factor": float(train["profit_factor"]),
                    "train_expectancy": float(train["expectancy"]),
                    "train_trade_count": float(train["trade_count"]),
                    "train_max_drawdown_abs": float(train["max_drawdown"]),
                    "validation_net_return": float(validation["total_return"]),
                    "validation_profit_factor": float(validation["profit_factor"]),
                    "validation_expectancy": float(validation["expectancy"]),
                    "validation_trade_count": float(validation["trade_count"]),
                    "validation_max_drawdown_abs": float(
                        validation["max_drawdown"]
                    ),
                    "validation_contaminated": 1.0,
                    "diagnostic_evidence": 1.0,
                },
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason="diagnostic_split_validation_already_observed",
            )
        except Exception as exc:
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="failed",
                error=str(exc),
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason="selective_reentry_component_evaluation_failed",
            )

    @staticmethod
    def _validate_single_component(parameters: Mapping[str, Any]) -> None:
        families = set()
        for name in parameters:
            if name == "enabled_side":
                families.add("side_filter")
            elif name == "max_reentries_per_trend_leg":
                families.add("reentry")
            elif name in {
                "max_holding_minutes",
                "time_stop_minutes",
                "time_stop_mfe_r",
                "exit_rule_variant",
            }:
                families.add("exit")
            else:
                raise ValueError(f"unsupported selective-reentry component parameter: {name}")
        if len(families) != 1:
            raise ValueError("each diagnostic batch must change exactly one component")

    @staticmethod
    def _overrides(parameters: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        side = parameters.get("enabled_side")
        if side is not None:
            side_map = {
                "both": ("long", "short"),
                "long_only": ("long",),
                "short_only": ("short",),
                "exclude_long": ("short",),
                "exclude_short": ("long",),
            }
            if side not in side_map:
                raise ValueError("unsupported enabled_side value")
            result["allowed_sides"] = side_map[side]
        if "max_reentries_per_trend_leg" in parameters:
            result["max_reentries_per_trend_leg"] = int(
                parameters["max_reentries_per_trend_leg"]
            )
        variant = parameters.get("exit_rule_variant")
        if variant is not None:
            variants = {
                "current": (90, 30, 0.5),
                "tighter": (60, 20, 0.4),
                "looser": (120, 45, 0.6),
            }
            if variant not in variants:
                raise ValueError("unsupported exit_rule_variant")
            (
                result["max_holding_minutes"],
                result["time_stop_minutes"],
                result["time_stop_mfe_r"],
            ) = variants[variant]
        for name in ("max_holding_minutes", "time_stop_minutes", "time_stop_mfe_r"):
            if name in parameters:
                result[name] = parameters[name]
        return result

    def _load_dataset(
        self,
        manifest: Mapping[str, Any],
        dataset: str,
        timeframe: str | None,
        end_utc_exclusive: str,
    ) -> pd.DataFrame:
        matches = [
            item
            for item in manifest["processed_datasets"]
            if item["dataset"] == dataset
            and (timeframe is None or item.get("timeframe") == timeframe)
        ]
        if len(matches) != 1:
            raise ValueError(f"manifest must contain exactly one {dataset}/{timeframe}")
        frames: list[pd.DataFrame] = []
        for output in matches[0]["outputs"]:
            content = self.artifacts.get(validate_artifact_key(output["path"]))
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {output['path']}")
            frame = pd.read_parquet(io.BytesIO(content))
            if "timestamp" in frame:
                frame = frame.loc[
                    pd.to_datetime(frame["timestamp"], utc=True)
                    < pd.Timestamp(end_utc_exclusive)
                ].copy()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError(f"dataset has no rows: {dataset}/{timeframe}")
        return pd.concat(frames, ignore_index=True)
