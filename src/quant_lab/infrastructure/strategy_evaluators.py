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

from quant_lab.application.boll_rsi_slope import BollRsiSlopeBacktester
from quant_lab.application.ema_mtf_scalp import EmaMtfScalpBacktester
from quant_lab.application.equity_series import compress_equity_points
from quant_lab.application.ports import TrialEvaluationRequest, TrialEvaluationResult
from quant_lab.application.selective_reentry_smoke import SelectiveReentrySmokeBacktester
from quant_lab.domain.models import Job, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository

from .artifact_store import LocalArtifactStore
from .execution_models import ExecutionModelCatalog


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
            equity_points=tuple(
                {
                    "split": "validation",
                    "timestamp": f"2026-01-{day:02d}T00:00:00+00:00",
                    "normalized_equity": 1.0 + score / 2000 * index / 9,
                    "drawdown": 0.0,
                    "point_index": index,
                }
                for index, day in enumerate(range(1, 11))
            ),
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
    snapshot_strategy_id: str | None = None
    config_artifact_key: str | None = None
    config_resolver: Callable[[Any, str], str] | None = None
    smoke_intent: str | None = None
    fast_screen_intent: str | None = None
    market_profile: str | None = None
    pipeline_profile: str = "fast_screen"


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

    def get_spec(self, strategy_spec_id: str) -> StrategySpec:
        try:
            return self._specs[strategy_spec_id]
        except KeyError as exc:
            raise ValueError(
                f"strategy spec is unsupported: {strategy_spec_id}; "
                "no generic strategy execution is being fabricated"
            ) from exc

    def find_for_snapshot(self, snapshot: Mapping[str, Any]) -> StrategySpec:
        explicit = snapshot.get("strategy_spec_id")
        if isinstance(explicit, str) and explicit:
            return self.get_spec(explicit)
        strategy_id = snapshot.get("strategy_id")
        matches = [
            spec
            for spec in self._specs.values()
            if spec.snapshot_strategy_id == strategy_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "baseline does not select exactly one reviewed StrategySpec"
            )
        return matches[0]


