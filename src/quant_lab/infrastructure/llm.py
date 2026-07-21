from __future__ import annotations

from typing import Any, Mapping

from quant_lab.application.ports import LLMProviderStatus
from quant_lab.domain.errors import ProviderNotConfiguredError
from quant_lab.domain.models import AgentProviderKind


class UnconfiguredLLMProvider:
    """Explicit stage-0 adapter: no key lookup and no remote model call."""

    def status(self) -> LLMProviderStatus:
        return LLMProviderStatus(
            configured=False,
            provider=None,
            message="AI Provider 未配置；当前只保存原始策略和人工确认结果。",
            kind=AgentProviderKind.EMBEDDED_CLOUD_PROVIDER,
        )

    def propose_formalization(self, raw_content: str) -> Mapping[str, Any]:
        raise ProviderNotConfiguredError(
            "AI provider is not configured; no formalization was generated"
        )
