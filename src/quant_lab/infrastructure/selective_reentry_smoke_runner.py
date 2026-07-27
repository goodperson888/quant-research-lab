from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from quant_lab.application.selective_reentry_smoke import (
    SelectiveReentrySmokeBacktester,
    SelectiveReentrySmokeResult,
)
from quant_lab.application.services import new_id
from quant_lab.domain.models import Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .artifact_store import LocalArtifactStore


class SelectiveReentrySmokeRunner:
    """Native smoke handler for the explicitly approved immutable Candidate."""

    ENGINE_ID = "quant_lab_native_selective_reentry_smoke_v1"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "intent",
            "session_id",
            "strategy_version_id",
            "subject_id",
            "config_artifact_key",
            "agent_run_id",
            "correctness_gate_result_id",
            "confirmed_by_user",
            "pipeline_profile",
            "locked_test_used",
            "run_fast_screen",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"candidate smoke payload missing: {sorted(missing)}")
        if payload["intent"] != "candidate_smoke":
            raise ValueError("candidate smoke handler requires candidate_smoke intent")
        if payload["subject_id"] != payload["strategy_version_id"]:
            raise ValueError("candidate smoke approval must identify the exact subject")
        if payload["confirmed_by_user"] is not True:
            raise ValueError("candidate smoke requires explicit user approval")
        if payload["pipeline_profile"] != "smoke":
            raise ValueError("candidate smoke requires the smoke Pipeline Profile")
        if payload["locked_test_used"] is not False:
            raise ValueError("candidate smoke must not use locked-test data")
        if payload["run_fast_screen"] is not False:
            raise ValueError("candidate smoke must stop before fast_screen")

        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        loaded = yaml.safe_load(self.artifacts.get(config_key))
        if not isinstance(loaded, dict):
            raise ValueError("candidate smoke config must be a YAML mapping")
        self._validate_config(loaded, payload)
        return loaded

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config = self.validate_payload(job.payload)
        manifest_key = validate_artifact_key(config["data_manifest_key"])
        manifest_bytes = self.artifacts.get(manifest_key)
        data_manifest = json.loads(manifest_bytes)
        self._validate_data_manifest(config, data_manifest)

        result = SelectiveReentrySmokeBacktester(
            candidate_version_id=config["candidate_version_id"],
            baseline_version_id=config["baseline_version_id"],
            fee_per_side=float(config["cost_model"]["fee_per_side"]),
            slippage_bps_per_side=float(
                config["cost_model"]["slippage_bps_per_side"]
            ),
            initial_equity=float(config["execution"]["initial_equity"]),
            tick_size=float(config["execution"]["tick_size"]),
            quantity_step=float(config["execution"]["quantity_step"]),
            minimum_notional=float(config["execution"]["minimum_notional"]),
        ).run(
            ohlcv_5m=self._load_dataset(data_manifest, "futures_ohlcv", "5m"),
            ohlcv_15m=self._load_dataset(data_manifest, "futures_ohlcv", "15m"),
            ohlcv_1h=self._load_dataset(data_manifest, "futures_ohlcv", "1h"),
            funding=self._load_dataset(data_manifest, "funding_rate", None),
            mark_15m=self._load_dataset(data_manifest, "mark_price", "15m"),
            warmup_start_utc_inclusive=config["time_range"][
                "warmup_start_utc_inclusive"
            ],
            start_utc_inclusive=config["time_range"]["start_utc_inclusive"],
            end_utc_exclusive=config["time_range"]["end_utc_exclusive"],
        )

        run_id = self._run_id(job)
        run_prefix = f"experiments/runs/{run_id}"
        immutable_manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(immutable_manifest_key):
            raise FileExistsError("smoke run artifacts are immutable and already exist")

        outputs = [
            self._put_json(
                f"{run_prefix}/metrics.json",
                {
                    "metrics": dict(result.metrics),
                    "data_quality": dict(result.data_quality),
                    "funding": dict(result.funding),
                    "execution": dict(result.execution),
                },
            ),
            self._put_parquet(f"{run_prefix}/signals.parquet", result.signals),
            self._put_parquet(f"{run_prefix}/trades.parquet", result.trades),
            self._put_parquet(f"{run_prefix}/equity.parquet", result.equity),
        ]
        log_key = f"{run_prefix}/job.log.jsonl"
        outputs.append(
            self._put_bytes(
                log_key,
                (
                    json.dumps(
                        {
                            "level": "info",
                            "event": "candidate_smoke.completed",
                            "job_id": job.id,
                            "subject_id": config["candidate_version_id"],
                            "window": {
                                "start_utc_inclusive": result.start_utc_inclusive,
                                "end_utc_exclusive": result.end_utc_exclusive,
                            },
                            "trade_count": result.metrics["trade_count"],
                            "fast_screen_started": False,
                            "locked_test_used": False,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
        )

        report_key = f"reports/backtests/{run_id}.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._build_report(run_id, config, data_manifest, result).encode(
                    "utf-8"
                ),
            )
        )
        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "candidate_smoke",
            "intent": "baseline_backtest",
            "pipeline_profile": "smoke",
            "pipeline_stage": "smoke_backtest",
            "status": "succeeded",
            "created_at": created_at,
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "approval": {
                "subject_id": job.payload["subject_id"],
                "confirmed_by_user": True,
                "correctness_gate_result_id": job.payload[
                    "correctness_gate_result_id"
                ],
            },
            "strategy": {
                "session_id": config["session_id"],
                "baseline_version_id": config["baseline_version_id"],
                "candidate_version_id": config["candidate_version_id"],
                "candidate_immutable": True,
                "strategy_artifact_key": config["strategy_artifact_key"],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_reference_only",
            "data_manifest": {
                "artifact_key": manifest_key,
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "time_range": dict(config["time_range"]),
            "cost_model": dict(config["cost_model"]),
            "funding": {
                **dict(config["funding"]),
                "result": dict(result.funding),
            },
            "execution": dict(result.execution),
            "random_seed": int(config["random_seed"]),
            "code_version": _source_tree_digest(self.root),
            "git_revision": _git_revision(self.root),
            "engine": self.ENGINE_ID,
            "results": {
                "metrics": dict(result.metrics),
                "data_quality": dict(result.data_quality),
            },
            "outputs": outputs,
            "locked_test": {
                "used": False,
                "inspection_count": 0,
                "contaminated": False,
            },
            "fast_screen": {"started": False, "authorized": False},
            "limitations": list(config["limitations"]),
            "claims": {
                "viability_proven": False,
                "profitability_proven": False,
                "leverage_safety_proven": False,
                "okx_validated": False,
                "production_ready": False,
            },
        }
        outputs.append(self._put_json(immutable_manifest_key, manifest))

        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="candidate_smoke",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "candidate_version_id": config["candidate_version_id"],
                    "trade_count": result.metrics["trade_count"],
                    "total_return": result.metrics["total_return"],
                    "max_drawdown": result.metrics["max_drawdown"],
                    "fast_screen_started": False,
                    "locked_test_used": False,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": immutable_manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [item["artifact_key"] for item in outputs],
            "summary": {
                "metrics": dict(result.metrics),
                "data_quality": dict(result.data_quality),
                "funding": dict(result.funding),
                "fast_screen_started": False,
                "locked_test_used": False,
            },
        }

    def _validate_config(
        self, config: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        required = {
            "schema_version",
            "session_id",
            "baseline_version_id",
            "candidate_version_id",
            "proposal_id",
            "strategy_artifact_key",
            "market_profile",
            "data_manifest_key",
            "time_range",
            "cost_model",
            "funding",
            "execution",
            "pipeline",
            "random_seed",
            "limitations",
        }
        missing = required - set(config)
        if missing:
            raise ValueError(f"candidate smoke config missing: {sorted(missing)}")
        if config["session_id"] != payload["session_id"]:
            raise ValueError("job session does not match smoke config")
        if config["candidate_version_id"] != payload["strategy_version_id"]:
            raise ValueError("job subject does not match smoke config")
        if config["market_profile"] != "crypto_perpetual.binance.eth":
            raise ValueError("candidate smoke is scoped to Binance reference data only")
        candidate = self.repository.get_strategy_version(config["candidate_version_id"])
        if candidate.status != "candidate" or not candidate.immutable:
            raise ValueError("candidate smoke requires an immutable Candidate")
        if (
            candidate.content_snapshot.get("baseline_version_id")
            != config["baseline_version_id"]
        ):
            raise ValueError("candidate does not preserve the configured baseline lineage")
        gate = self.repository.get_gate_evaluation(
            str(payload["correctness_gate_result_id"])
        )
        if (
            gate.subject_id != candidate.id
            or gate.gate_name != "correctness"
            or gate.status != "passed"
            or gate.profile_id != "smoke"
            or gate.market_profile != config["market_profile"]
        ):
            raise ValueError("candidate smoke requires the same subject's passed correctness gate")
        for key in ("strategy_artifact_key", "data_manifest_key"):
            validate_artifact_key(str(config[key]))
            if not self.artifacts.exists(str(config[key])):
                raise ValueError(f"required artifact does not exist: {config[key]}")
        time_range = config["time_range"]
        if time_range != {
            "warmup_start_utc_inclusive": "2025-07-20T00:00:00Z",
            "start_utc_inclusive": "2025-07-27T00:00:00Z",
            "end_utc_exclusive": "2025-08-10T00:00:00Z",
        }:
            raise ValueError("candidate smoke window differs from the exact user approval")
        if config["pipeline"] != {
            "profile_id": "smoke",
            "fast_screen_started": False,
            "locked_test_used": False,
        }:
            raise ValueError("candidate smoke must stop before fast_screen and locked test")
        if config["funding"].get("zero_funding_fallback_allowed") is not False:
            raise ValueError("funding must not be silently zero")
        if config["funding"].get("missing_policy") != "adverse_p99_abs_observed":
            raise ValueError("candidate smoke requires the declared adverse funding policy")

    @staticmethod
    def _validate_data_manifest(
        config: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> None:
        if manifest["market_profile"] != config["market_profile"]:
            raise ValueError("data manifest market profile does not match smoke scope")
        start = pd.Timestamp(config["time_range"]["warmup_start_utc_inclusive"])
        end = pd.Timestamp(config["time_range"]["end_utc_exclusive"])
        manifest_start = pd.Timestamp(manifest["range"]["start_utc_inclusive"])
        manifest_end = pd.Timestamp(manifest["range"]["end_utc_exclusive"])
        if manifest_start > start or manifest_end < end:
            raise ValueError("data manifest does not cover warmup and smoke window")

    def _load_dataset(
        self,
        manifest: Mapping[str, Any],
        dataset: str,
        timeframe: str | None,
        *,
        end_utc_exclusive: str | None = None,
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
            key = validate_artifact_key(output["path"])
            content = self.artifacts.get(key)
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"dataset checksum mismatch: {key}")
            frame = pd.read_parquet(io.BytesIO(content))
            if end_utc_exclusive is not None and "timestamp" in frame:
                timestamps = pd.to_datetime(frame["timestamp"], utc=True)
                frame = frame.loc[
                    timestamps < pd.Timestamp(end_utc_exclusive)
                ].copy()
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise ValueError(f"dataset has no outputs: {dataset}/{timeframe}")
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _run_id(job: Job) -> str:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        return f"{created.strftime('%Y%m%dT%H%M%SZ')}-candidate-smoke-{job.id[-8:]}"

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
    def _build_report(
        run_id: str,
        config: Mapping[str, Any],
        data_manifest: Mapping[str, Any],
        result: SelectiveReentrySmokeResult,
    ) -> str:
        metrics = result.metrics

        def value(item: Any, *, percent: bool = False) -> str:
            if item is None:
                return "—"
            return f"{float(item):.2%}" if percent else f"{float(item):.6f}"

        return "\n".join(
            [
                "# 趋势跟随择机再入 Candidate v1：Native smoke 报告",
                "",
                f"- Run：`{run_id}`",
                f"- Candidate：`{config['candidate_version_id']}`",
                f"- Baseline：`{config['baseline_version_id']}`（未覆盖）",
                f"- UTC 窗口：`[{result.start_utc_inclusive}, {result.end_utc_exclusive})`",
                f"- 数据版本：`{data_manifest['data_version']}`",
                "- 市场范围：Binance ETH/USDT USDT 本位永续参考数据；不传播为 OKX 验证。",
                "- Pipeline：smoke；fast_screen 未启动；locked test 未使用。",
                "",
                "## Smoke 结果",
                "",
                f"- 交易数：{metrics['trade_count']}",
                f"- 信号记录：{metrics['signal_records']}；成交入场：{metrics['filled_entries']}",
                f"- 净收益：{value(metrics['total_return'], percent=True)}",
                f"- 最大回撤：{value(metrics['max_drawdown'], percent=True)}",
                f"- Profit Factor：{value(metrics['profit_factor'])}",
                f"- 单笔期望：{value(metrics['expectancy'])}",
                f"- 手续费：{value(metrics['fees'])}；资金费率 PnL：{value(metrics['funding_pnl'])}",
                "",
                "## 数据与资金费率",
                "",
                f"- K线完整：{result.data_quality['complete']}；实际/理论行数：{result.data_quality['actual_rows']} / {result.data_quality['expected_rows']}。",
                f"- 资金费率预期 {result.funding['expected_events']}，可用 {result.funding['available_events']}。",
                f"- 持仓期间真实应用 {result.funding['observed_events_applied_while_open']}，保守插补 {result.funding['imputed_events_applied_while_open']}。",
                "- 资金费率缺失不会静默按 0；缺失时使用方向不利的观测绝对值 p99 代理。",
                "",
                "## 结论边界",
                "",
                "- 本报告只验证指定小窗口的信号、时点、成本和执行连线，不是 viability 或盈利证据。",
                "- 50x 只记录为逐仓保证金设置；名义仓位和账户风险仍受冻结上限约束。",
                "- 未完整建模历史杠杆阶梯、维持保证金、标记价格强平路径，因此不得声称 50x 安全。",
                "- fast_screen、参数搜索、locked test、stress、dry-run 和 live trade 均未启动。",
                "",
                "## 停止点",
                "",
                "本次精确授权在 Native smoke 完成后结束。后续任何 fast_screen 必须重新取得该 Candidate subject 的明确批准。",
                "",
            ]
        )


class NativeBacktestRouter:
    """Keep the historical baseline handler while adding a scoped smoke handler."""

    def __init__(
        self,
        *,
        baseline_runner: Any,
        candidate_smoke_runner: SelectiveReentrySmokeRunner,
        candidate_fast_screen_runner: Any | None = None,
    ) -> None:
        self.baseline_runner = baseline_runner
        self.candidate_smoke_runner = candidate_smoke_runner
        self.candidate_fast_screen_runner = candidate_fast_screen_runner

    def __call__(self, job: Job) -> Mapping[str, Any]:
        if job.payload.get("intent") == "candidate_smoke":
            return self.candidate_smoke_runner(job)
        if job.payload.get("intent") == "candidate_fast_screen":
            if self.candidate_fast_screen_runner is None:
                raise RuntimeError("candidate fast-screen runner is not configured")
            return self.candidate_fast_screen_runner(job)
        return self.baseline_runner(job)


class SelectiveReentryFastScreenRunner(SelectiveReentrySmokeRunner):
    """Bounded train/validation screen that excludes the reserved locked test."""

    ENGINE_ID = "quant_lab_native_selective_reentry_fast_screen_v1"

    def validate_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "intent",
            "session_id",
            "strategy_version_id",
            "subject_id",
            "config_artifact_key",
            "agent_run_id",
            "correctness_gate_result_id",
            "smoke_manifest_artifact_key",
            "confirmed_by_user",
            "pipeline_profile",
            "locked_test_used",
            "run_viability",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"candidate fast-screen payload missing: {sorted(missing)}")
        if payload["intent"] != "candidate_fast_screen":
            raise ValueError("fast-screen handler requires candidate_fast_screen intent")
        if payload["subject_id"] != payload["strategy_version_id"]:
            raise ValueError("fast-screen approval must identify the exact subject")
        if payload["confirmed_by_user"] is not True:
            raise ValueError("fast-screen requires explicit user approval")
        if payload["pipeline_profile"] != "fast_screen":
            raise ValueError("fast-screen requires the fast_screen Pipeline Profile")
        if payload["locked_test_used"] is not False:
            raise ValueError("fast-screen must not use locked-test data")
        if payload["run_viability"] is not False:
            raise ValueError("this approved scope must stop before viability")
        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        loaded = yaml.safe_load(self.artifacts.get(config_key))
        if not isinstance(loaded, dict):
            raise ValueError("candidate fast-screen config must be a YAML mapping")
        self._validate_fast_screen_config(loaded, payload)
        return loaded

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config = self.validate_payload(job.payload)
        data_manifest_key = validate_artifact_key(config["data_manifest_key"])
        data_manifest_bytes = self.artifacts.get(data_manifest_key)
        data_manifest = json.loads(data_manifest_bytes)
        self._validate_data_manifest_for_fast_screen(config, data_manifest)

        validation_end = config["time_splits"]["validation"]["end_utc_exclusive"]
        datasets = {
            "ohlcv_5m": self._load_dataset(
                data_manifest,
                "futures_ohlcv",
                "5m",
                end_utc_exclusive=validation_end,
            ),
            "ohlcv_15m": self._load_dataset(
                data_manifest,
                "futures_ohlcv",
                "15m",
                end_utc_exclusive=validation_end,
            ),
            "ohlcv_1h": self._load_dataset(
                data_manifest,
                "futures_ohlcv",
                "1h",
                end_utc_exclusive=validation_end,
            ),
            "funding": self._load_dataset(
                data_manifest,
                "funding_rate",
                None,
                end_utc_exclusive=validation_end,
            ),
            "mark_15m": self._load_dataset(
                data_manifest,
                "mark_price",
                "15m",
                end_utc_exclusive=validation_end,
            ),
        }
        backtester = SelectiveReentrySmokeBacktester(
            candidate_version_id=config["candidate_version_id"],
            baseline_version_id=config["baseline_version_id"],
            fee_per_side=float(config["cost_model"]["fee_per_side"]),
            slippage_bps_per_side=float(
                config["cost_model"]["slippage_bps_per_side"]
            ),
            initial_equity=float(config["execution"]["initial_equity"]),
            tick_size=float(config["execution"]["tick_size"]),
            quantity_step=float(config["execution"]["quantity_step"]),
            minimum_notional=float(config["execution"]["minimum_notional"]),
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

        run_id = self._fast_screen_run_id(job)
        run_prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("fast-screen artifacts are immutable and already exist")

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
            }
            for label, result in results.items()
        }
        combined_signals = pd.concat(
            [
                result.signals.assign(split=label)
                for label, result in results.items()
            ],
            ignore_index=True,
        )
        combined_trades = pd.concat(
            [
                result.trades.assign(split=label)
                for label, result in results.items()
            ],
            ignore_index=True,
        )
        combined_equity = pd.concat(
            [
                result.equity.assign(split=label)
                for label, result in results.items()
            ],
            ignore_index=True,
        )
        outputs = [
            self._put_json(f"{run_prefix}/metrics.json", metrics_payload),
            self._put_parquet(f"{run_prefix}/signals.parquet", combined_signals),
            self._put_parquet(f"{run_prefix}/trades.parquet", combined_trades),
            self._put_parquet(f"{run_prefix}/equity.parquet", combined_equity),
        ]
        outputs.append(
            self._put_bytes(
                f"{run_prefix}/job.log.jsonl",
                (
                    json.dumps(
                        {
                            "level": "info",
                            "event": "candidate_fast_screen.completed",
                            "job_id": job.id,
                            "subject_id": config["candidate_version_id"],
                            "train_trade_count": results["train"].metrics[
                                "trade_count"
                            ],
                            "validation_trade_count": results["validation"].metrics[
                                "trade_count"
                            ],
                            "viability_started": False,
                            "locked_test_used": False,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
        )
        report_key = f"reports/backtests/{run_id}.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._build_fast_screen_report(
                    run_id, config, data_manifest, results
                ).encode("utf-8"),
            )
        )

        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "candidate_fast_screen",
            "intent": "baseline_backtest",
            "pipeline_profile": "fast_screen",
            "pipeline_stage": "fast_screen",
            "status": "succeeded",
            "created_at": created_at,
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "approval": {
                "subject_id": job.payload["subject_id"],
                "confirmed_by_user": True,
                "correctness_gate_result_id": job.payload[
                    "correctness_gate_result_id"
                ],
                "smoke_manifest_artifact_key": job.payload[
                    "smoke_manifest_artifact_key"
                ],
            },
            "strategy": {
                "session_id": config["session_id"],
                "baseline_version_id": config["baseline_version_id"],
                "candidate_version_id": config["candidate_version_id"],
                "candidate_immutable": True,
                "strategy_artifact_key": config["strategy_artifact_key"],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_reference_only",
            "data_manifest": {
                "artifact_key": data_manifest_key,
                "sha256": hashlib.sha256(data_manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "time_splits": dict(config["time_splits"]),
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
            "viability": {"started": False, "authorized": False},
            "limitations": list(config["limitations"]),
            "claims": {
                "viability_proven": False,
                "profitability_proven": False,
                "leverage_safety_proven": False,
                "okx_validated": False,
                "production_ready": False,
            },
        }
        outputs.append(self._put_json(manifest_key, manifest))
        validation_metrics = results["validation"].metrics
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="candidate_fast_screen",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "candidate_version_id": config["candidate_version_id"],
                    "train_trade_count": results["train"].metrics["trade_count"],
                    "validation_trade_count": validation_metrics["trade_count"],
                    "validation_total_return": validation_metrics["total_return"],
                    "validation_profit_factor": validation_metrics["profit_factor"],
                    "validation_expectancy": validation_metrics["expectancy"],
                    "validation_max_drawdown": validation_metrics["max_drawdown"],
                    "viability_started": False,
                    "locked_test_used": False,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [item["artifact_key"] for item in outputs],
            "summary": {
                "train": metrics_payload["train"],
                "validation": metrics_payload["validation"],
                "viability_started": False,
                "locked_test_used": False,
            },
        }

    def _validate_fast_screen_config(
        self, config: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        required = {
            "schema_version",
            "session_id",
            "baseline_version_id",
            "candidate_version_id",
            "proposal_id",
            "strategy_artifact_key",
            "market_profile",
            "data_manifest_key",
            "smoke_manifest_artifact_key",
            "time_splits",
            "cost_model",
            "funding",
            "execution",
            "pipeline",
            "random_seed",
            "limitations",
        }
        missing = required - set(config)
        if missing:
            raise ValueError(f"candidate fast-screen config missing: {sorted(missing)}")
        if config["session_id"] != payload["session_id"]:
            raise ValueError("job session does not match fast-screen config")
        if config["candidate_version_id"] != payload["strategy_version_id"]:
            raise ValueError("job subject does not match fast-screen config")
        if (
            config["smoke_manifest_artifact_key"]
            != payload["smoke_manifest_artifact_key"]
        ):
            raise ValueError("fast-screen does not reference its approved smoke evidence")
        candidate = self.repository.get_strategy_version(config["candidate_version_id"])
        if candidate.status != "candidate" or not candidate.immutable:
            raise ValueError("fast-screen requires an immutable Candidate")
        if (
            candidate.content_snapshot.get("baseline_version_id")
            != config["baseline_version_id"]
        ):
            raise ValueError("candidate does not preserve the configured baseline lineage")
        gate = self.repository.get_gate_evaluation(
            str(payload["correctness_gate_result_id"])
        )
        if (
            gate.subject_id != candidate.id
            or gate.gate_name != "correctness"
            or gate.status != "passed"
            or gate.market_profile != config["market_profile"]
        ):
            raise ValueError("fast-screen requires the same subject's correctness gate")
        for key in (
            "strategy_artifact_key",
            "data_manifest_key",
            "smoke_manifest_artifact_key",
        ):
            validate_artifact_key(str(config[key]))
            if not self.artifacts.exists(str(config[key])):
                raise ValueError(f"required artifact does not exist: {config[key]}")
        smoke_manifest = json.loads(
            self.artifacts.get(config["smoke_manifest_artifact_key"])
        )
        if (
            smoke_manifest.get("run_type") != "candidate_smoke"
            or smoke_manifest.get("status") != "succeeded"
            or smoke_manifest.get("strategy", {}).get("candidate_version_id")
            != candidate.id
            or smoke_manifest.get("locked_test", {}).get("used") is not False
        ):
            raise ValueError("fast-screen requires successful same-subject smoke evidence")
        if config["pipeline"] != {
            "profile_id": "fast_screen",
            "viability_started": False,
            "locked_test_used": False,
        }:
            raise ValueError("fast-screen must stop before viability and locked test")
        splits = config["time_splits"]
        if set(splits) != {"train", "validation", "locked_test"}:
            raise ValueError("fast-screen requires train/validation/locked_test declarations")
        if (
            splits["train"]["end_utc_exclusive"]
            != splits["validation"]["start_utc_inclusive"]
        ):
            raise ValueError("validation must follow train without overlap")
        if (
            splits["validation"]["end_utc_exclusive"]
            != splits["locked_test"]["start_utc_inclusive"]
        ):
            raise ValueError("locked test must follow validation without overlap")
        if splits["locked_test"].get("use_in_this_job") is not False:
            raise ValueError("locked test must be explicitly excluded")
        for label in ("train", "validation"):
            split = splits[label]
            warmup = pd.Timestamp(split["warmup_start_utc_inclusive"])
            start = pd.Timestamp(split["start_utc_inclusive"])
            end = pd.Timestamp(split["end_utc_exclusive"])
            if not warmup < start < end:
                raise ValueError(f"invalid {label} warmup/evaluation range")
        if config["funding"].get("zero_funding_fallback_allowed") is not False:
            raise ValueError("funding must not be silently zero")
        if config["funding"].get("missing_policy") != "adverse_p99_abs_observed":
            raise ValueError("fast-screen requires the declared adverse funding policy")

    @staticmethod
    def _validate_data_manifest_for_fast_screen(
        config: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> None:
        if manifest["market_profile"] != config["market_profile"]:
            raise ValueError("data manifest market profile does not match fast-screen scope")
        start = pd.Timestamp(
            config["time_splits"]["train"]["warmup_start_utc_inclusive"]
        )
        end = pd.Timestamp(
            config["time_splits"]["validation"]["end_utc_exclusive"]
        )
        manifest_start = pd.Timestamp(manifest["range"]["start_utc_inclusive"])
        manifest_end = pd.Timestamp(manifest["range"]["end_utc_exclusive"])
        if manifest_start > start or manifest_end < end:
            raise ValueError("data manifest does not cover fast-screen train/validation")

    @staticmethod
    def _fast_screen_run_id(job: Job) -> str:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        return f"{created.strftime('%Y%m%dT%H%M%SZ')}-candidate-fast-{job.id[-8:]}"

    @staticmethod
    def _build_fast_screen_report(
        run_id: str,
        config: Mapping[str, Any],
        data_manifest: Mapping[str, Any],
        results: Mapping[str, SelectiveReentrySmokeResult],
    ) -> str:
        def metric(value: Any, *, percent: bool = False) -> str:
            if value is None:
                return "—"
            return f"{float(value):.2%}" if percent else f"{float(value):.6f}"

        rows = []
        for label in ("train", "validation"):
            result = results[label]
            values = result.metrics
            rows.append(
                "| {label} | [{start}, {end}) | {trades} | {ret} | {dd} | {pf} | {exp} |".format(
                    label=label,
                    start=result.start_utc_inclusive,
                    end=result.end_utc_exclusive,
                    trades=values["trade_count"],
                    ret=metric(values["total_return"], percent=True),
                    dd=metric(values["max_drawdown"], percent=True),
                    pf=metric(values["profit_factor"]),
                    exp=metric(values["expectancy"]),
                )
            )
        validation = results["validation"]
        return "\n".join(
            [
                "# 趋势跟随择机再入 Candidate v1：Native fast_screen 报告",
                "",
                f"- Run：`{run_id}`",
                f"- Candidate：`{config['candidate_version_id']}`",
                f"- Baseline：`{config['baseline_version_id']}`（未覆盖）",
                f"- 数据版本：`{data_manifest['data_version']}`",
                "- 市场范围：Binance ETH/USDT USDT 本位永续参考数据；不传播为 OKX 验证。",
                "- Pipeline：fast_screen；viability 未运行；locked test 未使用。",
                "",
                "## Train / Validation",
                "",
                "| split | UTC 范围 | 交易数 | 净收益 | 最大回撤 | PF | 单笔期望 |",
                "|---|---|---:|---:|---:|---:|---:|",
                *rows,
                "",
                "## Validation 数据与资金费率",
                "",
                f"- K线完整：{validation.data_quality['complete']}；实际/理论行数：{validation.data_quality['actual_rows']} / {validation.data_quality['expected_rows']}。",
                f"- 资金费率预期 {validation.funding['expected_events']}，可用 {validation.funding['available_events']}。",
                f"- 持仓期间真实应用 {validation.funding['observed_events_applied_while_open']}，保守插补 {validation.funding['imputed_events_applied_while_open']}。",
                "- 资金费率缺失不会静默按 0。",
                "",
                "## 结论边界",
                "",
                "- fast_screen 只提供有界 train/validation 初筛证据；是否满足 viability 必须作为下一道独立 Gate 重新评估。",
                "- 未运行参数搜索、成本压力、Regime、Pine 对账、locked test、dry-run 或 live trade。",
                "- 50x 历史杠杆阶梯、维持保证金和标记价格强平路径未完整建模，不能据此声称杠杆安全。",
                "",
                "## 停止点",
                "",
                "本次授权在 fast_screen 和 Gate 记录完成后结束。不得自动进入 viability。",
                "",
            ]
        )
