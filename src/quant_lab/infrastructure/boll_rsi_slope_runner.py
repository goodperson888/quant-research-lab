from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from quant_lab.application.boll_rsi_slope import (
    BollRsiSlopeBacktester,
    BollRsiSlopeResult,
)
from quant_lab.application.research_authorization import ResearchAuthorizationService
from quant_lab.application.services import new_id
from quant_lab.domain.models import Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .artifact_store import LocalArtifactStore
from .execution_models import ExecutionModelCatalog


class BollRsiSlopeRunner:
    """Reviewed Native adapter for the immutable BOLL-RSI-slope Baseline."""

    ENGINE_ID = "quant_lab_native_boll_rsi_slope_v1"
    STRATEGY_SPEC_ID = "boll_rsi_slope_deep_pullback_v0"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.authorizations = ResearchAuthorizationService(repository)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config, stage = self._validate(job)
        manifest_key = validate_artifact_key(str(config["data_manifest_key"]))
        manifest_bytes = self.artifacts.get(manifest_key)
        manifest = json.loads(manifest_bytes)
        self._validate_manifest(config, manifest, stage=stage)
        end = (
            config["smoke"]["end_utc_exclusive"]
            if stage == "smoke"
            else config["time_splits"]["validation"]["end_utc_exclusive"]
        )
        datasets = {
            "ohlcv_5m": self._load_dataset(
                manifest, "futures_ohlcv", "5m", end_utc_exclusive=end
            ),
            "ohlcv_15m": self._load_dataset(
                manifest, "futures_ohlcv", "15m", end_utc_exclusive=end
            ),
            "ohlcv_1h": self._load_dataset(
                manifest, "futures_ohlcv", "1h", end_utc_exclusive=end
            ),
            "funding": self._load_dataset(
                manifest, "funding_rate", None, end_utc_exclusive=end
            ),
        }
        execution = config["execution"]
        backtester = BollRsiSlopeBacktester(
            strategy_version_id=config["strategy_version_id"],
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
                execution["max_stop_distance_fraction"]
            ),
            stop_buffer_fraction=float(execution["stop_buffer_fraction"]),
            minimum_reward_r=float(execution["minimum_reward_r"]),
            early_exit_bars=int(execution["early_exit_bars_5m"]),
            early_exit_min_mfe_r=float(execution["early_exit_min_mfe_r"]),
            max_holding_bars=int(execution["max_holding_bars_5m"]),
            max_trigger_bars=int(execution["max_trigger_bars_5m"]),
            bollinger_period=int(config["indicators"]["bollinger_period"]),
            bollinger_stddev=float(config["indicators"]["bollinger_stddev"]),
            rsi_period=int(config["indicators"]["rsi_period_15m"]),
        )
        if stage == "smoke":
            split_results = {
                "smoke": backtester.run(
                    **datasets,
                    warmup_start_utc_inclusive=config["smoke"][
                        "warmup_start_utc_inclusive"
                    ],
                    start_utc_inclusive=config["smoke"]["start_utc_inclusive"],
                    end_utc_exclusive=config["smoke"]["end_utc_exclusive"],
                )
            }
        else:
            split_results = {
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
        return self._persist(
            job=job,
            config=config,
            data_manifest=manifest,
            data_manifest_bytes=manifest_bytes,
            stage=stage,
            results=split_results,
        )

    def _validate(self, job: Job) -> tuple[dict[str, Any], str]:
        payload = job.payload
        intent = str(payload.get("intent", ""))
        stage_by_intent = {
            "boll_rsi_slope_smoke": "smoke",
            "boll_rsi_slope_fast_screen": "fast_screen",
        }
        if intent not in stage_by_intent:
            raise ValueError("BOLL-RSI runner received an unsupported intent")
        stage = stage_by_intent[intent]
        required = {
            "session_id",
            "strategy_version_id",
            "subject_id",
            "config_artifact_key",
            "agent_run_id",
            "authorization_id",
            "correctness_gate_result_id",
            "locked_test_used",
        }
        if stage == "fast_screen":
            required.add("smoke_manifest_artifact_key")
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError("BOLL-RSI payload missing: " + ", ".join(missing))
        if payload["subject_id"] != payload["strategy_version_id"]:
            raise ValueError("BOLL-RSI Job subject must match strategy version")
        if payload["locked_test_used"] is not False:
            raise ValueError("BOLL-RSI smoke/fast-screen forbids locked test")
        self.authorizations.assert_stage_allowed(
            str(payload["authorization_id"]),
            subject_id=str(payload["strategy_version_id"]),
            stage=stage,
        )
        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        loaded = yaml.safe_load(self.artifacts.get(config_key))
        if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
            raise ValueError("BOLL-RSI config must be a schema v1 YAML mapping")
        if loaded.get("strategy_spec_id") != self.STRATEGY_SPEC_ID:
            raise ValueError("BOLL-RSI config selects another StrategySpec")
        if loaded.get("session_id") != payload["session_id"]:
            raise ValueError("BOLL-RSI config session mismatch")
        if loaded.get("strategy_version_id") != payload["strategy_version_id"]:
            raise ValueError("BOLL-RSI config subject mismatch")
        version = self.repository.get_strategy_version(str(payload["strategy_version_id"]))
        if version.status != "baseline" or not version.immutable:
            raise ValueError("BOLL-RSI execution requires the immutable Baseline")
        if (
            version.content_snapshot.get("strategy_id")
            != "boll_rsi_slope_deep_pullback_continuation_v0"
        ):
            raise ValueError("BOLL-RSI Baseline snapshot does not match StrategySpec")
        if (
            float(version.content_snapshot["parameters"]["leverage"]) != 1.0
            or float(loaded["execution"]["leverage"]) != 1.0
            or loaded["execution"]["leverage_explicitly_requested"] is not False
        ):
            raise ValueError("BOLL-RSI Baseline must preserve its immutable 1x setting")
        gate = self.repository.get_gate_evaluation(
            str(payload["correctness_gate_result_id"])
        )
        if (
            gate.subject_id != version.id
            or gate.gate_name != "correctness"
            or gate.status != "passed"
            or gate.profile_id != "fast_screen"
            or gate.market_profile != loaded["market_profile"]
        ):
            raise ValueError("BOLL-RSI requires its own passed correctness gate")
        for key in ("strategy_artifact_key", "data_manifest_key"):
            artifact_key = validate_artifact_key(str(loaded[key]))
            if not self.artifacts.exists(artifact_key):
                raise ValueError(f"BOLL-RSI required artifact is missing: {artifact_key}")
        if loaded["time_splits"]["locked_test"]["use_in_this_authorization"] is not False:
            raise ValueError("BOLL-RSI locked test must remain excluded")
        if loaded["funding"]["zero_funding_fallback_allowed"] is not False:
            raise ValueError("BOLL-RSI funding cannot be silently zero")
        if stage == "fast_screen":
            smoke_key = validate_artifact_key(
                str(payload["smoke_manifest_artifact_key"])
            )
            smoke = json.loads(self.artifacts.get(smoke_key))
            if (
                smoke.get("run_type") != "boll_rsi_slope_smoke"
                or smoke.get("strategy", {}).get("strategy_version_id") != version.id
                or smoke.get("locked_test", {}).get("used") is not False
                or smoke.get("status") != "succeeded"
            ):
                raise ValueError("fast-screen requires successful same-subject smoke evidence")
        return loaded, stage

    @staticmethod
    def _validate_manifest(
        config: Mapping[str, Any],
        manifest: Mapping[str, Any],
        *,
        stage: str,
    ) -> None:
        if manifest.get("market_profile") != config["market_profile"]:
            raise ValueError("BOLL-RSI data manifest market profile mismatch")
        start = pd.Timestamp(
            config["smoke"]["warmup_start_utc_inclusive"]
            if stage == "smoke"
            else config["time_splits"]["train"]["warmup_start_utc_inclusive"]
        )
        end = pd.Timestamp(
            config["smoke"]["end_utc_exclusive"]
            if stage == "smoke"
            else config["time_splits"]["validation"]["end_utc_exclusive"]
        )
        if (
            pd.Timestamp(manifest["range"]["start_utc_inclusive"]) > start
            or pd.Timestamp(manifest["range"]["end_utc_exclusive"]) < end
        ):
            raise ValueError("BOLL-RSI data manifest does not cover the requested range")

    def _load_dataset(
        self,
        manifest: Mapping[str, Any],
        dataset: str,
        timeframe: str | None,
        *,
        end_utc_exclusive: str,
    ) -> pd.DataFrame:
        matches = [
            item
            for item in manifest["processed_datasets"]
            if item["dataset"] == dataset
            and (timeframe is None or item.get("timeframe") == timeframe)
        ]
        if len(matches) != 1:
            raise ValueError(f"manifest requires one {dataset}/{timeframe}")
        frames: list[pd.DataFrame] = []
        end = pd.Timestamp(end_utc_exclusive)
        for output in matches[0]["outputs"]:
            key = validate_artifact_key(str(output["path"]))
            content = self.artifacts.get(key)
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {key}")
            frame = pd.read_parquet(io.BytesIO(content))
            timestamps = pd.to_datetime(frame["timestamp"], utc=True)
            frame = frame.loc[timestamps < end].copy()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError(f"dataset has no rows before requested end: {dataset}")
        return pd.concat(frames, ignore_index=True)

    def _persist(
        self,
        *,
        job: Job,
        config: Mapping[str, Any],
        data_manifest: Mapping[str, Any],
        data_manifest_bytes: bytes,
        stage: str,
        results: Mapping[str, BollRsiSlopeResult],
    ) -> Mapping[str, Any]:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        run_id = (
            f"{created.strftime('%Y%m%dT%H%M%SZ')}-boll-rsi-{stage.replace('_', '-')}-"
            f"{job.id[-8:]}"
        )
        run_prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("BOLL-RSI run artifacts are immutable")
        metrics_payload = {
            label: {
                "range": {
                    "start_utc_inclusive": result.start_utc_inclusive,
                    "end_utc_exclusive": result.end_utc_exclusive,
                },
                "metrics": dict(result.metrics),
                "data_quality": dict(result.data_quality),
                "funding": dict(result.funding),
                "execution": dict(result.execution),
                "signal_funnel": dict(result.signal_funnel),
            }
            for label, result in results.items()
        }
        combined_signals = self._combine(results, "signals")
        combined_trades = self._combine(results, "trades")
        combined_equity = self._combine(results, "equity")
        outputs = [
            self._put_json(f"{run_prefix}/metrics.json", metrics_payload),
            self._put_parquet(f"{run_prefix}/signals.parquet", combined_signals),
            self._put_parquet(f"{run_prefix}/trades.parquet", combined_trades),
            self._put_parquet(f"{run_prefix}/equity.parquet", combined_equity),
        ]
        report_key = f"reports/backtests/{run_id}.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._report(run_id, config, data_manifest, stage, results).encode(
                    "utf-8"
                ),
            )
        )
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": f"boll_rsi_slope_{stage}",
            "intent": "baseline_backtest",
            "pipeline_profile": "fast_screen",
            "pipeline_stage": stage,
            "status": "succeeded",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "authorization_id": job.payload["authorization_id"],
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "strategy": {
                "strategy_spec_id": self.STRATEGY_SPEC_ID,
                "session_id": config["session_id"],
                "strategy_version_id": config["strategy_version_id"],
                "baseline_immutable": True,
                "strategy_artifact_key": config["strategy_artifact_key"],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_reference_only",
            "data_manifest": {
                "artifact_key": config["data_manifest_key"],
                "sha256": hashlib.sha256(data_manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "time_range_or_splits": (
                dict(config["smoke"])
                if stage == "smoke"
                else dict(config["time_splits"])
            ),
            "cost_model": dict(config["cost_model"]),
            "funding": dict(config["funding"]),
            "execution": dict(config["execution"]),
            "random_seed": int(config["random_seed"]),
            "code_version": _source_tree_digest(self.root),
            "git_revision": _git_revision(self.root),
            "engine": self.ENGINE_ID,
            "results": metrics_payload,
            "outputs": outputs,
            "locked_test": {
                "used": False,
                "inspection_count": 0,
                "contaminated": False,
                "range": dict(config["time_splits"]["locked_test"]),
            },
            "claims": {
                "viability_proven": False,
                "profitability_proven": False,
                "leverage_safety_proven": False,
                "okx_validated": False,
                "production_ready": False,
            },
            "limitations": list(config["limitations"]),
        }
        outputs.append(self._put_json(manifest_key, manifest))
        summary = {
            "run_id": run_id,
            "strategy_version_id": config["strategy_version_id"],
            "stage": stage,
            "metrics": {
                label: dict(result.metrics) for label, result in results.items()
            },
            "locked_test_used": False,
        }
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type=f"boll_rsi_slope_{stage}",
                artifact_key=report_key,
                summary=summary,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [item["artifact_key"] for item in outputs],
            "summary": summary,
            "locked_test_used": False,
        }

    @staticmethod
    def _combine(
        results: Mapping[str, BollRsiSlopeResult], attribute: str
    ) -> pd.DataFrame:
        frames = []
        for label, result in results.items():
            frame = getattr(result, attribute)
            frames.append(frame.assign(split=label))
        return pd.concat(frames, ignore_index=True)

    def _put_json(self, artifact_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        return self._put_bytes(artifact_key, content)

    def _put_parquet(self, artifact_key: str, frame: pd.DataFrame) -> dict[str, Any]:
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False, compression="zstd")
        return self._put_bytes(artifact_key, buffer.getvalue(), rows=len(frame))

    def _put_bytes(
        self, artifact_key: str, content: bytes, *, rows: int | None = None
    ) -> dict[str, Any]:
        self.artifacts.put(artifact_key, content)
        record: dict[str, Any] = {
            "artifact_key": artifact_key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if rows is not None:
            record["rows"] = rows
        return record

    @staticmethod
    def _report(
        run_id: str,
        config: Mapping[str, Any],
        data_manifest: Mapping[str, Any],
        stage: str,
        results: Mapping[str, BollRsiSlopeResult],
    ) -> str:
        rows = []
        for label, result in results.items():
            metrics = result.metrics
            rows.append(
                "| {label} | {trades:.0f} | {ret:.2%} | {dd:.2%} | {pf:.4f} | {exp:.6f} | {hold:.2f} |".format(
                    label=label,
                    trades=metrics["trade_count"],
                    ret=metrics["total_return"],
                    dd=metrics["max_drawdown"],
                    pf=metrics["profit_factor"],
                    exp=metrics["expectancy"],
                    hold=metrics["average_holding_minutes"],
                )
            )
        return "\n".join(
            [
                "# ETH 永续 BOLL-RSI 斜率顺势深回调：Native 研究报告",
                "",
                f"- Run：`{run_id}`",
                f"- Baseline：`{config['strategy_version_id']}`（不可变）",
                f"- Stage：`{stage}`；Pipeline：`fast_screen`",
                f"- 数据版本：`{data_manifest['data_version']}`",
                "- 市场：Binance ETHUSDT USDT 本位永续参考数据；不传播为 OKX 验证。",
                "- 杠杆：1x研究设置；仓位上限和风险预算按保守执行模型计算。",
                "- locked test 未读取；参数搜索、压力测试、dry-run、live trade 均未运行。",
                "",
                "## 结果",
                "",
                "| split | 交易数 | 净收益 | 最大回撤 | PF | 单笔期望/初始权益 | 平均持仓分钟 |",
                "|---|---:|---:|---:|---:|---:|---:|",
                *rows,
                "",
                "## 执行边界",
                "",
                "- 手续费 0.05%/边，滑点 2 bps/边，funding 不静默补零。",
                "- Baseline只使用深回调外轨版本；浅回调与量价过滤没有在本Run中混入。",
                "- 成交量字段只保留用于未来独立假设，本报告不据其形成过滤结论。",
                "- 本报告不承诺盈利，也不构成实盘建议。",
                "",
            ]
        )
