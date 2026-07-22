from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from quant_lab.application.baseline_backtest import (
    BacktestSliceResult,
    PriceStructureBaselineBacktester,
)
from quant_lab.application.experiment_plan_config import parse_experiment_plan_draft
from quant_lab.application.services import ResearchApplicationService, new_id
from quant_lab.domain.models import ExperimentPlan, Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .baseline_backtest_runner import BaselineBacktestRunner


ENTRY_MODE = "confirmation_candle_breakout"
CANDIDATE_STRATEGY_KEY = (
    "strategies/research/price_structure_hh_hl_entry_confirmation_candidate_v1.py"
)


class EntryConfirmationExperimentRunner(BaselineBacktestRunner):
    """Run the single approved entry-confirmation Trial on train/validation only."""

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        super().__init__(root, repository)
        self.service = ResearchApplicationService(repository)

    def validate_payload(
        self, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], ExperimentPlan, dict[str, Any]]:
        required = {
            "intent",
            "experiment_plan_id",
            "config_artifact_key",
            "baseline_manifest_artifact_key",
            "candidate_strategy_artifact_key",
            "agent_run_id",
            "approval",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"parameter-search payload missing: {sorted(missing)}")
        if payload["intent"] != "parameter_optimization":
            raise ValueError("candidate handler only accepts parameter_optimization")

        approval = payload["approval"]
        if not isinstance(approval, Mapping) or approval.get("confirmed_by_user") is not True:
            raise ValueError("parameter search requires explicit user approval")

        plan = self.repository.get_experiment_plan(str(payload["experiment_plan_id"]))
        if plan.status != "approved" or plan.approved_by != "user":
            raise ValueError("experiment plan must be approved by the user")
        if plan.max_trials != 1:
            raise ValueError("this handler is bounded to exactly one Trial")

        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        raw = yaml.safe_load(self.artifacts.get(config_key))
        if not isinstance(raw, dict):
            raise ValueError("experiment plan config must be a YAML mapping")
        config = parse_experiment_plan_draft(raw)
        if config.baseline_version_id != plan.baseline_version_id:
            raise ValueError("config baseline does not match approved plan")
        if config.hypothesis != plan.hypothesis:
            raise ValueError("config hypothesis does not match approved plan")
        if config.parameter_space != plan.parameter_space:
            raise ValueError("config parameter space does not match approved plan")
        if config.objectives != plan.objectives or config.constraints != plan.constraints:
            raise ValueError("config objectives/constraints do not match approved plan")
        if dict(config.data_splits) != dict(plan.data_splits):
            raise ValueError("config data splits do not match approved plan")
        if dict(config.cost_model) != dict(plan.cost_model):
            raise ValueError("config cost model does not match approved plan")
        if config.max_trials != plan.max_trials:
            raise ValueError("config Trial budget does not match approved plan")
        if config.time_budget_seconds != plan.time_budget_seconds:
            raise ValueError("config time budget does not match approved plan")
        if config.stopping_conditions != plan.stopping_conditions:
            raise ValueError("config stopping conditions do not match approved plan")

        strategy_key = validate_artifact_key(
            str(payload["candidate_strategy_artifact_key"])
        )
        if strategy_key != CANDIDATE_STRATEGY_KEY or not self.artifacts.exists(strategy_key):
            raise ValueError("candidate strategy artifact is missing or not allowlisted")

        baseline_manifest_key = validate_artifact_key(
            str(payload["baseline_manifest_artifact_key"])
        )
        baseline_manifest = json.loads(self.artifacts.get(baseline_manifest_key))
        if (
            baseline_manifest["strategy"]["strategy_version_id"]
            != plan.baseline_version_id
        ):
            raise ValueError("baseline manifest does not match approved plan")
        if baseline_manifest["market_profile"] != config.market_profile:
            raise ValueError("baseline market profile does not match experiment config")
        return raw, plan, baseline_manifest

    def __call__(self, job: Job) -> Mapping[str, Any]:
        raw, plan, baseline_manifest = self.validate_payload(job.payload)
        data_manifest_key = validate_artifact_key(
            baseline_manifest["data_manifest"]["artifact_key"]
        )
        data_manifest_bytes = self.artifacts.get(data_manifest_key)
        data_manifest = json.loads(data_manifest_bytes)
        if data_manifest["market_profile"] != raw["market_profile"]:
            raise ValueError("data manifest market profile mismatch")

        run_id = self._candidate_run_id(job)
        run_prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("run artifacts are immutable and already exist")

        parameters = {"entry_mode": ENTRY_MODE}
        existing_trials = list(self.repository.list_trials(plan.id))
        if not existing_trials:
            trial = self.service.record_trial(
                experiment_plan_id=plan.id,
                parameters=parameters,
                data_version=data_manifest["data_version"],
                status="running",
            )
        elif (
            len(existing_trials) == 1
            and existing_trials[0].status == "failed"
            and dict(existing_trials[0].parameters) == parameters
            and existing_trials[0].data_version == data_manifest["data_version"]
        ):
            trial = self.service.update_trial(
                trial_id=existing_trials[0].id,
                status="running",
                metrics=existing_trials[0].metrics,
                log_artifact_key=existing_trials[0].log_artifact_key,
            )
        else:
            raise ValueError(
                "approved one-Trial budget is already consumed by another parameter set"
            )
        try:
            return self._execute(
                job=job,
                trial_id=trial.id,
                run_id=run_id,
                run_prefix=run_prefix,
                manifest_key=manifest_key,
                raw=raw,
                plan=plan,
                baseline_manifest=baseline_manifest,
                data_manifest_key=data_manifest_key,
                data_manifest_bytes=data_manifest_bytes,
                data_manifest=data_manifest,
            )
        except Exception:
            self.service.update_trial(
                trial_id=trial.id,
                status="failed",
                metrics={},
                log_artifact_key=None,
            )
            raise

    def _execute(
        self,
        *,
        job: Job,
        trial_id: str,
        run_id: str,
        run_prefix: str,
        manifest_key: str,
        raw: Mapping[str, Any],
        plan: ExperimentPlan,
        baseline_manifest: Mapping[str, Any],
        data_manifest_key: str,
        data_manifest_bytes: bytes,
        data_manifest: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ohlcv = self._load_dataset(data_manifest, "futures_ohlcv", "15m")
        funding = self._load_dataset(data_manifest, "funding_rate", None)
        mark = self._load_dataset(data_manifest, "mark_price", "15m")

        backtester = PriceStructureBaselineBacktester(
            fee_per_side=float(plan.cost_model["fee_per_side"]),
            slippage_bps_per_side=float(
                plan.cost_model["slippage_bps_per_side"]
            ),
            funding_missing_policy="adverse_p99_abs_observed",
            entry_mode=ENTRY_MODE,
        )
        results: dict[str, BacktestSliceResult] = {}
        for label in ("train", "validation"):
            start, end = self._split_bounds(plan.data_splits[label])
            results[label] = backtester.run(
                label=label,
                ohlcv=self._before(ohlcv, end),
                funding=self._before(funding, end),
                mark=self._before(mark, end),
                start_utc_inclusive=start,
                end_utc_exclusive=end,
            )

        baseline_results = {
            label: dict(baseline_manifest["results"][label]["metrics"])
            for label in ("train", "validation")
        }
        candidate_metrics = {
            label: dict(result.metrics) for label, result in results.items()
        }
        derived = {
            "train_same_bar_stop_rate": float(
                candidate_metrics["train"]["same_bar_stop_rate"] or 0.0
            ),
            "validation_trade_count": float(
                candidate_metrics["validation"]["trade_count"]
            ),
            "validation_max_drawdown_abs": abs(
                float(candidate_metrics["validation"]["max_drawdown"])
            ),
            "validation_net_return_delta_vs_baseline": float(
                candidate_metrics["validation"]["total_return"]
            )
            - float(baseline_results["validation"]["total_return"]),
        }
        constraint_results = [
            {
                "metric": constraint.metric,
                "operator": constraint.operator,
                "threshold": constraint.value,
                "actual": derived[constraint.metric],
                "passed": self._compare(
                    derived[constraint.metric],
                    constraint.operator,
                    constraint.value,
                ),
            }
            for constraint in plan.constraints
        ]
        all_constraints_passed = all(item["passed"] for item in constraint_results)
        objective_improved = derived["validation_net_return_delta_vs_baseline"] > 0
        decision = (
            "proposal_pending_user_acceptance"
            if all_constraints_passed and objective_improved
            else "hypothesis_rejected"
        )

        output_records: list[dict[str, Any]] = []
        comparison = {
            "experiment_plan_id": plan.id,
            "trial_id": trial_id,
            "parameters": {"entry_mode": ENTRY_MODE},
            "baseline": baseline_results,
            "candidate": candidate_metrics,
            "derived_metrics": derived,
            "constraints": constraint_results,
            "objective_improved": objective_improved,
            "all_constraints_passed": all_constraints_passed,
            "parameter_stability_analysis": {
                "status": "not_applicable_single_categorical_ablation",
                "reason": "The approved budget contains exactly one categorical Trial; no parameter surface was searched.",
            },
            "out_of_sample_comparison": {
                "split": "validation",
                "locked_test_used": False,
                "previous_locked_test_used": False,
                "future_locked_test_available": False,
            },
            "decision": decision,
            "baseline_immutable": True,
            "candidate_version_created": False,
            "production_promotion_requested": False,
        }
        output_records.append(
            self._put_json(f"{run_prefix}/comparison.json", comparison)
        )
        for label in ("train", "validation"):
            output_records.append(
                self._put_parquet(
                    f"{run_prefix}/trades-{label}.parquet", results[label].trades
                )
            )
            output_records.append(
                self._put_parquet(
                    f"{run_prefix}/equity-{label}.parquet", results[label].equity
                )
            )

        log_key = f"{run_prefix}/job.log.jsonl"
        log_lines = [
            {
                "level": "info",
                "event": "parameter_search.started",
                "job_id": job.id,
                "experiment_plan_id": plan.id,
                "max_trials": plan.max_trials,
            },
            {
                "level": "info",
                "event": "trial.completed",
                "trial_id": trial_id,
                "parameters": {"entry_mode": ENTRY_MODE},
                "constraints_passed": all_constraints_passed,
            },
            {
                "level": "info",
                "event": "locked_test.not_inspected",
                "previous_locked_test_excluded": True,
                "future_locked_test_available": False,
            },
            {
                "level": "info",
                "event": "parameter_search.completed",
                "decision": decision,
            },
        ]
        log_bytes = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in log_lines
        ).encode("utf-8")
        output_records.append(self._put_bytes(log_key, log_bytes))

        report_key = f"reports/backtests/{run_id}.md"
        report = self._build_candidate_report(
            run_id=run_id,
            plan=plan,
            baseline=baseline_results,
            candidate=candidate_metrics,
            derived=derived,
            constraints=constraint_results,
            decision=decision,
        )
        output_records.append(self._put_bytes(report_key, report.encode("utf-8")))

        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "trial_id": trial_id,
            "run_type": "parameter_search_trial",
            "intent": "parameter_optimization",
            "status": "succeeded",
            "created_at": created_at,
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "approval": dict(job.payload["approval"]),
            "experiment_plan": {
                "id": plan.id,
                "status": plan.status,
                "approved_by": plan.approved_by,
                "hypothesis": plan.hypothesis,
                "max_trials": plan.max_trials,
                "time_budget_seconds": plan.time_budget_seconds,
                "stopping_conditions": list(plan.stopping_conditions),
            },
            "strategy": {
                "baseline_version_id": plan.baseline_version_id,
                "baseline_immutable": True,
                "candidate_version_created": False,
                "candidate_implementation_artifact_key": CANDIDATE_STRATEGY_KEY,
                "single_change": {"entry_mode": ENTRY_MODE},
            },
            "market_profile": raw["market_profile"],
            "validation_scope": "binance_only",
            "data_manifest": {
                "artifact_key": data_manifest_key,
                "sha256": hashlib.sha256(data_manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "parameters": {"entry_mode": ENTRY_MODE},
            "cost_model": dict(plan.cost_model),
            "time_splits": dict(plan.data_splits),
            "locked_test": {
                "previous_interval_excluded": plan.data_splits.get(
                    "excluded_previous_locked_test"
                ),
                "future_interval": plan.data_splits["locked_test"],
                "future_data_available": False,
                "inspected_in_this_run": False,
            },
            "random_seed": 20260722,
            "code_version": _source_tree_digest(self.root),
            "git_revision": _git_revision(self.root),
            "engine": "quant_lab_native_event_driven_entry_confirmation_v1",
            "comparison": comparison,
            "outputs": output_records,
            "decision": decision,
            "claims": {
                "long_term_profitability_proven": False,
                "okx_validated": False,
                "production_ready": False,
            },
        }
        manifest_record = self._put_json(manifest_key, manifest)
        output_records.append(manifest_record)

        trial_metrics = {
            "train_total_return": float(candidate_metrics["train"]["total_return"]),
            "train_same_bar_stop_rate": derived["train_same_bar_stop_rate"],
            "validation_total_return": float(
                candidate_metrics["validation"]["total_return"]
            ),
            "validation_net_return_delta_vs_baseline": derived[
                "validation_net_return_delta_vs_baseline"
            ],
            "validation_max_drawdown_abs": derived[
                "validation_max_drawdown_abs"
            ],
            "validation_trade_count": derived["validation_trade_count"],
            "all_constraints_passed": float(all_constraints_passed),
            "objective_improved": float(objective_improved),
        }
        self.service.update_trial(
            trial_id=trial_id,
            status="succeeded",
            metrics=trial_metrics,
            log_artifact_key=log_key,
        )
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="entry_confirmation_experiment",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "trial_id": trial_id,
                    "decision": decision,
                    "validation_return_delta": derived[
                        "validation_net_return_delta_vs_baseline"
                    ],
                    "constraints_passed": all_constraints_passed,
                    "locked_test_used": False,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "trial_id": trial_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [record["artifact_key"] for record in output_records],
            "trial_metrics": trial_metrics,
            "decision": decision,
        }

    @staticmethod
    def _split_bounds(value: str) -> tuple[str, str]:
        closing = value.find(")")
        if closing < 0:
            raise ValueError(f"invalid UTC split interval: {value}")
        interval = value[: closing + 1]
        if not interval.startswith("[") or not interval.endswith(")"):
            raise ValueError(f"invalid UTC split interval: {value}")
        start, end = interval[1:-1].split(",", 1)
        return start.strip(), end.strip()

    @staticmethod
    def _before(frame: pd.DataFrame, end_utc_exclusive: str) -> pd.DataFrame:
        timestamps = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.loc[timestamps < pd.Timestamp(end_utc_exclusive)].copy()

    @staticmethod
    def _compare(actual: float, operator: str, threshold: float) -> bool:
        operations = {
            "lt": actual < threshold,
            "lte": actual <= threshold,
            "gt": actual > threshold,
            "gte": actual >= threshold,
            "eq": actual == threshold,
        }
        if operator not in operations:
            raise ValueError(f"unsupported constraint operator: {operator}")
        return operations[operator]

    @staticmethod
    def _candidate_run_id(job: Job) -> str:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        return f"{created.strftime('%Y%m%dT%H%M%SZ')}-candidate-{job.id[-8:]}"

    @staticmethod
    def _build_candidate_report(
        *,
        run_id: str,
        plan: ExperimentPlan,
        baseline: Mapping[str, Mapping[str, Any]],
        candidate: Mapping[str, Mapping[str, Any]],
        derived: Mapping[str, float],
        constraints: list[dict[str, Any]],
        decision: str,
    ) -> str:
        def percent(value: Any) -> str:
            return "—" if value is None else f"{float(value):.2%}"

        lines = [
            "# 入场确认单假设实验报告",
            "",
            f"- Run：`{run_id}`",
            f"- ExperimentPlan：`{plan.id}`（用户已批准）",
            f"- 冻结 baseline：`{plan.baseline_version_id}`（未修改）",
            "- 唯一改动：结构确认后，突破确认 K 线高/低点才入场。",
            "- 范围：Binance ETH/USDT USDT 本位永续 15m；杠杆 1；实盘关闭。",
            "- 本次只使用 train 与 validation；旧 locked test 和未来 locked test 均未使用。",
            "",
            "## 对照结果",
            "",
            "| 区间 | 版本 | 交易数 | 总收益 | 最大回撤 | 同根止损率 |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for label in ("train", "validation"):
            for version, metrics in (("baseline", baseline[label]), ("candidate", candidate[label])):
                lines.append(
                    f"| {label} | {version} | {metrics['trade_count']} | "
                    f"{percent(metrics['total_return'])} | "
                    f"{percent(metrics['max_drawdown'])} | "
                    f"{percent(metrics.get('same_bar_stop_rate'))} |"
                )
        lines.extend(
            [
                "",
                "## 预先声明约束",
                "",
            ]
        )
        for item in constraints:
            lines.append(
                "- `{metric}`：实际 `{actual:.8f}`，要求 `{operator} {threshold:.8f}`，{status}。".format(
                    metric=item["metric"],
                    actual=item["actual"],
                    operator=item["operator"],
                    threshold=item["threshold"],
                    status="通过" if item["passed"] else "未通过",
                )
            )
        lines.extend(
            [
                "",
                "## 样本外目标",
                "",
                "- validation 净收益相对 baseline 的变化："
                f"{percent(derived['validation_net_return_delta_vs_baseline'])}。",
                "- 参数稳定性：本计划只有一个分类 Trial，不存在可分析的参数平台；这是单规则消融，不是调参搜索。",
                "",
                "## 结论",
                "",
                f"`{decision}`。",
                "",
                "即使结果改善，也只能形成待人工接受的 Proposal；在未来锁定区间数据可用并通过压力测试前，不能标记 validated、dry-run 或 production。",
                "本报告不构成盈利保证或实盘授权。",
                "",
            ]
        )
        return "\n".join(lines)
