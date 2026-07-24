from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .models import (
    AgentRun,
    Artifact,
    AuditEvent,
    ExperimentPlan,
    GateEvaluation,
    Job,
    Message,
    Proposal,
    Report,
    ResearchSession,
    ResearchBudget,
    StrategyDraft,
    StrategyOutcome,
    StrategyVersion,
    ToolCall,
    Trial,
    ComponentCandidate,
    ComponentEvidence,
    RegimeValidation,
)


class ProductRepository(Protocol):
    """Persistence port for local product state."""

    def initialize(self) -> None: ...

    def create_session(self, session: ResearchSession) -> ResearchSession: ...

    def list_sessions(self) -> Sequence[ResearchSession]: ...

    def get_session(self, session_id: str) -> ResearchSession: ...

    def create_research_budget(self, budget: ResearchBudget) -> ResearchBudget: ...

    def get_research_budget(self, session_id: str) -> ResearchBudget: ...

    def reserve_hypothesis(self, session_id: str) -> ResearchBudget: ...

    def reserve_experiment_resources(
        self, session_id: str, *, trials: int, compute_minutes: int
    ) -> ResearchBudget: ...

    def reserve_locked_test_use(self, session_id: str) -> ResearchBudget: ...

    def get_session_id_for_strategy_version(self, version_id: str) -> str: ...

    def add_message(self, message: Message) -> Message: ...

    def list_messages(self, session_id: str) -> Sequence[Message]: ...

    def create_draft(self, draft: StrategyDraft) -> StrategyDraft: ...

    def list_drafts(self, session_id: str | None = None) -> Sequence[StrategyDraft]: ...

    def get_draft(self, draft_id: str) -> StrategyDraft: ...

    def freeze_baseline(
        self,
        *,
        draft_id: str,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion: ...

    def get_strategy_version(self, version_id: str) -> StrategyVersion: ...

    def create_proposal(self, proposal: Proposal) -> Proposal: ...

    def get_proposal(self, proposal_id: str) -> Proposal: ...

    def list_proposals(self, draft_id: str | None = None) -> Sequence[Proposal]: ...

    def update_proposal(self, proposal: Proposal) -> Proposal: ...

    def approve_proposal_candidate(
        self,
        proposal: Proposal,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion: ...

    def accept_proposal(
        self,
        proposal: Proposal,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion: ...

    def reject_candidate(
        self,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion: ...

    def create_job(self, job: Job) -> Job: ...

    def list_jobs(self) -> Sequence[Job]: ...

    def get_job(self, job_id: str) -> Job: ...

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        updated_at: str,
        error: str | None = None,
    ) -> Job: ...

    def append_job_log(
        self,
        job_id: str,
        *,
        level: str,
        message: str,
        created_at: str,
    ) -> None: ...

    def list_job_logs(self, job_id: str) -> Sequence[Mapping[str, Any]]: ...

    def create_report(self, report: Report) -> Report: ...

    def list_reports(self, job_id: str | None = None) -> Sequence[Report]: ...

    def append_event(self, event: AuditEvent) -> AuditEvent: ...

    def list_events(self, *, limit: int = 100) -> Sequence[AuditEvent]: ...

    def create_experiment_plan(self, plan: ExperimentPlan) -> ExperimentPlan: ...

    def get_experiment_plan(self, plan_id: str) -> ExperimentPlan: ...

    def list_experiment_plans(self) -> Sequence[ExperimentPlan]: ...

    def approve_experiment_plan(
        self,
        plan: ExperimentPlan,
        *,
        approval_id: str,
        created_at: str,
    ) -> ExperimentPlan: ...

    def create_trial(self, trial: Trial) -> Trial: ...

    def update_trial(
        self,
        trial_id: str,
        *,
        status: str,
        metrics: Mapping[str, float],
        log_artifact_key: str | None,
        error: str | None = None,
        elapsed_seconds: float | None = None,
        peak_rss_mb: float | None = None,
        result_artifact_key: str | None = None,
        metrics_artifact_key: str | None = None,
    ) -> Trial: ...

    def get_trial_by_signature(
        self, plan_id: str, parameter_signature: str
    ) -> Trial | None: ...

    def list_trials(self, plan_id: str) -> Sequence[Trial]: ...

    def create_agent_run(self, agent_run: AgentRun) -> AgentRun: ...

    def get_agent_run(self, agent_run_id: str) -> AgentRun: ...

    def update_agent_run_status(self, agent_run_id: str, *, status: str) -> AgentRun: ...

    def list_agent_runs(self) -> Sequence[AgentRun]: ...

    def create_tool_call(self, tool_call: ToolCall) -> ToolCall: ...

    def list_tool_calls(self, agent_run_id: str) -> Sequence[ToolCall]: ...

    def create_artifact(self, artifact: Artifact) -> Artifact: ...

    def list_artifacts(self, agent_run_id: str) -> Sequence[Artifact]: ...

    def create_gate_evaluation(self, evaluation: GateEvaluation) -> GateEvaluation: ...

    def get_gate_evaluation(self, evaluation_id: str) -> GateEvaluation: ...

    def list_gate_evaluations(
        self, *, subject_id: str | None = None
    ) -> Sequence[GateEvaluation]: ...

    def create_strategy_outcome(self, outcome: StrategyOutcome) -> StrategyOutcome: ...

    def list_strategy_outcomes(self) -> Sequence[StrategyOutcome]: ...

    def create_component_evidence(
        self, evidence: ComponentEvidence
    ) -> ComponentEvidence: ...

    def get_component_evidence(self, evidence_id: str) -> ComponentEvidence: ...

    def list_component_evidence(self) -> Sequence[ComponentEvidence]: ...

    def create_component_candidate(
        self, candidate: ComponentCandidate
    ) -> ComponentCandidate: ...

    def list_component_candidates(self) -> Sequence[ComponentCandidate]: ...

    def create_regime_validation(
        self, validation: RegimeValidation
    ) -> RegimeValidation: ...

    def list_regime_validations(self) -> Sequence[RegimeValidation]: ...
