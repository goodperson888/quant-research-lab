from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CreateSessionRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)


class SessionResponse(StrictModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    research_mode: Literal["quick", "guided", "expert"]
    mode_config: dict[str, Any]
    mode_revision: int


class ResearchModeDefinitionResponse(StrictModel):
    mode: Literal["quick", "guided", "expert"]
    label: str
    description: str
    agent_run_mode: Literal["supervised", "guided", "bounded_autonomous"]
    pause_policy: Literal["critical_only", "key_decisions", "every_stage"]
    stage_visibility: Literal["summary", "guided", "full"]
    default_trial_budget: int
    auto_failure_diagnostics: bool


class ResearchModeConfigRequest(StrictModel):
    agent_run_mode: Literal["supervised", "guided", "bounded_autonomous"] | None = None
    pause_policy: Literal["critical_only", "key_decisions", "every_stage"] | None = None
    stage_visibility: Literal["summary", "guided", "full"] | None = None
    default_trial_budget: int | None = Field(default=None, ge=1, le=200)
    auto_failure_diagnostics: bool | None = None


class UpdateResearchModeRequest(StrictModel):
    mode: Literal["quick", "guided", "expert"]
    mode_config: ResearchModeConfigRequest | None = None
    confirmed_by_user: bool


class CreateMessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=50_000)


