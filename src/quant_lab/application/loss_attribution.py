from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def _compound(returns: pd.Series) -> float:
    return float((1.0 + returns.astype(float)).prod() - 1.0)


def _group_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    returns = frame["net_return"].astype(float)
    return {
        "trade_count": int(len(frame)),
        "win_rate": float((returns > 0).mean()) if len(frame) else None,
        "average_net_return": float(returns.mean()) if len(frame) else None,
        "sum_net_return": float(returns.sum()),
        "compound_net_return": _compound(returns) if len(frame) else 0.0,
    }


class TrainingLossAttribution:
    """Describe baseline losses using training trades only.

    This class does not change strategy rules or choose parameters. Its output is
    evidence for a later single-hypothesis proposal.
    """

    def analyze(self, trades: pd.DataFrame) -> dict[str, Any]:
        if trades.empty:
            raise ValueError("training loss attribution requires trades")
        frame = trades.copy().sort_values(["entry_time", "trade_id"])
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        direction = np.where(frame["side"] == "long", 1.0, -1.0)
        frame["raw_price_return"] = direction * (
            frame["exit_raw_price"] / frame["entry_raw_price"] - 1.0
        )
        frame["slippage_drag"] = (
            frame["gross_return"] - frame["raw_price_return"]
        )
        frame["fee_drag"] = -(frame["fees"] / frame["entry_equity"])
        frame["funding_return"] = frame["funding_pnl"] / frame["entry_equity"]
        frame["recomposed_net_return"] = (
            frame["raw_price_return"]
            + frame["slippage_drag"]
            + frame["fee_drag"]
            + frame["funding_return"]
        )
        max_recomposition_error = float(
            (frame["recomposed_net_return"] - frame["net_return"]).abs().max()
        )
        if max_recomposition_error > 1e-12:
            raise ValueError("loss attribution does not reconcile to recorded net returns")

        raw = frame["raw_price_return"]
        after_slippage = raw + frame["slippage_drag"]
        after_fees = after_slippage + frame["fee_drag"]
        net = frame["net_return"]
        cost_waterfall = {
            "raw_price_compound_return": _compound(raw),
            "after_slippage_compound_return": _compound(after_slippage),
            "after_fees_compound_return": _compound(after_fees),
            "net_after_funding_compound_return": _compound(net),
            "sum_raw_price_return": float(raw.sum()),
            "sum_slippage_drag": float(frame["slippage_drag"].sum()),
            "sum_fee_drag": float(frame["fee_drag"].sum()),
            "sum_funding_return": float(frame["funding_return"].sum()),
            "sum_net_return": float(net.sum()),
            "max_recomposition_error": max_recomposition_error,
        }

        frame["same_bar"] = frame["entry_time"] == frame["exit_time"]
        holding_minutes = (
            (frame["exit_time"] - frame["entry_time"]).dt.total_seconds() / 60.0
        )
        frame["holding_bucket"] = pd.cut(
            holding_minutes,
            bins=[-0.1, 0.1, 60, 240, float("inf")],
            labels=["same_bar", "up_to_1h", "1h_to_4h", "over_4h"],
            right=True,
        ).astype(str)
        frame["utc_session"] = pd.cut(
            frame["entry_time"].dt.hour,
            bins=[-1, 5, 11, 17, 23],
            labels=["00-05", "06-11", "12-17", "18-23"],
        ).astype(str)

        losing_total = -float(frame.loc[frame["net_return"] < 0, "net_return"].sum())
        same_bar_loss = -float(
            frame.loc[frame["same_bar"] & (frame["net_return"] < 0), "net_return"].sum()
        )
        same_bar = frame[frame["same_bar"]]

        return {
            "summary": {
                **_group_metrics(frame),
                "long_trade_count": int((frame["side"] == "long").sum()),
                "short_trade_count": int((frame["side"] == "short").sum()),
                "same_bar_stop_count": int(frame["same_bar"].sum()),
                "same_bar_stop_rate": float(frame["same_bar"].mean()),
                "same_bar_compound_return": _compound(same_bar["net_return"]),
                "same_bar_share_of_gross_losing_returns": (
                    same_bar_loss / losing_total if losing_total > 0 else None
                ),
                "funding_imputation_events": int(
                    frame["imputed_funding_events"].sum()
                ),
            },
            "cost_waterfall": cost_waterfall,
            "by_side": {
                str(name): _group_metrics(group)
                for name, group in frame.groupby("side", observed=True)
            },
            "by_exit_reason": {
                str(name): _group_metrics(group)
                for name, group in frame.groupby("exit_reason", observed=True)
            },
            "by_holding_period": {
                str(name): _group_metrics(group)
                for name, group in frame.groupby("holding_bucket", observed=True)
            },
            "by_entry_utc_session": {
                str(name): _group_metrics(group)
                for name, group in frame.groupby("utc_session", observed=True)
            },
            "proposal": {
                "status": "draft_unapproved",
                "evidence_scope": "train_only",
                "single_hypothesis": (
                    "Requiring price to break the structure-confirmation candle high "
                    "for longs or low for shorts before entry will reduce immediate "
                    "false entries and execution-cost drag without using future data."
                ),
                "single_rule_change": (
                    "Replace next-bar-open entry with a stop-entry at the confirmation "
                    "candle high/low; all pivot, exit, stop, cost and leverage rules stay fixed."
                ),
                "primary_falsification_metrics": [
                    "same_bar_stop_rate",
                    "trade_count",
                    "net_return_after_costs",
                    "max_drawdown",
                ],
                "approval_required_before_experiment": True,
                "parameter_search_requested": False,
                "locked_test_requirement": (
                    "Current locked test has been viewed. A candidate needs a new locked "
                    "period before final evaluation."
                ),
            },
            "guardrails": {
                "validation_or_locked_data_used_for_hypothesis_selection": False,
                "baseline_changed": False,
                "optimization_performed": False,
                "production_promotion_requested": False,
            },
        }
