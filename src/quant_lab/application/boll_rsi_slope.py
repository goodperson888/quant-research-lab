from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quant_lab.application.execution import ConservativeExecutionModel


@dataclass(frozen=True, slots=True)
class BollRsiSlopeResult:
    start_utc_inclusive: str
    end_utc_exclusive: str
    metrics: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    funding: Mapping[str, Any]
    execution: Mapping[str, Any]
    signal_funnel: Mapping[str, Any]
    signals: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame


@dataclass(slots=True)
class _Position:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    quantity: float
    stop_price: float
    take_profit_price: float
    liquidation_price: float | None
    entry_fee: float
    initial_equity: float
    held_bars: int = 0
    funding_pnl: float = 0.0
    max_favorable_r: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _prepare_ohlcv(frame: pd.DataFrame, *, timeframe: str) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{timeframe} OHLCV missing columns: {sorted(missing)}")
    result = frame.loc[:, ["timestamp", "open", "high", "low", "close", "volume"]].copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    for name in ("open", "high", "low", "close", "volume"):
        result[name] = pd.to_numeric(result[name], errors="raise")
    invalid = (result["high"] < result[["open", "close", "low"]].max(axis=1)) | (
        result["low"] > result[["open", "close", "high"]].min(axis=1)
    )
    if invalid.any():
        raise ValueError(f"{timeframe} OHLC relationships are invalid")
    duration = pd.Timedelta(timeframe)
    result["event_time"] = result["timestamp"] + duration
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
    return (
        values.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .set_index("timestamp")["last_funding_rate"]
    )


