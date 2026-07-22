from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from quant_lab.domain.price_structure import (
    ConfirmedPivot,
    classify_structure,
    confirmed_pivots_at,
)


Side = Literal["long", "short"]
EntryMode = Literal["next_bar_open", "confirmation_candle_breakout"]


@dataclass(slots=True)
class _Position:
    side: Side
    entry_time: pd.Timestamp
    entry_raw_price: float
    entry_execution_price: float
    entry_equity: float
    quantity: float
    entry_fee: float
    stop_price: float
    entry_mode: EntryMode
    entry_trigger_price: float | None = None
    funding_pnl: float = 0.0
    observed_funding_events: int = 0
    imputed_funding_events: int = 0

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    side: Side
    trigger_price: float
    signal_time: pd.Timestamp


@dataclass(frozen=True, slots=True)
class BacktestSliceResult:
    label: str
    start_utc_inclusive: str
    end_utc_exclusive: str
    metrics: Mapping[str, Any]
    funding: Mapping[str, Any]
    execution: Mapping[str, Any]
    trades: pd.DataFrame
    equity: pd.DataFrame


def _utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _safe_float(value: float | np.floating[Any]) -> float:
    return float(value)


class PriceStructureBaselineBacktester:
    """Deterministic event-driven price-structure engine.

    The default ``next_bar_open`` mode is the frozen baseline v0 behavior. The only
    supported candidate mode replaces entry timing with a stop-entry at the
    confirmation candle high/low; structure exits remain next-bar-open. Signals use
    only pivots confirmed at a completed candle. The engine does not depend on
    exchange APIs and records precision/liquidation limitations rather than
    fabricating exchange metadata.
    """

    def __init__(
        self,
        *,
        fee_per_side: float,
        slippage_bps_per_side: float,
        funding_missing_policy: str = "adverse_p99_abs_observed",
        entry_mode: EntryMode = "next_bar_open",
    ) -> None:
        if fee_per_side < 0:
            raise ValueError("fee_per_side must be non-negative")
        if slippage_bps_per_side < 0:
            raise ValueError("slippage_bps_per_side must be non-negative")
        if funding_missing_policy != "adverse_p99_abs_observed":
            raise ValueError("unsupported funding missing policy")
        if entry_mode not in {"next_bar_open", "confirmation_candle_breakout"}:
            raise ValueError("unsupported entry mode")
        self.fee_per_side = fee_per_side
        self.slippage = slippage_bps_per_side / 10_000.0
        self.funding_missing_policy = funding_missing_policy
        self.entry_mode = entry_mode

    def run(
        self,
        *,
        label: str,
        ohlcv: pd.DataFrame,
        funding: pd.DataFrame,
        mark: pd.DataFrame,
        start_utc_inclusive: str,
        end_utc_exclusive: str,
    ) -> BacktestSliceResult:
        start = _utc_timestamp(start_utc_inclusive)
        end = _utc_timestamp(end_utc_exclusive)
        if start >= end:
            raise ValueError("backtest start must be before end")

        bars = self._prepare_bars(ohlcv)
        if bars.empty or start < bars.index.min() or end > bars.index.max() + pd.Timedelta(minutes=15):
            raise ValueError("backtest slice is outside the selected OHLCV range")
        evaluation = bars.loc[(bars.index >= start) & (bars.index < end)]
        if evaluation.empty:
            raise ValueError("backtest slice has no OHLCV rows")
        last_evaluation_index = evaluation.index[-1]

        funding_rates = self._prepare_funding(funding)
        observed_abs = funding_rates.abs().to_numpy(dtype=float)
        if observed_abs.size == 0:
            raise ValueError("funding data is required and cannot be silently zero")
        adverse_rate = float(np.quantile(observed_abs, 0.99))
        if adverse_rate <= 0:
            raise ValueError("funding proxy requires non-zero observed funding data")

        mark_prices = self._prepare_mark(mark)
        mark_open = mark_prices["open"].to_dict()
        mark_close = mark_prices["close"].to_dict()

        expected_funding = pd.date_range(start, end, freq="8h", inclusive="left")
        observed_expected = sum(timestamp in funding_rates.index for timestamp in expected_funding)

        confirmed_highs: list[ConfirmedPivot] = []
        confirmed_lows: list[ConfirmedPivot] = []
        previous_structure = "neutral"
        pending_signal: Side | None = None
        pending_entry: _PendingEntry | None = None
        pending_structure_exit: Side | None = None
        position: _Position | None = None
        realized_equity = 1.0
        trades: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        mark_fallback_bars = 0
        funding_mark_fallbacks = 0
        fees_paid = 0.0
        total_funding_pnl = 0.0

        highs = bars["high"].to_numpy(dtype=float)
        lows = bars["low"].to_numpy(dtype=float)
        timestamps = bars.index

        def execution_price(raw_price: float, side: Side, *, entering: bool) -> float:
            adverse_direction = 1 if (side == "long") == entering else -1
            return raw_price * (1 + adverse_direction * self.slippage)

        def current_stop(side: Side) -> float:
            pivots = confirmed_lows if side == "long" else confirmed_highs
            if not pivots:
                raise RuntimeError("structure signal has no confirmed protective pivot")
            return pivots[-1].price

        def open_position(
            side: Side,
            raw_price: float,
            timestamp: pd.Timestamp,
            *,
            trigger_price: float | None = None,
        ) -> _Position:
            nonlocal fees_paid
            adjusted = execution_price(raw_price, side, entering=True)
            entry_equity = realized_equity
            quantity = entry_equity / adjusted
            entry_fee = entry_equity * self.fee_per_side
            fees_paid += entry_fee
            return _Position(
                side=side,
                entry_time=timestamp,
                entry_raw_price=raw_price,
                entry_execution_price=adjusted,
                entry_equity=entry_equity,
                quantity=quantity,
                entry_fee=entry_fee,
                stop_price=current_stop(side),
                entry_mode=self.entry_mode,
                entry_trigger_price=trigger_price,
            )

        def close_position(
            raw_price: float,
            timestamp: pd.Timestamp,
            reason: str,
        ) -> None:
            nonlocal position, realized_equity, fees_paid, total_funding_pnl
            if position is None:
                return
            adjusted = execution_price(raw_price, position.side, entering=False)
            price_pnl = (
                position.direction
                * position.quantity
                * (adjusted - position.entry_execution_price)
            )
            exit_fee = abs(position.quantity * adjusted) * self.fee_per_side
            fees_paid += exit_fee
            ending_equity = (
                position.entry_equity
                - position.entry_fee
                + price_pnl
                + position.funding_pnl
                - exit_fee
            )
            total_funding_pnl += position.funding_pnl
            net_return = ending_equity / position.entry_equity - 1.0
            gross_return = price_pnl / position.entry_equity
            trades.append(
                {
                    "trade_id": len(trades) + 1,
                    "side": position.side,
                    "entry_time": position.entry_time,
                    "exit_time": timestamp,
                    "entry_raw_price": position.entry_raw_price,
                    "entry_execution_price": position.entry_execution_price,
                    "entry_mode": position.entry_mode,
                    "entry_trigger_price": position.entry_trigger_price,
                    "exit_raw_price": raw_price,
                    "exit_execution_price": adjusted,
                    "stop_price_at_exit": position.stop_price,
                    "exit_reason": reason,
                    "gross_return": gross_return,
                    "fees": position.entry_fee + exit_fee,
                    "funding_pnl": position.funding_pnl,
                    "net_return": net_return,
                    "entry_equity": position.entry_equity,
                    "exit_equity": ending_equity,
                    "observed_funding_events": position.observed_funding_events,
                    "imputed_funding_events": position.imputed_funding_events,
                }
            )
            realized_equity = ending_equity
            position = None

        for index, (timestamp, row) in enumerate(bars.iterrows()):
            if timestamp >= end:
                break

            signal_to_execute = pending_signal
            structure_exit_to_execute = pending_structure_exit
            if self.entry_mode == "next_bar_open":
                pending_signal = None
            else:
                pending_structure_exit = None
            trading_enabled = timestamp >= start

            if trading_enabled and position is not None and timestamp in expected_funding:
                if timestamp in funding_rates.index:
                    effective_rate = float(funding_rates.loc[timestamp])
                    position.observed_funding_events += 1
                else:
                    effective_rate = position.direction * adverse_rate
                    position.imputed_funding_events += 1
                if timestamp in mark_open:
                    funding_price = float(mark_open[timestamp])
                else:
                    funding_price = float(row["open"])
                    funding_mark_fallbacks += 1
                position.funding_pnl += (
                    -position.direction
                    * effective_rate
                    * abs(position.quantity)
                    * funding_price
                )

            if (
                trading_enabled
                and self.entry_mode == "next_bar_open"
                and signal_to_execute is not None
            ):
                if position is not None and position.side != signal_to_execute:
                    close_position(float(row["open"]), timestamp, "structure_reversal")
                if position is None:
                    position = open_position(
                        signal_to_execute, float(row["open"]), timestamp
                    )

            if trading_enabled and self.entry_mode == "confirmation_candle_breakout":
                if (
                    structure_exit_to_execute is not None
                    and position is not None
                    and position.side != structure_exit_to_execute
                ):
                    close_position(float(row["open"]), timestamp, "structure_reversal")
                if position is None and pending_entry is not None:
                    triggered = (
                        pending_entry.side == "long"
                        and float(row["high"]) >= pending_entry.trigger_price
                    ) or (
                        pending_entry.side == "short"
                        and float(row["low"]) <= pending_entry.trigger_price
                    )
                    if triggered:
                        if pending_entry.side == "long":
                            raw_entry = max(
                                float(row["open"]), pending_entry.trigger_price
                            )
                        else:
                            raw_entry = min(
                                float(row["open"]), pending_entry.trigger_price
                            )
                        position = open_position(
                            pending_entry.side,
                            raw_entry,
                            timestamp,
                            trigger_price=pending_entry.trigger_price,
                        )
                        pending_entry = None

            if trading_enabled and position is not None:
                if position.side == "long" and float(row["low"]) <= position.stop_price:
                    stop_fill = min(float(row["open"]), position.stop_price)
                    close_position(stop_fill, timestamp, "protective_stop")
                elif position.side == "short" and float(row["high"]) >= position.stop_price:
                    stop_fill = max(float(row["open"]), position.stop_price)
                    close_position(stop_fill, timestamp, "protective_stop")

            for pivot in confirmed_pivots_at(highs, lows, index):
                if pivot.kind == "high":
                    confirmed_highs.append(pivot)
                else:
                    confirmed_lows.append(pivot)

            current_structure = classify_structure(confirmed_highs, confirmed_lows)
            if current_structure != previous_structure:
                if self.entry_mode == "next_bar_open":
                    if current_structure == "bull":
                        pending_signal = "long"
                    elif current_structure == "bear":
                        pending_signal = "short"
                else:
                    if current_structure in {"bull", "bear"}:
                        candidate_side: Side = (
                            "long" if current_structure == "bull" else "short"
                        )
                        trigger_price = (
                            float(row["high"])
                            if candidate_side == "long"
                            else float(row["low"])
                        )
                        if position is not None and position.side == candidate_side:
                            pending_entry = None
                        else:
                            pending_entry = _PendingEntry(
                                side=candidate_side,
                                trigger_price=trigger_price,
                                signal_time=timestamp,
                            )
                        if position is not None and position.side != candidate_side:
                            pending_structure_exit = candidate_side
                    else:
                        pending_entry = None
            previous_structure = current_structure

            if position is not None:
                position.stop_price = current_stop(position.side)

            if trading_enabled and timestamp == last_evaluation_index and position is not None:
                close_position(float(row["close"]), timestamp, "end_of_data")

            if trading_enabled:
                if position is None:
                    marked_equity = realized_equity
                else:
                    if timestamp in mark_close:
                        price = float(mark_close[timestamp])
                    else:
                        price = float(row["close"])
                        mark_fallback_bars += 1
                    unrealized = (
                        position.direction
                        * position.quantity
                        * (price - position.entry_execution_price)
                    )
                    marked_equity = (
                        position.entry_equity
                        - position.entry_fee
                        + unrealized
                        + position.funding_pnl
                    )
                equity_rows.append(
                    {
                        "timestamp": timestamp,
                        "equity": marked_equity,
                        "position": position.side if position is not None else "flat",
                    }
                )

        trades_frame = pd.DataFrame(trades)
        equity_frame = pd.DataFrame(equity_rows)
        metrics = self._metrics(trades_frame, equity_frame, realized_equity)
        funding_summary = {
            "required": True,
            "missing_policy": self.funding_missing_policy,
            "adverse_proxy_rate": adverse_rate,
            "expected_events": len(expected_funding),
            "observed_events_available": observed_expected,
            "missing_events_in_slice": len(expected_funding) - observed_expected,
            "observed_events_applied_while_position_open": int(
                trades_frame.get("observed_funding_events", pd.Series(dtype=int)).sum()
            ),
            "imputed_events_applied_while_position_open": int(
                trades_frame.get("imputed_funding_events", pd.Series(dtype=int)).sum()
            ),
            "funding_pnl": total_funding_pnl,
            "funding_mark_price_fallbacks": funding_mark_fallbacks,
            "zero_funding_fallback_used": False,
        }
        execution_summary = {
            "entry_mode": self.entry_mode,
            "fee_per_side": self.fee_per_side,
            "slippage_bps_per_side": self.slippage * 10_000.0,
            "fees_paid": fees_paid,
            "leverage": 1.0,
            "same_bar_policy": "protective_stop_before_new_close_signal",
            "gap_stop_policy": "next_available_open_when_open_crosses_stop",
            "mark_to_market_fallback_bars": mark_fallback_bars,
        }
        return BacktestSliceResult(
            label=label,
            start_utc_inclusive=start.isoformat(),
            end_utc_exclusive=end.isoformat(),
            metrics=metrics,
            funding=funding_summary,
            execution=execution_summary,
            trades=trades_frame,
            equity=equity_frame,
        )

    @staticmethod
    def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"OHLCV data missing columns: {sorted(missing)}")
        result = frame.loc[:, sorted(required)].copy()
        result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
        result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        result = result.set_index("timestamp")
        if not result.index.is_monotonic_increasing:
            raise ValueError("OHLCV timestamps must be monotonic")
        return result

    @staticmethod
    def _prepare_funding(frame: pd.DataFrame) -> pd.Series:
        required = {"timestamp", "last_funding_rate"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"funding data missing columns: {sorted(missing)}")
        result = frame.loc[:, ["timestamp", "last_funding_rate"]].copy()
        result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
        result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
        return result.set_index("timestamp")["last_funding_rate"].astype(float)

    @staticmethod
    def _prepare_mark(frame: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp", "open", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"mark data missing columns: {sorted(missing)}")
        result = frame.loc[:, ["timestamp", "open", "close"]].copy()
        result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
        return (
            result.sort_values("timestamp")
            .drop_duplicates("timestamp", keep="last")
            .set_index("timestamp")
        )

    @staticmethod
    def _metrics(
        trades: pd.DataFrame, equity: pd.DataFrame, ending_equity: float
    ) -> dict[str, Any]:
        if equity.empty:
            raise ValueError("equity series is empty")
        equity_values = equity["equity"].astype(float)
        running_max = equity_values.cummax()
        drawdown = equity_values / running_max - 1.0
        returns = equity_values.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = (
            float(returns.mean() / volatility * sqrt(96 * 365))
            if volatility > 0
            else None
        )
        if trades.empty:
            wins = 0
            gross_profit = 0.0
            gross_loss = 0.0
            net_returns = pd.Series(dtype=float)
        else:
            net_returns = trades["net_return"].astype(float)
            wins = int((net_returns > 0).sum())
            gross_profit = float(net_returns[net_returns > 0].sum())
            gross_loss = float(-net_returns[net_returns < 0].sum())
        same_bar_stop_count = (
            int(
                (
                    (trades["entry_time"] == trades["exit_time"])
                    & (trades["exit_reason"] == "protective_stop")
                ).sum()
            )
            if not trades.empty
            else 0
        )
        return {
            "starting_equity": 1.0,
            "ending_equity": ending_equity,
            "total_return": ending_equity - 1.0,
            "max_drawdown": float(drawdown.min()),
            "sharpe_15m_annualized": sharpe,
            "trade_count": int(len(trades)),
            "long_trades": int((trades.get("side") == "long").sum()) if not trades.empty else 0,
            "short_trades": int((trades.get("side") == "short").sum()) if not trades.empty else 0,
            "wins": wins,
            "win_rate": wins / len(trades) if len(trades) else None,
            "same_bar_stop_count": same_bar_stop_count,
            "same_bar_stop_rate": (
                same_bar_stop_count / len(trades) if len(trades) else None
            ),
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
            "average_trade_return": float(net_returns.mean()) if len(net_returns) else None,
            "worst_trade_return": float(net_returns.min()) if len(net_returns) else None,
            "best_trade_return": float(net_returns.max()) if len(net_returns) else None,
        }