class MessageResponse(StrictModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: str


class CreateIntakeRequest(StrictModel):
    source_type: Literal["natural_language", "pine", "file"]
    source_name: str | None = Field(default=None, max_length=240)
    raw_content: str = Field(min_length=1, max_length=500_000)


class StrategyDraftResponse(StrictModel):
    id: str
    session_id: str
    source_type: str
    source_name: str | None
    raw_content: str
    structured_content: dict[str, Any]
    status: str
    baseline_version_id: str | None
    created_at: str


class FormalizeStrategyRequest(StrictModel):
    subject_id: str = Field(min_length=1)
    confirmed_by_user: bool
    structured_content: dict[str, Any]


class FreezeBaselineRequest(StrictModel):
    confirmed_by_user: bool
    subject_id: str | None = Field(default=None, min_length=1)


class StrategyVersionResponse(StrictModel):
    id: str
    strategy_id: str
    version: int
    status: str
    content_snapshot: dict[str, Any]
    source_snapshot: str
    created_at: str
    immutable: bool


class CreateJobRequest(StrictModel):
    job_type: Literal[
        "backtest", "data_quality", "parameter_search", "report", "stress_test"
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResponse(StrictModel):
    id: str
    job_type: str
    status: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    error: str | None


class AuditEventResponse(StrictModel):
    id: int | None
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    payload: dict[str, Any]
    created_at: str


class ResearchHandoffResponse(StrictModel):
    id: str
    session_id: str
    agent_run_id: str | None
    subject_id: str
    status: Literal[
        "completed_scope",
        "waiting_user_approval",
        "waiting_required_input",
        "gate_failed",
        "budget_exhausted",
        "blocked_dependency",
        "safety_refusal",
        "failed",
    ]
    stop_reason_code: str
    stop_reason_text: str
    completed_actions: list[str]
    not_started_actions: list[str]
    user_action_required: bool
    required_user_action: str | None
    next_recommended_action: str
    approval_subject_id: str | None
    safe_to_continue: bool
    created_at: str


class CreateResearchAuthorizationRequest(StrictModel):
    subject_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    allowed_stages: list[
        Literal[
            "correctness",
            "smoke",
            "fast_screen",
            "viability",
            "loss_attribution",
            "regime_diagnostic",
            "component_hypothesis_generation",
        ]
    ]
    auto_continue: bool = True
    max_cost_usdt: float = Field(default=0.0, ge=0)
    max_time_minutes: int = Field(default=45, gt=0, le=240)
    max_trials: int = Field(default=0, ge=0, le=200)
    locked_test_allowed: Literal[False] = False
    stop_conditions: list[str] = Field(min_length=1)
    expires_at: str = Field(min_length=1)
    confirmed_by_user: bool


class ResearchAuthorizationResponse(StrictModel):
    id: str
    subject_id: str
    session_id: str
    allowed_stages: list[str]
    auto_continue: bool
    max_cost_usdt: float
    max_time_minutes: int
    max_trials: int
    locked_test_allowed: bool
    stop_conditions: list[str]
    expires_at: str
    approved_by: str
    status: str
    created_at: str
    used_cost_usdt: float
    used_time_minutes: float
    used_trials: int


class ResearchAuthorizationStageResponse(StrictModel):
    id: str
    authorization_id: str
    stage: str
    status: str
    evidence_refs: list[str]
    reason: str
    elapsed_minutes: float
    cost_usdt: float
    trials_used: int
    created_at: str


class ParameterSpaceRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["integer", "float", "categorical"]
    values: tuple[Any, ...] = ()
    lower: float | None = None
    upper: float | None = None
    step: float | None = None


class ObjectiveRequest(StrictModel):
    metric: str = Field(min_length=1, max_length=120)
    direction: Literal["minimize", "maximize"]


class ConstraintRequest(StrictModel):
    metric: str = Field(min_length=1, max_length=120)
    operator: Literal["lt", "lte", "gt", "gte", "eq"]
    value: float


class CreateExperimentPlanRequest(StrictModel):
    baseline_version_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1, max_length=2_000)
    parameter_space: list[ParameterSpaceRequest]
    objectives: list[ObjectiveRequest]
    constraints: list[ConstraintRequest] = Field(default_factory=list)
    data_splits: dict[str, str]
    cost_model: dict[str, Any]
    max_trials: int | None = Field(default=None, gt=0)
    time_budget_seconds: int | None = Field(default=None, gt=0)
    stopping_conditions: list[str]
    proposal_id: str | None = None
    candidate_version_id: str | None = None
    search_strategy: Literal["grid", "random"] = "grid"
    random_seed: int = 0


class ExperimentPlanResponse(StrictModel):
    id: str
    baseline_version_id: str
    hypothesis: str
    parameter_space: list[ParameterSpaceRequest]
    objectives: list[ObjectiveRequest]
    constraints: list[ConstraintRequest]
    data_splits: dict[str, str]
    cost_model: dict[str, Any]
    max_trials: int | None
    time_budget_seconds: int | None
    stopping_conditions: list[str]
    proposal_id: str | None
    candidate_version_id: str | None
    search_strategy: str
    random_seed: int
    status: str
    approved_by: str | None
    created_at: str


class SessionDetailResponse(StrictModel):
    session: SessionResponse
    messages: list[MessageResponse]
    drafts: list[StrategyDraftResponse]


class PipelineProfileResponse(StrictModel):
    id: str
    label: str
    description: str
    candidate_eligible: bool
    stages: list[dict[str, Any]]
    gates: dict[str, dict[str, Any]]
    acceptance_policies: dict[str, Any]
    pine_validation: dict[str, Any]


class EvaluateGateRequest(StrictModel):
    profile_id: Literal["smoke", "fast_screen", "full_validation"]
    gate_name: Literal[
        "correctness",
        "fast_screen",
        "incremental",
        "viability",
        "robustness",
        "locked_test",
        "dry_run",
    ]
    subject_type: Literal[
        "strategy_version", "component_candidate", "experiment_plan"
    ]
    subject_id: str = Field(min_length=1)
    market_profile: str = Field(min_length=1)
    strategy_objective: str = Field(default="standalone", min_length=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    checks: dict[str, bool] = Field(default_factory=dict)


class GateEvaluationResponse(StrictModel):
    id: str
    profile_id: str
    gate_name: str
    subject_type: str
    subject_id: str
    market_profile: str
    strategy_objective: str
    status: str
    metrics: dict[str, float]
    reasons: list[str]
    created_at: str


class CreateStrategyOutcomeRequest(StrictModel):
    strategy_version_id: str = Field(min_length=1)
    market_profile: str = Field(min_length=1)
    pipeline_profile_id: Literal["smoke", "fast_screen", "full_validation"]
    outcome_type: Literal[
        "diagnostic_improvement", "strategy_candidate", "validated", "rejected"
    ]
    viability_gate_result_id: str | None = None
    evidence_artifact_keys: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=4_000)


class StrategyOutcomeResponse(StrictModel):
    id: str
    strategy_version_id: str
    market_profile: str
    pipeline_profile_id: str
    outcome_type: str
    viability_gate_result_id: str | None
    evidence_artifact_keys: list[str]
    notes: str
    created_at: str


class CreateComponentCandidateRequest(StrictModel):
    source_strategy_version_id: str = Field(min_length=1)
    lineage: dict[str, Any]
    component_type: Literal["entry", "filter", "exit", "risk", "execution"]
    target_market_profile: str = Field(min_length=1)
    incremental_metrics: dict[str, float]
    out_of_sample_status: Literal[
        "not_tested", "screening", "insufficient_history", "passed", "failed"
    ]
    failure_conditions: list[dict[str, Any]] = Field(default_factory=list)
    name: str = Field(min_length=1, max_length=200)
    status: Literal["diagnostic_improvement", "component_candidate", "rejected"]


class ComponentCandidateResponse(StrictModel):
    id: str
    evidence_id: str
    name: str
    status: str
    created_at: str
    logic_signature: str
    target_market_profile: str
    timeframe: str
    archived_at: str | None
    archive_reason: str | None


class ComponentArchiveRequest(StrictModel):
    subject_id: str = Field(min_length=1)
    confirmed_by_user: bool
    reason: str = Field(default="user_archived", min_length=1, max_length=500)


class ComponentEvidenceResponse(StrictModel):
    id: str
    source_strategy_version_id: str
    lineage: dict[str, Any]
    component_type: str
    target_market_profile: str
    incremental_metrics: dict[str, float]
    out_of_sample_status: str
    failure_conditions: list[dict[str, Any]]
    created_at: str
    logic_signature: str
    timeframe: str
    source_experiment_plan_id: str | None
    source_trial_ids: list[str]
    stable_parameter_ranges: dict[str, Any]
    failed_parameter_ranges: dict[str, Any]
    regimes: list[str]
    evidence_level: str


class ComponentTriageResponse(StrictModel):
    evidence: ComponentEvidenceResponse
    candidate: ComponentCandidateResponse
    automatic_validation: Literal[False]


class ComponentHypothesisResponse(StrictModel):
    id: str
    session_id: str
    subject_id: str
    title: str
    hypothesis: str
    component_type: str
    source: str
    evidence_refs: list[str]
    expected_improvement: str
    parameter_space: dict[str, Any]
    suggested_trials: int
    failure_conditions: list[str]
    evidence_level: str
    contamination_status: str
    status: str
    created_at: str


class CreateRegimeValidationJobRequest(StrictModel):
    mode: Literal["regime_diagnostic", "regime_validation"]
    subject_type: Literal["strategy_version", "component_candidate"]
    subject_id: str = Field(min_length=1)
    market_profile: str = Field(min_length=1)
    detector_config_artifact_key: str = Field(min_length=1)
    data_manifest_artifact_key: str = Field(min_length=1)
    trades_artifact_key: str = Field(min_length=1)
    agent_run_id: str | None = None
    viability_gate_result_id: str | None = None


class CreateResearchDiagnosticJobRequest(StrictModel):
    authorization_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    fast_screen_manifest_artifact_key: str = Field(min_length=1)
    metrics_artifact_key: str = Field(min_length=1)
    trades_artifact_key: str = Field(min_length=1)
    signals_artifact_key: str = Field(min_length=1)
    data_manifest_artifact_key: str = Field(min_length=1)
    detector_config_artifact_key: str = Field(min_length=1)
    agent_run_id: str | None = None
    retry_of_job_id: str | None = None


class CreateRegimeValidationRequest(StrictModel):
    mode: Literal["regime_diagnostic", "regime_validation"]
    subject_type: Literal["strategy_version", "component_candidate"]
    subject_id: str = Field(min_length=1)
    market_profile: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    ex_ante_observable: bool
    target_regimes: list[str] = Field(default_factory=list)
    suitable_regimes: list[str] = Field(default_factory=list)
    conditional_regimes: list[str] = Field(default_factory=list)
    blocked_regimes: list[str] = Field(default_factory=list)
    unknown_regimes: list[str] = Field(default_factory=list)
    regime_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    transition_policy: dict[str, Any]
    history_days: int = Field(gt=0)
    evidence_status: Literal[
        "screening", "insufficient_history", "extended_validation"
    ]
    viability_gate_result_id: str | None = None


class RegimeValidationResponse(StrictModel):
    id: str
    subject_type: str
    subject_id: str
    mode: str
    market_profile: str
    detector_version: str
    ex_ante_observable: bool
    target_regimes: list[str]
    suitable_regimes: list[str]
    conditional_regimes: list[str]
    blocked_regimes: list[str]
    unknown_regimes: list[str]
    regime_metrics: dict[str, dict[str, float]]
    transition_policy: dict[str, Any]
    history_days: int
    evidence_status: str
    viability_gate_result_id: str | None
    created_at: str


class CreateCorrectnessDiagnosticJobRequest(StrictModel):
    strategy_version_id: str = Field(min_length=1)
    analysis_type: Literal["lookahead-analysis", "recursive-analysis"]
    strategy_name: str = Field(min_length=1, max_length=160)
    strategy_artifact_key: str = Field(min_length=1)
    config_artifact_key: str = Field(min_length=1)
    timerange: str | None = Field(default=None, pattern=r"^[0-9]{8}-[0-9]{8}$")
    agent_run_id: str | None = None


class CreateImprovementDirectionRequest(StrictModel):
    baseline_version_id: str
    subject_id: str
    hypothesis: str
    rule_diff: dict[str, Any]
    evidence_refs: list[str] = []
    parameter_space: list[ParameterSpaceRequest]
    data_splits: dict[str, str]
    cost_model: dict[str, Any]
    objectives: list[ObjectiveRequest]
    constraints: list[ConstraintRequest]
    estimated_trials: int
    estimated_minutes: int
    failure_conditions: list[str]
    stopping_conditions: list[str]
    rollback_plan: str
    source: Literal["manual", "external_agent"] = "external_agent"


class ImprovementDirectionResponse(StrictModel):
    id: str
    draft_id: str
    proposal_type: str
    content: dict[str, Any]
    status: str
    baseline_version_id: str | None
    subject_id: str | None
    hypothesis: str
    rule_diff: dict[str, Any]
    evidence_refs: list[str]
    parameter_space: list[ParameterSpaceRequest]
    data_splits: dict[str, str]
    cost_model: dict[str, Any]
    objectives: list[ObjectiveRequest]
    constraints: list[ConstraintRequest]
    estimated_trials: int | None
    estimated_minutes: int | None
    failure_conditions: list[str]
    stopping_conditions: list[str]
    rollback_plan: str
    candidate_version_id: str | None
    created_at: str


class ProposalTransitionRequest(StrictModel):
    subject_id: str
    target: Literal["executing", "evaluated", "accepted", "rejected", "expired"]
    confirmed_by_user: bool = False


class ProposalBudgetRequest(StrictModel):
    subject_id: str
    estimated_trials: int = Field(gt=0)
    estimated_minutes: int = Field(gt=0)


class JobActionRequest(StrictModel):
    subject_id: str
    confirmed_by_user: bool


class CreateEngineReconciliationRequest(StrictModel):
    subject_id: str = Field(min_length=1)
    market_profile: str = Field(min_length=1)
    native_run_bundle_id: str = Field(min_length=1)
    confirmed_by_user: bool
    locked_test_used: Literal[False] = False


class TrialResponse(StrictModel):
    id: str
    experiment_plan_id: str
    parameters: dict[str, Any]
    data_version: str
    status: str
    metrics: dict[str, float]
    log_artifact_key: str | None
    candidate_version_id: str | None
    parameter_signature: str | None
    split: str
    cost_model: dict[str, Any]
    seed: int
    error: str | None
    elapsed_seconds: float | None
    peak_rss_mb: float | None
    result_artifact_key: str | None
    metrics_artifact_key: str | None
    created_at: str


class ComponentAggregationRequest(StrictModel):
    experiment_plan_id: str
    name: str
    component_type: Literal["entry", "filter", "exit", "risk", "execution"]
    logic: dict[str, Any]
    market_profile: str
    timeframe: str
    out_of_sample_status: Literal[
        "not_tested", "screening", "insufficient_history", "passed", "failed"
    ]
    evidence_level: Literal[
        "fixture", "diagnostic", "screening", "insufficient_history", "validated"
    ]
    min_incremental_net_return: float
    min_validation_trades: int
    failure_conditions: list[dict[str, Any]] = []
    regimes: list[str] = []
