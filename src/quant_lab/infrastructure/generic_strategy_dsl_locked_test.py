from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_lab.application.batch_trials import constraints_pass
from quant_lab.application.services import new_id
from quant_lab.application.strategy_dsl import (
    GenericStrategyDslBacktester,
    StrategyDslResult,
    analyze_strategy_dsl,
)
from quant_lab.domain.models import Job, Report
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .artifact_store import LocalArtifactStore
from .generic_strategy_dsl_runner import GenericStrategyDslRunner


class GenericStrategyDslLockedTestRunner:
    """Run one explicitly approved, immutable final holdout evaluation."""

    ENGINE_ID = "quant_lab_generic_strategy_dsl_locked_test_v1"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.generic = GenericStrategyDslRunner(root, repository)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        payload = job.payload
        if payload.get("intent") != "generic_strategy_dsl_locked_test":
            raise ValueError("最终保留测试收到不支持的研究意图")
        if (
            payload.get("confirmed_by_user") is not True
            or payload.get("locked_test_used") is not True
        ):
            raise ValueError("最终保留测试要求用户明确批准并登记一次使用")

        plan = self.repository.get_experiment_plan(
            str(payload["experiment_plan_id"])
        )
        trial = next(
            (
                item
                for item in self.repository.list_trials(plan.id)
                if item.id == payload.get("recommended_trial_id")
            ),
            None,
        )
        if trial is None or trial.status != "succeeded":
            raise ValueError("最终保留测试要求同一计划内成功的冻结推荐方案")
        if (
            plan.candidate_version_id is None
            or payload.get("candidate_version_id") != plan.candidate_version_id
        ):
            raise ValueError("最终保留测试的候选版本与试验计划不一致")

        validation_report = self._candidate_validation_report(plan.id)
        decision = dict(validation_report.summary.get("decision") or {})
        if decision.get("status") != "ready_for_locked_test_review":
            raise ValueError("候选尚未通过有界稳健性检查，不能查看最终保留测试")

        baseline = self.repository.get_strategy_version(
            plan.baseline_version_id
        )
        capability = analyze_strategy_dsl(baseline.content_snapshot)
        if not capability.executable or capability.normalized is None:
            raise ValueError("最终保留测试只支持可执行的通用策略 DSL")

        session_id = self.repository.get_session_id_for_strategy_version(
            baseline.id
        )
        config_key = self.generic.prepare_config(baseline, session_id)
        config = json.loads(self.artifacts.get(config_key))
        locked = dict(config["time_splits"]["locked_test"])
        if locked.get("status") != "reserved_uninspected":
            raise ValueError("最终保留区间状态异常，拒绝继续")
        locked.pop("use_in_this_authorization", None)
        locked.pop("status", None)

        manifest_bytes = self.artifacts.get(config["data_manifest_key"])
        manifest = json.loads(manifest_bytes)
        requested_timeframes = {
            "15m",
            *{
                str(item["timeframe"])
                for item in capability.normalized["indicators"]
            },
        }
        end = str(locked["end_utc_exclusive"])
        ohlcv = {
            timeframe: self.generic._load_dataset(
                manifest,
                "futures_ohlcv",
                timeframe,
                end_utc_exclusive=end,
            )
            for timeframe in requested_timeframes
        }
        funding = self.generic._load_dataset(
            manifest,
            "funding_rate",
            None,
            end_utc_exclusive=end,
        )
        result = GenericStrategyDslBacktester(
            strategy_version_id=plan.candidate_version_id,
            dsl=capability.normalized,
            parameters=trial.parameters,
            fee_per_side=_fee_per_side(plan.cost_model),
            slippage_bps_per_side=_slippage_bps_per_side(plan.cost_model),
        ).run(
            label="locked_test",
            ohlcv_by_timeframe=ohlcv,
            funding=funding,
            warmup_start_utc_inclusive=(
                pd.Timestamp(locked["start_utc_inclusive"])
                - pd.Timedelta(days=30)
            ).isoformat(),
            start_utc_inclusive=str(locked["start_utc_inclusive"]),
            end_utc_exclusive=end,
        )
        locked_metrics = _constraint_metrics(result)
        passed = constraints_pass(locked_metrics, plan.constraints) and (
            float(result.metrics["total_return"]) >= 0.0
            and float(result.metrics["expectancy"]) >= 0.0
        )
        return self._persist(
            job=job,
            plan_id=plan.id,
            baseline_id=baseline.id,
            candidate_id=plan.candidate_version_id,
            trial_id=trial.id,
            parameters=trial.parameters,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            result=result,
            passed=passed,
        )

    def _candidate_validation_report(self, plan_id: str) -> Report:
        jobs = {
            item.id: item
            for item in self.repository.list_jobs()
            if item.payload.get("experiment_plan_id") == plan_id
        }
        report = next(
            (
                item
                for item in self.repository.list_reports()
                if item.report_type == "generic_candidate_validation"
                and item.job_id in jobs
            ),
            None,
        )
        if report is None:
            raise ValueError("缺少同一试验计划的候选验证报告")
        return report

    def _persist(
        self,
        *,
        job: Job,
        plan_id: str,
        baseline_id: str,
        candidate_id: str,
        trial_id: str,
        parameters: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_bytes: bytes,
        result: StrategyDslResult,
        passed: bool,
    ) -> Mapping[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        run_id = (
            f"{pd.Timestamp(job.created_at).strftime('%Y%m%dT%H%M%SZ')}"
            f"-locked-{job.id[-8:]}"
        )
        prefix = f"experiments/runs/{run_id}"
        metrics = dict(result.metrics)
        decision = {
            "status": "passed" if passed else "failed",
            "headline": (
                "最终保留测试通过，可进入人工审阅"
                if passed
                else "最终保留测试未通过，停止当前候选"
            ),
            "next_action": (
                "审阅完整证据包；如需模拟运行，必须单独批准，系统不会自动晋升。"
                if passed
                else "保留失败证据，不得针对本保留区间继续调参；如需继续，请建立新的研究假设和新的保留期。"
            ),
        }
        summary = {
            "run_id": run_id,
            "experiment_plan_id": plan_id,
            "baseline_version_id": baseline_id,
            "candidate_version_id": candidate_id,
            "recommended_trial_id": trial_id,
            "parameters": dict(parameters),
            "metrics": metrics,
            "decision": decision,
            "locked_test_used": True,
            "locked_test_automatically_started": False,
            "contaminated": False,
        }
        outputs = [
            self._put_json(f"{prefix}/locked-test.json", summary),
            self._put_parquet(f"{prefix}/trades.parquet", result.trades),
            self._put_parquet(f"{prefix}/equity.parquet", result.equity),
        ]
        report_key = f"reports/experiments/{run_id}-locked-test.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._report(summary).encode("utf-8"),
            )
        )
        manifest_key = f"{prefix}/manifest.json"
        outputs.append(
            self._put_json(
                manifest_key,
                {
                    "manifest_version": 1,
                    "run_id": run_id,
                    "job_id": job.id,
                    "run_type": "generic_strategy_dsl_locked_test",
                    "status": "succeeded",
                    "created_at": created_at,
                    "strategy": {
                        "baseline_version_id": baseline_id,
                        "candidate_version_id": candidate_id,
                        "recommended_trial_id": trial_id,
                    },
                    "data_manifest": {
                        "artifact_key": (
                            "data/manifests/"
                            "binance_ethusdt_perpetual_20240720_20260720_v2.json"
                        ),
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                        "dataset_id": manifest["dataset_id"],
                        "data_version": manifest["data_version"],
                    },
                    "parameters": dict(parameters),
                    "engine": self.ENGINE_ID,
                    "code_version": _source_tree_digest(self.root),
                    "git_revision": _git_revision(self.root),
                    "results": summary,
                    "outputs": outputs,
                    "locked_test": {
                        "used": True,
                        "inspection_count": 1,
                        "contaminated": False,
                        "automatically_started": False,
                    },
                    "claims": {
                        "production_ready": False,
                        "live_trading_ready": False,
                    },
                },
            )
        )
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="generic_locked_test",
                artifact_key=report_key,
                summary=summary,
                created_at=created_at,
            )
        )
        return {
            **summary,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [item["artifact_key"] for item in outputs],
        }

    def _put_json(
        self, key: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._put_bytes(
            key,
            (
                json.dumps(
                    value, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n"
            ).encode("utf-8"),
        )

    def _put_parquet(
        self, key: str, frame: pd.DataFrame
    ) -> dict[str, Any]:
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False, compression="zstd")
        return self._put_bytes(key, buffer.getvalue(), rows=len(frame))

    def _put_bytes(
        self, key: str, content: bytes, *, rows: int | None = None
    ) -> dict[str, Any]:
        self.artifacts.put(key, content)
        output: dict[str, Any] = {
            "artifact_key": key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if rows is not None:
            output["rows"] = rows
        return output

    @staticmethod
    def _report(summary: Mapping[str, Any]) -> str:
        metrics = summary["metrics"]
        decision = summary["decision"]
        return "\n".join(
            [
                "# 通用策略最终保留测试",
                "",
                f"- 结论：**{decision['headline']}**",
                f"- 推荐 Trial：`{summary['recommended_trial_id']}`",
                "- 本次只运行一次冻结候选和冻结参数；未参与搜索。",
                "- 查看后不得针对本区间继续调参，否则必须标记污染并建立新保留期。",
                "",
                "## 关键指标",
                "",
                f"- 净收益：{float(metrics['total_return']):.2%}",
                f"- 最大回撤：{abs(float(metrics['max_drawdown'])):.2%}",
                f"- 盈亏效率：{float(metrics['profit_factor']):.3f}",
                f"- 单笔期望：{float(metrics['expectancy']):.5f}",
                f"- 交易次数：{int(metrics['trade_count'])}",
                "",
                "## 下一步",
                "",
                str(decision["next_action"]),
                "",
            ]
        )


def _constraint_metrics(result: StrategyDslResult) -> dict[str, float]:
    metrics = result.metrics
    return {
        "validation_net_return": float(metrics["total_return"]),
        "validation_profit_factor": float(metrics["profit_factor"]),
        "validation_expectancy": float(metrics["expectancy"]),
        "validation_trade_count": float(metrics["trade_count"]),
        "validation_max_drawdown_abs": abs(
            float(metrics["max_drawdown"])
        ),
    }


def _fee_per_side(cost_model: Mapping[str, Any]) -> float:
    value = cost_model.get(
        "fee_per_side", cost_model.get("taker_fee_per_side")
    )
    if not isinstance(value, (int, float)):
        raise ValueError("最终保留测试成本模型缺少单边手续费")
    return float(value)


def _slippage_bps_per_side(cost_model: Mapping[str, Any]) -> float:
    value = cost_model.get("slippage_bps_per_side")
    if isinstance(value, (int, float)):
        return float(value)
    fraction = cost_model.get("slippage_per_side")
    if isinstance(fraction, (int, float)):
        return float(fraction) * 10_000.0
    raise ValueError("最终保留测试成本模型缺少单边滑点")
