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


class ParameterSpaceRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["integer", "float", "categorical"]
    values: tuple[Any, ...] = ()
    lower: float | None = None
    upper: float | None = None


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


class CreateRegimeValidationRequest(StrictModel):
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


class RegimeValidationResponse(StrictModel):
    id: str
    subject_type: str
    subject_id: str
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
    created_at: str
