from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from quant_lab.domain.price_structure import (
    ConfirmedPivot,
    classify_structure,
    confirmed_pivots_at,
)
from quant_lab.domain.trend_follow_selective_reentry import (
    SelectiveReentryCorrectnessAdapter,
    Side,
    TrendLegState,
    advance_trend_leg,
    bounded_position_size,
    consume_reentry,
)


@dataclass(frozen=True, slots=True)
class SelectiveReentrySmokeResult:
    start_utc_inclusive: str
    end_utc_exclusive: str
    metrics: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    funding: Mapping[str, Any]
    execution: Mapping[str, Any]
    signals: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame


@dataclass(slots=True)
class _PendingSetup:
    side: Side
    signal_time: pd.Timestamp
    trigger_price: float
    stop_price: float
    trend_leg_number: int
    bars_remaining: int = 3


@dataclass(slots=True)
class _Position:
    side: Side
    entry_time: pd.Timestamp
    entry_raw_price: float
    entry_execution_price: float
    entry_equity: float
    initial_quantity: float
    remaining_quantity: float
    initial_stop_price: float
    active_stop_price: float
    risk_per_unit: float
    target_1r: float
    target_1_8r: float
    trend_leg_number: int
    is_reentry: bool
    entry_fee: float
    fees: float
    slippage_cost: float
    price_pnl: float = 0.0
    funding_pnl: float = 0.0
    partial_taken: bool = False
    mfe_r: float = 0.0
    exit_fills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _prepare_bars(frame: pd.DataFrame, *, timeframe: str) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{timeframe} OHLCV missing columns: {sorted(missing)}")
    result = frame.loc[:, sorted(required)].copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    for name in ("open", "high", "low", "close", "volume"):
        result[name] = pd.to_numeric(result[name], errors="raise")
    if (
        (result["high"] < result[["open", "close", "low"]].max(axis=1))
        | (result["low"] > result[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ValueError(f"{timeframe} OHLC relationships are invalid")
    duration = pd.Timedelta(timeframe)
    result["close_event_time"] = result["timestamp"] + duration
    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        (
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    result["atr14"] = true_range.rolling(14, min_periods=14).mean()
    return result.reset_index(drop=True)


def _prepare_funding(frame: pd.DataFrame) -> pd.Series:
    required = {"timestamp", "last_funding_rate"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"funding data missing columns: {sorted(missing)}")
    values = frame.loc[:, ["timestamp", "last_funding_rate"]].copy()
    values["timestamp"] = pd.to_datetime(values["timestamp"], utc=True)
    values["last_funding_rate"] = pd.to_numeric(
        values["last_funding_rate"], errors="raise"
    )
    values = values.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return values.set_index("timestamp")["last_funding_rate"]


def _prepare_mark(frame: pd.DataFrame) -> pd.Series:
    required = {"timestamp", "open"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"mark data missing columns: {sorted(missing)}")
    values = frame.loc[:, ["timestamp", "open"]].copy()
    values["timestamp"] = pd.to_datetime(values["timestamp"], utc=True)
    values["open"] = pd.to_numeric(values["open"], errors="raise")
    values = values.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return values.set_index("timestamp")["open"]


def resolve_funding_rate(
    *,
    timestamp: pd.Timestamp,
    funding_rates: pd.Series,
    position_side: Side,
    adverse_proxy_rate: float,
) -> tuple[float, bool]:
    """Return an observed rate or a directionally adverse non-zero proxy."""

    if timestamp in funding_rates.index:
        return float(funding_rates.loc[timestamp]), False
    if adverse_proxy_rate <= 0:
        raise ValueError("missing funding requires a positive adverse proxy rate")
    return (
        adverse_proxy_rate if position_side == "long" else -adverse_proxy_rate,
        True,
    )


class SelectiveReentrySmokeBacktester:
    """Deterministic 5m event simulation for one approved Candidate smoke slice."""

    def __init__(
        self,
        *,
        candidate_version_id: str,
        baseline_version_id: str,
        fee_per_side: float = 0.0005,
        slippage_bps_per_side: float = 2.0,
        initial_equity: float = 10_000.0,
        tick_size: float = 0.01,
        quantity_step: float = 0.001,
        minimum_notional: float = 20.0,
        allowed_sides: tuple[Side, ...] = ("long", "short"),
        max_reentries_per_trend_leg: int = 1,
        max_holding_minutes: int = 90,
        time_stop_minutes: int = 30,
        time_stop_mfe_r: float = 0.5,
    ) -> None:
        if fee_per_side < 0 or slippage_bps_per_side < 0:
            raise ValueError("costs must be non-negative")
        if initial_equity <= 0 or tick_size <= 0 or quantity_step <= 0:
            raise ValueError("invalid equity or exchange precision")
        if not allowed_sides or not set(allowed_sides).issubset({"long", "short"}):
            raise ValueError("allowed_sides must contain long and/or short")
        if max_reentries_per_trend_leg not in {0, 1, 2}:
            raise ValueError("diagnostic reentry limit must be 0, 1, or 2")
        if not 15 <= time_stop_minutes <= max_holding_minutes <= 240:
            raise ValueError("diagnostic holding thresholds are out of bounds")
        if not 0 <= time_stop_mfe_r <= 2:
            raise ValueError("diagnostic MFE threshold is out of bounds")
        self.adapter = SelectiveReentryCorrectnessAdapter(
            candidate_version_id=candidate_version_id,
            baseline_version_id=baseline_version_id,
        )
        self.fee_per_side = fee_per_side
        self.slippage = slippage_bps_per_side / 10_000.0
        self.initial_equity = initial_equity
        self.tick_size = tick_size
        self.quantity_step = quantity_step
        self.minimum_notional = minimum_notional
        self.allowed_sides = frozenset(allowed_sides)
        self.max_reentries_per_trend_leg = max_reentries_per_trend_leg
        self.max_holding_minutes = max_holding_minutes
        self.time_stop_minutes = time_stop_minutes
        self.time_stop_mfe_r = time_stop_mfe_r

    def run(
        self,
        *,
        ohlcv_5m: pd.DataFrame,
        ohlcv_15m: pd.DataFrame,
        ohlcv_1h: pd.DataFrame,
        funding: pd.DataFrame,
        mark_15m: pd.DataFrame,
        warmup_start_utc_inclusive: str,
        start_utc_inclusive: str,
        end_utc_exclusive: str,
    ) -> SelectiveReentrySmokeResult:
        warmup_start = _utc(warmup_start_utc_inclusive)
        start = _utc(start_utc_inclusive)
        end = _utc(end_utc_exclusive)
        if not warmup_start < start < end:
            raise ValueError("smoke requires warmup < start < end")

        bars_5m = _prepare_bars(ohlcv_5m, timeframe="5min")
        bars_15m = _prepare_bars(ohlcv_15m, timeframe="15min")
        bars_1h = _prepare_bars(ohlcv_1h, timeframe="1h")
        for name, frame in (
            ("5m", bars_5m),
            ("15m", bars_15m),
            ("1h", bars_1h),
        ):
            selected = frame.loc[
                (frame["timestamp"] >= warmup_start) & (frame["timestamp"] < end)
            ]
            if selected.empty:
                raise ValueError(f"{name} data does not cover the smoke window")
        bars_5m = bars_5m.loc[
            (bars_5m["timestamp"] >= warmup_start) & (bars_5m["timestamp"] < end)
        ].reset_index(drop=True)
        bars_15m = bars_15m.loc[
            (bars_15m["timestamp"] >= warmup_start) & (bars_15m["timestamp"] < end)
        ].reset_index(drop=True)
        bars_1h = bars_1h.loc[
            (bars_1h["timestamp"] >= warmup_start) & (bars_1h["timestamp"] < end)
        ].reset_index(drop=True)

        funding_rates = _prepare_funding(funding)
        mark_open = _prepare_mark(mark_15m)
        expected_funding_index = pd.date_range(
            start, end, freq="8h", inclusive="left"
        )
        expected_funding_events = frozenset(expected_funding_index)
        observed_abs = funding_rates.loc[
            (funding_rates.index >= warmup_start) & (funding_rates.index < end)
        ].abs()
        if observed_abs.empty or float(observed_abs.quantile(0.99)) <= 0:
            raise ValueError("funding is required and cannot be silently zero")
        adverse_funding_rate = float(observed_abs.quantile(0.99))

        one_highs = bars_1h["high"].to_numpy(dtype=float)
        one_lows = bars_1h["low"].to_numpy(dtype=float)
        fifteen_opens = bars_15m["open"].to_numpy(dtype=float)
        fifteen_highs = bars_15m["high"].to_numpy(dtype=float)
        fifteen_lows = bars_15m["low"].to_numpy(dtype=float)
        fifteen_closes = bars_15m["close"].to_numpy(dtype=float)
        five_highs = bars_5m["high"].to_numpy(dtype=float)
        five_lows = bars_5m["low"].to_numpy(dtype=float)

        one_high_pivots: list[ConfirmedPivot] = []
        one_low_pivots: list[ConfirmedPivot] = []
        five_high_pivots: list[ConfirmedPivot] = []
        five_low_pivots: list[ConfirmedPivot] = []
        trend_leg = TrendLegState()
        one_cursor = 0
        fifteen_cursor = 0
        position: _Position | None = None
        pending: _PendingSetup | None = None
        equity = self.initial_equity
        day_open_realized_equity = equity
        current_day = start.date()
        entries_today = 0
        consecutive_losses = 0
        stopped_leg: int | None = None
        stop_exit_bar_index: int | None = None
        failed_entries_by_leg: dict[int, int] = {}
        signals: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        observed_funding_events = 0
        imputed_funding_events = 0
        mark_fallback_events = 0
        skipped: dict[str, int] = {}

        def increment_skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        def execution_price(raw_price: float, side: Side, *, entering: bool) -> float:
            adverse = 1 if (side == "long") == entering else -1
            return raw_price * (1 + adverse * self.slippage)

        def close_quantity(
            *,
            raw_price: float,
            timestamp: pd.Timestamp,
            quantity: float,
            reason: str,
        ) -> None:
            nonlocal equity, position, consecutive_losses, stopped_leg
            nonlocal stop_exit_bar_index
            if position is None or quantity <= 0:
                return
            quantity = min(quantity, position.remaining_quantity)
            adjusted = execution_price(raw_price, position.side, entering=False)
            price_pnl = (
                position.direction
                * quantity
                * (adjusted - position.entry_execution_price)
            )
            exit_fee = abs(quantity * adjusted) * self.fee_per_side
            exit_slippage = abs(quantity * (adjusted - raw_price))
            equity += price_pnl - exit_fee
            position.price_pnl += price_pnl
            position.fees += exit_fee
            position.slippage_cost += exit_slippage
            position.remaining_quantity -= quantity
            position.exit_fills.append(
                {
                    "timestamp": timestamp,
                    "reason": reason,
                    "quantity": quantity,
                    "raw_price": raw_price,
                    "execution_price": adjusted,
                    "fee": exit_fee,
                }
            )
            if position.remaining_quantity > self.quantity_step / 2:
                return

            net_pnl = position.price_pnl + position.funding_pnl - position.fees
            total_exit_quantity = sum(fill["quantity"] for fill in position.exit_fills)
            weighted_raw = sum(
                fill["quantity"] * fill["raw_price"] for fill in position.exit_fills
            ) / total_exit_quantity
            weighted_execution = sum(
                fill["quantity"] * fill["execution_price"]
                for fill in position.exit_fills
            ) / total_exit_quantity
            trade = {
                "trade_id": len(trades) + 1,
                "side": position.side,
                "entry_time": position.entry_time,
                "exit_time": timestamp,
                "entry_raw_price": position.entry_raw_price,
                "entry_execution_price": position.entry_execution_price,
                "exit_raw_price": weighted_raw,
                "exit_execution_price": weighted_execution,
                "exit_reason": reason,
                "initial_stop_price": position.initial_stop_price,
                "initial_quantity": position.initial_quantity,
                "notional": position.initial_quantity
                * position.entry_execution_price,
                "trend_leg_number": position.trend_leg_number,
                "is_reentry": position.is_reentry,
                "partial_taken": position.partial_taken,
                "price_pnl": position.price_pnl,
                "funding_pnl": position.funding_pnl,
                "fees": position.fees,
                "slippage_cost": position.slippage_cost,
                "net_pnl": net_pnl,
                "net_return_on_entry_equity": net_pnl / position.entry_equity,
                "holding_minutes": (
                    timestamp - position.entry_time
                ).total_seconds()
                / 60.0,
                "mfe_r": position.mfe_r,
            }
            trades.append(trade)
            if net_pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            if reason == "protective_stop":
                stopped_leg = position.trend_leg_number
                stop_exit_bar_index = current_five_index
                failed_entries_by_leg[stopped_leg] = (
                    failed_entries_by_leg.get(stopped_leg, 0) + 1
                )
            position = None

        def close_all(raw_price: float, timestamp: pd.Timestamp, reason: str) -> None:
            if position is not None:
                close_quantity(
                    raw_price=raw_price,
                    timestamp=timestamp,
                    quantity=position.remaining_quantity,
                    reason=reason,
                )

        def latest_invalidation(side: Side) -> float | None:
            pivots = one_low_pivots if side == "long" else one_high_pivots
            return pivots[-1].price if pivots else None

        def evaluate_confirmation(
            confirmation_index: int,
            *,
            known_at: pd.Timestamp,
            five_atr: float | None,
        ) -> tuple[_PendingSetup | None, Side | None]:
            if trend_leg.side is None:
                return None, None
            side = trend_leg.side
            if side not in self.allowed_sides:
                increment_skip("side_disabled_by_diagnostic_ablation")
                return None, side
            window = self.adapter.pullback_window(
                opens=fifteen_opens,
                highs=fifteen_highs,
                lows=fifteen_lows,
                closes=fifteen_closes,
                confirmation_index=confirmation_index,
                side=side,
            )
            if window is None:
                return None, None
            row = bars_15m.iloc[confirmation_index]
            previous = bars_15m.iloc[confirmation_index - 1]
            candle_range = float(row["high"] - row["low"])
            atr_15m = float(row["atr14"])
            if not np.isfinite(atr_15m) or not five_atr or not np.isfinite(five_atr):
                increment_skip("atr_warmup")
                return None, None
            if candle_range <= 0 or candle_range > 1.8 * atr_15m:
                increment_skip("confirmation_range")
                return None, None
            location = float((row["close"] - row["low"]) / candle_range)
            if side == "long":
                confirmed = float(row["close"]) > float(previous["high"]) and location >= 0.60
            else:
                confirmed = float(row["close"]) < float(previous["low"]) and location <= 0.40
            if not confirmed:
                return None, None

            invalidation = latest_invalidation(side)
            if invalidation is None:
                increment_skip("missing_1h_invalidation")
                return None, side
            pullback_closes = [fifteen_closes[index] for index in window.bar_indexes]
            if side == "long" and any(value <= invalidation for value in pullback_closes):
                increment_skip("pullback_beyond_invalidation")
                return None, side
            if side == "short" and any(value >= invalidation for value in pullback_closes):
                increment_skip("pullback_beyond_invalidation")
                return None, side

            buffer = max(self.tick_size, 0.1 * five_atr)
            if side == "long":
                trigger = float(row["high"]) + self.tick_size
                stop = window.swing_price - buffer
            else:
                trigger = float(row["low"]) - self.tick_size
                stop = window.swing_price + buffer
            stop_distance = abs(trigger - stop) / trigger
            if not 0.0025 <= stop_distance <= 0.0060:
                increment_skip("stop_distance_ineligible")
                return None, side
            return (
                _PendingSetup(
                    side=side,
                    signal_time=known_at,
                    trigger_price=trigger,
                    stop_price=stop,
                    trend_leg_number=trend_leg.leg_number,
                ),
                side,
            )

        def can_enter(current_index: int, setup: _PendingSetup) -> tuple[bool, bool]:
            nonlocal trend_leg
            if setup.trend_leg_number != trend_leg.leg_number or setup.side != trend_leg.side:
                increment_skip("trend_invalidated_before_fill")
                return False, False
            if entries_today >= 3:
                increment_skip("daily_entry_limit")
                return False, False
            if consecutive_losses >= 2:
                increment_skip("consecutive_loss_limit")
                return False, False
            if equity <= day_open_realized_equity * (1 - 0.0075):
                increment_skip("daily_loss_limit")
                return False, False
            if failed_entries_by_leg.get(setup.trend_leg_number, 0) >= 2:
                increment_skip("trend_leg_two_failures")
                return False, False
            is_reentry = stopped_leg == setup.trend_leg_number
            if is_reentry:
                if stop_exit_bar_index is None or current_index - stop_exit_bar_index <= 3:
                    increment_skip("reentry_cooldown")
                    return False, True
                try:
                    trend_leg = consume_reentry(
                        trend_leg,
                        max_reentries_per_leg=self.max_reentries_per_trend_leg,
                    )
                except ValueError:
                    increment_skip("trend_leg_reentry_limit")
                    return False, True
            return True, is_reentry

        def open_position(
            *,
            setup: _PendingSetup,
            row: pd.Series,
            timestamp: pd.Timestamp,
            is_reentry: bool,
        ) -> bool:
            nonlocal equity, position, entries_today
            if setup.side == "long":
                touched = float(row["high"]) >= setup.trigger_price
                raw_price = max(float(row["open"]), setup.trigger_price)
            else:
                touched = float(row["low"]) <= setup.trigger_price
                raw_price = min(float(row["open"]), setup.trigger_price)
            if not touched:
                return False
            stop_distance = abs(raw_price - setup.stop_price) / raw_price
            sizing = bounded_position_size(
                equity=equity,
                stop_distance_fraction=stop_distance,
            )
            entry_execution = execution_price(raw_price, setup.side, entering=True)
            quantity = floor(
                (sizing.notional / entry_execution) / self.quantity_step
            ) * self.quantity_step
            notional = quantity * entry_execution
            if quantity <= 0 or notional < self.minimum_notional:
                increment_skip("minimum_notional_or_precision")
                return False
            entry_fee = notional * self.fee_per_side
            entry_slippage = abs(quantity * (entry_execution - raw_price))
            equity -= entry_fee
            risk_per_unit = abs(raw_price - setup.stop_price)
            target_1r = raw_price + (risk_per_unit if setup.side == "long" else -risk_per_unit)
            target_1_8r = raw_price + (
                1.8 * risk_per_unit if setup.side == "long" else -1.8 * risk_per_unit
            )
            position = _Position(
                side=setup.side,
                entry_time=timestamp,
                entry_raw_price=raw_price,
                entry_execution_price=entry_execution,
                entry_equity=equity + entry_fee,
                initial_quantity=quantity,
                remaining_quantity=quantity,
                initial_stop_price=setup.stop_price,
                active_stop_price=setup.stop_price,
                risk_per_unit=risk_per_unit,
                target_1r=target_1r,
                target_1_8r=target_1_8r,
                trend_leg_number=setup.trend_leg_number,
                is_reentry=is_reentry,
                entry_fee=entry_fee,
                fees=entry_fee,
                slippage_cost=entry_slippage,
            )
            entries_today += 1
            signals.append(
                {
                    "signal_time": setup.signal_time,
                    "entry_time": timestamp,
                    "side": setup.side,
                    "trigger_price": setup.trigger_price,
                    "stop_price": setup.stop_price,
                    "trend_leg_number": setup.trend_leg_number,
                    "is_reentry": is_reentry,
                    "status": "filled",
                }
            )
            return True

        current_five_index = -1
        for current_five_index, row in bars_5m.iterrows():
            timestamp = pd.Timestamp(row["timestamp"])
            if timestamp >= end:
                break

            while (
                one_cursor < len(bars_1h)
                and pd.Timestamp(bars_1h.iloc[one_cursor]["close_event_time"]) <= timestamp
            ):
                for pivot in confirmed_pivots_at(
                    one_highs,
                    one_lows,
                    one_cursor,
                    left_bars=2,
                    right_bars=2,
                ):
                    (one_high_pivots if pivot.kind == "high" else one_low_pivots).append(
                        pivot
                    )
                trend_leg = advance_trend_leg(
                    trend_leg,
                    confirmed_structure=classify_structure(
                        one_high_pivots, one_low_pivots
                    ),
                )
                one_cursor += 1

            if current_five_index > 0:
                for pivot in confirmed_pivots_at(
                    five_highs,
                    five_lows,
                    current_five_index - 1,
                    left_bars=2,
                    right_bars=2,
                ):
                    (five_high_pivots if pivot.kind == "high" else five_low_pivots).append(
                        pivot
                    )

            opposite_confirmation_side: Side | None = None
            while (
                fifteen_cursor < len(bars_15m)
                and pd.Timestamp(bars_15m.iloc[fifteen_cursor]["close_event_time"])
                <= timestamp
            ):
                five_atr = (
                    float(bars_5m.iloc[current_five_index - 1]["atr14"])
                    if current_five_index > 0
                    else None
                )
                setup, confirmation_side = evaluate_confirmation(
                    fifteen_cursor,
                    known_at=pd.Timestamp(
                        bars_15m.iloc[fifteen_cursor]["close_event_time"]
                    ),
                    five_atr=five_atr,
                )
                if (
                    position is not None
                    and confirmation_side is not None
                    and confirmation_side != position.side
                ):
                    opposite_confirmation_side = confirmation_side
                if setup is not None and position is None:
                    pending = setup
                    signals.append(
                        {
                            "signal_time": setup.signal_time,
                            "entry_time": pd.NaT,
                            "side": setup.side,
                            "trigger_price": setup.trigger_price,
                            "stop_price": setup.stop_price,
                            "trend_leg_number": setup.trend_leg_number,
                            "is_reentry": stopped_leg == setup.trend_leg_number,
                            "status": "pending",
                        }
                    )
                fifteen_cursor += 1

            if timestamp >= start and timestamp.date() != current_day:
                current_day = timestamp.date()
                day_open_realized_equity = equity
                entries_today = 0
                consecutive_losses = 0

            if timestamp < start:
                continue

            if position is not None:
                if trend_leg.side != position.side:
                    close_all(float(row["open"]), timestamp, "1h_trend_invalidation")
                elif opposite_confirmation_side is not None:
                    close_all(float(row["open"]), timestamp, "opposite_15m_confirmation")
                elif (
                    len(five_high_pivots) >= 2
                    and len(five_low_pivots) >= 2
                    and (
                        classify_structure(five_high_pivots, five_low_pivots)
                        == ("bear" if position.side == "long" else "bull")
                    )
                ):
                    close_all(float(row["open"]), timestamp, "opposite_5m_structure")

            if position is not None and timestamp in expected_funding_events:
                rate, imputed = resolve_funding_rate(
                    timestamp=timestamp,
                    funding_rates=funding_rates,
                    position_side=position.side,
                    adverse_proxy_rate=adverse_funding_rate,
                )
                price = (
                    float(mark_open.loc[timestamp])
                    if timestamp in mark_open.index
                    else float(row["open"])
                )
                if timestamp not in mark_open.index:
                    mark_fallback_events += 1
                funding_pnl = (
                    -position.direction
                    * rate
                    * position.remaining_quantity
                    * price
                )
                position.funding_pnl += funding_pnl
                equity += funding_pnl
                if imputed:
                    imputed_funding_events += 1
                else:
                    observed_funding_events += 1

            if position is None and pending is not None:
                allowed, is_reentry = can_enter(current_five_index, pending)
                if not allowed and pending.trend_leg_number != trend_leg.leg_number:
                    pending = None
                elif allowed and open_position(
                    setup=pending,
                    row=row,
                    timestamp=timestamp,
                    is_reentry=is_reentry,
                ):
                    pending = None
                else:
                    pending.bars_remaining -= 1
                    if pending.bars_remaining <= 0:
                        signals.append(
                            {
                                "signal_time": pending.signal_time,
                                "entry_time": pd.NaT,
                                "side": pending.side,
                                "trigger_price": pending.trigger_price,
                                "stop_price": pending.stop_price,
                                "trend_leg_number": pending.trend_leg_number,
                                "is_reentry": is_reentry,
                                "status": "expired",
                            }
                        )
                        pending = None

            if position is not None:
                if position.side == "long":
                    favorable = float(row["high"]) - position.entry_raw_price
                    stop_touched = float(row["low"]) <= position.active_stop_price
                    stop_raw = min(float(row["open"]), position.active_stop_price)
                    target_1r_touched = float(row["high"]) >= position.target_1r
                    target_1_8r_touched = float(row["high"]) >= position.target_1_8r
                else:
                    favorable = position.entry_raw_price - float(row["low"])
                    stop_touched = float(row["high"]) >= position.active_stop_price
                    stop_raw = max(float(row["open"]), position.active_stop_price)
                    target_1r_touched = float(row["low"]) <= position.target_1r
                    target_1_8r_touched = float(row["low"]) <= position.target_1_8r
                position.mfe_r = max(
                    position.mfe_r, max(0.0, favorable / position.risk_per_unit)
                )

                if stop_touched:
                    close_all(stop_raw, timestamp, "protective_stop")
                elif not position.partial_taken and target_1r_touched:
                    partial_quantity = floor(
                        (position.initial_quantity * 0.5) / self.quantity_step
                    ) * self.quantity_step
                    if partial_quantity <= 0:
                        partial_quantity = position.remaining_quantity
                    close_quantity(
                        raw_price=position.target_1r,
                        timestamp=timestamp,
                        quantity=partial_quantity,
                        reason="partial_take_profit_1r",
                    )
                    if position is not None:
                        position.partial_taken = True
                        cost_fraction = 2 * (
                            self.fee_per_side + self.slippage
                        )
                        if position.side == "long":
                            position.active_stop_price = position.entry_raw_price * (
                                1 + cost_fraction
                            )
                            new_stop_touched = (
                                float(row["low"]) <= position.active_stop_price
                            )
                            new_stop_raw = min(
                                float(row["open"]), position.active_stop_price
                            )
                        else:
                            position.active_stop_price = position.entry_raw_price * (
                                1 - cost_fraction
                            )
                            new_stop_touched = (
                                float(row["high"]) >= position.active_stop_price
                            )
                            new_stop_raw = max(
                                float(row["open"]), position.active_stop_price
                            )
                        if new_stop_touched:
                            close_all(
                                new_stop_raw,
                                timestamp,
                                "post_partial_cost_stop",
                            )
                        elif target_1_8r_touched:
                            close_all(
                                position.target_1_8r,
                                timestamp,
                                "final_take_profit_1_8r",
                            )
                elif position.partial_taken and target_1_8r_touched:
                    close_all(
                        position.target_1_8r,
                        timestamp,
                        "final_take_profit_1_8r",
                    )

            if position is not None:
                bar_close_time = timestamp + pd.Timedelta(minutes=5)
                holding_minutes = (
                    bar_close_time - position.entry_time
                ).total_seconds() / 60.0
                if holding_minutes >= self.max_holding_minutes:
                    close_all(float(row["close"]), bar_close_time, "max_holding_time")
                elif (
                    holding_minutes >= self.time_stop_minutes
                    and position.mfe_r < self.time_stop_mfe_r
                ):
                    close_all(float(row["close"]), bar_close_time, "time_stop")

            marked_equity = equity
            if position is not None:
                marked_equity += (
                    position.direction
                    * position.remaining_quantity
                    * (float(row["close"]) - position.entry_execution_price)
                )
            equity_rows.append(
                {
                    "timestamp": timestamp + pd.Timedelta(minutes=5),
                    "realized_equity": equity,
                    "marked_equity": marked_equity,
                }
            )

        if position is not None:
            last_row = bars_5m.loc[bars_5m["timestamp"] < end].iloc[-1]
            close_all(
                float(last_row["close"]),
                end,
                "smoke_window_end",
            )
            equity_rows.append(
                {
                    "timestamp": end,
                    "realized_equity": equity,
                    "marked_equity": equity,
                }
            )

        trades_frame = pd.DataFrame(
            trades,
            columns=[
                "trade_id",
                "side",
                "entry_time",
                "exit_time",
                "entry_raw_price",
                "entry_execution_price",
                "exit_raw_price",
                "exit_execution_price",
                "exit_reason",
                "initial_stop_price",
                "initial_quantity",
                "notional",
                "trend_leg_number",
                "is_reentry",
                "partial_taken",
                "price_pnl",
                "funding_pnl",
                "fees",
                "slippage_cost",
                "net_pnl",
                "net_return_on_entry_equity",
                "holding_minutes",
                "mfe_r",
            ],
        )
        signals_frame = pd.DataFrame(
            signals,
            columns=[
                "signal_time",
                "entry_time",
                "side",
                "trigger_price",
                "stop_price",
                "trend_leg_number",
                "is_reentry",
                "status",
            ],
        )
        equity_frame = pd.DataFrame(
            equity_rows,
            columns=["timestamp", "realized_equity", "marked_equity"],
        )
        if equity_frame.empty:
            raise ValueError("smoke produced no equity observations")
        equity_values = equity_frame["marked_equity"].to_numpy(dtype=float)
        peaks = np.maximum.accumulate(equity_values)
        drawdowns = equity_values / peaks - 1.0
        net_pnls = (
            trades_frame["net_pnl"].to_numpy(dtype=float)
            if not trades_frame.empty
            else np.array([], dtype=float)
        )
        gains = float(net_pnls[net_pnls > 0].sum()) if net_pnls.size else 0.0
        losses = float(-net_pnls[net_pnls < 0].sum()) if net_pnls.size else 0.0
        metrics = {
            "trade_count": int(len(trades_frame)),
            "signal_records": int(len(signals_frame)),
            "filled_entries": int(
                (signals_frame["status"] == "filled").sum()
                if not signals_frame.empty
                else 0
            ),
            "total_return": float(equity / self.initial_equity - 1.0),
            "ending_equity": float(equity),
            "max_drawdown": float(abs(drawdowns.min())),
            "win_rate": (
                float((net_pnls > 0).mean()) if net_pnls.size else None
            ),
            "profit_factor": gains / losses if losses > 0 else None,
            "expectancy": float(net_pnls.mean()) if net_pnls.size else None,
            "fees": (
                float(trades_frame["fees"].sum()) if not trades_frame.empty else 0.0
            ),
            "funding_pnl": (
                float(trades_frame["funding_pnl"].sum())
                if not trades_frame.empty
                else 0.0
            ),
            "skipped_setups": dict(sorted(skipped.items())),
        }
        expected_counts = {
            "5m": int((end - start) / pd.Timedelta(minutes=5)),
            "15m": int((end - start) / pd.Timedelta(minutes=15)),
            "1h": int((end - start) / pd.Timedelta(hours=1)),
        }
        actual_counts = {
            "5m": int(
                (
                    (bars_5m["timestamp"] >= start)
                    & (bars_5m["timestamp"] < end)
                ).sum()
            ),
            "15m": int(
                (
                    (bars_15m["timestamp"] >= start)
                    & (bars_15m["timestamp"] < end)
                ).sum()
            ),
            "1h": int(
                (
                    (bars_1h["timestamp"] >= start)
                    & (bars_1h["timestamp"] < end)
                ).sum()
            ),
        }
        available_funding = int(
            sum(timestamp in funding_rates.index for timestamp in expected_funding_index)
        )
        data_quality = {
            "expected_rows": expected_counts,
            "actual_rows": actual_counts,
            "complete": actual_counts == expected_counts,
            "duplicate_timestamps": {
                "5m": int(bars_5m["timestamp"].duplicated().sum()),
                "15m": int(bars_15m["timestamp"].duplicated().sum()),
                "1h": int(bars_1h["timestamp"].duplicated().sum()),
            },
        }
        funding_summary = {
            "expected_events": len(expected_funding_index),
            "available_events": available_funding,
            "observed_events_applied_while_open": observed_funding_events,
            "imputed_events_applied_while_open": imputed_funding_events,
            "adverse_proxy_rate": adverse_funding_rate,
            "zero_funding_fallback_used": False,
            "mark_fallback_events": mark_fallback_events,
        }
        execution = {
            "engine": "quant_lab_native_selective_reentry_smoke_v1",
            "bar_event_order": (
                "known higher-timeframe closes -> known structure exits -> funding -> "
                "pending entry -> stop -> 1R partial -> cost stop/1.8R -> time exits"
            ),
            "closed_candles_only": True,
            "tick_size": self.tick_size,
            "quantity_step": self.quantity_step,
            "minimum_notional": self.minimum_notional,
            "requested_exchange_leverage": 50,
            "max_notional_fraction_of_equity": 0.50,
            "liquidation_model_complete": False,
        }
        return SelectiveReentrySmokeResult(
            start_utc_inclusive=start.isoformat(),
            end_utc_exclusive=end.isoformat(),
            metrics=metrics,
            data_quality=data_quality,
            funding=funding_summary,
            execution=execution,
            signals=signals_frame,
            trades=trades_frame,
            equity=equity_frame,
        )