class EmaMtfScalpComponentEvaluator:
    """Real Native evaluator for the approved EMA structure-exit diagnostic batch."""

    evaluator_id = "ema_mtf_scalp_exit_component_v1"
    _SUPPORTED_VARIANTS = frozenset({"current", "tighter", "looser"})

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
            if not isinstance(config, dict) or config.get("schema_version") != 1:
                raise ValueError("EMA component evaluator config must be schema v1 YAML")
            if "locked_test" in request.data_splits:
                raise ValueError("EMA component Trial must not receive locked-test data")
            plan = self.repository.get_experiment_plan(request.experiment_plan_id)
            if (
                plan.baseline_version_id != request.baseline_version_id
                or plan.candidate_version_id != request.candidate_version_id
            ):
                raise ValueError("EMA component Trial does not match its approved Plan")
            if request.baseline_version_id != config["strategy_version_id"]:
                raise ValueError("EMA component Trial baseline/config mismatch")
            candidate = self.repository.get_strategy_version(
                str(request.candidate_version_id)
            )
            if (
                candidate.status != "candidate"
                or not candidate.immutable
                or candidate.content_snapshot.get("baseline_version_id")
                != request.baseline_version_id
                or candidate.content_snapshot.get("locked_test_used") is not False
            ):
                raise ValueError("EMA component Trial requires its immutable Candidate")
            variant = self._variant(request.parameters)
            self._validate_splits(config, request)
            self._validate_cost_model(config, request)

            data_manifest = json.loads(
                self.artifacts.get(validate_artifact_key(config["data_manifest_key"]))
            )
            diagnostic_reference = config.get("diagnostic_reference")
            if not isinstance(diagnostic_reference, Mapping):
                raise ValueError("EMA component evaluator diagnostic reference is missing")
            baseline_manifest = json.loads(
                self.artifacts.get(
                    validate_artifact_key(
                        str(diagnostic_reference["baseline_manifest_artifact_key"])
                    )
                )
            )
            baseline_metrics = json.loads(
                self.artifacts.get(
                    validate_artifact_key(
                        str(diagnostic_reference["baseline_metrics_artifact_key"])
                    )
                )
            )
            self._validate_baseline_evidence(
                config=config,
                request=request,
                data_manifest=data_manifest,
                baseline_manifest=baseline_manifest,
                baseline_metrics=baseline_metrics,
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
            }
            execution = config["execution"]
            backtester = EmaMtfScalpBacktester(
                strategy_version_id=candidate.id,
                execution_model=ExecutionModelCatalog(self.root).get(venue="binance"),
                initial_equity=float(execution["initial_equity"]),
                leverage=float(execution["leverage"]),
                risk_per_trade_fraction=float(execution["risk_per_trade_fraction"]),
                max_trades_per_day=int(execution["max_trades_per_day"]),
                cooldown_minutes=int(execution["cooldown_minutes"]),
                cooldown_after_loss_minutes=int(
                    execution["cooldown_after_loss_minutes"]
                ),
                min_stop_distance_fraction=float(
                    execution["min_stop_distance_fraction"]
                ),
                max_stop_distance_fraction=float(
                    execution["max_stop_distance_fraction"]
                ),
                stop_buffer_atr_fraction=float(
                    execution["stop_buffer_atr_fraction"]
                ),
                take_profit_r_multiple=float(execution["take_profit_r_multiple"]),
                max_holding_bars=int(execution["max_holding_bars_5m"]),
                max_entry_distance_fraction=float(
                    execution["max_entry_distance_fraction"]
                ),
                max_trigger_bars=int(execution["max_trigger_bars_5m"]),
                structure_exit_variant=variant,
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
            baseline_train = baseline_metrics["train"]["metrics"]
            baseline_validation = baseline_metrics["validation"]["metrics"]
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="succeeded",
                metrics={
                    "train_net_return": float(train["total_return"]),
                    "train_profit_factor": float(train["profit_factor"]),
                    "train_expectancy": float(train["expectancy"]),
                    "train_trade_count": float(train["trade_count"]),
                    "train_max_drawdown_abs": float(train["max_drawdown"]),
                    "train_average_holding_minutes": float(
                        train["average_holding_minutes"]
                    ),
                    "train_structure_exit_count": self._structure_exit_count(
                        results["train"].trades
                    ),
                    "validation_net_return": float(validation["total_return"]),
                    "validation_profit_factor": float(validation["profit_factor"]),
                    "validation_expectancy": float(validation["expectancy"]),
                    "validation_trade_count": float(validation["trade_count"]),
                    "validation_max_drawdown_abs": float(
                        validation["max_drawdown"]
                    ),
                    "validation_average_holding_minutes": float(
                        validation["average_holding_minutes"]
                    ),
                    "validation_structure_exit_count": self._structure_exit_count(
                        results["validation"].trades
                    ),
                    "incremental_train_net_return": float(
                        train["total_return"] - baseline_train["total_return"]
                    ),
                    "incremental_net_return": float(
                        validation["total_return"]
                        - baseline_validation["total_return"]
                    ),
                    "incremental_profit_factor": float(
                        validation["profit_factor"]
                        - baseline_validation["profit_factor"]
                    ),
                    "incremental_drawdown_reduction": float(
                        baseline_validation["max_drawdown"]
                        - validation["max_drawdown"]
                    ),
                    "validation_contaminated": 1.0,
                    "diagnostic_evidence": 1.0,
                    "locked_test_used": 0.0,
                },
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason="diagnostic_split_validation_already_observed",
                equity_points=compress_equity_points(
                    results["validation"].equity,
                    split="validation",
                ),
            )
        except Exception as exc:
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="failed",
                error=str(exc),
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason="ema_mtf_scalp_component_evaluation_failed",
            )

    @classmethod
    def _variant(cls, parameters: Mapping[str, Any]) -> str:
        if set(parameters) != {"structure_exit_variant"}:
            raise ValueError(
                "EMA diagnostic batch must change only structure_exit_variant"
            )
        variant = str(parameters["structure_exit_variant"])
        if variant not in cls._SUPPORTED_VARIANTS:
            raise ValueError("unsupported EMA structure-exit variant")
        return variant

    @staticmethod
    def _validate_splits(
        config: Mapping[str, Any], request: TrialEvaluationRequest
    ) -> None:
        if set(request.data_splits) != {"train", "validation"}:
            raise ValueError("EMA diagnostic evaluator accepts train/validation only")
        for label in ("train", "validation"):
            configured = config["time_splits"][label]
            declared = request.data_splits[label]
            if (
                configured["start_utc_inclusive"] not in declared
                or configured["end_utc_exclusive"] not in declared
            ):
                raise ValueError(f"EMA component Trial {label} split mismatch")
        if "screening_contaminated" not in request.data_splits["validation"]:
            raise ValueError("EMA diagnostic validation must remain contaminated screening")

    @staticmethod
    def _validate_cost_model(
        config: Mapping[str, Any], request: TrialEvaluationRequest
    ) -> None:
        expected = config["cost_model"]
        if (
            float(request.cost_model["taker_fee_per_side"])
            != float(expected["fee_per_side"])
            or float(request.cost_model["slippage_bps_per_side"])
            != float(expected["slippage_bps_per_side"])
            or float(request.cost_model["leverage"])
            != float(config["execution"]["leverage"])
            or request.cost_model.get("zero_funding_fallback_allowed") is not False
        ):
            raise ValueError("EMA component Trial cost model differs from the Baseline")

    @staticmethod
    def _validate_baseline_evidence(
        *,
        config: Mapping[str, Any],
        request: TrialEvaluationRequest,
        data_manifest: Mapping[str, Any],
        baseline_manifest: Mapping[str, Any],
        baseline_metrics: Mapping[str, Any],
    ) -> None:
        if (
            baseline_manifest.get("status") != "succeeded"
            or baseline_manifest.get("run_type") != "ema_mtf_scalp_fast_screen"
            or baseline_manifest.get("strategy", {}).get("strategy_version_id")
            != request.baseline_version_id
            or baseline_manifest.get("locked_test", {}).get("used") is not False
        ):
            raise ValueError("EMA component Trial baseline evidence is invalid")
        if (
            baseline_manifest.get("data_manifest", {}).get("data_version")
            != request.data_version
            or data_manifest.get("data_version") != request.data_version
            or baseline_manifest.get("market_profile") != config["market_profile"]
        ):
            raise ValueError("EMA component Trial data version/market mismatch")
        if not {"train", "validation"}.issubset(baseline_metrics):
            raise ValueError("EMA baseline metrics are incomplete")

    @staticmethod
    def _structure_exit_count(trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        return float(
            trades["exit_reason"].astype(str).str.contains("structure_exit").sum()
        )

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
        end = pd.Timestamp(end_utc_exclusive)
        for output in matches[0]["outputs"]:
            content = self.artifacts.get(validate_artifact_key(str(output["path"])))
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {output['path']}")
            frame = pd.read_parquet(io.BytesIO(content))
            if "timestamp" in frame:
                frame = frame.loc[
                    pd.to_datetime(frame["timestamp"], utc=True) < end
                ].copy()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError(f"dataset has no rows: {dataset}/{timeframe}")
        return pd.concat(frames, ignore_index=True)


class BollRsiSlopeExitComponentEvaluator:
    """Real Native evaluator for the approved BOLL-RSI early-exit diagnostic."""

    evaluator_id = "boll_rsi_slope_exit_component_v1"
    _VARIANT_BARS = {
        "current": 3,
        "tighter": 2,
        "looser": 4,
    }

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
            if not isinstance(config, dict) or config.get("schema_version") != 1:
                raise ValueError("BOLL-RSI component evaluator config must be schema v1 YAML")
            if "locked_test" in request.data_splits:
                raise ValueError("BOLL-RSI component Trial must not receive locked-test data")
            plan = self.repository.get_experiment_plan(request.experiment_plan_id)
            if (
                plan.baseline_version_id != request.baseline_version_id
                or plan.candidate_version_id != request.candidate_version_id
            ):
                raise ValueError("BOLL-RSI component Trial does not match its Plan")
            if request.baseline_version_id != config["strategy_version_id"]:
                raise ValueError("BOLL-RSI component Trial baseline/config mismatch")
            candidate = self.repository.get_strategy_version(
                str(request.candidate_version_id)
            )
            if (
                candidate.status != "candidate"
                or not candidate.immutable
                or candidate.content_snapshot.get("baseline_version_id")
                != request.baseline_version_id
                or candidate.content_snapshot.get("locked_test_used") is not False
            ):
                raise ValueError("BOLL-RSI component Trial requires its immutable Candidate")
            self._validate_component_candidate(candidate)
            component_settings = self._component_settings(
                request.parameters, config["execution"]
            )
            self._validate_splits(config, request)
            self._validate_cost_model(config, request)

            data_manifest = json.loads(
                self.artifacts.get(validate_artifact_key(config["data_manifest_key"]))
            )
            diagnostic_reference = config.get("diagnostic_reference")
            if not isinstance(diagnostic_reference, Mapping):
                raise ValueError(
                    "BOLL-RSI component evaluator diagnostic reference is missing"
                )
            baseline_manifest = json.loads(
                self.artifacts.get(
                    validate_artifact_key(
                        str(diagnostic_reference["baseline_manifest_artifact_key"])
                    )
                )
            )
            baseline_metrics = json.loads(
                self.artifacts.get(
                    validate_artifact_key(
                        str(diagnostic_reference["baseline_metrics_artifact_key"])
                    )
                )
            )
            self._validate_baseline_evidence(
                config=config,
                request=request,
                data_manifest=data_manifest,
                baseline_manifest=baseline_manifest,
                baseline_metrics=baseline_metrics,
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
            }
            execution = config["execution"]
            indicators = config["indicators"]
            backtester = BollRsiSlopeBacktester(
                strategy_version_id=candidate.id,
                execution_model=ExecutionModelCatalog(self.root).get(venue="binance"),
                initial_equity=float(execution["initial_equity"]),
                leverage=float(execution["leverage"]),
                risk_per_trade_fraction=float(execution["risk_per_trade_fraction"]),
                max_trades_per_day=int(execution["max_trades_per_day"]),
                cooldown_minutes=int(execution["cooldown_minutes"]),
                cooldown_after_loss_minutes=int(
                    execution["cooldown_after_loss_minutes"]
                ),
                max_stop_distance_fraction=float(
                    component_settings["max_stop_distance_fraction"]
                ),
                stop_buffer_fraction=float(execution["stop_buffer_fraction"]),
                minimum_reward_r=float(component_settings["minimum_reward_r"]),
                early_exit_bars=int(component_settings["early_exit_bars"]),
                early_exit_min_mfe_r=float(execution["early_exit_min_mfe_r"]),
                max_holding_bars=int(execution["max_holding_bars_5m"]),
                max_trigger_bars=int(execution["max_trigger_bars_5m"]),
                bollinger_period=int(indicators["bollinger_period"]),
                bollinger_stddev=float(indicators["bollinger_stddev"]),
                rsi_period=int(indicators["rsi_period_15m"]),
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
            baseline_train = baseline_metrics["train"]["metrics"]
            baseline_validation = baseline_metrics["validation"]["metrics"]
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="succeeded",
                metrics={
                    "train_net_return": float(train["total_return"]),
                    "train_profit_factor": float(train["profit_factor"]),
                    "train_expectancy": float(train["expectancy"]),
                    "train_trade_count": float(train["trade_count"]),
                    "train_max_drawdown_abs": float(train["max_drawdown"]),
                    "train_average_holding_minutes": float(
                        train["average_holding_minutes"]
                    ),
                    "train_early_time_stop_count": self._early_exit_count(
                        results["train"].trades
                    ),
                    **self._admission_diagnostics("train", results["train"]),
                    "validation_net_return": float(validation["total_return"]),
                    "validation_profit_factor": float(
                        validation["profit_factor"]
                    ),
                    "validation_expectancy": float(validation["expectancy"]),
                    "validation_trade_count": float(validation["trade_count"]),
                    "validation_max_drawdown_abs": float(
                        validation["max_drawdown"]
                    ),
                    "validation_average_holding_minutes": float(
                        validation["average_holding_minutes"]
                    ),
                    "validation_early_time_stop_count": self._early_exit_count(
                        results["validation"].trades
                    ),
                    **self._admission_diagnostics(
                        "validation", results["validation"]
                    ),
                    "incremental_train_net_return": float(
                        train["total_return"] - baseline_train["total_return"]
                    ),
                    "incremental_net_return": float(
                        validation["total_return"]
                        - baseline_validation["total_return"]
                    ),
                    "incremental_profit_factor": float(
                        validation["profit_factor"]
                        - baseline_validation["profit_factor"]
                    ),
                    "incremental_drawdown_reduction": float(
                        baseline_validation["max_drawdown"]
                        - validation["max_drawdown"]
                    ),
                    "validation_contaminated": 1.0,
                    "diagnostic_evidence": 1.0,
                    "locked_test_used": 0.0,
                },
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason="diagnostic_split_validation_already_observed",
                equity_points=compress_equity_points(
                    results["validation"].equity,
                    split="validation",
                ),
            )
        except Exception as exc:
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="failed",
                error=str(exc),
                elapsed_seconds=time.monotonic() - started,
                peak_rss_mb=0.0,
                stop_reason=f"{self.evaluator_id}_evaluation_failed",
            )

    def _component_settings(
        self,
        parameters: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> Mapping[str, float | int]:
        return {
            "max_stop_distance_fraction": float(
                execution["max_stop_distance_fraction"]
            ),
            "minimum_reward_r": float(execution["minimum_reward_r"]),
            "early_exit_bars": self._early_exit_bars(parameters),
        }

    @staticmethod
    def _validate_component_candidate(candidate) -> None:
        return None

    @classmethod
    def _early_exit_bars(cls, parameters: Mapping[str, Any]) -> int:
        if set(parameters) != {"exit_rule_variant"}:
            raise ValueError(
                "BOLL-RSI diagnostic batch must change only exit_rule_variant"
            )
        variant = str(parameters["exit_rule_variant"])
        try:
            return cls._VARIANT_BARS[variant]
        except KeyError as exc:
            raise ValueError("unsupported BOLL-RSI early-exit variant") from exc

    @staticmethod
    def _validate_splits(
        config: Mapping[str, Any], request: TrialEvaluationRequest
    ) -> None:
        if set(request.data_splits) != {"train", "validation"}:
            raise ValueError(
                "BOLL-RSI diagnostic evaluator accepts train/validation only"
            )
        for label in ("train", "validation"):
            configured = config["time_splits"][label]
            declared = request.data_splits[label]
            if (
                configured["start_utc_inclusive"] not in declared
                or configured["end_utc_exclusive"] not in declared
            ):
                raise ValueError(f"BOLL-RSI component Trial {label} split mismatch")
        if "screening_contaminated" not in request.data_splits["validation"]:
            raise ValueError(
                "BOLL-RSI diagnostic validation must remain contaminated screening"
            )

    @staticmethod
    def _validate_cost_model(
        config: Mapping[str, Any], request: TrialEvaluationRequest
    ) -> None:
        expected = config["cost_model"]
        if (
            float(request.cost_model["taker_fee_per_side"])
            != float(expected["fee_per_side"])
            or float(request.cost_model["slippage_bps_per_side"])
            != float(expected["slippage_bps_per_side"])
            or float(request.cost_model["leverage"])
            != float(config["execution"]["leverage"])
            or request.cost_model.get("funding_required") is not True
            or request.cost_model.get("zero_funding_fallback_allowed") is not False
        ):
            raise ValueError("BOLL-RSI component Trial cost model differs from Baseline")

    @staticmethod
    def _validate_baseline_evidence(
        *,
        config: Mapping[str, Any],
        request: TrialEvaluationRequest,
        data_manifest: Mapping[str, Any],
        baseline_manifest: Mapping[str, Any],
        baseline_metrics: Mapping[str, Any],
    ) -> None:
        if (
            baseline_manifest.get("status") != "succeeded"
            or baseline_manifest.get("run_type") != "boll_rsi_slope_fast_screen"
            or baseline_manifest.get("strategy", {}).get("strategy_version_id")
            != request.baseline_version_id
            or baseline_manifest.get("locked_test", {}).get("used") is not False
        ):
            raise ValueError("BOLL-RSI component Trial baseline evidence is invalid")
        if (
            baseline_manifest.get("data_manifest", {}).get("data_version")
            != request.data_version
            or data_manifest.get("data_version") != request.data_version
            or baseline_manifest.get("market_profile") != config["market_profile"]
        ):
            raise ValueError("BOLL-RSI component Trial data version/market mismatch")
        if not {"train", "validation"}.issubset(baseline_metrics):
            raise ValueError("BOLL-RSI baseline metrics are incomplete")

    @staticmethod
    def _early_exit_count(trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        return float((trades["exit_reason"] == "early_time_stop").sum())

    @staticmethod
    def _admission_diagnostics(prefix: str, result: Any) -> dict[str, float]:
        funnel = result.signal_funnel
        trades = result.trades
        skipped = funnel.get("skipped", {})
        if trades.empty:
            average_stop_distance = 0.0
            average_quantity = 0.0
        else:
            average_stop_distance = float(
                (
                    (trades["entry_price"] - trades["stop_price"]).abs()
                    / trades["entry_price"]
                ).mean()
            )
            average_quantity = float(trades["quantity"].mean())
        return {
            f"{prefix}_entry_candidates": float(
                funnel.get("entry_candidates", 0)
            ),
            f"{prefix}_stop_distance_skip_count": float(
                skipped.get("stop_distance", 0)
            ),
            f"{prefix}_insufficient_reward_skip_count": float(
                skipped.get("insufficient_reward", 0)
            ),
            f"{prefix}_entered_count": float(funnel.get("entered", 0)),
            f"{prefix}_average_stop_distance_fraction": average_stop_distance,
            f"{prefix}_average_position_quantity": average_quantity,
        }

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
        end = pd.Timestamp(end_utc_exclusive)
        for output in matches[0]["outputs"]:
            content = self.artifacts.get(validate_artifact_key(str(output["path"])))
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {output['path']}")
            frame = pd.read_parquet(io.BytesIO(content))
            if "timestamp" in frame:
                frame = frame.loc[
                    pd.to_datetime(frame["timestamp"], utc=True) < end
                ].copy()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError(f"dataset has no rows: {dataset}/{timeframe}")
        return pd.concat(frames, ignore_index=True)


class BollRsiSlopeStopDistanceComponentEvaluator(
    BollRsiSlopeExitComponentEvaluator
):
    """BOLL-RSI risk-admission evaluator with fixed account-risk sizing."""

    evaluator_id = "boll_rsi_slope_stop_distance_component_v1"
    _SUPPORTED_DISTANCES = (0.0045, 0.0060, 0.0075)

    @classmethod
    def _max_stop_distance(cls, parameters: Mapping[str, Any]) -> float:
        if set(parameters) != {"max_stop_distance_fraction"}:
            raise ValueError(
                "BOLL-RSI stop-distance batch must change only "
                "max_stop_distance_fraction"
            )
        value = float(parameters["max_stop_distance_fraction"])
        if not any(abs(value - allowed) < 1e-12 for allowed in cls._SUPPORTED_DISTANCES):
            raise ValueError("unsupported BOLL-RSI max stop-distance variant")
        return value

    def _component_settings(
        self,
        parameters: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> Mapping[str, float | int]:
        return {
            "max_stop_distance_fraction": self._max_stop_distance(parameters),
            "minimum_reward_r": float(execution["minimum_reward_r"]),
            "early_exit_bars": int(execution["early_exit_bars_5m"]),
        }

    @staticmethod
    def _validate_component_candidate(candidate) -> None:
        rule_diff = candidate.content_snapshot.get("rule_diff")
        if not isinstance(rule_diff, Mapping):
            raise ValueError("BOLL-RSI stop-distance Candidate rule diff is missing")
        fixed = rule_diff.get("fixed_rules")
        mapping = rule_diff.get("parameter_mapping")
        if (
            rule_diff.get("component_type") != "risk"
            or rule_diff.get("single_component_only") is not True
            or not isinstance(fixed, Mapping)
            or float(fixed.get("risk_per_trade_equity_fraction", 0.0)) != 0.0025
            or fixed.get("entry_rules") != "unchanged"
            or fixed.get("early_exit_and_max_holding_rules") != "unchanged"
            or not isinstance(mapping, Mapping)
        ):
            raise ValueError(
                "BOLL-RSI stop-distance Candidate must preserve fixed-risk "
                "single-component lineage"
            )
        values = sorted(
            float(item["max_stop_distance_fraction"])
            for item in mapping.values()
            if isinstance(item, Mapping)
            and "max_stop_distance_fraction" in item
        )
        if values != [0.0045, 0.006, 0.0075]:
            raise ValueError(
                "BOLL-RSI stop-distance Candidate parameter mapping is not reviewed"
            )


class BollRsiSlopeMinimumRewardComponentEvaluator(
    BollRsiSlopeExitComponentEvaluator
):
    """BOLL-RSI target-admission evaluator with all execution risk fixed."""

    evaluator_id = "boll_rsi_slope_minimum_reward_component_v1"
    _SUPPORTED_MINIMUM_REWARDS = (0.6, 0.8, 1.0)

    @classmethod
    def _minimum_reward(cls, parameters: Mapping[str, Any]) -> float:
        if set(parameters) != {"minimum_reward_r"}:
            raise ValueError(
                "BOLL-RSI minimum-reward batch must change only minimum_reward_r"
            )
        value = float(parameters["minimum_reward_r"])
        if not any(
            abs(value - allowed) < 1e-12
            for allowed in cls._SUPPORTED_MINIMUM_REWARDS
        ):
            raise ValueError("unsupported BOLL-RSI minimum-reward variant")
        return value

    def _component_settings(
        self,
        parameters: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> Mapping[str, float | int]:
        return {
            "max_stop_distance_fraction": float(
                execution["max_stop_distance_fraction"]
            ),
            "minimum_reward_r": self._minimum_reward(parameters),
            "early_exit_bars": int(execution["early_exit_bars_5m"]),
        }

    @staticmethod
    def _validate_component_candidate(candidate) -> None:
        rule_diff = candidate.content_snapshot.get("rule_diff")
        if not isinstance(rule_diff, Mapping):
            raise ValueError("BOLL-RSI minimum-reward Candidate rule diff is missing")
        fixed = rule_diff.get("fixed_rules")
        mapping = rule_diff.get("parameter_mapping")
        deferred = rule_diff.get("deferred_separate_hypothesis")
        if (
            rule_diff.get("component_type") != "filter"
            or rule_diff.get("single_component_only") is not True
            or rule_diff.get("canonical_strategy_parameter")
            != "minimum_reward_to_middle_band_r"
            or not isinstance(fixed, Mapping)
            or float(fixed.get("risk_per_trade_equity_fraction", 0.0)) != 0.0025
            or float(fixed.get("leverage_for_this_ablation", 0.0)) != 1.0
            or float(fixed.get("max_stop_distance_fraction", 0.0)) != 0.0045
            or fixed.get("trend_setup_confirmation_trigger")
            != "unchanged_1h_15m_5m"
            or fixed.get("exit_rules") != "unchanged"
            or fixed.get("cost_and_funding_model") != "unchanged"
            or not isinstance(mapping, Mapping)
            or not isinstance(deferred, Mapping)
            or deferred.get("approved_for_current_trials") is not False
        ):
            raise ValueError(
                "BOLL-RSI minimum-reward Candidate must preserve fixed-risk "
                "single-component lineage"
            )
        values = sorted(
            float(item["minimum_reward_r"])
            for item in mapping.values()
            if isinstance(item, Mapping) and "minimum_reward_r" in item
        )
        if values != [0.6, 0.8, 1.0]:
            raise ValueError(
                "BOLL-RSI minimum-reward Candidate parameter mapping is not reviewed"
            )


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
                equity_points=compress_equity_points(
                    results["validation"].equity,
                    split="validation",
                ),
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
