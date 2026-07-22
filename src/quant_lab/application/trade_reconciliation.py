from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quant_lab.domain.price_structure import (
    ConfirmedPivot,
    classify_structure,
    confirmed_pivots_at,
)


@dataclass(frozen=True, slots=True)
class TradeReconciliationResult:
    summary: Mapping[str, Any]
    samples: tuple[Mapping[str, Any], ...]


def _iso(value: Any) -> str:
    return pd.Timestamp(value).tz_convert("UTC").isoformat()


class BaselineTradeReconciler:
    """Independent transaction-level checks for a completed baseline run."""

    def reconcile(
        self,
        *,
        bars: pd.DataFrame,
        trades: pd.DataFrame,
        fee_per_side: float,
        slippage_bps_per_side: float,
        train_end: str,
        validation_end: str,
        sample_size: int = 20,
    ) -> TradeReconciliationResult:
        ordered_bars = bars.copy()
        ordered_bars["timestamp"] = pd.to_datetime(ordered_bars["timestamp"], utc=True)
        ordered_bars = ordered_bars.sort_values("timestamp").drop_duplicates("timestamp")
        ordered_bars = ordered_bars.reset_index(drop=True)
        ordered_trades = trades.copy().sort_values(["entry_time", "trade_id"])
        ordered_trades["entry_time"] = pd.to_datetime(ordered_trades["entry_time"], utc=True)
        ordered_trades["exit_time"] = pd.to_datetime(ordered_trades["exit_time"], utc=True)

        events = self._structure_events(ordered_bars)
        event_by_entry = {event["entry_time"]: event for event in events}
        bar_by_time = ordered_bars.set_index("timestamp")
        slippage = slippage_bps_per_side / 10_000.0

        audited: list[dict[str, Any]] = []
        entry_mismatches = 0
        invalid_exit_fills = 0
        fee_math_mismatches = 0
        slippage_mismatches = 0

        for trade in ordered_trades.to_dict("records"):
            entry_time = pd.Timestamp(trade["entry_time"])
            exit_time = pd.Timestamp(trade["exit_time"])
            event = event_by_entry.get(entry_time)
            expected_side = event["side"] if event else None
            entry_ok = expected_side == trade["side"]
            entry_mismatches += int(not entry_ok)

            exit_bar = bar_by_time.loc[exit_time]
            exit_ok, exit_mode = self._validate_exit(trade, exit_bar, event_by_entry)
            invalid_exit_fills += int(not exit_ok)

            direction = 1 if trade["side"] == "long" else -1
            expected_entry_execution = float(trade["entry_raw_price"]) * (
                1 + direction * slippage
            )
            expected_exit_execution = float(trade["exit_raw_price"]) * (
                1 - direction * slippage
            )
            slippage_ok = bool(
                np.isclose(trade["entry_execution_price"], expected_entry_execution)
                and np.isclose(trade["exit_execution_price"], expected_exit_execution)
            )
            slippage_mismatches += int(not slippage_ok)

            quantity = float(trade["entry_equity"]) / float(
                trade["entry_execution_price"]
            )
            entry_fee = float(trade["entry_equity"]) * fee_per_side
            exit_fee = abs(quantity * float(trade["exit_execution_price"])) * fee_per_side
            price_pnl = (
                direction
                * quantity
                * (
                    float(trade["exit_execution_price"])
                    - float(trade["entry_execution_price"])
                )
            )
            expected_exit_equity = (
                float(trade["entry_equity"])
                - entry_fee
                + price_pnl
                + float(trade["funding_pnl"])
                - exit_fee
            )
            fee_math_ok = bool(
                np.isclose(trade["fees"], entry_fee + exit_fee, rtol=0, atol=1e-12)
                and np.isclose(
                    trade["exit_equity"], expected_exit_equity, rtol=0, atol=1e-12
                )
            )
            fee_math_mismatches += int(not fee_math_ok)

            audited.append(
                {
                    **trade,
                    "split": self._split(entry_time, train_end, validation_end),
                    "signal_event": event,
                    "entry_signal_ok": entry_ok,
                    "exit_fill_ok": exit_ok,
                    "exit_fill_mode": exit_mode,
                    "slippage_ok": slippage_ok,
                    "fee_math_ok": fee_math_ok,
                }
            )

        overlaps = int(
            (
                ordered_trades["entry_time"].iloc[1:].reset_index(drop=True)
                < ordered_trades["exit_time"].iloc[:-1].reset_index(drop=True)
            ).sum()
        )
        samples = self._select_samples(audited, sample_size)
        summary = {
            "trades_checked": len(audited),
            "structure_events": len(events),
            "entry_signal_mismatches": entry_mismatches,
            "position_overlaps": overlaps,
            "invalid_exit_fills": invalid_exit_fills,
            "slippage_mismatches": slippage_mismatches,
            "fee_math_mismatches": fee_math_mismatches,
            "same_bar_round_trips": int(
                (ordered_trades["entry_time"] == ordered_trades["exit_time"]).sum()
            ),
            "gap_stop_fills": sum(
                item["exit_fill_mode"] == "gap_open" for item in audited
            ),
            "sample_size": len(samples),
            "all_checks_passed": all(
                value == 0
                for value in (
                    entry_mismatches,
                    overlaps,
                    invalid_exit_fills,
                    slippage_mismatches,
                    fee_math_mismatches,
                )
            ),
            "baseline_changed": False,
            "optimization_performed": False,
        }
        return TradeReconciliationResult(summary=summary, samples=tuple(samples))

    @staticmethod
    def _structure_events(bars: pd.DataFrame) -> list[dict[str, Any]]:
        highs = bars["high"].to_numpy(dtype=float)
        lows = bars["low"].to_numpy(dtype=float)
        confirmed_highs: list[ConfirmedPivot] = []
        confirmed_lows: list[ConfirmedPivot] = []
        previous_state = "neutral"
        events: list[dict[str, Any]] = []

        for index, row in bars.iterrows():
            newly_confirmed = confirmed_pivots_at(highs, lows, index)
            for pivot in newly_confirmed:
                if pivot.kind == "high":
                    confirmed_highs.append(pivot)
                else:
                    confirmed_lows.append(pivot)
            state = classify_structure(confirmed_highs, confirmed_lows)
            if state != previous_state and state in {"bull", "bear"} and index + 1 < len(bars):
                events.append(
                    {
                        "side": "long" if state == "bull" else "short",
                        "from_structure": previous_state,
                        "to_structure": state,
                        "confirmation_bar_time": bars.iloc[index]["timestamp"],
                        "confirmation_available_at": bars.iloc[index + 1]["timestamp"],
                        "entry_time": bars.iloc[index + 1]["timestamp"],
                        "trigger_pivots": [
                            {
                                "kind": pivot.kind,
                                "pivot_time": bars.iloc[pivot.pivot_index]["timestamp"],
                                "confirmation_time": bars.iloc[pivot.confirmation_index][
                                    "timestamp"
                                ],
                                "price": pivot.price,
                                "right_bar_lag": (
                                    pivot.confirmation_index - pivot.pivot_index
                                ),
                            }
                            for pivot in newly_confirmed
                        ],
                        "previous_high": confirmed_highs[-2].price,
                        "latest_high": confirmed_highs[-1].price,
                        "previous_low": confirmed_lows[-2].price,
                        "latest_low": confirmed_lows[-1].price,
                    }
                )
            previous_state = state
        return events

    @staticmethod
    def _validate_exit(
        trade: Mapping[str, Any],
        exit_bar: pd.Series,
        event_by_entry: Mapping[pd.Timestamp, Mapping[str, Any]],
    ) -> tuple[bool, str]:
        reason = trade["exit_reason"]
        if reason == "protective_stop":
            stop = float(trade["stop_price_at_exit"])
            opened = float(exit_bar["open"])
            if trade["side"] == "long":
                expected = opened if opened <= stop else stop
            else:
                expected = opened if opened >= stop else stop
            mode = "gap_open" if expected == opened and opened != stop else "stop_level"
            return bool(np.isclose(trade["exit_raw_price"], expected)), mode
        if reason == "structure_reversal":
            event = event_by_entry.get(pd.Timestamp(trade["exit_time"]))
            expected_side = "short" if trade["side"] == "long" else "long"
            return bool(event is not None and event["side"] == expected_side), "next_open"
        if reason == "end_of_data":
            return bool(np.isclose(trade["exit_raw_price"], exit_bar["close"])), "final_close"
        return False, "unknown"

    @staticmethod
    def _split(entry_time: pd.Timestamp, train_end: str, validation_end: str) -> str:
        if entry_time < pd.Timestamp(train_end):
            return "train"
        if entry_time < pd.Timestamp(validation_end):
            return "validation"
        return "locked_test"

    @staticmethod
    def _select_samples(
        audited: list[dict[str, Any]], sample_size: int
    ) -> list[dict[str, Any]]:
        selected: dict[int, dict[str, Any]] = {}

        def add(items: list[dict[str, Any]], category: str, limit: int) -> None:
            for item in items:
                if len(selected) >= sample_size:
                    return
                trade_id = int(item["trade_id"])
                if trade_id in selected:
                    continue
                selected[trade_id] = {**item, "selection_category": category}
                if sum(
                    value["selection_category"] == category
                    for value in selected.values()
                ) >= limit:
                    break

        add(
            [item for item in audited if item["exit_reason"] == "structure_reversal"],
            "structure_reversal",
            5,
        )
        add(
            [item for item in audited if item["exit_fill_mode"] == "gap_open"],
            "gap_stop",
            4,
        )
        add(
            [item for item in audited if item["entry_time"] == item["exit_time"]],
            "same_bar_stop",
            4,
        )
        add(sorted(audited, key=lambda item: item["net_return"], reverse=True), "best_trade", 2)
        add(sorted(audited, key=lambda item: item["net_return"]), "worst_trade", 2)
        add(
            [item for item in audited if item["split"] == "locked_test"],
            "locked_test_trace",
            3,
        )
        if len(selected) < sample_size:
            indexes = np.linspace(0, len(audited) - 1, sample_size, dtype=int)
            add([audited[index] for index in indexes], "timeline_fill", sample_size)
        return list(selected.values())[:sample_size]


