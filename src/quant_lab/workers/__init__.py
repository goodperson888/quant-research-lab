"""Local worker boundary.

Stage 0 intentionally registers no backtest or optimization handler. Jobs remain
queued until a deterministic, reviewed handler is added in a later phase.
"""

from .runner import LocalWorker

__all__ = ["LocalWorker"]
