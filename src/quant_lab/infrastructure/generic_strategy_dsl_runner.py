from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_lab.application.equity_series import compress_equity_points
from quant_lab.application.ports import TrialEvaluationRequest, TrialEvaluationResult
from quant_lab.application.research_authorization import ResearchAuthorizationService
from quant_lab.application.services import new_id
from quant_lab.application.strategy_dsl import (
    GenericStrategyDslBacktester,
    StrategyDslResult,
    analyze_strategy_dsl,
)
from quant_lab.domain.models import Job, Report, StrategyVersion, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .artifact_store import LocalArtifactStore


GENERIC_DSL_SPEC_ID = "generic_strategy_dsl_v1"
DEFAULT_DATA_MANIFEST = (
    "data/manifests/binance_ethusdt_perpetual_20240720_20260720_v2.json"
)


class GenericStrategyDslRunner:
    ENGINE_ID = "quant_lab_generic_strategy_dsl_v1"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.authorizations = ResearchAuthorizationService(repository)

    def prepare_config(self, version: StrategyVersion, session_id: str) -> str:
        capability = analyze_strategy_dsl(version.content_snapshot)
        if not capability.executable or capability.normalized is None:
            raise ValueError("通用策略执行器无法运行：" + "；".join(capability.reasons))
        manifest_key = validate_artifact_key(DEFAULT_DATA_MANIFEST)
        manifest = json.loads(self.artifacts.get(manifest_key))
        start = pd.Timestamp(manifest["range"]["start_utc_inclusive"])
        end = pd.Timestamp(manifest["range"]["end_utc_exclusive"])
        locked_start = end - pd.Timedelta(days=30)
        validation_start = locked_start - pd.Timedelta(days=90)
        trade_start = start + pd.Timedelta(days=30)
        if validation_start <= trade_start:
            raise ValueError("当前数据范围不足以建立 train/validation/locked_test")
        config = {
            "schema_version": 1,
            "strategy_spec_id": GENERIC_DSL_SPEC_ID,
            "session_id": session_id,
            "strategy_version_id": version.id,
            "strategy_artifact_key": f"artifacts/strategy-specs/{version.id}.json",
            "market_profile": "crypto_perpetual.binance.eth",
            "data_manifest_key": manifest_key,
            "strategy_dsl": dict(capability.normalized),
            "smoke": {
                "warmup_start_utc_inclusive": start.isoformat(),
                "start_utc_inclusive": trade_start.isoformat(),
                "end_utc_exclusive": (trade_start + pd.Timedelta(days=14)).isoformat(),
            },
            "time_splits": {
                "train": {
                    "warmup_start_utc_inclusive": start.isoformat(),
                    "start_utc_inclusive": trade_start.isoformat(),
                    "end_utc_exclusive": validation_start.isoformat(),
                },
                "validation": {
                    "warmup_start_utc_inclusive": (
                        validation_start - pd.Timedelta(days=30)
                    ).isoformat(),
                    "start_utc_inclusive": validation_start.isoformat(),
                    "end_utc_exclusive": locked_start.isoformat(),
                },
                "locked_test": {
                    "start_utc_inclusive": locked_start.isoformat(),
                    "end_utc_exclusive": end.isoformat(),
                    "use_in_this_authorization": False,
                    "status": "reserved_uninspected",
                },
            },
            "cost_model": {
                "execution": "taker",
                "fee_per_side": 0.0005,
                "slippage_bps_per_side": 2.0,
            },
            "funding": {
                "required": True,
                "missing_policy": "adverse_p99_abs_observed",
                "zero_funding_fallback_allowed": False,
            },
            "random_seed": 20260801,
            "limitations": [
                "通用 DSL v1 只支持 Binance ETH 永续参考数据与 15m 执行周期。",
                "仅支持受控指标、条件树、1倍杠杆、固定止损止盈和最长持仓。",
                "高周期指标使用上一根已收盘高周期K线，禁止未来数据。",
                "locked test 未进入本次 smoke、fast_screen 或参数搜索。",
                "结果不构成盈利承诺、模拟盘或实盘授权。",
            ],
        }
        strategy_key = str(config["strategy_artifact_key"])
        config_key = f"artifacts/strategy-specs/{version.id}.config.json"
        strategy_bytes = _json_bytes(capability.normalized)
        config_bytes = _json_bytes(config)
        if self.artifacts.exists(strategy_key):
            if self.artifacts.get(strategy_key) != strategy_bytes:
                raise ValueError("已存在的通用策略定义与冻结 Baseline 不一致")
        else:
            self.artifacts.put(strategy_key, strategy_bytes)
        if self.artifacts.exists(config_key):
            if self.artifacts.get(config_key) != config_bytes:
                raise ValueError("已存在的通用策略配置与冻结 Baseline 不一致")
        else:
            self.artifacts.put(config_key, config_bytes)
        return config_key

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config, stage = self._validate(job)
        manifest_key = validate_artifact_key(str(config["data_manifest_key"]))
        manifest_bytes = self.artifacts.get(manifest_key)
        manifest = json.loads(manifest_bytes)
        requested_timeframes = {
            "15m",
            *{
                str(item["timeframe"])
                for item in config["strategy_dsl"]["indicators"]
            },
        }
        end = (
            config["smoke"]["end_utc_exclusive"]
            if stage == "smoke"
            else config["time_splits"]["validation"]["end_utc_exclusive"]
        )
        ohlcv = {
            timeframe: self._load_dataset(
                manifest,
                "futures_ohlcv",
                timeframe,
                end_utc_exclusive=end,
            )
            for timeframe in requested_timeframes
        }
        funding = self._load_dataset(
            manifest, "funding_rate", None, end_utc_exclusive=end
        )
        backtester = GenericStrategyDslBacktester(
            strategy_version_id=str(config["strategy_version_id"]),
            dsl=config["strategy_dsl"],
            fee_per_side=float(config["cost_model"]["fee_per_side"]),
            slippage_bps_per_side=float(
                config["cost_model"]["slippage_bps_per_side"]
            ),
        )
        if stage == "smoke":
            split_results = {
                "smoke": backtester.run(
                    label="smoke",
                    ohlcv_by_timeframe=ohlcv,
                    funding=funding,
                    **config["smoke"],
                )
            }
        else:
            split_results = {
                label: backtester.run(
                    label=label,
                    ohlcv_by_timeframe=ohlcv,
                    funding=funding,
                    **split,
                )
                for label, split in (
                    ("train", config["time_splits"]["train"]),
                    ("validation", config["time_splits"]["validation"]),
                )
            }
        return self._persist(
            job=job,
            config=config,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            stage=stage,
            results=split_results,
        )

    def _validate(self, job: Job) -> tuple[dict[str, Any], str]:
        stage_by_intent = {
            "generic_strategy_dsl_smoke": "smoke",
            "generic_strategy_dsl_fast_screen": "fast_screen",
        }
        intent = str(job.payload.get("intent", ""))
        if intent not in stage_by_intent:
            raise ValueError("通用策略执行器收到不支持的研究意图")
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
        missing = sorted(required - set(job.payload))
        if missing:
            raise ValueError("通用策略 Job 缺少：" + "、".join(missing))
        if job.payload["subject_id"] != job.payload["strategy_version_id"]:
            raise ValueError("通用策略 Job 的研究对象不一致")
        if job.payload["locked_test_used"] is not False:
            raise ValueError("通用策略 smoke/fast_screen 禁止使用 locked test")
        self.authorizations.assert_stage_allowed(
            str(job.payload["authorization_id"]),
            subject_id=str(job.payload["strategy_version_id"]),
            stage=stage,
        )
        config_key = validate_artifact_key(str(job.payload["config_artifact_key"]))
        config = json.loads(self.artifacts.get(config_key))
        if (
            config.get("schema_version") != 1
            or config.get("strategy_spec_id") != GENERIC_DSL_SPEC_ID
            or config.get("session_id") != job.payload["session_id"]
            or config.get("strategy_version_id")
            != job.payload["strategy_version_id"]
        ):
            raise ValueError("通用策略配置与当前研究对象不匹配")
        version = self.repository.get_strategy_version(
            str(job.payload["strategy_version_id"])
        )
        capability = analyze_strategy_dsl(version.content_snapshot)
        if (
            version.status != "baseline"
            or not version.immutable
            or not capability.executable
        ):
            raise ValueError("通用策略执行要求可执行且不可变的 Baseline")
        gate = self.repository.get_gate_evaluation(
            str(job.payload["correctness_gate_result_id"])
        )
        if (
            gate.subject_id != version.id
            or gate.gate_name != "correctness"
            or gate.status != "passed"
        ):
            raise ValueError("通用策略执行要求同一对象通过 correctness")
        if stage == "fast_screen":
            smoke = json.loads(
                self.artifacts.get(
                    validate_artifact_key(
                        str(job.payload["smoke_manifest_artifact_key"])
                    )
                )
            )
            if (
                smoke.get("run_type") != "generic_strategy_dsl_smoke"
                or smoke.get("strategy", {}).get("strategy_version_id")
                != version.id
                or smoke.get("locked_test", {}).get("used") is not False
                or smoke.get("status") != "succeeded"
            ):
                raise ValueError("通用策略 fast-screen 缺少同对象 smoke 证据")
        return config, stage

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
            raise ValueError(f"数据清单缺少唯一 {dataset}/{timeframe}")
        frames: list[pd.DataFrame] = []
        end = pd.Timestamp(end_utc_exclusive)
        for output in matches[0]["outputs"]:
            key = validate_artifact_key(str(output["path"]))
            content = self.artifacts.get(key)
            if hashlib.sha256(content).hexdigest() != output["sha256"]:
                raise ValueError(f"数据校验和不匹配：{key}")
            frame = pd.read_parquet(io.BytesIO(content))
            timestamps = pd.to_datetime(frame["timestamp"], utc=True)
            selected = frame.loc[timestamps < end].copy()
            if not selected.empty:
                frames.append(selected)
        if not frames:
            raise ValueError(f"{dataset}/{timeframe} 在研究区间没有数据")
        return pd.concat(frames, ignore_index=True)

    def _persist(
        self,
        *,
        job: Job,
        config: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_bytes: bytes,
        stage: str,
        results: Mapping[str, StrategyDslResult],
    ) -> Mapping[str, Any]:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        run_id = (
            f"{created.strftime('%Y%m%dT%H%M%SZ')}-dsl-{stage.replace('_', '-')}-"
            f"{job.id[-8:]}"
        )
        prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("通用策略运行产物已存在且不可覆盖")
        metrics_payload = {
            label: {
                "range": {
                    "start_utc_inclusive": result.start_utc_inclusive,
                    "end_utc_exclusive": result.end_utc_exclusive,
                },
                "metrics": dict(result.metrics),
                "funding": dict(result.funding),
                "execution": dict(result.execution),
                "signal_funnel": dict(result.signal_funnel),
            }
            for label, result in results.items()
        }
        outputs = [
            self._put_json(f"{prefix}/metrics.json", metrics_payload),
            self._put_parquet(
                f"{prefix}/signals.parquet", self._combine(results, "signals")
            ),
            self._put_parquet(
                f"{prefix}/trades.parquet", self._combine(results, "trades")
            ),
            self._put_parquet(
                f"{prefix}/equity.parquet", self._combine(results, "equity")
            ),
        ]
        report_key = f"reports/backtests/{run_id}.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._report(run_id, config, stage, results).encode("utf-8"),
            )
        )
        run_manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": f"generic_strategy_dsl_{stage}",
            "pipeline_profile": "fast_screen",
            "pipeline_stage": stage,
            "status": "succeeded",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "authorization_id": job.payload["authorization_id"],
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "strategy": {
                "strategy_spec_id": GENERIC_DSL_SPEC_ID,
                "session_id": config["session_id"],
                "strategy_version_id": config["strategy_version_id"],
                "baseline_immutable": True,
                "strategy_artifact_key": config["strategy_artifact_key"],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_reference_only",
            "data_manifest": {
                "artifact_key": config["data_manifest_key"],
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "dataset_id": manifest["dataset_id"],
                "data_version": manifest["data_version"],
            },
            "time_range_or_splits": (
                dict(config["smoke"])
                if stage == "smoke"
                else dict(config["time_splits"])
            ),
            "parameters": dict(config["strategy_dsl"]["parameters"]),
            "cost_model": dict(config["cost_model"]),
            "funding": dict(config["funding"]),
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
                "okx_validated": False,
                "production_ready": False,
            },
            "limitations": list(config["limitations"]),
        }
        outputs.append(self._put_json(manifest_key, run_manifest))
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
                report_type=f"generic_strategy_dsl_{stage}",
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
        results: Mapping[str, StrategyDslResult], attribute: str
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for label, result in results.items():
            frame = getattr(result, attribute)
            if frame.empty:
                continue
            if "split" not in frame.columns:
                frame = frame.assign(split=label)
            frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _put_json(
        self, artifact_key: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._put_bytes(artifact_key, _json_bytes(payload))

    def _put_parquet(
        self, artifact_key: str, frame: pd.DataFrame
    ) -> dict[str, Any]:
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False, compression="zstd")
        return self._put_bytes(
            artifact_key, buffer.getvalue(), rows=len(frame)
        )

    def _put_bytes(
        self, artifact_key: str, content: bytes, *, rows: int | None = None
    ) -> dict[str, Any]:
        self.artifacts.put(artifact_key, content)
        result: dict[str, Any] = {
            "artifact_key": artifact_key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if rows is not None:
            result["rows"] = rows
        return result

    @staticmethod
    def _report(
        run_id: str,
        config: Mapping[str, Any],
        stage: str,
        results: Mapping[str, StrategyDslResult],
    ) -> str:
        rows = []
        for label, result in results.items():
            metrics = result.metrics
            rows.append(
                "| {label} | {trades:.0f} | {ret:.2%} | {dd:.2%} | "
                "{pf:.3f} | {expectancy:.5f} |".format(
                    label=label,
                    trades=metrics["trade_count"],
                    ret=metrics["total_return"],
                    dd=metrics["max_drawdown"],
                    pf=metrics["profit_factor"],
                    expectancy=metrics["expectancy"],
                )
            )
        return "\n".join(
            [
                "# 受控通用策略 DSL：Native 研究报告",
                "",
                f"- Run：`{run_id}`",
                f"- Baseline：`{config['strategy_version_id']}`（不可变）",
                f"- 阶段：`{stage}`；locked test 未使用。",
                "- 市场：Binance ETH 永续参考数据；15m 执行；1倍杠杆。",
                "- 本报告只评价已确认 DSL 的历史表现，不承诺未来盈利。",
                "",
                "| 区间 | 交易数 | 净收益 | 最大回撤 | 盈亏效率 | 单笔期望 |",
                "|---|---:|---:|---:|---:|---:|",
                *rows,
                "",
                "## 当前结论边界",
                "",
                "- 通用执行器只运行已列入白名单的指标、条件和风险语义。",
                "- 未运行 Walk-forward、正式行情验证、最终保留测试或实盘。",
                "- 超出 DSL 的价格行为、订单和特殊 Pine 语义需要独立插件。",
                "",
            ]
        )


class GenericStrategyDslEvaluator:
    evaluator_id = "generic_strategy_dsl_v1"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.runner = GenericStrategyDslRunner(root, repository)
        self.artifacts = LocalArtifactStore(self.root)

    def evaluate(
        self, request: TrialEvaluationRequest
    ) -> TrialEvaluationResult:
        started = pd.Timestamp.now(tz="UTC")
        try:
            baseline = self.repository.get_strategy_version(
                request.baseline_version_id
            )
            capability = analyze_strategy_dsl(baseline.content_snapshot)
            if not capability.executable or capability.normalized is None:
                raise ValueError("参数试验要求可执行的通用 DSL Baseline")
            config_key = self.runner.prepare_config(
                baseline,
                self.repository.get_session_id_for_strategy_version(baseline.id),
            )
            config = json.loads(self.artifacts.get(config_key))
            manifest = json.loads(
                self.artifacts.get(
                    validate_artifact_key(config["data_manifest_key"])
                )
            )
            requested_timeframes = {
                "15m",
                *{
                    str(item["timeframe"])
                    for item in capability.normalized["indicators"]
                },
            }
            end = config["time_splits"]["validation"]["end_utc_exclusive"]
            ohlcv = {
                timeframe: self.runner._load_dataset(
                    manifest,
                    "futures_ohlcv",
                    timeframe,
                    end_utc_exclusive=end,
                )
                for timeframe in requested_timeframes
            }
            funding = self.runner._load_dataset(
                manifest,
                "funding_rate",
                None,
                end_utc_exclusive=end,
            )
            backtester = GenericStrategyDslBacktester(
                strategy_version_id=str(
                    request.candidate_version_id or request.baseline_version_id
                ),
                dsl=capability.normalized,
                fee_per_side=_fee_per_side(request.cost_model),
                slippage_bps_per_side=_slippage_bps_per_side(
                    request.cost_model
                ),
                parameters=request.parameters,
            )
            results = {
                label: backtester.run(
                    label=label,
                    ohlcv_by_timeframe=ohlcv,
                    funding=funding,
                    **config["time_splits"][label],
                )
                for label in ("train", "validation")
            }
            train = results["train"].metrics
            validation = results["validation"].metrics
            points = compress_equity_points(
                results["validation"].equity,
                split="validation",
                max_points=500,
            )
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="succeeded",
                metrics={
                    "train_net_return": float(train["total_return"]),
                    "train_profit_factor": float(train["profit_factor"]),
                    "train_expectancy": float(train["expectancy"]),
                    "train_trade_count": float(train["trade_count"]),
                    "train_max_drawdown_abs": abs(
                        float(train["max_drawdown"])
                    ),
                    "validation_net_return": float(
                        validation["total_return"]
                    ),
                    "validation_profit_factor": float(
                        validation["profit_factor"]
                    ),
                    "validation_expectancy": float(
                        validation["expectancy"]
                    ),
                    "validation_trade_count": float(
                        validation["trade_count"]
                    ),
                    "validation_max_drawdown_abs": abs(
                        float(validation["max_drawdown"])
                    ),
                },
                elapsed_seconds=(
                    pd.Timestamp.now(tz="UTC") - started
                ).total_seconds(),
                peak_rss_mb=0.0,
                stop_reason=None,
                equity_points=tuple(
                    {
                        "split": "validation",
                        "timestamp": str(item["timestamp"]),
                        "normalized_equity": float(item["normalized_equity"]),
                        "drawdown": float(item["drawdown"]),
                        "point_index": int(item["point_index"]),
                    }
                    for item in points
                ),
            )
        except Exception as exc:
            return TrialEvaluationResult(
                trial_id=request.trial_id,
                status="failed",
                metrics={},
                elapsed_seconds=(
                    pd.Timestamp.now(tz="UTC") - started
                ).total_seconds(),
                peak_rss_mb=0.0,
                stop_reason=str(exc),
                error=str(exc),
            )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _fee_per_side(cost_model: Mapping[str, Any]) -> float:
    value = cost_model.get(
        "fee_per_side", cost_model.get("taker_fee_per_side")
    )
    if not isinstance(value, (int, float)):
        raise ValueError("通用策略参数试验成本模型缺少单边手续费")
    return float(value)


def _slippage_bps_per_side(cost_model: Mapping[str, Any]) -> float:
    basis_points = cost_model.get("slippage_bps_per_side")
    if isinstance(basis_points, (int, float)):
        return float(basis_points)
    fraction = cost_model.get("slippage_per_side")
    if isinstance(fraction, (int, float)):
        return float(fraction) * 10_000.0
    raise ValueError("通用策略参数试验成本模型缺少单边滑点")
