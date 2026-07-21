from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class LLMProviderStatus:
    configured: bool
    provider: str | None
    message: str


class LLMProvider(Protocol):
    """Port for future strategy formalization providers.

    Stage 0 only supplies an unconfigured adapter. Provider output must always be a
    draft/proposal and can never freeze or overwrite a strategy by itself.
    """

    def status(self) -> LLMProviderStatus: ...

    def propose_formalization(self, raw_content: str) -> Mapping[str, Any]: ...
