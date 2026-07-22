from __future__ import annotations

from quant_lab.application.ports import BacktestEnginePort


class BacktestEngineRegistry:
    """Small in-process registry for reviewed engine adapters."""

    def __init__(self, engines: tuple[BacktestEnginePort, ...] = ()) -> None:
        self._engines: dict[str, BacktestEnginePort] = {}
        for engine in engines:
            self.register(engine)

    def register(self, engine: BacktestEnginePort) -> None:
        engine_id = engine.capabilities().engine_id
        if engine_id in self._engines:
            raise ValueError(f"backtest engine already registered: {engine_id}")
        self._engines[engine_id] = engine

    def get(self, engine_id: str) -> BacktestEnginePort:
        try:
            return self._engines[engine_id]
        except KeyError as exc:
            raise ValueError(f"backtest engine is not registered: {engine_id}") from exc

    def capabilities(self):
        return tuple(engine.capabilities() for engine in self._engines.values())