def reconciliation_json(result: TradeReconciliationResult) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for item in result.samples:
        event = item["signal_event"]
        samples.append(
            {
                "trade_id": item["trade_id"],
                "selection_category": item["selection_category"],
                "split": item["split"],
                "side": item["side"],
                "entry_time": _iso(item["entry_time"]),
                "exit_time": _iso(item["exit_time"]),
                "exit_reason": item["exit_reason"],
                "entry_raw_price": item["entry_raw_price"],
                "exit_raw_price": item["exit_raw_price"],
                "stop_price_at_exit": item["stop_price_at_exit"],
                "net_return": item["net_return"],
                "funding_pnl": item["funding_pnl"],
                "signal_event": {
                    **event,
                    "confirmation_bar_time": _iso(event["confirmation_bar_time"]),
                    "confirmation_available_at": _iso(
                        event["confirmation_available_at"]
                    ),
                    "entry_time": _iso(event["entry_time"]),
                    "trigger_pivots": [
                        {
                            **pivot,
                            "pivot_time": _iso(pivot["pivot_time"]),
                            "confirmation_time": _iso(pivot["confirmation_time"]),
                        }
                        for pivot in event["trigger_pivots"]
                    ],
                },
                "checks": {
                    "entry_signal_ok": item["entry_signal_ok"],
                    "exit_fill_ok": item["exit_fill_ok"],
                    "exit_fill_mode": item["exit_fill_mode"],
                    "slippage_ok": item["slippage_ok"],
                    "fee_math_ok": item["fee_math_ok"],
                },
            }
        )
    return {"summary": dict(result.summary), "samples": samples}
