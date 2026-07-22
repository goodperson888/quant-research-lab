from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from quant_lab.domain.errors import GatePolicyError
from quant_lab.domain.models import (
    AuditEvent,
    ComponentCandidate,
    ComponentEvidence,
    GateEvaluation,
    PipelineProfile,
    RegimeValidation,
    StrategyOutcome,
)
from quant_lab.domain.repositories import ProductRepository

from .services import new_id, utc_now


VIABILITY_METRICS = (
    "validation_net_return",
    "validation_profit_factor",
    "validation_expectancy",
    "validation_trade_count",
    "validation_max_drawdown_abs",
)


class PipelineProfileCatalog:
    """Load small, versioned pipeline policy files from the project."""

    def __init__(self, project_root: Path) -> None:
        self.directory = project_root / "configs" / "pipelines"

    def list(self) -> tuple[PipelineProfile, ...]:
        profiles = [self._load(path) for path in sorted(self.directory.glob("*.yaml"))]
        if not profiles:
            raise GatePolicyError("no pipeline profiles are configured")
        return tuple(profiles)

    def get(self, profile_id: str) -> PipelineProfile:
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        raise GatePolicyError(f"pipeline profile not found: {profile_id}")

    @staticmethod
    def _load(path: Path) -> PipelineProfile:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise GatePolicyError(f"invalid pipeline profile: {path.name}")
        required = {
            "profile_id",
            "label",
            "description",
            "candidate_eligible",
            "stages",
            "gates",
            "acceptance_policies",
            "pine_validation",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise GatePolicyError(
                f"pipeline profile {path.name} missing: {', '.join(missing)}"
            )
        return PipelineProfile(
            id=str(raw["profile_id"]),
            label=str(raw["label"]),
            description=str(raw["description"]),
            candidate_eligible=bool(raw["candidate_eligible"]),
            stages=tuple(dict(item) for item in raw["stages"]),
            gates={str(key): dict(value) for key, value in raw["gates"].items()},
            acceptance_policies=dict(raw["acceptance_policies"]),
            pine_validation=dict(raw["pine_validation"]),
        )


class PipelineApplicationService:
    def __init__(
        self, repository: ProductRepository, catalog: PipelineProfileCatalog
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.repository.initialize()

    def _audit(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                actor_type="external_agent",
                payload=dict(payload),
                created_at=utc_now(),
            )
        )

    def list_profiles(self) -> tuple[PipelineProfile, ...]:
        return self.catalog.list()

    def evaluate_gate(
        self,
        *,
        profile_id: str,
        gate_name: str,
        subject_type: str,
        subject_id: str,
        market_profile: str,
        strategy_objective: str,
        metrics: Mapping[str, float],
        checks: Mapping[str, bool],
    ) -> GateEvaluation:
        profile = self.catalog.get(profile_id)
        gate = profile.gates.get(gate_name)
        if gate is None:
            raise GatePolicyError(
                f"gate {gate_name} is not configured for profile {profile_id}"
            )
        if subject_type == "strategy_version":
            self.repository.get_strategy_version(subject_id)

        previous = self.repository.list_gate_evaluations(subject_id=subject_id)
        passed = {item.gate_name for item in previous if item.status == "passed"}
        missing_dependencies = [
            name for name in gate.get("requires", []) if name not in passed
        ]
        reasons: list[str] = []
        if missing_dependencies:
            status = "blocked"
            reasons.append(
                "missing passed prerequisite gates: " + ", ".join(missing_dependencies)
            )
        elif gate_name == "viability":
            status, reasons = self._evaluate_viability(
                profile=profile,
                market_profile=market_profile,
                strategy_objective=strategy_objective,
                metrics=metrics,
            )
        else:
            if not checks:
                status = "not_evaluated"
                reasons.append("gate requires explicit deterministic checks")
            else:
                failed_checks = sorted(key for key, value in checks.items() if not value)
                status = "failed" if failed_checks else "passed"
                if failed_checks:
                    reasons.append("failed checks: " + ", ".join(failed_checks))
                else:
                    reasons.append("all declared deterministic checks passed")

        evaluation = GateEvaluation(
            id=new_id("gate"),
            profile_id=profile_id,
            gate_name=gate_name,
            subject_type=subject_type,  # type: ignore[arg-type]
            subject_id=subject_id,
            market_profile=market_profile,
            strategy_objective=strategy_objective,
            status=status,  # type: ignore[arg-type]
            metrics=dict(metrics),
            reasons=tuple(reasons),
            created_at=utc_now(),
        )
        created = self.repository.create_gate_evaluation(evaluation)
        self._audit(
            event_type="pipeline_gate.evaluated",
            aggregate_type="gate_evaluation",
            aggregate_id=created.id,
            payload={
                "profile_id": created.profile_id,
                "gate_name": created.gate_name,
                "subject_id": created.subject_id,
                "status": created.status,
                "reasons": created.reasons,
            },
        )
        return created

    def _evaluate_viability(
        self,
        *,
        profile: PipelineProfile,
        market_profile: str,
        strategy_objective: str,
        metrics: Mapping[str, float],
    ) -> tuple[str, list[str]]:
        missing = [name for name in VIABILITY_METRICS if name not in metrics]
        if missing:
            return "not_evaluated", ["missing viability metrics: " + ", ".join(missing)]
        market_family = market_profile.split(".", 1)[0]
        market_policy = profile.acceptance_policies.get(market_family)
        if not isinstance(market_policy, Mapping):
            raise GatePolicyError(
                f"profile {profile.id} has no AcceptancePolicy for {market_family}"
            )
        thresholds = market_policy.get(strategy_objective)
        if not isinstance(thresholds, Mapping):
            raise GatePolicyError(
                f"profile {profile.id} has no objective policy: {strategy_objective}"
            )
        if strategy_objective != "standalone":
            raise GatePolicyError("viability gate is defined for standalone strategies")

        comparisons = (
            (
                "validation_net_return",
                metrics["validation_net_return"] >= thresholds["min_validation_net_return"],
                f">= {thresholds['min_validation_net_return']}",
            ),
            (
                "validation_profit_factor",
                metrics["validation_profit_factor"]
                >= thresholds["min_validation_profit_factor"],
                f">= {thresholds['min_validation_profit_factor']}",
            ),
            (
                "validation_expectancy",
                metrics["validation_expectancy"] >= thresholds["min_validation_expectancy"],
                f">= {thresholds['min_validation_expectancy']}",
            ),
            (
                "validation_trade_count",
                metrics["validation_trade_count"] >= thresholds["min_validation_trades"],
                f">= {thresholds['min_validation_trades']}",
            ),
            (
                "validation_max_drawdown_abs",
                metrics["validation_max_drawdown_abs"]
                <= thresholds["max_validation_drawdown_abs"],
                f"<= {thresholds['max_validation_drawdown_abs']}",
            ),
        )
        failed = [
            f"{metric}={metrics[metric]:.8f} required {requirement}"
            for metric, ok, requirement in comparisons
            if not ok
        ]
        return ("failed", failed) if failed else ("passed", ["all viability thresholds passed"])

    def list_gate_results(self, *, subject_id: str | None = None):
        return self.repository.list_gate_evaluations(subject_id=subject_id)

    def create_strategy_outcome(
        self,
        *,
        strategy_version_id: str,
        market_profile: str,
        pipeline_profile_id: str,
        outcome_type: str,
        viability_gate_result_id: str | None,
        evidence_artifact_keys: Sequence[str],
        notes: str,
    ) -> StrategyOutcome:
        self.repository.get_strategy_version(strategy_version_id)
        profile = self.catalog.get(pipeline_profile_id)
        if outcome_type in {"strategy_candidate", "validated"}:
            if not profile.candidate_eligible:
                raise GatePolicyError(
                    f"pipeline profile {profile.id} cannot produce strategy candidates"
                )
            if not viability_gate_result_id:
                raise GatePolicyError("strategy candidate requires a passed viability gate")
            gate = self.repository.get_gate_evaluation(viability_gate_result_id)
            if (
                gate.subject_id != strategy_version_id
                or gate.gate_name != "viability"
                or gate.status != "passed"
            ):
                raise GatePolicyError("strategy candidate requires its own passed viability gate")
            if outcome_type == "validated":
                passed = {
                    item.gate_name
                    for item in self.repository.list_gate_evaluations(
                        subject_id=strategy_version_id
                    )
                    if item.status == "passed"
                }
                if not {"robustness", "locked_test"}.issubset(passed):
                    raise GatePolicyError(
                        "validated outcome requires passed robustness and locked_test gates"
                    )
        outcome = StrategyOutcome(
            id=new_id("outcome"),
            strategy_version_id=strategy_version_id,
            market_profile=market_profile,
            pipeline_profile_id=pipeline_profile_id,
            outcome_type=outcome_type,  # type: ignore[arg-type]
            viability_gate_result_id=viability_gate_result_id,
            evidence_artifact_keys=tuple(evidence_artifact_keys),
            notes=notes.strip(),
            created_at=utc_now(),
        )
        created = self.repository.create_strategy_outcome(outcome)
        self._audit(
            event_type="strategy_outcome.created",
            aggregate_type="strategy_outcome",
            aggregate_id=created.id,
            payload={
                "strategy_version_id": created.strategy_version_id,
                "outcome_type": created.outcome_type,
                "market_profile": created.market_profile,
            },
        )
        return created

    def create_component_evidence(
        self,
        *,
        source_strategy_version_id: str,
        lineage: Mapping[str, Any],
        component_type: str,
        target_market_profile: str,
        incremental_metrics: Mapping[str, float],
        out_of_sample_status: str,
        failure_conditions: Sequence[Mapping[str, Any]],
    ) -> ComponentEvidence:
        self.repository.get_strategy_version(source_strategy_version_id)
        evidence = ComponentEvidence(
            id=new_id("component_evidence"),
            source_strategy_version_id=source_strategy_version_id,
            lineage=dict(lineage),
            component_type=component_type,  # type: ignore[arg-type]
            target_market_profile=target_market_profile,
            incremental_metrics=dict(incremental_metrics),
            out_of_sample_status=out_of_sample_status,  # type: ignore[arg-type]
            failure_conditions=tuple(dict(item) for item in failure_conditions),
            created_at=utc_now(),
        )
        created = self.repository.create_component_evidence(evidence)
        self._audit(
            event_type="component_evidence.created",
            aggregate_type="component_evidence",
            aggregate_id=created.id,
            payload={
                "source_strategy_version_id": created.source_strategy_version_id,
                "component_type": created.component_type,
                "out_of_sample_status": created.out_of_sample_status,
            },
        )
        return created

    def create_component_candidate(
        self, *, evidence_id: str, name: str, status: str
    ) -> ComponentCandidate:
        self.repository.get_component_evidence(evidence_id)
        if status not in {"diagnostic_improvement", "component_candidate", "rejected"}:
            raise GatePolicyError(
                "failed-strategy components cannot be automatically marked validated"
            )
        candidate = ComponentCandidate(
            id=new_id("component"),
            evidence_id=evidence_id,
            name=name.strip(),
            status=status,  # type: ignore[arg-type]
            created_at=utc_now(),
        )
        created = self.repository.create_component_candidate(candidate)
        self._audit(
            event_type="component_candidate.created",
            aggregate_type="component_candidate",
            aggregate_id=created.id,
            payload={
                "evidence_id": created.evidence_id,
                "status": created.status,
                "automatic_validation": False,
            },
        )
        return created

    def triage_component(
        self,
        *,
        source_strategy_version_id: str,
        lineage: Mapping[str, Any],
        component_type: str,
        target_market_profile: str,
        incremental_metrics: Mapping[str, float],
        out_of_sample_status: str,
        failure_conditions: Sequence[Mapping[str, Any]],
        name: str,
        status: str,
    ) -> tuple[ComponentEvidence, ComponentCandidate]:
        """Record bounded ablation evidence without implying factor validation."""

        required_lineage = {
            "hypothesis",
            "baseline_version_id",
            "source_run_ids",
            "ablation",
        }
        missing = sorted(required_lineage - set(lineage))
        if missing:
            raise GatePolicyError(
                "component triage requires lineage fields: " + ", ".join(missing)
            )
        if status not in {"diagnostic_improvement", "component_candidate", "rejected"}:
            raise GatePolicyError("component triage cannot create a validated component")
        evidence = self.create_component_evidence(
            source_strategy_version_id=source_strategy_version_id,
            lineage=lineage,
            component_type=component_type,
            target_market_profile=target_market_profile,
            incremental_metrics=incremental_metrics,
            out_of_sample_status=out_of_sample_status,
            failure_conditions=failure_conditions,
        )
        candidate = self.create_component_candidate(
            evidence_id=evidence.id, name=name, status=status
        )
        self._audit(
            event_type="component_triage.recorded",
            aggregate_type="component_candidate",
            aggregate_id=candidate.id,
            payload={
                "source_strategy_version_id": source_strategy_version_id,
                "evidence_id": evidence.id,
                "status": candidate.status,
                "automatic_validation": False,
            },
        )
        return evidence, candidate

    def create_regime_validation(self, **values: Any) -> RegimeValidation:
        history_days = int(values["history_days"])
        evidence_status = str(values["evidence_status"])
        mode = str(values["mode"])
        viability_gate_result_id = values.get("viability_gate_result_id")
        if values.get("ex_ante_observable") is not True:
            raise GatePolicyError("regime labels must be ex-ante observable")
        if mode not in {"regime_diagnostic", "regime_validation"}:
            raise GatePolicyError("unknown regime evidence mode")
        if history_days <= 90 and evidence_status not in {
            "screening",
            "insufficient_history",
        }:
            raise GatePolicyError(
                "90-day regime evidence must be screening or insufficient_history"
            )
        if mode == "regime_diagnostic":
            if evidence_status not in {"screening", "insufficient_history"}:
                raise GatePolicyError(
                    "regime diagnostic cannot produce formal validation evidence"
                )
            if viability_gate_result_id is not None:
                raise GatePolicyError("regime diagnostic must not claim viability approval")
        else:
            if not isinstance(viability_gate_result_id, str):
                raise GatePolicyError(
                    "formal regime validation requires a passed viability gate"
                )
            gate = self.repository.get_gate_evaluation(viability_gate_result_id)
            if (
                gate.subject_id != values["subject_id"]
                or gate.gate_name != "viability"
                or gate.status != "passed"
                or gate.market_profile != values["market_profile"]
            ):
                raise GatePolicyError(
                    "formal regime validation requires the subject's passed viability gate"
                )
        if values["subject_type"] == "strategy_version":
            self.repository.get_strategy_version(values["subject_id"])
        else:
            candidates = {
                item.id for item in self.repository.list_component_candidates()
            }
            if values["subject_id"] not in candidates:
                raise GatePolicyError("component candidate subject does not exist")
        validation = RegimeValidation(
            id=new_id("regime"),
            subject_type=values["subject_type"],
            subject_id=values["subject_id"],
            mode=mode,  # type: ignore[arg-type]
            market_profile=values["market_profile"],
            detector_version=values["detector_version"],
            ex_ante_observable=values["ex_ante_observable"],
            target_regimes=tuple(values["target_regimes"]),
            suitable_regimes=tuple(values["suitable_regimes"]),
            conditional_regimes=tuple(values["conditional_regimes"]),
            blocked_regimes=tuple(values["blocked_regimes"]),
            unknown_regimes=tuple(values["unknown_regimes"]),
            regime_metrics={
                str(key): dict(item) for key, item in values["regime_metrics"].items()
            },
            transition_policy=dict(values["transition_policy"]),
            history_days=history_days,
            evidence_status=evidence_status,  # type: ignore[arg-type]
            viability_gate_result_id=viability_gate_result_id,
            created_at=utc_now(),
        )
        created = self.repository.create_regime_validation(validation)
        self._audit(
            event_type=f"{created.mode}.created",
            aggregate_type="regime_validation",
            aggregate_id=created.id,
            payload={
                "subject_id": created.subject_id,
                "mode": created.mode,
                "detector_version": created.detector_version,
                "ex_ante_observable": created.ex_ante_observable,
                "history_days": created.history_days,
                "evidence_status": created.evidence_status,
                "viability_gate_result_id": created.viability_gate_result_id,
                "strategy_promotion_allowed": False,
            },
        )
        return created

    def list_strategy_outcomes(self):
        return self.repository.list_strategy_outcomes()

    def list_component_candidates(self):
        return self.repository.list_component_candidates()

    def list_component_evidence(self):
        return self.repository.list_component_evidence()

    def list_regime_validations(self):
        return self.repository.list_regime_validations()


def profile_as_dict(profile: PipelineProfile) -> dict[str, Any]:
    return asdict(profile)
