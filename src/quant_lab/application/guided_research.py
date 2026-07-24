from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from quant_lab.domain.errors import ApprovalRequiredError, ConflictError
from quant_lab.domain.models import (
    AuditEvent,
    Constraint,
    Objective,
    ParameterSpace,
    Proposal,
    ProposalStatus,
    StrategyVersion,
)
from quant_lab.domain.repositories import ProductRepository

from .services import new_id, utc_now


TERMINAL_PROPOSAL_STATUSES = frozenset({"accepted", "rejected", "expired"})


class GuidedResearchService:
    """Manage structured improvement directions without fabricating AI output."""

    def __init__(self, repository: ProductRepository, *, max_directions: int = 3) -> None:
        self.repository = repository
        self.max_directions = max_directions
        self.repository.initialize()

    def _audit(self, event_type: str, proposal: Proposal, payload: Mapping[str, Any]) -> None:
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type=event_type,
                aggregate_type="proposal",
                aggregate_id=proposal.id,
                actor_type="external_agent",
                payload=dict(payload),
                created_at=utc_now(),
            )
        )

    def create_direction(
        self,
        *,
        baseline_version_id: str,
        subject_id: str,
        hypothesis: str,
        rule_diff: Mapping[str, Any],
        evidence_refs: Sequence[str],
        parameter_space: Sequence[ParameterSpace],
        data_splits: Mapping[str, str],
        cost_model: Mapping[str, Any],
        objectives: Sequence[Objective],
        constraints: Sequence[Constraint],
        estimated_trials: int,
        estimated_minutes: int,
        failure_conditions: Sequence[str],
        stopping_conditions: Sequence[str],
        rollback_plan: str,
        source: str = "external_agent",
    ) -> Proposal:
        baseline = self.repository.get_strategy_version(baseline_version_id)
        if baseline.status != "baseline" or not baseline.immutable:
            raise ConflictError("improvement direction requires an immutable baseline")
        if subject_id != baseline_version_id:
            raise ConflictError("proposal subject_id must identify its baseline")
        active = [
            item
            for item in self.repository.list_proposals(baseline.strategy_id)
            if item.proposal_type == "improvement_direction"
            and item.status not in TERMINAL_PROPOSAL_STATUSES
        ]
        if len(active) >= self.max_directions:
            raise ConflictError(
                f"guided research allows at most {self.max_directions} active directions"
            )
        proposal = Proposal(
            id=new_id("proposal"),
            draft_id=baseline.strategy_id,
            proposal_type="improvement_direction",
            content={
                "source": source,
                "ai_generated": False,
                "baseline_immutable": True,
                "production_promotion_requested": False,
            },
            status="draft",
            baseline_version_id=baseline.id,
            subject_id=subject_id,
            hypothesis=hypothesis.strip(),
            rule_diff=dict(rule_diff),
            evidence_refs=tuple(evidence_refs),
            parameter_space=tuple(parameter_space),
            data_splits=dict(data_splits),
            cost_model=dict(cost_model),
            objectives=tuple(objectives),
            constraints=tuple(constraints),
            estimated_trials=estimated_trials,
            estimated_minutes=estimated_minutes,
            failure_conditions=tuple(failure_conditions),
            stopping_conditions=tuple(stopping_conditions),
            rollback_plan=rollback_plan.strip(),
            created_at=utc_now(),
        )
        created = self.repository.create_proposal(proposal)
        self._audit(
            "improvement_direction.created",
            created,
            {
                "baseline_version_id": baseline.id,
                "subject_id": subject_id,
                "status": created.status,
                "source": source,
                "automatic_approval": False,
            },
        )
        return created

    def list_directions(self, *, baseline_version_id: str | None = None):
        proposals = list(self.repository.list_proposals())
        return [
            item
            for item in proposals
            if item.proposal_type == "improvement_direction"
            and (baseline_version_id is None or item.baseline_version_id == baseline_version_id)
        ]

    @staticmethod
    def _validate_for_approval(proposal: Proposal) -> None:
        missing: list[str] = []
        if not proposal.baseline_version_id or proposal.subject_id != proposal.baseline_version_id:
            missing.append("baseline_version_id/subject_id")
        if not proposal.hypothesis:
            missing.append("single hypothesis")
        if not proposal.rule_diff:
            missing.append("rule diff")
        if not proposal.parameter_space:
            missing.append("parameter space")
        if not {"train", "validation", "locked_test"}.issubset(proposal.data_splits):
            missing.append("train/validation/locked_test split")
        if not proposal.cost_model:
            missing.append("cost model")
        if not proposal.objectives or not proposal.constraints:
            missing.append("objective/constraints")
        if not proposal.estimated_trials or proposal.estimated_trials <= 0:
            missing.append("estimated Trials")
        if not proposal.estimated_minutes or proposal.estimated_minutes <= 0:
            missing.append("estimated time")
        if not proposal.failure_conditions or not proposal.stopping_conditions:
            missing.append("failure/stopping conditions")
        if not proposal.rollback_plan:
            missing.append("rollback plan")
        if missing:
            raise ConflictError("proposal is incomplete: " + ", ".join(missing))

    def submit_for_approval(self, proposal_id: str) -> Proposal:
        proposal = self.repository.get_proposal(proposal_id)
        self._validate_for_approval(proposal)
        submitted = proposal.transition("waiting_approval")
        saved = self.repository.update_proposal(submitted)
        self._audit("improvement_direction.waiting_approval", saved, {"status": saved.status})
        return saved

    def revise_budget(
        self,
        *,
        proposal_id: str,
        subject_id: str,
        estimated_trials: int,
        estimated_minutes: int,
    ) -> Proposal:
        if subject_id != proposal_id:
            raise ApprovalRequiredError("budget revision requires the exact subject_id")
        if estimated_trials <= 0 or estimated_minutes <= 0:
            raise ConflictError("proposal budget values must be positive")
        current = self.repository.get_proposal(proposal_id)
        if current.status not in {"draft", "waiting_approval"}:
            raise ConflictError("only draft/waiting proposals can revise their budget")
        revised = replace(
            current,
            status="draft",
            estimated_trials=estimated_trials,
            estimated_minutes=estimated_minutes,
        )
        saved = self.repository.update_proposal(revised)
        self._audit(
            "improvement_direction.budget_revised",
            saved,
            {
                "subject_id": subject_id,
                "estimated_trials": estimated_trials,
                "estimated_minutes": estimated_minutes,
                "approval_reset": current.status == "waiting_approval",
            },
        )
        return saved

    def approve(
        self, *, proposal_id: str, subject_id: str, confirmed_by_user: bool
    ) -> tuple[Proposal, StrategyVersion]:
        if not confirmed_by_user or subject_id != proposal_id:
            raise ApprovalRequiredError(
                "proposal approval requires explicit confirmation for the exact subject_id"
            )
        current = self.repository.get_proposal(proposal_id)
        self._validate_for_approval(current)
        approved = current.transition("approved")
        candidate = self.repository.approve_proposal_candidate(
            approved,
            version_id=new_id("version"),
            approval_id=new_id("approval"),
            created_at=utc_now(),
        )
        saved = replace(approved, candidate_version_id=candidate.id)
        self._audit(
            "improvement_direction.approved",
            saved,
            {
                "subject_id": subject_id,
                "candidate_version_id": candidate.id,
                "baseline_overwritten": False,
                "automatic_validation": False,
            },
        )
        return saved, candidate

    def transition(
        self,
        *,
        proposal_id: str,
        target: ProposalStatus,
        subject_id: str,
        confirmed_by_user: bool = False,
    ) -> Proposal:
        if subject_id != proposal_id:
            raise ApprovalRequiredError("proposal transition requires the exact subject_id")
        if target in {"accepted", "rejected"} and not confirmed_by_user:
            raise ApprovalRequiredError(f"proposal {target} requires explicit user confirmation")
        current = self.repository.get_proposal(proposal_id)
        if target == "evaluated":
            plans = [
                item
                for item in self.repository.list_experiment_plans()
                if item.proposal_id == proposal_id
            ]
            if not plans:
                raise ConflictError("proposal cannot be evaluated without an ExperimentPlan")
            trials = [
                trial
                for plan in plans
                for trial in self.repository.list_trials(plan.id)
            ]
            if not trials or any(item.status in {"queued", "running"} for item in trials):
                raise ConflictError(
                    "proposal cannot be evaluated until persisted Trials are terminal"
                )
        saved = self.repository.update_proposal(current.transition(target))
        self._audit(
            f"improvement_direction.{target}",
            saved,
            {"subject_id": subject_id, "confirmed_by_user": confirmed_by_user},
        )
        return saved
