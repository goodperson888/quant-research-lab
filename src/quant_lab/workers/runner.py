from __future__ import annotations

from typing import Any, Callable, Mapping

from quant_lab.domain.models import ALLOWED_JOB_TYPES
from quant_lab.domain.repositories import ProductRepository


JobHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class LocalWorker:
    """Deterministic local worker shell with injected, allowlisted handlers only."""

    def __init__(
        self,
        repository: ProductRepository,
        *,
        handlers: Mapping[str, JobHandler] | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = dict(handlers or {})
        unknown = set(self.handlers) - ALLOWED_JOB_TYPES
        if unknown:
            raise ValueError(f"worker handler is not allowlisted: {sorted(unknown)}")

    def status(self) -> dict[str, Any]:
        return {
            "worker": "local_sqlite",
            "registered_handlers": sorted(self.handlers),
            "phase_0_note": (
                "No long-running research handler is enabled by default; queued jobs "
                "are not executed or reported as successful."
            ),
            "arbitrary_shell_enabled": False,
            "live_trading_enabled": False,
        }
