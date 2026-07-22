from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


StructureState = Literal["bull", "bear", "neutral"]


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    kind: Literal["high", "low"]
    pivot_index: int
    confirmation_index: int
    price: float


def confirmed_pivots_at(
    highs: Sequence[float],
    lows: Sequence[float],
    confirmation_index: int,
    *,
    left_bars: int = 2,
    right_bars: int = 2,
) -> tuple[ConfirmedPivot, ...]:
    """Return pivots that become knowable at one completed bar.

    The candidate pivot is ``right_bars`` behind the confirmation bar. Strict
    comparisons deliberately reject equal highs/lows. No value to the right of
    ``confirmation_index`` is read.
    """

    if left_bars < 1 or right_bars < 1:
        raise ValueError("pivot confirmation requires at least one bar per side")
    if len(highs) != len(lows):
        raise ValueError("high and low series must have equal length")

    pivot_index = confirmation_index - right_bars
    left_index = pivot_index - left_bars
    right_index = pivot_index + right_bars
    if left_index < 0 or right_index >= len(highs):
        return ()

    pivot_high = float(highs[pivot_index])
    pivot_low = float(lows[pivot_index])
    neighbor_indexes = range(left_index, right_index + 1)
    is_high = all(
        pivot_high > float(highs[index])
        for index in neighbor_indexes
        if index != pivot_index
    )
    is_low = all(
        pivot_low < float(lows[index])
        for index in neighbor_indexes
        if index != pivot_index
    )

    pivots: list[ConfirmedPivot] = []
    if is_high:
        pivots.append(
            ConfirmedPivot(
                kind="high",
                pivot_index=pivot_index,
                confirmation_index=confirmation_index,
                price=pivot_high,
            )
        )
    if is_low:
        pivots.append(
            ConfirmedPivot(
                kind="low",
                pivot_index=pivot_index,
                confirmation_index=confirmation_index,
                price=pivot_low,
            )
        )
    return tuple(pivots)


def classify_structure(
    confirmed_highs: Sequence[ConfirmedPivot],
    confirmed_lows: Sequence[ConfirmedPivot],
) -> StructureState:
    """Classify the latest two confirmed swing highs and lows."""

    if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
        return "neutral"
    previous_high, latest_high = confirmed_highs[-2:]
    previous_low, latest_low = confirmed_lows[-2:]
    if latest_high.price > previous_high.price and latest_low.price > previous_low.price:
        return "bull"
    if latest_high.price < previous_high.price and latest_low.price < previous_low.price:
        return "bear"
    return "neutral"
