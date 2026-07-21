from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
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


class AgentProviderKind(StrEnum):
    EXTERNAL_LOCAL_AGENT = "external_local_agent"
    EMBEDDED_CLOUD_PROVIDER = "embedded_cloud_provider"
    BYOK_PROVIDER = "byok_provider"
    LOCAL_MODEL_PROVIDER = "local_model_provider"


class ExecutionTargetKind(StrEnum):
    LOCAL_RUNTIME = "local_runtime"
    HOSTED_SANDBOX = "hosted_sandbox"

ALLOWED_JOB_TYPES = frozenset({"backtest", "data_quality", "report"})


@dataclass(frozen=True, slots=True)
class ResearchSession:
    id: str
    title: str
    status: SessionStatus
    created_at: str
    updated_at: str


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
    status: Literal["baseline", "candidate", "validated", "dry_run"]
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
    path: str
    summary: Mapping[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    session_id: str
    agent_name: str
    mode: AgentRunMode = "guided"
    status: Literal["queued", "running", "waiting_approval", "paused", "completed", "failed", "cancelled"] = "queued"
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
    agent_step_id: str
    tool_name: str
    sanitized_input: Mapping[str, Any]
    sanitized_output: Mapping[str, Any] | None
    status: Literal["requested", "approved", "running", "completed", "failed", "rejected"]


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    agent_run_id: str
    artifact_type: Literal["strategy", "diff", "experiment", "report", "manifest", "log"]
    project_relative_path: str
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    name: str
    kind: Literal["integer", "float", "categorical"]
    values: tuple[Any, ...] = ()
    lower: float | None = None
    upper: float | None = None


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
    log_reference: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: int | None
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: Literal["user", "external_agent", "embedded_provider", "system"]
    payload: Mapping[str, Any]
    created_at: str
