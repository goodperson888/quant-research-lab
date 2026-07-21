from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

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


class LLMProvider(AgentProvider, Protocol):
    """Port for future strategy formalization providers.

    Stage 0 only supplies an unconfigured adapter. Provider output must always be a
    draft/proposal and can never freeze or overwrite a strategy by itself.
    """

    def status(self) -> LLMProviderStatus: ...

    def propose_formalization(self, raw_content: str) -> Mapping[str, Any]: ...
