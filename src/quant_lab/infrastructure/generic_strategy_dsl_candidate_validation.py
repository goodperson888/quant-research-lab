from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

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


class GenericStrategyDslCandidateValidationRunner:
    """Bounded post-batch robustness evidence without locked-test use."""

    ENGINE_ID = "quant_lab_generic_strategy_dsl_candidate_validation_v1"

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        self.root = root.resolve()
        self.repository = repository
        self.artifacts = LocalArtifactStore(self.root)
        self.generic = GenericStrategyDslRunner(root, repository)

    def __call__(self, job: Job) -> Mapping[str, Any]:
        payload = job.payload
        if payload.get("intent") != "generic_strategy_dsl_candidate_validation":
            raise ValueError("通用候选验证收到不支持的研究意图")
        if payload.get("locked_test_used") is not False:
            raise ValueError("候选验证禁止使用最终保留测试")
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
            raise ValueError("候选验证要求同一试验计划内成功的推荐方案")
        baseline = self.repository.get_strategy_version(
            plan.baseline_version_id
        )
        capability = analyze_strategy_dsl(baseline.content_snapshot)
        if not capability.executable or capability.normalized is None:
            raise ValueError("候选验证只支持可执行的通用策略 DSL")
        session_id = self.repository.get_session_id_for_strategy_version(
            baseline.id
        )
        config_key = self.generic.prepare_config(baseline, session_id)
        config = json.loads(self.artifacts.get(config_key))
        manifest_bytes = self.artifacts.get(config["data_manifest_key"])
        manifest = json.loads(manifest_bytes)
        validation = config["time_splits"]["validation"]
        requested_timeframes = {
            "15m",
            *{
                str(item["timeframe"])
                for item in capability.normalized["indicators"]
            },
        }
        end = validation["end_utc_exclusive"]
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
        fee = _fee_per_side(plan.cost_model)
        slippage = _slippage_bps_per_side(plan.cost_model)
        base = self._run(
            capability.normalized,
            trial.parameters,
            fee,
            slippage,
            validation,
            ohlcv,
            funding,
            "base_validation",
            trial.id,
        )
        cost_results = {
            "approved_cost": base,
            "cost_x1_5": self._run(
                capability.normalized,
                trial.parameters,
                fee * 1.5,
                slippage * 1.5,
                validation,
                ohlcv,
                funding,
                "cost_x1_5",
                trial.id,
            ),
            "cost_x2": self._run(
                capability.normalized,
                trial.parameters,
                fee * 2.0,
                slippage * 2.0,
                validation,
                ohlcv,
                funding,
                "cost_x2",
                trial.id,
            ),
        }
        rolling_results = self._rolling_results(
            capability.normalized,
            trial.parameters,
            fee,
            slippage,
            validation,
            ohlcv,
            funding,
            trial.id,
        )
        perturbation_results = self._perturbation_results(
            capability.normalized,
            trial.parameters,
            fee,
            slippage,
            validation,
            ohlcv,
            funding,
            trial.id,
        )
        regime = self._regime_breakdown(
            base.trades,
            ohlcv.get("1h", ohlcv["15m"]),
        )
        decision = self._decision(
            base=base,
            costs=cost_results,
            rolling=rolling_results,
            perturbations=perturbation_results,
            regime=regime,
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
            base=base,
            costs=cost_results,
            rolling=rolling_results,
            perturbations=perturbation_results,
            regime=regime,
            decision=decision,
        )

    @staticmethod
    def _run(
        dsl: Mapping[str, Any],
        parameters: Mapping[str, Any],
        fee: float,
        slippage: float,
        split: Mapping[str, str],
        ohlcv: Mapping[str, pd.DataFrame],
        funding: pd.DataFrame,
        label: str,
        trial_id: str,
    ) -> StrategyDslResult:
        return GenericStrategyDslBacktester(
            strategy_version_id=trial_id,
            dsl=dsl,
            parameters=parameters,
            fee_per_side=fee,
            slippage_bps_per_side=slippage,
        ).run(
            label=label,
            ohlcv_by_timeframe=ohlcv,
            funding=funding,
            **split,
        )

    def _rolling_results(
        self,
        dsl: Mapping[str, Any],
        parameters: Mapping[str, Any],
        fee: float,
        slippage: float,
        validation: Mapping[str, str],
        ohlcv: Mapping[str, pd.DataFrame],
        funding: pd.DataFrame,
        trial_id: str,
    ) -> dict[str, StrategyDslResult]:
        start = pd.Timestamp(validation["start_utc_inclusive"])
        end = pd.Timestamp(validation["end_utc_exclusive"])
        boundaries = pd.date_range(start, end, periods=4)
        results: dict[str, StrategyDslResult] = {}
        for index in range(3):
            window_start = boundaries[index]
            window_end = boundaries[index + 1]
            split = {
                "warmup_start_utc_inclusive": (
                    window_start - pd.Timedelta(days=30)
                ).isoformat(),
                "start_utc_inclusive": window_start.isoformat(),
                "end_utc_exclusive": window_end.isoformat(),
            }
            label = f"rolling_{index + 1}"
            results[label] = self._run(
                dsl,
                parameters,
                fee,
                slippage,
                split,
                ohlcv,
                funding,
                label,
                trial_id,
            )
        return results

    def _perturbation_results(
        self,
        dsl: Mapping[str, Any],
        parameters: Mapping[str, Any],
        fee: float,
        slippage: float,
        validation: Mapping[str, str],
        ohlcv: Mapping[str, pd.DataFrame],
        funding: pd.DataFrame,
        trial_id: str,
    ) -> dict[str, StrategyDslResult]:
        scenarios: dict[str, dict[str, Any]] = {"base": dict(parameters)}
        declared = dict(dsl.get("parameters", {}))
        for name, value in parameters.items():
            if name not in declared or isinstance(value, bool) or not isinstance(
                value, (int, float)
            ):
                continue
            for suffix, multiplier in (("minus10", 0.9), ("plus10", 1.1)):
                adjusted: float | int = float(value) * multiplier
                if isinstance(value, int):
                    adjusted = max(1, int(round(adjusted)))
                scenario = dict(parameters)
                scenario[name] = adjusted
                scenarios[f"{name}_{suffix}"] = scenario
        results: dict[str, StrategyDslResult] = {}
        for label, scenario in scenarios.items():
            results[label] = self._run(
                dsl,
                scenario,
                fee,
                slippage,
                validation,
                ohlcv,
                funding,
                f"perturb_{label}",
                trial_id,
            )
        return results

    @staticmethod
    def _regime_breakdown(
        trades: pd.DataFrame, hourly: pd.DataFrame
    ) -> dict[str, Any]:
        if trades.empty:
            return {
                "evidence_level": "insufficient_history",
                "regimes": {},
            }
        bars = hourly.loc[:, ["timestamp", "close"]].copy()
        bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
        bars = bars.sort_values("timestamp").drop_duplicates("timestamp")
        close = bars["close"].astype(float)
        bars["ema_fast"] = close.ewm(
            span=20, adjust=False, min_periods=20
        ).mean()
        bars["ema_slow"] = close.ewm(
            span=50, adjust=False, min_periods=50
        ).mean()
        bars["volatility"] = close.pct_change().rolling(
            24, min_periods=24
        ).std()
        bars["volatility_median"] = bars["volatility"].expanding(
            min_periods=24
        ).median()
        bars["regime"] = "range"
        bars.loc[
            (bars["ema_fast"] > bars["ema_slow"])
            & (bars["volatility"] >= bars["volatility_median"]),
            "regime",
        ] = "uptrend_high_vol"
        bars.loc[
            (bars["ema_fast"] < bars["ema_slow"])
            & (bars["volatility"] >= bars["volatility_median"]),
            "regime",
        ] = "downtrend_high_vol"
        observable = bars.loc[:, ["timestamp", "regime"]].copy()
        observable["timestamp"] = observable["timestamp"] + pd.Timedelta(
            hours=1
        )
        trade_rows = trades.copy()
        trade_rows["entry_time"] = pd.to_datetime(
            trade_rows["entry_time"], utc=True
        )
        joined = pd.merge_asof(
            trade_rows.sort_values("entry_time"),
            observable.sort_values("timestamp"),
            left_on="entry_time",
            right_on="timestamp",
            direction="backward",
        )
        output: dict[str, Any] = {}
        for label, group in joined.dropna(subset=["regime"]).groupby(
            "regime", observed=True
        ):
            returns = group["net_return"].astype(float)
            output[str(label)] = {
                "trade_count": int(len(group)),
                "net_return_sum": float(returns.sum()),
                "win_rate": float((returns > 0).mean()),
            }
        evidenced = sum(
            1 for item in output.values() if item["trade_count"] >= 5
        )
        return {
            "evidence_level": (
                "screening" if evidenced >= 2 else "insufficient_history"
            ),
            "ex_ante_observable": True,
            "regimes": output,
        }

    @staticmethod
    def _decision(
        *,
        base: StrategyDslResult,
        costs: Mapping[str, StrategyDslResult],
        rolling: Mapping[str, StrategyDslResult],
        perturbations: Mapping[str, StrategyDslResult],
        regime: Mapping[str, Any],
    ) -> dict[str, Any]:
        base_drawdown = abs(float(base.metrics["max_drawdown"]))
        cost_ok = all(
            float(item.metrics["total_return"]) >= 0.0
            for item in costs.values()
        )
        rolling_positive = sum(
            float(item.metrics["total_return"]) >= 0.0
            for item in rolling.values()
        )
        perturbation_ok = [
            float(item.metrics["total_return"]) >= 0.0
            and abs(float(item.metrics["max_drawdown"]))
            <= max(base_drawdown * 1.5, 0.01)
            for item in perturbations.values()
        ]
        perturbation_ratio = (
            sum(perturbation_ok) / len(perturbation_ok)
            if perturbation_ok
            else 0.0
        )
        regime_sufficient = regime.get("evidence_level") == "screening"
        passed = (
            cost_ok
            and rolling_positive >= 2
            and perturbation_ratio >= 0.6
            and regime_sufficient
        )
        return {
            "status": (
                "ready_for_locked_test_review"
                if passed
                else "needs_revision"
            ),
            "cost_sensitivity_passed": cost_ok,
            "positive_rolling_windows": rolling_positive,
            "rolling_window_count": len(rolling),
            "perturbation_pass_ratio": perturbation_ratio,
            "regime_evidence_sufficient": regime_sufficient,
            "locked_test_automatically_started": False,
            "next_action": (
                "用户可审阅证据后，单独批准一次最终保留测试。"
                if passed
                else "不要扩大搜索；审阅最弱证据并决定修改假设或停止。"
            ),
        }

    def _persist(
        self,
        *,
        job: Job,
        plan_id: str,
        baseline_id: str,
        candidate_id: str | None,
        trial_id: str,
        parameters: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_bytes: bytes,
        base: StrategyDslResult,
        costs: Mapping[str, StrategyDslResult],
        rolling: Mapping[str, StrategyDslResult],
        perturbations: Mapping[str, StrategyDslResult],
        regime: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        run_id = f"{pd.Timestamp(job.created_at).strftime('%Y%m%dT%H%M%SZ')}-candidate-{job.id[-8:]}"
        prefix = f"experiments/runs/{run_id}"
        result_payload = {
            "run_id": run_id,
            "experiment_plan_id": plan_id,
            "baseline_version_id": baseline_id,
            "candidate_version_id": candidate_id,
            "recommended_trial_id": trial_id,
            "parameters": dict(parameters),
            "cost_sensitivity": _metrics(costs),
            "rolling_windows": _metrics(rolling),
            "parameter_perturbations": _metrics(perturbations),
            "regime_screening": dict(regime),
            "decision": dict(decision),
            "locked_test_used": False,
        }
        outputs = [
            self._put_json(f"{prefix}/validation.json", result_payload),
            self._put_parquet(f"{prefix}/base-trades.parquet", base.trades),
            self._put_parquet(f"{prefix}/base-equity.parquet", base.equity),
        ]
        report_key = f"reports/experiments/{run_id}-candidate-validation.md"
        outputs.append(
            self._put_bytes(
                report_key,
                self._report(result_payload).encode("utf-8"),
            )
        )
        manifest_key = f"{prefix}/manifest.json"
        run_manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "generic_strategy_dsl_candidate_validation",
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
            "results": result_payload,
            "outputs": outputs,
            "locked_test": {
                "used": False,
                "automatically_started": False,
            },
            "claims": {
                "production_ready": False,
                "live_trading_ready": False,
            },
        }
        outputs.append(self._put_json(manifest_key, run_manifest))
        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="generic_candidate_validation",
                artifact_key=report_key,
                summary=result_payload,
                created_at=created_at,
            )
        )
        return {
            **result_payload,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [
                item["artifact_key"] for item in outputs
            ],
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
        output = {
            "artifact_key": key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if rows is not None:
            output["rows"] = rows
        return output

    @staticmethod
    def _report(payload: Mapping[str, Any]) -> str:
        decision = payload["decision"]
        return "\n".join(
            [
                "# 通用策略候选验证",
                "",
                f"- 推荐 Trial：`{payload['recommended_trial_id']}`",
                f"- 结论：**{decision['status']}**",
                "- 最终保留测试：未使用、未自动启动。",
                "",
                "## 证据摘要",
                "",
                (
                    f"- 成本敏感性：{'通过' if decision['cost_sensitivity_passed'] else '未通过'}"
                ),
                (
                    "- 滚动窗口："
                    f"{decision['positive_rolling_windows']}/"
                    f"{decision['rolling_window_count']} 个非负"
                ),
                (
                    "- 参数 ±10% 扰动通过比例："
                    f"{decision['perturbation_pass_ratio']:.0%}"
                ),
                (
                    "- 行情拆分证据："
                    f"{'足够' if decision['regime_evidence_sufficient'] else '不足'}"
                ),
                "",
                "## 下一步",
                "",
                f"- {decision['next_action']}",
                "- 本结果不构成盈利承诺、生产晋升或实盘授权。",
                "",
            ]
        )


def _metrics(
    results: Mapping[str, StrategyDslResult]
) -> dict[str, Any]:
    return {
        label: {
            "metrics": dict(result.metrics),
            "range": {
                "start_utc_inclusive": result.start_utc_inclusive,
                "end_utc_exclusive": result.end_utc_exclusive,
            },
        }
        for label, result in results.items()
    }


def _fee_per_side(cost_model: Mapping[str, Any]) -> float:
    value = cost_model.get(
        "fee_per_side", cost_model.get("taker_fee_per_side")
    )
    if not isinstance(value, (int, float)):
        raise ValueError("候选验证成本模型缺少单边手续费")
    return float(value)


def _slippage_bps_per_side(cost_model: Mapping[str, Any]) -> float:
    value = cost_model.get("slippage_bps_per_side")
    if isinstance(value, (int, float)):
        return float(value)
    fraction = cost_model.get("slippage_per_side")
    if isinstance(fraction, (int, float)):
        return float(fraction) * 10_000.0
    raise ValueError("候选验证成本模型缺少单边滑点")


class StressTestRouter:
    def __init__(
        self,
        *,
        generic_validation,
        generic_locked_test,
        legacy_cost_stress,
    ) -> None:
        self.generic_validation = generic_validation
        self.generic_locked_test = generic_locked_test
        self.legacy_cost_stress = legacy_cost_stress

    def __call__(self, job: Job) -> Mapping[str, Any]:
        if (
            job.payload.get("intent")
            == "generic_strategy_dsl_candidate_validation"
        ):
            return self.generic_validation(job)
        if job.payload.get("intent") == "generic_strategy_dsl_locked_test":
            return self.generic_locked_test(job)
        return self.legacy_cost_stress(job)
