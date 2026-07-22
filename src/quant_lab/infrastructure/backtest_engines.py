from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from quant_lab.application.ports import BacktestEngineCapabilities
from quant_lab.domain.models import Job


class NativeBacktestEngineAdapter:
    """Compatibility adapter around a reviewed native Job handler."""

    def __init__(self, handler: Callable[[Job], Mapping[str, Any]]) -> None:
        self.handler = handler

    def capabilities(self) -> BacktestEngineCapabilities:
        return BacktestEngineCapabilities(
            engine_id="native_research",
            supported_stages=("smoke", "fast_screen", "cheap_cost_sensitivity"),
            supports_exchange_execution_model=False,
            supports_dry_run=False,
            requires_exchange_metadata=False,
            notes=(
                "Conservative project-owned semantics for fast fail and custom evidence.",
                "It is the engine used by the current recorded research runs.",
            ),
        )

    def run(self, job: Job) -> Mapping[str, Any]:
        return self.handler(job)


class FreqtradeBacktestEngineAdapter:
    """Boundary for an optional, customer-installed external Freqtrade executor.

    Core product code does not import or bundle Freqtrade. The adapter deliberately has
    no subprocess implementation. Tests or a future reviewed Worker connector may inject
    a deterministic executor that accepts a validated Job through a standard protocol.
    """

    def __init__(
        self, executor: Callable[[Job], Mapping[str, Any]] | None = None
    ) -> None:
        self.executor = executor

    def capabilities(self) -> BacktestEngineCapabilities:
        return BacktestEngineCapabilities(
            engine_id="freqtrade_2026_6",
            supported_stages=("engine_reconciliation", "full_validation", "future_dry_run"),
            supports_exchange_execution_model=True,
            supports_dry_run=True,
            requires_exchange_metadata=True,
            notes=(
                "Optional external engine; the Native Engine remains the default core.",
                "Customer installs Freqtrade separately; it is not bundled with the commercial package.",
                "No live trade capability is exposed through this adapter.",
                "Exchange metadata and explicit funding/mark/index treatment are required.",
                "Hyperopt is not enabled for conversational or unbudgeted tuning.",
            ),
        )

    def run(self, job: Job) -> Mapping[str, Any]:
        if job.payload.get("live") or job.payload.get("trade"):
            raise ValueError("Freqtrade adapter does not expose live trade")
        if self.executor is None:
            raise RuntimeError("Freqtrade executor is not configured")
        return self.executor(job)
