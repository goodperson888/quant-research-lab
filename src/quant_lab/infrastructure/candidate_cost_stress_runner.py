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
from quant_lab.application.services import new_id
from quant_lab.domain.models import Job, Report, validate_artifact_key
from quant_lab.domain.repositories import ProductRepository
from quant_lab.runs import _git_revision, _source_tree_digest

from .baseline_backtest_runner import BaselineBacktestRunner


EXPECTED_SCENARIOS = {
    "high_slippage_5bps": (0.0005, 5.0),
    "doubled_fee_and_slippage": (0.001, 4.0),
}


class CandidateCostStressRunner(BaselineBacktestRunner):
    """Run two fixed cost stresses without changing the candidate version."""

    def __init__(self, root: Path, repository: ProductRepository) -> None:
        super().__init__(root, repository)

    def validate_payload(
        self, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        required = {
            "intent",
            "candidate_version_id",
            "config_artifact_key",
            "agent_run_id",
        }
        missing = required - set(payload)
        if missing:
            raise ValueError(f"stress-test payload missing: {sorted(missing)}")
        if payload["intent"] != "stress_test":
            raise ValueError("cost stress handler only accepts stress_test intent")

        config_key = validate_artifact_key(str(payload["config_artifact_key"]))
        config = yaml.safe_load(self.artifacts.get(config_key))
        if not isinstance(config, dict):
            raise ValueError("stress config must be a YAML mapping")
        self._validate_config(config, payload)

        candidate = self.repository.get_strategy_version(
            str(payload["candidate_version_id"])
        )
        if candidate.status != "candidate" or not candidate.immutable:
            raise ValueError("stress test requires an immutable candidate version")
        if candidate.version != 1:
            raise ValueError("this stress plan is scoped to candidate v1")
        if (
            candidate.content_snapshot.get("baseline_version_id")
            != config["baseline_version_id"]
        ):
            raise ValueError("candidate baseline lineage mismatch")
        if candidate.content_snapshot.get("automatic_validation") is not False:
            raise ValueError("candidate must not be automatically validated")

        candidate_manifest_key = validate_artifact_key(
            config["candidate_run_manifest_artifact_key"]
        )
        candidate_manifest = json.loads(self.artifacts.get(candidate_manifest_key))
        if candidate_manifest["market_profile"] != config["market_profile"]:
            raise ValueError("candidate run market profile mismatch")
        if candidate_manifest["locked_test"]["inspected_in_this_run"] is not False:
            raise ValueError("source candidate run must exclude locked-test data")

        acceptance_key = validate_artifact_key(
            config["candidate_acceptance_artifact_key"]
        )
        acceptance = json.loads(self.artifacts.get(acceptance_key))
        if acceptance["strategy_version_id"] != candidate.id:
            raise ValueError("candidate acceptance artifact mismatch")
        if acceptance["validated"] is not False:
            raise ValueError("accepted candidate must still be unvalidated")
        return config, candidate_manifest, acceptance

    def __call__(self, job: Job) -> Mapping[str, Any]:
        config, candidate_manifest, acceptance = self.validate_payload(job.payload)
        data_manifest_key = validate_artifact_key(
            candidate_manifest["data_manifest"]["artifact_key"]
        )
        data_manifest_bytes = self.artifacts.get(data_manifest_key)
        data_manifest = json.loads(data_manifest_bytes)

        ohlcv = self._load_dataset(data_manifest, "futures_ohlcv", "15m")
        funding = self._load_dataset(data_manifest, "funding_rate", None)
        mark = self._load_dataset(data_manifest, "mark_price", "15m")

        run_id = self._stress_run_id(job)
        run_prefix = f"experiments/runs/{run_id}"
        manifest_key = f"{run_prefix}/manifest.json"
        if self.artifacts.exists(manifest_key):
            raise FileExistsError("stress run artifacts are immutable and already exist")

        reference = {
            label: dict(candidate_manifest["comparison"]["candidate"][label])
            for label in ("train", "validation")
        }
        scenario_results: dict[str, dict[str, Any]] = {}
        raw_results: dict[str, dict[str, BacktestSliceResult]] = {}
        output_records: list[dict[str, Any]] = []
        for scenario in config["scenarios"]:
            scenario_id = scenario["id"]
            backtester = PriceStructureBaselineBacktester(
                fee_per_side=float(scenario["fee_per_side"]),
                slippage_bps_per_side=float(scenario["slippage_bps_per_side"]),
                funding_missing_policy=config["funding"]["missing_policy"],
                entry_mode="confirmation_candle_breakout",
            )
            results: dict[str, BacktestSliceResult] = {}
            for label in ("train", "validation"):
                start, end = self._split_bounds(config["scope"][label])
                results[label] = backtester.run(
                    label=label,
                    ohlcv=self._before(ohlcv, end),
                    funding=self._before(funding, end),
                    mark=self._before(mark, end),
                    start_utc_inclusive=start,
                    end_utc_exclusive=end,
                )
                output_records.append(
                    self._put_parquet(
                        f"{run_prefix}/trades-{scenario_id}-{label}.parquet",
                        results[label].trades,
                    )
                )
                output_records.append(
                    self._put_parquet(
                        f"{run_prefix}/equity-{scenario_id}-{label}.parquet",
                        results[label].equity,
                    )
                )
            raw_results[scenario_id] = results
            scenario_results[scenario_id] = {
                "description": scenario["description"],
                "cost_model": {
                    "fee_per_side": float(scenario["fee_per_side"]),
                    "slippage_bps_per_side": float(
                        scenario["slippage_bps_per_side"]
                    ),
                },
                "train": dict(results["train"].metrics),
                "validation": dict(results["validation"].metrics),
                "sensitivity": {
                    "train_total_return_delta_vs_candidate": float(
                        results["train"].metrics["total_return"]
                    )
                    - float(reference["train"]["total_return"]),
                    "validation_total_return_delta_vs_candidate": float(
                        results["validation"].metrics["total_return"]
                    )
                    - float(reference["validation"]["total_return"]),
                    "validation_max_drawdown_abs": abs(
                        float(results["validation"].metrics["max_drawdown"])
                    ),
                },
                "funding": {
                    "train": dict(results["train"].funding),
                    "validation": dict(results["validation"].funding),
                },
            }

        triggered_failures: list[dict[str, Any]] = []
        for scenario_id, result in scenario_results.items():
            for condition in config["failure_conditions"]:
                actual = float(result["sensitivity"][condition["metric"]])
                triggered = self._compare(
                    actual,
                    condition["operator"],
                    float(condition["value"]),
                )
                if triggered:
                    triggered_failures.append(
                        {
                            "scenario_id": scenario_id,
                            "metric": condition["metric"],
                            "operator": condition["operator"],
                            "threshold": float(condition["value"]),
                            "actual": actual,
                            "interpretation": condition["interpretation"],
                        }
                    )

        sensitivity_payload = {
            "candidate_version_id": config["candidate_version_id"],
            "reference_candidate_run_id": candidate_manifest["run_id"],
            "reference": reference,
            "scenarios": scenario_results,
            "failure_conditions": list(config["failure_conditions"]),
            "triggered_failures": triggered_failures,
            "cost_stress_passed": not triggered_failures,
            "candidate_status_changed": False,
            "locked_test_used": False,
            "production_promotion_requested": False,
        }
        output_records.append(
            self._put_json(f"{run_prefix}/sensitivity.json", sensitivity_payload)
        )

        log_key = f"{run_prefix}/job.log.jsonl"
        log_lines = [
            {
                "level": "info",
                "event": "cost_stress.started",
                "job_id": job.id,
                "scenario_ids": sorted(scenario_results),
            },
            {
                "level": "info",
                "event": "locked_test.not_used",
                "previous_locked_test_used": False,
                "future_locked_test_used": False,
            },
            {
                "level": "info",
                "event": "cost_stress.completed",
                "cost_stress_passed": not triggered_failures,
                "triggered_failure_count": len(triggered_failures),
            },
        ]
        log_bytes = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in log_lines
        ).encode("utf-8")
        output_records.append(self._put_bytes(log_key, log_bytes))

        report_key = f"reports/risk/{run_id}-cost-stress.md"
        report = self._build_report(
            run_id=run_id,
            config=config,
            reference=reference,
            scenarios=scenario_results,
            triggered_failures=triggered_failures,
        )
        output_records.append(self._put_bytes(report_key, report.encode("utf-8")))

        created_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_version": 1,
            "run_id": run_id,
            "job_id": job.id,
            "run_type": "cost_stress_test",
            "intent": "stress_test",
            "status": "succeeded",
            "created_at": created_at,
            "agent_run_id": job.payload["agent_run_id"],
            "routing": dict(job.payload.get("routing", {})),
            "strategy": {
                "candidate_version_id": config["candidate_version_id"],
                "candidate_version": acceptance["version"],
                "status_before": "candidate",
                "status_after": "candidate",
                "immutable": True,
                "implementation_artifact_key": acceptance[
                    "candidate_implementation_artifact_key"
                ],
            },
            "market_profile": config["market_profile"],
            "validation_scope": "binance_only",
            "data_manifest": {
                "artifact_key": data_manifest_key,
                "sha256": hashlib.sha256(data_manifest_bytes).hexdigest(),
                "dataset_id": data_manifest["dataset_id"],
                "data_version": data_manifest["data_version"],
            },
            "reference_cost_model": dict(config["reference_cost_model"]),
            "stress_scenarios": list(config["scenarios"]),
            "time_splits": dict(config["scope"]),
            "funding": dict(config["funding"]),
            "random_seed": 20260722,
            "code_version": _source_tree_digest(self.root),
            "git_revision": _git_revision(self.root),
            "engine": "quant_lab_native_event_driven_cost_stress_v1",
            "sensitivity_results": sensitivity_payload,
            "outputs": output_records,
            "claims": {
                "long_term_profitability_proven": False,
                "okx_validated": False,
                "validated": False,
                "production_ready": False,
            },
        }
        manifest_record = self._put_json(manifest_key, manifest)
        output_records.append(manifest_record)

        self.repository.create_report(
            Report(
                id=new_id("report"),
                job_id=job.id,
                report_type="candidate_cost_stress",
                artifact_key=report_key,
                summary={
                    "run_id": run_id,
                    "candidate_version_id": config["candidate_version_id"],
                    "cost_stress_passed": not triggered_failures,
                    "triggered_failure_count": len(triggered_failures),
                    "candidate_status_changed": False,
                    "locked_test_used": False,
                },
                created_at=created_at,
            )
        )
        return {
            "run_id": run_id,
            "manifest_artifact_key": manifest_key,
            "report_artifact_key": report_key,
            "artifact_keys": [record["artifact_key"] for record in output_records],
            "cost_stress_passed": not triggered_failures,
            "triggered_failures": triggered_failures,
            "candidate_status_changed": False,
        }

    def _validate_config(
        self, config: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        if config.get("schema_version") != 1 or config.get("intent") != "stress_test":
            raise ValueError("unsupported stress config schema or intent")
        if config.get("candidate_version_id") != payload["candidate_version_id"]:
            raise ValueError("stress config candidate does not match Job")
        if config.get("market_profile") != "crypto_perpetual.binance.eth":
            raise ValueError("this cost stress is scoped to Binance only")
        if config.get("entry_rule", {}).get("mode") != "confirmation_candle_breakout":
            raise ValueError("stress config must preserve candidate entry mode")
        if config.get("entry_rule", {}).get("leverage") != 1.0:
            raise ValueError("stress config leverage must remain 1")
        if config.get("funding", {}).get("required") is not True:
            raise ValueError("funding must remain required")
        if config.get("funding", {}).get("zero_funding_fallback_allowed") is not False:
            raise ValueError("zero funding fallback remains forbidden")
        if config.get("scope", {}).get("previous_locked_test_used") is not False:
            raise ValueError("previous locked test must remain excluded")
        if config.get("scope", {}).get("future_locked_test_used") is not False:
            raise ValueError("future locked test must remain excluded")
        guardrails = config.get("guardrails", {})
        for field in (
            "candidate_status_change_allowed",
            "automatic_validation_allowed",
            "dry_run_allowed",
            "production_allowed",
            "live_trading_enabled",
        ):
            if guardrails.get(field) is not False:
                raise ValueError(f"guardrails.{field} must be false")
        scenarios = config.get("scenarios")
        if not isinstance(scenarios, list) or len(scenarios) != len(EXPECTED_SCENARIOS):
            raise ValueError("stress config must contain the two fixed cost scenarios")
        actual = {
            item["id"]: (
                float(item["fee_per_side"]),
                float(item["slippage_bps_per_side"]),
            )
            for item in scenarios
        }
        if actual != EXPECTED_SCENARIOS:
            raise ValueError("stress scenarios differ from the declared fixed set")
        if not config.get("failure_conditions"):
            raise ValueError("stress failure conditions are required")

    @staticmethod
    def _split_bounds(value: str) -> tuple[str, str]:
        closing = value.find(")")
        if closing < 0 or not value.startswith("["):
            raise ValueError(f"invalid UTC split interval: {value}")
        start, end = value[1:closing].split(",", 1)
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
            raise ValueError(f"unsupported failure-condition operator: {operator}")
        return operations[operator]

    @staticmethod
    def _stress_run_id(job: Job) -> str:
        created = pd.Timestamp(job.created_at).tz_convert("UTC")
        return f"{created.strftime('%Y%m%dT%H%M%SZ')}-stress-{job.id[-8:]}"

    @staticmethod
    def _build_report(
        *,
        run_id: str,
        config: Mapping[str, Any],
        reference: Mapping[str, Mapping[str, Any]],
        scenarios: Mapping[str, Mapping[str, Any]],
        triggered_failures: list[dict[str, Any]],
    ) -> str:
        def percent(value: Any) -> str:
            return "—" if value is None else f"{float(value):.2%}"

        lines = [
            "# Candidate v1 成本压力测试",
            "",
            f"- Run：`{run_id}`",
            f"- Candidate：`{config['candidate_version_id']}`，状态保持 `candidate`。",
            "- 范围：Binance ETH/USDT 永续 15m，train + validation。",
            "- 旧锁定测试和未来锁定测试均未使用；杠杆 1；实盘关闭。",
            "",
            "## Validation 对照",
            "",
            "| 场景 | 手续费/边 | 滑点/边 | 交易数 | 总收益 | 相对 Candidate | 最大回撤 |",
            "|---|---:|---:|---:|---:|---:|---:|",
            "| reference candidate | 0.05% | 2 bps | {trades} | {ret} | — | {dd} |".format(
                trades=reference["validation"]["trade_count"],
                ret=percent(reference["validation"]["total_return"]),
                dd=percent(reference["validation"]["max_drawdown"]),
            ),
        ]
        for scenario_id, result in scenarios.items():
            costs = result["cost_model"]
            metrics = result["validation"]
            lines.append(
                "| {scenario} | {fee} | {slippage:g} bps | {trades} | {ret} | {delta} | {dd} |".format(
                    scenario=scenario_id,
                    fee=percent(costs["fee_per_side"]),
                    slippage=costs["slippage_bps_per_side"],
                    trades=metrics["trade_count"],
                    ret=percent(metrics["total_return"]),
                    delta=percent(
                        result["sensitivity"][
                            "validation_total_return_delta_vs_candidate"
                        ]
                    ),
                    dd=percent(metrics["max_drawdown"]),
                )
            )
        lines.extend(["", "## 失败条件", ""])
        if triggered_failures:
            for failure in triggered_failures:
                lines.append(
                    "- `{scenario_id}` 触发 `{metric}`：实际 `{actual:.8f}`，条件 `{operator} {threshold:.8f}`。{interpretation}".format(
                        **failure
                    )
                )
        else:
            lines.append("- 两个场景均未触发预先声明的成本脆弱性条件。")
        lines.extend(
            [
                "",
                "## 结论与边界",
                "",
                "本轮只覆盖成本压力；入场延迟、漏单、参数扰动、数据异常、不同周期和跨交易所仍待后续测试。",
                "压力结果不会自动改变 candidate 状态，也不构成 validated、dry-run、production 或盈利承诺。",
                "",
            ]
        )
        return "\n".join(lines)
