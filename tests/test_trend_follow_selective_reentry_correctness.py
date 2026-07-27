from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from quant_lab.domain.price_structure import ConfirmedPivot, confirmed_pivots_at
from quant_lab.domain.trend_follow_selective_reentry import (
    ExitCandidate,
    TrendLegState,
    advance_trend_leg,
    assess_daily_loss,
    bounded_position_size,
    build_pullback_window,
    consume_reentry,
    is_opposite_confirmed_structure,
    select_exit_candidate,
)


CANDIDATE_ID = "version_12e1307c9c0e43268f6fdbdda5b9bd20"
BASELINE_ID = "version_070593dcd9034dfe8f31b8429d605153"


def test_pullback_is_complete_contiguous_sequence_and_swing_uses_full_extreme() -> None:
    window = build_pullback_window(
        opens=[100, 103, 102, 101, 99],
        highs=[104, 104, 103, 102, 103],
        lows=[99, 101, 98, 97, 98],
        closes=[103, 102, 101, 99, 102],
        confirmation_index=4,
        side="long",
    )
    assert window is not None
    assert window.bar_indexes == (1, 2, 3)
    assert window.swing_price == 97

    too_long = build_pullback_window(
        opens=[110, 109, 108, 107, 106, 105, 104],
        highs=[111, 110, 109, 108, 107, 106, 108],
        lows=[108, 107, 106, 105, 104, 103, 103],
        closes=[109, 108, 107, 106, 105, 104, 107],
        confirmation_index=6,
        side="long",
    )
    assert too_long is None

    doji_break = build_pullback_window(
        opens=[100, 102, 101, 100, 99],
        highs=[103, 103, 102, 101, 102],
        lows=[99, 100, 99, 98, 98],
        closes=[102, 101, 101, 99, 101],
        confirmation_index=4,
        side="long",
    )
    assert doji_break is None


def test_opposite_structure_uses_only_pivots_confirmed_after_two_right_bars() -> None:
    highs = [5.0, 7.0, 10.0, 8.0, 6.0]
    lows = [4.0, 5.0, 6.0, 5.0, 4.5]
    assert confirmed_pivots_at(highs, lows, 3, left_bars=2, right_bars=2) == ()
    pivot = confirmed_pivots_at(highs, lows, 4, left_bars=2, right_bars=2)[0]
    assert pivot.pivot_index == 2
    assert pivot.confirmation_index == 4

    confirmed_highs = (
        ConfirmedPivot("high", 1, 3, 110.0),
        ConfirmedPivot("high", 5, 7, 105.0),
    )
    confirmed_lows = (
        ConfirmedPivot("low", 2, 4, 100.0),
        ConfirmedPivot("low", 6, 8, 95.0),
    )
    assert is_opposite_confirmed_structure(
        position_side="long",
        confirmed_highs=confirmed_highs,
        confirmed_lows=confirmed_lows,
    )


def test_trend_leg_identity_and_one_reentry_limit_are_deterministic() -> None:
    state = advance_trend_leg(TrendLegState(), confirmed_structure="bull")
    assert state == TrendLegState(side="long", leg_number=1, reentries_used=0)
    assert advance_trend_leg(state, confirmed_structure="bull") == state

    used = consume_reentry(state)
    assert used.reentries_used == 1
    with pytest.raises(ValueError, match="limit"):
        consume_reentry(used)

    neutral = advance_trend_leg(used, confirmed_structure="neutral")
    assert neutral.side is None
    new_leg = advance_trend_leg(neutral, confirmed_structure="bull")
    assert new_leg == TrendLegState(side="long", leg_number=2, reentries_used=0)


def test_same_bar_exit_priority_is_conservative_and_ties_use_adverse_price() -> None:
    decision = select_exit_candidate(
        (
            ExitCandidate("partial_take_profit_1r", 105.0),
            ExitCandidate("protective_stop", 96.0),
            ExitCandidate("structure_invalidation", 97.0),
            ExitCandidate("final_take_profit_1_8r", 108.0),
        ),
        position_side="long",
    )
    assert decision == ExitCandidate("protective_stop", 96.0)

    short_decision = select_exit_candidate(
        (
            ExitCandidate("protective_stop", 104.0),
            ExitCandidate("structure_invalidation", 105.0),
        ),
        position_side="short",
    )
    assert short_decision == ExitCandidate("structure_invalidation", 105.0)


def test_daily_loss_uses_frozen_utc_day_open_realized_equity_only() -> None:
    unrealized_only = assess_daily_loss(
        day_open_realized_equity=1000.0,
        closed_trade_price_pnl=0.0,
        fees=0.0,
        slippage=0.0,
        funding_pnl=0.0,
        unrealized_pnl=-100.0,
    )
    assert unrealized_only.entry_allowed is True
    assert unrealized_only.unrealized_pnl_excluded is True
    assert unrealized_only.loss_limit == 7.5

    realized_loss = assess_daily_loss(
        day_open_realized_equity=1000.0,
        closed_trade_price_pnl=-6.0,
        fees=1.0,
        slippage=0.4,
        funding_pnl=-0.1,
    )
    assert realized_loss.realized_drawdown == pytest.approx(7.5)
    assert realized_loss.entry_allowed is False


def test_50x_is_margin_setting_but_notional_and_risk_remain_bounded() -> None:
    sizing = bounded_position_size(
        equity=1000.0,
        stop_distance_fraction=0.0025,
    )
    assert sizing.requested_exchange_leverage == 50.0
    assert sizing.notional <= 500.0
    assert sizing.margin_required <= 10.0
    assert sizing.estimated_account_risk <= 3.0
    assert sizing.liquidation_model_complete is False
    assert sizing.leverage_safety_claim_allowed is False


def test_candidate_declaration_preserves_baseline_and_disables_smoke_locked_test() -> None:
    project_root = Path(__file__).resolve().parents[1]
    declaration = runpy.run_path(
        project_root
        / "strategies/research/trend-follow-selective-reentry-x50-20260724/candidate-v1.py"
    )
    assert declaration["CANDIDATE_VERSION_ID"] == CANDIDATE_ID
    assert declaration["BASELINE_VERSION_ID"] == BASELINE_ID
    assert declaration["RULES"]["liquidation_model_complete"] is False
    assert declaration["RULES"]["locked_test_used"] is False
    assert declaration["RULES"]["smoke_started"] is False
    adapter = declaration["build_correctness_adapter"]()
    assert adapter.candidate_version_id == CANDIDATE_ID
    assert adapter.baseline_version_id == BASELINE_ID
    assert adapter.closed_candles_only is True
    assert adapter.zero_funding_fallback_allowed is False
