from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Sequence

from .price_structure import ConfirmedPivot, classify_structure


Side = Literal["long", "short"]
ExitEvent = Literal[
    "protective_stop",
    "structure_invalidation",
    "partial_take_profit_1r",
    "final_take_profit_1_8r",
    "opposite_5m_structure",
    "time_stop",
    "max_holding_time",
]


@dataclass(frozen=True, slots=True)
class PullbackWindow:
    side: Side
    bar_indexes: tuple[int, ...]
    swing_price: float


@dataclass(frozen=True, slots=True)
class TrendLegState:
    side: Side | None = None
    leg_number: int = 0
    reentries_used: int = 0


@dataclass(frozen=True, slots=True)
class ExitCandidate:
    event: ExitEvent
    raw_price: float


@dataclass(frozen=True, slots=True)
class DailyLossAssessment:
    day_open_realized_equity: float
    realized_equity: float
    realized_drawdown: float
    loss_limit: float
    entry_allowed: bool
    unrealized_pnl_excluded: bool = True


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    notional: float
    margin_required: float
    estimated_account_risk: float
    requested_exchange_leverage: float
    liquidation_model_complete: bool = False
    leverage_safety_claim_allowed: bool = False


def identify_pullback_indexes(
    *,
    opens: Sequence[float],
    closes: Sequence[float],
    confirmation_index: int,
    side: Side,
    min_bars: int = 2,
    max_bars: int = 5,
) -> tuple[int, ...]:
    """Return the complete counter-trend sequence immediately before confirmation.

    A long pullback bar has ``close < open``; a short pullback bar has
    ``close > open``. Doji and trend-direction bars terminate the sequence. If
    the complete contiguous sequence falls outside the configured bounds, the
    setup is ineligible rather than truncating history to fit.
    """

    if len(opens) != len(closes):
        raise ValueError("open and close series must have equal length")
    if side not in {"long", "short"}:
        raise ValueError("unsupported side")
    if min_bars < 1 or max_bars < min_bars:
        raise ValueError("invalid pullback bounds")
    if confirmation_index <= 0 or confirmation_index >= len(opens):
        return ()

    def is_countertrend(index: int) -> bool:
        open_price = float(opens[index])
        close_price = float(closes[index])
        return close_price < open_price if side == "long" else close_price > open_price

    indexes: list[int] = []
    cursor = confirmation_index - 1
    while cursor >= 0 and is_countertrend(cursor):
        indexes.append(cursor)
        cursor -= 1
    indexes.reverse()
    if not min_bars <= len(indexes) <= max_bars:
        return ()
    return tuple(indexes)


