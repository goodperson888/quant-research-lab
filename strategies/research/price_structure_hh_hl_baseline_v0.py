"""Immutable implementation declaration for the user-approved baseline v0.

The executable logic lives in ``quant_lab.domain.price_structure`` and
``quant_lab.application.baseline_backtest`` so it can be unit-tested without a
Freqtrade exchange connection. Changing any rule below requires a new strategy
version; this file must not be edited to improve a historical result.
"""

BASELINE_VERSION_ID = "version_2dae8104719c43398a2760421e363a9c"
MARKET_PROFILE = "crypto_perpetual.binance.eth"
TIMEFRAME = "15m"

RULES = {
    "pivot_left_bars": 2,
    "pivot_right_bars": 2,
    "strict_pivot_comparison": True,
    "long_structure": "higher_high_and_higher_low",
    "short_structure": "lower_high_and_lower_low",
    "signal_timing": "first_structure_transition_after_confirmation_close",
    "entry_timing": "next_bar_open",
    "protective_stop": "latest_confirmed_opposite_swing",
    "structure_exit": "opposite_transition_next_bar_open",
    "fixed_take_profit": None,
    "same_bar_conflict": "protective_stop_first",
    "pyramiding": False,
    "hedging": False,
    "maximum_positions": 1,
    "leverage": 1.0,
}
