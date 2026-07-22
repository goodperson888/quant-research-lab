from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping


SessionStatus = Literal[
    "inbox",
    "formalized",
    "baseline",
    "candidate",
    "validated",
    "dry_run",
    "degraded",
    "retired",
    "rejected",
]
DraftStatus = Literal["draft", "awaiting_confirmation", "baseline_frozen"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
AgentRunMode = Literal["supervised", "guided", "bounded_autonomous"]
GateStatus = Literal["passed", "failed", "blocked", "not_evaluated"]
RegimeMode = Literal["regime_diagnostic", "regime_validation"]


class AgentProviderKind(StrEnum):
    EXTERNAL_LOCAL_AGENT = "external_local_agent"
    EMBEDDED_CLOUD_PROVIDER = "embedded_cloud_provider"
    BYOK_PROVIDER = "byok_provider"
    LOCAL_MODEL_PROVIDER = "local_model_provider"


class ExecutionTargetKind(StrEnum):
    LOCAL_RUNTIME = "local_runtime"
    HOSTED_SANDBOX = "hosted_sandbox"

ALLOWED_JOB_TYPES = frozenset(
    {
        "backtest",
        "correctness_diagnostic",
        "data_quality",
        "parameter_search",
        "regime_validation",
        "report",
        "stress_test",
    }
)


def validate_artifact_key(artifact_key: str) -> str:
    """Validate a portable project-relative artifact key."""

    if not artifact_key or artifact_key != artifact_key.strip():
        raise ValueError("artifact_key must be a non-empty trimmed project-relative key")
    if "\\" in artifact_key or "://" in artifact_key:
        raise ValueError("artifact_key must use a project-relative POSIX path")
    path = PurePosixPath(artifact_key)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("artifact_key must not be absolute or traverse outside the project")
    if path.parts[0].startswith("~") or path.parts[0].endswith(":"):
        raise ValueError("artifact_key must not contain a workstation path prefix")
    return artifact_key


@dataclass(frozen=True, slots=True)
class ResearchSession:
    id: str
    title: str
    status: SessionStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    session_id: str
    max_hypotheses: int
    max_trials_total: int
    max_compute_minutes: int
    max_locked_test_uses: int
    require_user_approval_for_new_hypothesis: bool
    used_hypotheses: int = 0
    reserved_trials: int = 0
    reserved_compute_minutes: int = 0
    used_locked_test_uses: int = 0

    @property
    def remaining_hypotheses(self) -> int:
        return max(0, self.max_hypotheses - self.used_hypotheses)

    @property
    def remaining_trials(self) -> int:
        return max(0, self.max_trials_total - self.reserved_trials)

    @property
    def remaining_compute_minutes(self) -> int:
        return max(0, self.max_compute_minutes - self.reserved_compute_minutes)

    @property
    def remaining_locked_test_uses(self) -> int:
        return max(0, self.max_locked_test_uses - self.used_locked_test_uses)


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StrategyDraft:
    id: str
    session_id: str
    source_type: Literal["natural_language", "pine", "file"]
    source_name: str | None
    raw_content: str
    structured_content: Mapping[str, Any] = field(default_factory=dict)
    status: DraftStatus = "draft"
    baseline_version_id: str | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class Ambiguity:
    id: str
    draft_id: str
    question: str
    status: Literal["open", "resolved"] = "open"
    resolution: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    draft_id: str
    proposal_type: str
    content: Mapping[str, Any]
    status: Literal["draft", "accepted", "rejected"] = "draft"


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    subject_type: str
    subject_id: str
    decision: Literal["approved", "rejected"]
    actor: Literal["user"]
    created_at: str


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    id: str
    strategy_id: str
    version: int
    status: Literal[
        "baseline", "candidate", "validated", "dry_run", "degraded", "retired", "rejected"
    ]
    content_snapshot: Mapping[str, Any]
    source_snapshot: str
    created_at: str
    immutable: bool = True


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    job_type: str
    status: JobStatus
    payload: Mapping[str, Any]
    created_at: str
    updated_at: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Report:
    id: str
    job_id: str
    report_type: str
    artifact_key: str
    summary: Mapping[str, Any]
    created_at: str

    def __post_init__(self) -> None:
        validate_artifact_key(self.artifact_key)


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    session_id: str
    agent_name: str
    agent_provider: AgentProviderKind = AgentProviderKind.EXTERNAL_LOCAL_AGENT
    execution_target: ExecutionTargetKind = ExecutionTargetKind.LOCAL_RUNTIME
    mode: AgentRunMode = "guided"
    status: Literal[
        "queued",
        "running",
        "waiting_approval",
        "paused",
        "completed",
        "failed",
        "cancelled",
    ] = "queued"
    plan_summary: str | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class AgentStep:
    id: str
    agent_run_id: str
    title: str
    status: Literal["pending", "running", "waiting_approval", "completed", "failed", "cancelled"]
    sequence: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    agent_run_id: str
    tool_name: str
    sanitized_input: Mapping[str, Any]
    sanitized_output: Mapping[str, Any] | None
    status: Literal[
        "requested", "approved", "running", "completed", "failed", "rejected"
    ]
    agent_step_id: str | None = None
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    agent_run_id: str
    artifact_type: Literal["strategy", "diff", "experiment", "report", "manifest", "log"]
    artifact_key: str
    checksum: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        validate_artifact_key(self.artifact_key)


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    name: str
    kind: Literal["integer", "float", "categorical"]
    values: tuple[Any, ...] = ()
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if self.kind == "categorical" and not self.values:
            raise ValueError("categorical parameter space requires values")
        if self.kind in {"integer", "float"}:
            if self.lower is None or self.upper is None:
                raise ValueError("numeric parameter space requires lower and upper")
            if self.lower > self.upper:
                raise ValueError("parameter space lower must be <= upper")


@dataclass(frozen=True, slots=True)
class Objective:
    metric: str
    direction: Literal["minimize", "maximize"]


@dataclass(frozen=True, slots=True)
class Constraint:
    metric: str
    operator: Literal["lt", "lte", "gt", "gte", "eq"]
    value: float


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    id: str
    baseline_version_id: str
    hypothesis: str
    parameter_space: tuple[ParameterSpace, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...]
    data_splits: Mapping[str, str]
    cost_model: Mapping[str, Any]
    max_trials: int | None
    time_budget_seconds: int | None
    stopping_conditions: tuple[str, ...]
    status: Literal["draft", "approved", "rejected"] = "draft"
    approved_by: str | None = None
    created_at: str = ""

    def approve(self, *, actor: Literal["user"] = "user") -> "ExperimentPlan":
        from .errors import ExperimentPlanValidationError

        missing: list[str] = []
        if not self.baseline_version_id:
            missing.append("baseline_version_id")
        if not self.hypothesis.strip():
            missing.append("hypothesis")
        if not self.parameter_space:
            missing.append("parameter_space")
        if not self.objectives:
            missing.append("objectives")
        if not self.constraints:
            missing.append("constraints")
        if not self.cost_model:
            missing.append("cost_model")
        required_splits = {"train", "validation", "locked_test"}
        if not required_splits.issubset(self.data_splits):
            missing.append("data_splits(train, validation, locked_test)")
        has_trial_budget = self.max_trials is not None and self.max_trials > 0
        has_time_budget = (
            self.time_budget_seconds is not None and self.time_budget_seconds > 0
        )
        if not (has_trial_budget or has_time_budget):
            missing.append("max_trials or time_budget_seconds")
        if not self.stopping_conditions:
            missing.append("stopping_conditions")
        if missing:
            raise ExperimentPlanValidationError(
                "experiment plan cannot be approved; missing: " + ", ".join(missing)
            )
        return replace(self, status="approved", approved_by=actor)


@dataclass(frozen=True, slots=True)
class Trial:
    id: str
    experiment_plan_id: str
    parameters: Mapping[str, Any]
    data_version: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    metrics: Mapping[str, float] = field(default_factory=dict)
    log_artifact_key: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.log_artifact_key is not None:
            validate_artifact_key(self.log_artifact_key)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: int | None
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: Literal["user", "external_agent", "embedded_provider", "system"]
    payload: Mapping[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class PipelineProfile:
    id: str
    label: str
    description: str
    candidate_eligible: bool
    stages: tuple[Mapping[str, Any], ...]
    gates: Mapping[str, Mapping[str, Any]]
    acceptance_policies: Mapping[str, Any]
    pine_validation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    id: str
    profile_id: str
    gate_name: str
    subject_type: Literal["strategy_version", "component_candidate", "experiment_plan"]
    subject_id: str
    market_profile: str
    strategy_objective: str
    status: GateStatus
    metrics: Mapping[str, float]
    reasons: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    id: str
    strategy_version_id: str
    market_profile: str
    pipeline_profile_id: str
    outcome_type: Literal[
        "diagnostic_improvement", "strategy_candidate", "validated", "rejected"
    ]
    viability_gate_result_id: str | None
    evidence_artifact_keys: tuple[str, ...]
    notes: str
    created_at: str

    def __post_init__(self) -> None:
        for artifact_key in self.evidence_artifact_keys:
            validate_artifact_key(artifact_key)


@dataclass(frozen=True, slots=True)
class ComponentEvidence:
    id: str
    source_strategy_version_id: str
    lineage: Mapping[str, Any]
    component_type: Literal["entry", "filter", "exit", "risk", "execution"]
    target_market_profile: str
    incremental_metrics: Mapping[str, float]
    out_of_sample_status: Literal[
        "not_tested", "screening", "insufficient_history", "passed", "failed"
    ]
    failure_conditions: tuple[Mapping[str, Any], ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class ComponentCandidate:
    id: str
    evidence_id: str
    name: str
    status: Literal["diagnostic_improvement", "component_candidate", "rejected"]
    created_at: str


@dataclass(frozen=True, slots=True)
class RegimeValidation:
    id: str
    subject_type: Literal["strategy_version", "component_candidate"]
    subject_id: str
    mode: RegimeMode
    market_profile: str
    detector_version: str
    ex_ante_observable: bool
    target_regimes: tuple[str, ...]
    suitable_regimes: tuple[str, ...]
    conditional_regimes: tuple[str, ...]
    blocked_regimes: tuple[str, ...]
    unknown_regimes: tuple[str, ...]
    regime_metrics: Mapping[str, Mapping[str, float]]
    transition_policy: Mapping[str, Any]
    history_days: int
    evidence_status: Literal[
        "screening", "insufficient_history", "extended_validation"
    ]
    viability_gate_result_id: str | None
    created_at: str

    def __post_init__(self) -> None:
        if not self.ex_ante_observable:
            raise ValueError("regime labels must be ex-ante observable")
        if self.mode == "regime_diagnostic":
            if self.evidence_status not in {"screening", "insufficient_history"}:
                raise ValueError(
                    "regime diagnostic evidence must remain screening or insufficient_history"
                )
            if self.viability_gate_result_id is not None:
                raise ValueError("regime diagnostic must not claim a viability gate")
        elif self.viability_gate_result_id is None:
            raise ValueError("regime validation requires a passed viability gate result")
        if self.history_days <= 90 and self.evidence_status == "extended_validation":
            raise ValueError(
                "90-day regime evidence must remain screening or insufficient_history"
            )
