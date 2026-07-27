"""Approved correctness-clarification Candidate for trend-follow reentry.

This file declares the immutable Candidate created from Proposal
``proposal_0365aa2b0c154490a899a6efdf435564``. It does not overwrite Baseline
v0.1, run a backtest, inspect locked-test data, or claim that 50x leverage is
safe. The pure executable rule components live in
``quant_lab.domain.trend_follow_selective_reentry``.
"""

from quant_lab.domain.trend_follow_selective_reentry import (
    SelectiveReentryCorrectnessAdapter,
)


CANDIDATE_VERSION_ID = "version_12e1307c9c0e43268f6fdbdda5b9bd20"
BASELINE_VERSION_ID = "version_070593dcd9034dfe8f31b8429d605153"
PROPOSAL_ID = "proposal_0365aa2b0c154490a899a6efdf435564"
MARKET_PROFILE = "crypto_perpetual.binance.eth"

RULES = {
    "clarification_version": "v0.1-c1",
    "pullback_bars": {"min": 2, "max": 5, "contiguous": True},
    "pullback_swing": "full_pullback_extreme",
    "five_minute_pivots": {"left": 2, "right": 2, "confirmed_only": True},
    "trend_leg_resets": "neutral_or_opposite_then_new_confirmed_direction",
    "same_bar_exit_priority": (
        "protective_stop_or_invalidation",
        "partial_take_profit_1r",
        "final_take_profit_1_8r",
        "structure_or_time_exit",
    ),
    "daily_loss_basis": "utc_day_open_realized_equity",
    "daily_loss_fraction": 0.0075,
    "requested_exchange_leverage": 50,
    "max_notional_fraction_of_equity": 0.50,
    "max_estimated_account_risk_fraction": 0.003,
    "liquidation_model_complete": False,
    "locked_test_used": False,
    "smoke_started": False,
}


def build_correctness_adapter() -> SelectiveReentryCorrectnessAdapter:
    return SelectiveReentryCorrectnessAdapter(
        candidate_version_id=CANDIDATE_VERSION_ID,
        baseline_version_id=BASELINE_VERSION_ID,
    )
