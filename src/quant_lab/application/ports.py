from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from quant_lab.domain.models import AgentProviderKind, ExecutionTargetKind, Job


@dataclass(frozen=True, slots=True)
class LLMProviderStatus:
    configured: bool
    provider: str | None
    message: str
    kind: AgentProviderKind = AgentProviderKind.EXTERNAL_LOCAL_AGENT


@dataclass(frozen=True, slots=True)
class ExecutionTargetStatus:
    available: bool
    kind: ExecutionTargetKind
    message: str


class AgentProvider(Protocol):
    """Provider port shared by external and future embedded agents."""

    def status(self) -> LLMProviderStatus: ...


class ExecutionTarget(Protocol):
    """Execution-location port; it does not broaden the Job allowlist."""

    def status(self) -> ExecutionTargetStatus: ...

    def submit(self, job: Job) -> str: ...


class ArtifactStore(Protocol):
    """Store bytes by validated project-relative artifact key only."""

    def put(self, artifact_key: str, content: bytes) -> None: ...

    def get(self, artifact_key: str) -> bytes: ...

    def exists(self, artifact_key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class BacktestEngineCapabilities:
    engine_id: str
    supported_stages: tuple[str, ...]
    supports_exchange_execution_model: bool
    supports_dry_run: bool
    requires_exchange_metadata: bool
    notes: tuple[str, ...] = ()


class BacktestEnginePort(Protocol):
    """Deterministic backtest boundary; never exposes live trade or arbitrary shell."""

    def capabilities(self) -> BacktestEngineCapabilities: ...

    def run(self, job: Job) -> Mapping[str, Any]: ...


class TrialMetricsSink(Protocol):
    """Batch Trial metrics to columnar storage instead of one tiny file per Trial."""

    def append_batch(
        self, *, experiment_plan_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class TrialEvaluationRequest:
    trial_id: str
    experiment_plan_id: str
    baseline_version_id: str
    candidate_version_id: str | None
    parameters: Mapping[str, Any]
    data_version: str
    data_splits: Mapping[str, str]
    cost_model: Mapping[str, Any]
    seed: int


@dataclass(frozen=True, slots=True)
class TrialEvaluationResult:
    trial_id: str
    status: str
    metrics: Mapping[str, float] = field(default_factory=dict)
    error: str | None = None
    elapsed_seconds: float = 0.0
    peak_rss_mb: float = 0.0
    result_artifact_key: str | None = None
    stop_reason: str | None = None


class StrategyEvaluator(Protocol):
    """Evaluate one immutable candidate without selecting parameters or using locked data."""

    evaluator_id: str

    def evaluate(self, request: TrialEvaluationRequest) -> TrialEvaluationResult: ...


class TrialExecutor(Protocol):
    """Execute deterministic Trial requests under process/resource bounds."""

    def execute(
        self,
        *,
        evaluator: StrategyEvaluator,
        requests: Sequence[TrialEvaluationRequest],
        max_concurrency: int,
        timeout_seconds: int,
        max_rss_mb: int,
        kill_on_memory_limit: bool,
    ) -> Sequence[TrialEvaluationResult]: ...


class ComponentEvidenceAggregator(Protocol):
    """Aggregate Trial evidence by normalized component logic, never parameter values."""

    def aggregate(
        self, *, experiment_plan_id: str, attribution: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class LLMProvider(AgentProvider, Protocol):
    """Port for future strategy formalization providers.

    Stage 0 only supplies an unconfigured adapter. Provider output must always be a
    draft/proposal and can never freeze or overwrite a strategy by itself.
    """

    def status(self) -> LLMProviderStatus: ...

    def propose_formalization(self, raw_content: str) -> Mapping[str, Any]: ...
