from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from quant_lab.application.batch_trials import summarize_stable_ranges
from quant_lab.domain.errors import GatePolicyError
from quant_lab.domain.models import AuditEvent, ComponentCandidate, ComponentEvidence
from quant_lab.domain.repositories import ProductRepository

from .services import new_id, utc_now


def normalized_logic_signature(
    *, component_type: str, logic: Mapping[str, Any], market_profile: str, timeframe: str
) -> str:
    canonical = {
        "component_type": component_type,
        "logic": logic,
        "market_profile": market_profile,
        "timeframe": timeframe,
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class DefaultComponentEvidenceAggregator:
    """Aggregate a component once per logic signature, not once per parameter value."""

    def __init__(self, repository: ProductRepository) -> None:
        self.repository = repository

    def aggregate(
        self, *, experiment_plan_id: str, attribution: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        plan = self.repository.get_experiment_plan(experiment_plan_id)
        trials = list(self.repository.list_trials(experiment_plan_id))
        if not trials:
            raise GatePolicyError("component attribution requires persisted Trials")
        required = {
            "name",
            "component_type",
            "logic",
            "market_profile",
            "timeframe",
            "out_of_sample_status",
            "evidence_level",
            "min_incremental_net_return",
            "min_validation_trades",
        }
        missing = sorted(required - set(attribution))
        if missing:
            raise GatePolicyError("component attribution missing: " + ", ".join(missing))
        if attribution["component_type"] not in {
            "entry",
            "filter",
            "exit",
            "risk",
            "execution",
        }:
            raise GatePolicyError("unsupported component type")
        logic = attribution["logic"]
        if not isinstance(logic, Mapping) or not logic:
            raise GatePolicyError("component logic must be structured and non-empty")
        signature = normalized_logic_signature(
            component_type=str(attribution["component_type"]),
            logic=logic,
            market_profile=str(attribution["market_profile"]),
            timeframe=str(attribution["timeframe"]),
        )
        summary = summarize_stable_ranges(trials, plan.constraints)
        succeeded = [item for item in trials if item.status == "succeeded"]
        best_incremental = max(
            (item.metrics.get("incremental_net_return", float("-inf")) for item in succeeded),
            default=float("-inf"),
        )
        max_trades = max(
            (item.metrics.get("validation_trade_count", 0.0) for item in succeeded),
            default=0.0,
        )
        eligible = (
            summary["stable_count"] >= 2
            and best_incremental >= float(attribution["min_incremental_net_return"])
            and max_trades >= float(attribution["min_validation_trades"])
            and attribution["out_of_sample_status"] in {"screening", "passed"}
            and attribution["evidence_level"] != "fixture"
        )
        evidence = ComponentEvidence(
            id=new_id("component_evidence"),
            source_strategy_version_id=plan.candidate_version_id
            or plan.baseline_version_id,
            lineage={
                "hypothesis": plan.hypothesis,
                "baseline_version_id": plan.baseline_version_id,
                "candidate_version_id": plan.candidate_version_id,
                "source_run_ids": [experiment_plan_id],
                "ablation": dict(logic),
                "parameters_are_not_components": True,
            },
            component_type=str(attribution["component_type"]),  # type: ignore[arg-type]
            target_market_profile=str(attribution["market_profile"]),
            incremental_metrics={
                "best_incremental_net_return": best_incremental,
                "max_validation_trade_count": max_trades,
                "stable_trial_count": float(summary["stable_count"]),
            },
            out_of_sample_status=str(attribution["out_of_sample_status"]),  # type: ignore[arg-type]
            failure_conditions=tuple(
                dict(item) for item in attribution.get("failure_conditions", [])
            ),
            created_at=utc_now(),
            logic_signature=signature,
            timeframe=str(attribution["timeframe"]),
            source_experiment_plan_id=experiment_plan_id,
            source_trial_ids=tuple(item.id for item in trials),
            stable_parameter_ranges=summary["stable_parameter_ranges"],
            failed_parameter_ranges=summary["failed_parameter_ranges"],
            regimes=tuple(str(item) for item in attribution.get("regimes", [])),
            evidence_level=str(attribution["evidence_level"]),  # type: ignore[arg-type]
        )
        created_evidence = self.repository.create_component_evidence(evidence)

        existing_candidate = None
        evidence_by_id = {
            item.id: item for item in self.repository.list_component_evidence()
        }
        for item in self.repository.list_component_candidates():
            linked = evidence_by_id.get(item.evidence_id)
            if (
                item.logic_signature == signature
                or (linked is not None and linked.logic_signature == signature)
            ):
                existing_candidate = item
                break
        candidate = existing_candidate
        if candidate is None:
            candidate = ComponentCandidate(
                id=new_id("component"),
                evidence_id=created_evidence.id,
                name=str(attribution["name"]).strip(),
                status="component_candidate" if eligible else "diagnostic_improvement",
                created_at=utc_now(),
                logic_signature=signature,
                target_market_profile=str(attribution["market_profile"]),
                timeframe=str(attribution["timeframe"]),
            )
            candidate = self.repository.create_component_candidate(candidate)

        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type="component_evidence.aggregated",
                aggregate_type="component_candidate",
                aggregate_id=candidate.id,
                actor_type="system",
                payload={
                    "experiment_plan_id": experiment_plan_id,
                    "logic_signature": signature,
                    "trial_count": len(trials),
                    "candidate_status": candidate.status,
                    "deduplicated": existing_candidate is not None,
                    "automatic_validation": False,
                },
                created_at=utc_now(),
            )
        )
        return {
            "evidence": created_evidence,
            "candidate": candidate,
            "eligible_for_component_candidate": eligible,
            "deduplicated": existing_candidate is not None,
            "automatic_validation": False,
        }
