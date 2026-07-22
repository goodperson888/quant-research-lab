from __future__ import annotations

import pandas as pd

from quant_lab.application.baseline_backtest import PriceStructureBaselineBacktester
from quant_lab.application.loss_attribution import TrainingLossAttribution
from quant_lab.application.trade_reconciliation import BaselineTradeReconciler
from quant_lab.domain.price_structure import confirmed_pivots_at


def test_pivot_is_available_only_after_two_right_bars_and_equal_highs_are_rejected() -> None:
    highs = [5.0, 7.0, 10.0, 8.0, 6.0]
    lows = [4.0, 5.0, 6.0, 5.0, 4.5]

    assert confirmed_pivots_at(highs, lows, 3) == ()
    pivots = confirmed_pivots_at(highs, lows, 4)
    assert [(pivot.kind, pivot.pivot_index, pivot.confirmation_index) for pivot in pivots] == [
        ("high", 2, 4)
    ]

    equal_highs = [5.0, 10.0, 10.0, 8.0, 6.0]
    assert confirmed_pivots_at(equal_highs, lows, 4) == ()


def _structure_bars(periods: int = 40) -> pd.DataFrame:
    index = pd.date_range("2026-01-01T00:00:00Z", periods=periods, freq="15min")
    highs = [5, 7, 10, 8, 7, 9, 12, 10, 9, 10, 11, 12, 10]
    lows = [4, 5, 6, 5, 3, 5, 7, 6.5, 5, 6, 7, 8, 6]
    while len(highs) < periods:
        highs.append(10.0)
        lows.append(7.0)
    opens = [(high + low) / 2 for high, low in zip(highs, lows, strict=True)]
    closes = list(opens)
    return pd.DataFrame(
        {
            "timestamp": index,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1.0,
        }
    )


def _mark_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.loc[:, ["timestamp", "open", "close"]].copy()


def test_structure_transition_enters_next_bar_and_stop_uses_latest_confirmed_low() -> None:
    bars = _structure_bars(13)
    # Force the bar after entry to touch the confirmed higher low at 5.
    bars.loc[12, ["open", "high", "low", "close"]] = [8.0, 9.0, 5.0, 6.0]
    funding = pd.DataFrame(
        {"timestamp": [bars.iloc[0]["timestamp"]], "last_funding_rate": [0.0001]}
    )
    result = PriceStructureBaselineBacktester(
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
    ).run(
        label="synthetic",
        ohlcv=bars,
        funding=funding,
        mark=_mark_from_bars(bars),
        start_utc_inclusive=bars.iloc[0]["timestamp"].isoformat(),
        end_utc_exclusive=(bars.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15)).isoformat(),
    )

    assert result.metrics["trade_count"] == 1
    trade = result.trades.iloc[0]
    assert trade["side"] == "long"
    assert trade["entry_time"] == bars.iloc[11]["timestamp"]
    assert trade["entry_raw_price"] == bars.iloc[11]["open"]
    assert trade["entry_mode"] == "next_bar_open"
    assert trade["exit_time"] == bars.iloc[12]["timestamp"]
    assert trade["exit_reason"] == "protective_stop"
    assert trade["exit_raw_price"] == 5.0


def test_entry_confirmation_candidate_waits_for_confirmation_candle_breakout() -> None:
    bars = _structure_bars(13)
    funding = pd.DataFrame(
        {"timestamp": [bars.iloc[0]["timestamp"]], "last_funding_rate": [0.0001]}
    )
    result = PriceStructureBaselineBacktester(
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
        entry_mode="confirmation_candle_breakout",
    ).run(
        label="candidate",
        ohlcv=bars,
        funding=funding,
        mark=_mark_from_bars(bars),
        start_utc_inclusive=bars.iloc[0]["timestamp"].isoformat(),
        end_utc_exclusive=(
            bars.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15)
        ).isoformat(),
    )

    assert result.metrics["trade_count"] == 1
    trade = result.trades.iloc[0]
    assert trade["side"] == "long"
    assert trade["entry_time"] == bars.iloc[11]["timestamp"]
    assert trade["entry_trigger_price"] == bars.iloc[10]["high"]
    assert trade["entry_raw_price"] == bars.iloc[10]["high"]
    assert trade["entry_mode"] == "confirmation_candle_breakout"


def test_missing_funding_is_explicitly_charged_adversely_not_zero() -> None:
    bars = _structure_bars(40)
    funding = pd.DataFrame(
        {"timestamp": [bars.iloc[0]["timestamp"]], "last_funding_rate": [0.0002]}
    )
    result = PriceStructureBaselineBacktester(
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
    ).run(
        label="funding-gap",
        ohlcv=bars,
        funding=funding,
        mark=_mark_from_bars(bars),
        start_utc_inclusive=bars.iloc[0]["timestamp"].isoformat(),
        end_utc_exclusive=(bars.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15)).isoformat(),
    )

    assert result.funding["missing_events_in_slice"] == 1
    assert result.funding["imputed_events_applied_while_position_open"] == 1
    assert result.funding["zero_funding_fallback_used"] is False
    assert result.trades.iloc[0]["funding_pnl"] < 0


def test_trade_reconciliation_independently_checks_signal_exit_and_cost_math() -> None:
    bars = _structure_bars(13)
    bars.loc[12, ["open", "high", "low", "close"]] = [8.0, 9.0, 5.0, 6.0]
    funding = pd.DataFrame(
        {"timestamp": [bars.iloc[0]["timestamp"]], "last_funding_rate": [0.0001]}
    )
    backtest = PriceStructureBaselineBacktester(
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
    ).run(
        label="synthetic",
        ohlcv=bars,
        funding=funding,
        mark=_mark_from_bars(bars),
        start_utc_inclusive=bars.iloc[0]["timestamp"].isoformat(),
        end_utc_exclusive=(bars.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15)).isoformat(),
    )

    audit = BaselineTradeReconciler().reconcile(
        bars=bars,
        trades=backtest.trades,
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
        train_end="2026-01-02T00:00:00Z",
        validation_end="2026-01-03T00:00:00Z",
        sample_size=1,
    )

    assert audit.summary["trades_checked"] == 1
    assert audit.summary["all_checks_passed"] is True
    assert audit.summary["baseline_changed"] is False


def test_training_loss_attribution_reconciles_costs_without_approving_hypothesis() -> None:
    bars = _structure_bars(13)
    bars.loc[12, ["open", "high", "low", "close"]] = [8.0, 9.0, 5.0, 6.0]
    funding = pd.DataFrame(
        {"timestamp": [bars.iloc[0]["timestamp"]], "last_funding_rate": [0.0001]}
    )
    backtest = PriceStructureBaselineBacktester(
        fee_per_side=0.0005,
        slippage_bps_per_side=2.0,
    ).run(
        label="synthetic",
        ohlcv=bars,
        funding=funding,
        mark=_mark_from_bars(bars),
        start_utc_inclusive=bars.iloc[0]["timestamp"].isoformat(),
        end_utc_exclusive=(bars.iloc[-1]["timestamp"] + pd.Timedelta(minutes=15)).isoformat(),
    )

    attribution = TrainingLossAttribution().analyze(backtest.trades)

    assert attribution["cost_waterfall"]["max_recomposition_error"] < 1e-12
    assert attribution["proposal"]["status"] == "draft_unapproved"
    assert attribution["proposal"]["parameter_search_requested"] is False
    assert attribution["guardrails"][
        "validation_or_locked_data_used_for_hypothesis_selection"
    ] is False