def build_pullback_window(
    *,
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    confirmation_index: int,
    side: Side,
    min_bars: int = 2,
    max_bars: int = 5,
) -> PullbackWindow | None:
    if not (len(opens) == len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC series must have equal length")
    indexes = identify_pullback_indexes(
        opens=opens,
        closes=closes,
        confirmation_index=confirmation_index,
        side=side,
        min_bars=min_bars,
        max_bars=max_bars,
    )
    if not indexes:
        return None
    swing_price = (
        min(float(lows[index]) for index in indexes)
        if side == "long"
        else max(float(highs[index]) for index in indexes)
    )
    return PullbackWindow(side=side, bar_indexes=indexes, swing_price=swing_price)


def is_opposite_confirmed_structure(
    *,
    position_side: Side,
    confirmed_highs: Sequence[ConfirmedPivot],
    confirmed_lows: Sequence[ConfirmedPivot],
) -> bool:
    structure = classify_structure(confirmed_highs, confirmed_lows)
    return structure == ("bear" if position_side == "long" else "bull")


def advance_trend_leg(
    previous: TrendLegState,
    *,
    confirmed_structure: Literal["bull", "bear", "neutral"],
) -> TrendLegState:
    current_side: Side | None
    if confirmed_structure == "bull":
        current_side = "long"
    elif confirmed_structure == "bear":
        current_side = "short"
    else:
        current_side = None

    if current_side is None:
        return TrendLegState(side=None, leg_number=previous.leg_number)
    if previous.side == current_side:
        return previous
    return TrendLegState(
        side=current_side,
        leg_number=previous.leg_number + 1,
        reentries_used=0,
    )


def consume_reentry(
    state: TrendLegState, *, max_reentries_per_leg: int = 1
) -> TrendLegState:
    if state.side is None:
        raise ValueError("reentry requires an active trend leg")
    if state.reentries_used >= max_reentries_per_leg:
        raise ValueError("trend-leg reentry limit reached")
    return replace(state, reentries_used=state.reentries_used + 1)


_EXIT_PRIORITY: dict[ExitEvent, int] = {
    "protective_stop": 0,
    "structure_invalidation": 0,
    "partial_take_profit_1r": 1,
    "final_take_profit_1_8r": 2,
    "opposite_5m_structure": 3,
    "time_stop": 3,
    "max_holding_time": 3,
}


def select_exit_candidate(
    candidates: Sequence[ExitCandidate], *, position_side: Side
) -> ExitCandidate | None:
    """Select the frozen same-bar priority and the more adverse tied price."""

    if not candidates:
        return None
    if position_side not in {"long", "short"}:
        raise ValueError("unsupported side")

    best_priority = min(_EXIT_PRIORITY[item.event] for item in candidates)
    eligible = [item for item in candidates if _EXIT_PRIORITY[item.event] == best_priority]
    if position_side == "long":
        return min(eligible, key=lambda item: (item.raw_price, item.event))
    return max(eligible, key=lambda item: (item.raw_price, item.event))


def assess_daily_loss(
    *,
    day_open_realized_equity: float,
    closed_trade_price_pnl: float,
    fees: float,
    slippage: float,
    funding_pnl: float,
    unrealized_pnl: float = 0.0,
    max_daily_loss_fraction: float = 0.0075,
) -> DailyLossAssessment:
    """Assess the UTC-day loss limit using realized equity only."""

    del unrealized_pnl
    if day_open_realized_equity <= 0:
        raise ValueError("day-open realized equity must be positive")
    if fees < 0 or slippage < 0:
        raise ValueError("fees and slippage must be non-negative")
    if not 0 < max_daily_loss_fraction < 1:
        raise ValueError("daily loss fraction must be between zero and one")

    realized_delta = closed_trade_price_pnl - fees - slippage + funding_pnl
    realized_equity = day_open_realized_equity + realized_delta
    realized_drawdown = max(0.0, day_open_realized_equity - realized_equity)
    loss_limit = day_open_realized_equity * max_daily_loss_fraction
    return DailyLossAssessment(
        day_open_realized_equity=day_open_realized_equity,
        realized_equity=realized_equity,
        realized_drawdown=realized_drawdown,
        loss_limit=loss_limit,
        entry_allowed=realized_drawdown < loss_limit,
    )


def bounded_position_size(
    *,
    equity: float,
    stop_distance_fraction: float,
    round_trip_cost_fraction: float = 0.0014,
    requested_exchange_leverage: float = 50.0,
    max_margin_fraction_of_equity: float = 0.01,
    max_notional_fraction_of_equity: float = 0.50,
    max_estimated_account_risk_fraction: float = 0.003,
) -> PositionSizingResult:
    """Apply the frozen risk/notional caps without claiming liquidation safety."""

    if equity <= 0:
        raise ValueError("equity must be positive")
    if stop_distance_fraction <= 0 or round_trip_cost_fraction < 0:
        raise ValueError("invalid stop or cost fraction")
    if requested_exchange_leverage <= 0:
        raise ValueError("requested leverage must be positive")

    risk_budget = equity * max_estimated_account_risk_fraction
    risk_per_notional = stop_distance_fraction + round_trip_cost_fraction
    risk_bounded_notional = risk_budget / risk_per_notional
    margin_bounded_notional = (
        equity * max_margin_fraction_of_equity * requested_exchange_leverage
    )
    absolute_notional_cap = equity * max_notional_fraction_of_equity
    notional = min(
        risk_bounded_notional,
        margin_bounded_notional,
        absolute_notional_cap,
    )
    return PositionSizingResult(
        notional=notional,
        margin_required=notional / requested_exchange_leverage,
        estimated_account_risk=notional * risk_per_notional,
        requested_exchange_leverage=requested_exchange_leverage,
    )


@dataclass(frozen=True, slots=True)
class SelectiveReentryCorrectnessAdapter:
    candidate_version_id: str
    baseline_version_id: str
    pivot_left_bars: int = 2
    pivot_right_bars: int = 2
    closed_candles_only: bool = True
    zero_funding_fallback_allowed: bool = False
    locked_test_used: bool = False
    liquidation_model_complete: bool = False

    def pullback_window(
        self,
        *,
        opens: Sequence[float],
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        confirmation_index: int,
        side: Side,
    ) -> PullbackWindow | None:
        return build_pullback_window(
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            confirmation_index=confirmation_index,
            side=side,
        )
