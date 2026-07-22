"""Single-hypothesis candidate derived from immutable baseline v0.

This artifact does not replace or modify the frozen baseline. It exists only for
ExperimentPlan ``plan_58cb87968168418f9dc7711429993848`` and may become a
StrategyVersion only after separate human review.
"""

EXPERIMENT_PLAN_ID = "plan_58cb87968168418f9dc7711429993848"
BASELINE_VERSION_ID = "version_2dae8104719c43398a2760421e363a9c"
MARKET_PROFILE = "crypto_perpetual.binance.eth"
TIMEFRAME = "15m"

SINGLE_CHANGE = {
    "from": "next_bar_open",
    "to": "confirmation_candle_breakout",
    "long_trigger": "confirmation_candle_high",
    "short_trigger": "confirmation_candle_low",
    "activation": "next_bar_or_later",
    "gap_fill": "next_available_open_when_open_crosses_trigger",
    "pending_order_cancel": "structure_becomes_neutral_or_opposite",
    "same_bar_trigger_and_stop": "assume_entry_then_protective_stop",
}

UNCHANGED_RULES = {
    "pivot_left_bars": 2,
    "pivot_right_bars": 2,
    "strict_pivot_comparison": True,
    "long_structure": "higher_high_and_higher_low",
    "short_structure": "lower_high_and_lower_low",
    "signal_timing": "first_structure_transition_after_confirmation_close",
    "protective_stop": "latest_confirmed_opposite_swing",
    "structure_exit": "opposite_transition_next_bar_open",
    "fixed_take_profit": None,
    "pyramiding": False,
    "hedging": False,
    "maximum_positions": 1,
    "leverage": 1.0,
}

SAFETY = {
    "live_trading": False,
    "automatic_production_promotion": False,
    "old_locked_test_used": False,
    "future_locked_test_used": False,
}