class BollRsiSlopeBacktester:
    """Closed-bar 1h/15m/5m BOLL-RSI deep-pullback continuation simulator."""

    def __init__(
        self,
        *,
        strategy_version_id: str,
        execution_model: ConservativeExecutionModel,
        initial_equity: float = 10_000.0,
        leverage: float = 1.0,
        risk_per_trade_fraction: float = 0.0025,
        max_trades_per_day: int = 10,
        cooldown_minutes: int = 0,
        cooldown_after_loss_minutes: int = 0,
        max_stop_distance_fraction: float = 0.0045,
        stop_buffer_fraction: float = 0.0005,
        minimum_reward_r: float = 1.0,
        early_exit_bars: int = 3,
        early_exit_min_mfe_r: float = 0.3,
        max_holding_bars: int = 6,
        max_trigger_bars: int = 3,
        bollinger_period: int = 20,
        bollinger_stddev: float = 2.0,
        rsi_period: int = 7,
    ) -> None:
        if initial_equity <= 0:
            raise ValueError("initial equity must be positive")
        if not 1 <= leverage <= 50:
            raise ValueError("research leverage must remain between 1 and 50")
        if not 0 < risk_per_trade_fraction <= 0.01:
            raise ValueError("risk per trade is outside the reviewed bound")
        if not 1 <= max_trades_per_day <= 10:
            raise ValueError("daily trade limit is outside the reviewed bound")
        if not 0 < stop_buffer_fraction < max_stop_distance_fraction:
            raise ValueError("invalid BOLL stop buffer")
        if not 0 < minimum_reward_r <= 10:
            raise ValueError("minimum reward must be positive and at most 10R")
        if not 1 <= early_exit_bars < max_holding_bars:
            raise ValueError("early exit must precede the maximum holding time")
        if bollinger_period < 5 or bollinger_stddev <= 0 or rsi_period < 2:
            raise ValueError("invalid BOLL/RSI parameters")
        self.strategy_version_id = strategy_version_id
        self.execution_model = execution_model
        self.initial_equity = initial_equity
        self.leverage = leverage
        self.risk_per_trade_fraction = risk_per_trade_fraction
        self.max_trades_per_day = max_trades_per_day
        self.cooldown_minutes = cooldown_minutes
        self.cooldown_after_loss_minutes = cooldown_after_loss_minutes
        self.max_stop_distance_fraction = max_stop_distance_fraction
        self.stop_buffer_fraction = stop_buffer_fraction
        self.minimum_reward_r = minimum_reward_r
        self.early_exit_bars = early_exit_bars
        self.early_exit_min_mfe_r = early_exit_min_mfe_r
        self.max_holding_bars = max_holding_bars
        self.max_trigger_bars = max_trigger_bars
        self.bollinger_period = bollinger_period
        self.bollinger_stddev = bollinger_stddev
        self.rsi_period = rsi_period

    def run(
        self,
        *,
        ohlcv_5m: pd.DataFrame,
        ohlcv_15m: pd.DataFrame,
        ohlcv_1h: pd.DataFrame,
        funding: pd.DataFrame,
        warmup_start_utc_inclusive: str,
        start_utc_inclusive: str,
        end_utc_exclusive: str,
    ) -> BollRsiSlopeResult:
        warmup = _utc(warmup_start_utc_inclusive)
        start = _utc(start_utc_inclusive)
        end = _utc(end_utc_exclusive)
        if not warmup < start < end:
            raise ValueError("BOLL-RSI research requires warmup < start < end")

        bars_5m = _prepare_ohlcv(ohlcv_5m, timeframe="5min")
        bars_15m = _prepare_ohlcv(ohlcv_15m, timeframe="15min")
        bars_1h = _prepare_ohlcv(ohlcv_1h, timeframe="1h")
        for name, frame in (("5m", bars_5m), ("15m", bars_15m), ("1h", bars_1h)):
            covered = frame.loc[
                (frame["timestamp"] >= warmup) & (frame["timestamp"] < end)
            ]
            if covered.empty:
                raise ValueError(f"{name} data does not cover the requested range")

        bars_5m = bars_5m.loc[
            (bars_5m["timestamp"] >= warmup) & (bars_5m["timestamp"] < end)
        ].reset_index(drop=True)
        bars_15m = bars_15m.loc[
            (bars_15m["timestamp"] >= warmup) & (bars_15m["timestamp"] < end)
        ].reset_index(drop=True)
        bars_1h = bars_1h.loc[
            (bars_1h["timestamp"] >= warmup) & (bars_1h["timestamp"] < end)
        ].reset_index(drop=True)

        bars_1h["basis_1h"] = bars_1h["close"].rolling(
            self.bollinger_period, min_periods=self.bollinger_period
        ).mean()
        bars_1h["basis_lag4_1h"] = bars_1h["basis_1h"].shift(4)
        bars_1h["normalized_slope_1h"] = (
            bars_1h["basis_1h"] / bars_1h["basis_lag4_1h"] - 1
        ) / 4
        bars_1h["trend_side"] = np.select(
            (
                (bars_1h["normalized_slope_1h"] > 0)
                & (bars_1h["close"] > bars_1h["basis_1h"]),
                (bars_1h["normalized_slope_1h"] < 0)
                & (bars_1h["close"] < bars_1h["basis_1h"]),
            ),
            ("long", "short"),
            default="none",
        )

        bars_15m["basis_15m"] = bars_15m["close"].rolling(
            self.bollinger_period, min_periods=self.bollinger_period
        ).mean()
        band_std = bars_15m["close"].rolling(
            self.bollinger_period, min_periods=self.bollinger_period
        ).std(ddof=0)
        bars_15m["lower_band_15m"] = (
            bars_15m["basis_15m"] - self.bollinger_stddev * band_std
        )
        bars_15m["upper_band_15m"] = (
            bars_15m["basis_15m"] + self.bollinger_stddev * band_std
        )
        delta = bars_15m["close"].diff()
        average_gain = delta.clip(lower=0).ewm(
            alpha=1 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()
        average_loss = (-delta.clip(upper=0)).ewm(
            alpha=1 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()
        relative_strength = average_gain / average_loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + relative_strength)
        rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)
        rsi = rsi.mask((average_gain == 0) & (average_loss == 0), 50.0)
        bars_15m["rsi_15m"] = rsi.fillna(50.0)
        bars_15m["recent_rsi_min_15m"] = bars_15m["rsi_15m"].rolling(
            3, min_periods=1
        ).min()
        bars_15m["recent_rsi_max_15m"] = bars_15m["rsi_15m"].rolling(
            3, min_periods=1
        ).max()
        rsi_previous = bars_15m["rsi_15m"].shift(1)
        bars_15m["setup_side"] = np.select(
            (
                (bars_15m["low"] <= bars_15m["lower_band_15m"])
                & (bars_15m["close"] > bars_15m["lower_band_15m"])
                & (bars_15m["recent_rsi_min_15m"] <= 30)
                & (rsi_previous <= 35)
                & (bars_15m["rsi_15m"] > 35),
                (bars_15m["high"] >= bars_15m["upper_band_15m"])
                & (bars_15m["close"] < bars_15m["upper_band_15m"])
                & (bars_15m["recent_rsi_max_15m"] >= 70)
                & (rsi_previous >= 65)
                & (bars_15m["rsi_15m"] < 65),
            ),
            ("long", "short"),
            default="none",
        )
        setups = bars_15m.loc[
            bars_15m["setup_side"] != "none",
            [
                "event_time",
                "setup_side",
                "low",
                "high",
                "basis_15m",
                "rsi_15m",
            ],
        ].rename(
            columns={
                "event_time": "setup_event_time",
                "low": "setup_low",
                "high": "setup_high",
            }
        )

        bars_5m["previous_high_5m"] = bars_5m["high"].shift(1)
        bars_5m["previous_low_5m"] = bars_5m["low"].shift(1)
        bars_5m["recent_low_5m"] = bars_5m["low"].rolling(
            self.max_trigger_bars, min_periods=1
        ).min()
        bars_5m["recent_high_5m"] = bars_5m["high"].rolling(
            self.max_trigger_bars, min_periods=1
        ).max()
        trend_features = bars_1h.loc[
            :, ["event_time", "trend_side", "basis_1h", "normalized_slope_1h"]
        ].sort_values("event_time")
        event_frame = pd.merge_asof(
            bars_5m.sort_values("event_time"),
            trend_features,
            on="event_time",
            direction="backward",
            allow_exact_matches=True,
        )
        if setups.empty:
            for name, default in (
                ("setup_event_time", pd.NaT),
                ("setup_side", "none"),
                ("setup_low", np.nan),
                ("setup_high", np.nan),
                ("basis_15m", np.nan),
                ("rsi_15m", np.nan),
            ):
                event_frame[name] = default
        else:
            event_frame = pd.merge_asof(
                event_frame.sort_values("event_time"),
                setups.sort_values("setup_event_time"),
                left_on="event_time",
                right_on="setup_event_time",
                direction="backward",
                allow_exact_matches=False,
            )
        setup_age = event_frame["event_time"] - event_frame["setup_event_time"]
        max_age = pd.Timedelta(minutes=5 * self.max_trigger_bars)
        valid_setup_age = (setup_age > pd.Timedelta(0)) & (setup_age <= max_age)
        event_frame["trigger_side"] = np.select(
            (
                valid_setup_age
                & (event_frame["setup_side"] == "long")
                & (event_frame["trend_side"] == "long")
                & (event_frame["close"] > event_frame["previous_high_5m"]),
                valid_setup_age
                & (event_frame["setup_side"] == "short")
                & (event_frame["trend_side"] == "short")
                & (event_frame["close"] < event_frame["previous_low_5m"]),
            ),
            ("long", "short"),
            default="none",
        )
        for name in (
            "trigger_side",
            "setup_low",
            "setup_high",
            "basis_15m",
            "rsi_15m",
            "setup_event_time",
            "recent_low_5m",
            "recent_high_5m",
            "normalized_slope_1h",
        ):
            event_frame[f"entry_{name}"] = event_frame[name].shift(1)

        funding_rates = _prepare_funding(funding)
        observed = funding_rates.loc[
            (funding_rates.index >= warmup) & (funding_rates.index < end)
        ].abs()
        if observed.empty or float(observed.quantile(0.99)) <= 0:
            raise ValueError("funding is required and cannot be silently zero")
        adverse_funding_rate = float(observed.quantile(0.99))

        equity = self.initial_equity
        position: _Position | None = None
        cooldown_until: pd.Timestamp | None = None
        daily_counts: dict[str, int] = {}
        trades: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        funding_imputed = 0
        funding_events = 0
        mark_fallback_checks = 0
        entry_candidates = 0
        skipped = {
            "outside_window": 0,
            "cooldown": 0,
            "daily_limit": 0,
            "position_open": 0,
            "stop_distance": 0,
            "insufficient_reward": 0,
            "order_rejected": 0,
        }
        pending_time_exit_reason: str | None = None

        def close_position(
            *,
            timestamp: pd.Timestamp,
            raw_price: float,
            reason: str,
        ) -> None:
            nonlocal equity, position, cooldown_until
            nonlocal pending_time_exit_reason
            if position is None:
                return
            if reason == "liquidation":
                execution_price = raw_price
                exit_cost = self.execution_model.liquidation_cost(
                    quantity=position.quantity, price=execution_price
                )
            else:
                execution_price = self.execution_model.market_execution_price(
                    raw_price=raw_price,
                    side=position.side,  # type: ignore[arg-type]
                    entering=False,
                )
                exit_cost = (
                    execution_price
                    * position.quantity
                    * self.execution_model.policy.taker_fee_per_side
                )
            price_pnl = (
                position.direction
                * position.quantity
                * (execution_price - position.entry_price)
            )
            net_pnl = (
                price_pnl
                + position.funding_pnl
                - position.entry_fee
                - exit_cost
            )
            equity += net_pnl
            trades.append(
                {
                    "strategy_version_id": self.strategy_version_id,
                    "side": position.side,
                    "entry_time": position.entry_time,
                    "exit_time": timestamp,
                    "entry_price": position.entry_price,
                    "exit_price": execution_price,
                    "quantity": position.quantity,
                    "stop_price": position.stop_price,
                    "take_profit_price": position.take_profit_price,
                    "liquidation_price": position.liquidation_price,
                    "holding_minutes": (
                        timestamp - position.entry_time
                    ).total_seconds()
                    / 60,
                    "exit_reason": reason,
                    "price_pnl": price_pnl,
                    "funding_pnl": position.funding_pnl,
                    "fees_and_liquidation_cost": position.entry_fee + exit_cost,
                    "net_pnl": net_pnl,
                    "return_on_initial_equity": net_pnl
                    / position.initial_equity,
                }
            )
            cooldown = (
                self.cooldown_after_loss_minutes
                if net_pnl < 0
                else self.cooldown_minutes
            )
            cooldown_until = timestamp + pd.Timedelta(minutes=cooldown)
            position = None
            pending_time_exit_reason = None

        active_rows = event_frame.loc[
            (event_frame["timestamp"] >= start)
            & (event_frame["timestamp"] < end)
        ].reset_index(drop=True)
        for row in active_rows.itertuples(index=False):
            timestamp = row.timestamp

            if position is not None and (
                pending_time_exit_reason is not None
                or position.held_bars >= self.max_holding_bars
            ):
                close_position(
                    timestamp=timestamp,
                    raw_price=float(row.open),
                    reason=(
                        pending_time_exit_reason
                        if pending_time_exit_reason is not None
                        else "time_stop"
                    ),
                )

            entry_side = str(row.entry_trigger_side)
            if entry_side != "none":
                entry_candidates += 1
                signal_record = {
                    "signal_time": timestamp,
                    "side": entry_side,
                    "setup_event_time": row.entry_setup_event_time,
                    "status": "candidate",
                    "reason": "boll_rsi_deep_pullback_trigger",
                    "rsi_15m": float(row.entry_rsi_15m),
                    "trend_slope_1h": float(row.entry_normalized_slope_1h),
                }
                if position is not None:
                    skipped["position_open"] += 1
                    signal_record.update(status="skipped", reason="position_open")
                elif cooldown_until is not None and timestamp < cooldown_until:
                    skipped["cooldown"] += 1
                    signal_record.update(status="skipped", reason="cooldown")
                else:
                    day = timestamp.strftime("%Y-%m-%d")
                    if daily_counts.get(day, 0) >= self.max_trades_per_day:
                        skipped["daily_limit"] += 1
                        signal_record.update(status="skipped", reason="daily_limit")
                    else:
                        entry_raw = float(row.open)
                        setup_low = float(row.entry_setup_low)
                        setup_high = float(row.entry_setup_high)
                        observed_low = min(
                            setup_low, float(row.entry_recent_low_5m)
                        )
                        observed_high = max(
                            setup_high, float(row.entry_recent_high_5m)
                        )
                        stop_raw = (
                            observed_low * (1 - self.stop_buffer_fraction)
                            if entry_side == "long"
                            else observed_high * (1 + self.stop_buffer_fraction)
                        )
                        stop_fraction = abs(entry_raw - stop_raw) / entry_raw
                        stop_is_adverse = (
                            stop_raw < entry_raw
                            if entry_side == "long"
                            else stop_raw > entry_raw
                        )
                        if (
                            not stop_is_adverse
                            or not 0 < stop_fraction <= self.max_stop_distance_fraction
                        ):
                            skipped["stop_distance"] += 1
                            signal_record.update(
                                status="skipped", reason="stop_distance"
                            )
                        else:
                            static_target = float(row.entry_basis_15m)
                            raw_risk = abs(entry_raw - stop_raw)
                            raw_reward = (
                                static_target - entry_raw
                                if entry_side == "long"
                                else entry_raw - static_target
                            )
                            if (
                                raw_reward <= 0
                                or raw_reward / raw_risk < self.minimum_reward_r
                            ):
                                skipped["insufficient_reward"] += 1
                                signal_record.update(
                                    status="skipped", reason="insufficient_reward"
                                )
                            else:
                                entry_price = self.execution_model.market_execution_price(
                                    raw_price=entry_raw,
                                    side=entry_side,  # type: ignore[arg-type]
                                    entering=True,
                                )
                                stop_execution = self.execution_model.market_execution_price(
                                    raw_price=stop_raw,
                                    side=entry_side,  # type: ignore[arg-type]
                                    entering=False,
                                )
                                per_base_risk = (
                                    abs(entry_price - stop_execution)
                                    + entry_price
                                    * self.execution_model.policy.taker_fee_per_side
                                    + stop_execution
                                    * self.execution_model.policy.taker_fee_per_side
                                )
                                desired_base = (
                                    equity * self.risk_per_trade_fraction
                                ) / per_base_risk
                                requested_notional = min(
                                    desired_base * entry_price,
                                    equity * self.leverage * 0.95,
                                )
                                order = self.execution_model.prepare_market_order(
                                    side=entry_side,  # type: ignore[arg-type]
                                    entering=True,
                                    raw_price=entry_raw,
                                    requested_notional=requested_notional,
                                    account_equity=equity,
                                    leverage=self.leverage,
                                    explicitly_requested_leverage=self.leverage > 1,
                                )
                                if not order.accepted:
                                    skipped["order_rejected"] += 1
                                    signal_record.update(
                                        status="skipped",
                                        reason=order.rejection_reason,
                                    )
                                else:
                                    take_profit = static_target
                                    liquidation = self.execution_model.liquidation_price(
                                        side=entry_side,  # type: ignore[arg-type]
                                        entry_price=order.execution_price,
                                        quantity=order.quantity,
                                        leverage=self.leverage,
                                    )
                                    position = _Position(
                                        side=entry_side,
                                        entry_time=timestamp,
                                        entry_price=order.execution_price,
                                        quantity=order.quantity,
                                        stop_price=stop_raw,
                                        take_profit_price=take_profit,
                                        liquidation_price=liquidation,
                                        entry_fee=order.fee,
                                        initial_equity=equity,
                                    )
                                    daily_counts[day] = daily_counts.get(day, 0) + 1
                                    signal_record.update(
                                        status="entered",
                                        reason="next_bar_market_entry",
                                        entry_price=order.execution_price,
                                        stop_price=stop_raw,
                                        take_profit_price=take_profit,
                                        quantity=order.quantity,
                                    )
                signals.append(signal_record)

            if position is not None and timestamp in funding_rates.index:
                funding_value, imputed = self.execution_model.funding_pnl(
                    side=position.side,  # type: ignore[arg-type]
                    quantity=position.quantity,
                    mark_price=float(row.open),
                    funding_rate=float(funding_rates.loc[timestamp]),
                    adverse_proxy_rate=adverse_funding_rate,
                )
                position.funding_pnl += funding_value
                funding_events += 1
                funding_imputed += int(imputed)

            if position is not None:
                decision = self.execution_model.select_intrabar_exit(
                    side=position.side,  # type: ignore[arg-type]
                    bar_open=float(row.open),
                    bar_high=float(row.high),
                    bar_low=float(row.low),
                    stop_price=position.stop_price,
                    take_profit_price=position.take_profit_price,
                    liquidation_price=position.liquidation_price,
                )
                if position.liquidation_price is not None:
                    mark_fallback_checks += 1
                if decision.reason is not None and decision.raw_price is not None:
                    close_position(
                        timestamp=row.event_time,
                        raw_price=float(decision.raw_price),
                        reason=decision.reason,
                    )

            if position is not None:
                position.held_bars += 1
                risk_distance = abs(
                    position.entry_price - position.stop_price
                )
                favorable_move = (
                    float(row.high) - position.entry_price
                    if position.side == "long"
                    else position.entry_price - float(row.low)
                )
                position.max_favorable_r = max(
                    position.max_favorable_r,
                    favorable_move / risk_distance if risk_distance > 0 else 0.0,
                )
                if (
                    position.held_bars >= self.early_exit_bars
                    and position.max_favorable_r < self.early_exit_min_mfe_r
                ):
                    pending_time_exit_reason = "early_time_stop"
                unrealized = (
                    position.direction
                    * position.quantity
                    * (float(row.close) - position.entry_price)
                    + position.funding_pnl
                    - position.entry_fee
                )
                mark_equity = equity + unrealized
            else:
                mark_equity = equity
            equity_rows.append(
                {
                    "timestamp": row.event_time,
                    "equity": mark_equity,
                    "closed_equity": equity,
                    "position_side": position.side if position else "flat",
                }
            )

        if position is not None:
            last = active_rows.iloc[-1]
            close_position(
                timestamp=_utc(last["event_time"]),
                raw_price=float(last["close"]),
                reason="end_of_window",
            )
            equity_rows.append(
                {
                    "timestamp": _utc(last["event_time"]),
                    "equity": equity,
                    "closed_equity": equity,
                    "position_side": "flat",
                }
            )

        trades_frame = pd.DataFrame(
            trades,
            columns=(
                "strategy_version_id",
                "side",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "quantity",
                "stop_price",
                "take_profit_price",
                "liquidation_price",
                "holding_minutes",
                "exit_reason",
                "price_pnl",
                "funding_pnl",
                "fees_and_liquidation_cost",
                "net_pnl",
                "return_on_initial_equity",
            ),
        )
        signals_frame = pd.DataFrame(
            signals,
            columns=(
                "signal_time",
                "side",
                "setup_event_time",
                "status",
                "reason",
                "rsi_15m",
                "trend_slope_1h",
                "entry_price",
                "stop_price",
                "take_profit_price",
                "quantity",
            ),
        )
        equity_frame = pd.DataFrame(equity_rows)
        metrics = self._metrics(trades_frame, equity_frame, equity)
        signal_funnel = {
            "trend_bars_1h": {
                "long": int((bars_1h["trend_side"] == "long").sum()),
                "short": int((bars_1h["trend_side"] == "short").sum()),
            },
            "pullback_candidates_15m": {
                "long": int((bars_15m["setup_side"] == "long").sum()),
                "short": int((bars_15m["setup_side"] == "short").sum()),
            },
            "triggers_5m": int((event_frame["trigger_side"] != "none").sum()),
            "entry_candidates": entry_candidates,
            "entered": int(
                (signals_frame.get("status", pd.Series(dtype=str)) == "entered").sum()
            ),
            "skipped": skipped,
        }
        expected_5m = int((end - start) / pd.Timedelta(minutes=5))
        actual_5m = int(len(active_rows))
        return BollRsiSlopeResult(
            start_utc_inclusive=start.isoformat(),
            end_utc_exclusive=end.isoformat(),
            metrics=metrics,
            data_quality={
                "expected_5m_rows": expected_5m,
                "actual_5m_rows": actual_5m,
                "missing_5m_rows": max(0, expected_5m - actual_5m),
                "monotonic_5m": bool(active_rows["timestamp"].is_monotonic_increasing),
                "duplicate_5m_timestamps": int(
                    active_rows["timestamp"].duplicated().sum()
                ),
            },
            funding={
                "required": True,
                "events_charged": funding_events,
                "imputed_events": funding_imputed,
                "adverse_proxy_rate": adverse_funding_rate,
                "silent_zero_allowed": False,
            },
            execution={
                "model_id": self.execution_model.policy.model_id,
                "venue": self.execution_model.rules.venue,
                "leverage": self.leverage,
                "margin_mode": self.execution_model.policy.margin_mode,
                "mark_price_fallback_used": mark_fallback_checks > 0,
                "mark_price_fallback_checks": mark_fallback_checks,
                "leverage_safety_proven": False,
                "historical_tiers_complete": self.execution_model.policy.historical_tiers_complete,
            },
            signal_funnel=signal_funnel,
            signals=signals_frame,
            trades=trades_frame,
            equity=equity_frame,
        )

    def _metrics(
        self, trades: pd.DataFrame, equity: pd.DataFrame, ending_equity: float
    ) -> dict[str, float]:
        if trades.empty:
            profit_factor = 0.0
            expectancy = 0.0
            win_rate = 0.0
            average_holding = 0.0
            liquidation_count = 0.0
            total_cost = 0.0
        else:
            wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"].sum()
            losses = -trades.loc[trades["net_pnl"] < 0, "net_pnl"].sum()
            profit_factor = float(wins / losses) if losses > 0 else 999.0
            expectancy = float(trades["net_pnl"].mean() / self.initial_equity)
            win_rate = float((trades["net_pnl"] > 0).mean())
            average_holding = float(trades["holding_minutes"].mean())
            liquidation_count = float(
                (trades["exit_reason"] == "liquidation").sum()
            )
            total_cost = float(
                trades["fees_and_liquidation_cost"].sum()
                - trades["funding_pnl"].sum()
            )
        if equity.empty:
            max_drawdown = 0.0
        else:
            values = equity["equity"].astype(float)
            peaks = values.cummax()
            drawdowns = (peaks - values) / peaks.replace(0, np.nan)
            max_drawdown = float(drawdowns.fillna(0).max())
        total_return = float(ending_equity / self.initial_equity - 1)
        values = {
            "total_return": total_return,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "trade_count": float(len(trades)),
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "average_holding_minutes": average_holding,
            "liquidation_count": liquidation_count,
            "total_cost": total_cost,
            "ending_equity": float(ending_equity),
        }
        if not all(isfinite(value) for value in values.values()):
            raise ValueError("BOLL-RSI metrics contain non-finite values")
        return values
