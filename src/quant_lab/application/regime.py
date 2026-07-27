from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True, slots=True)
class RegimeDetectorConfig:
    detector_id: str
    detector_version: str
    market_profile: str
    timeframe: str
    decision_lag_bars: int
    fast_ema_bars: int
    slow_ema_bars: int
    realized_vol_window_bars: int
    annualization_bars: int
    trend_spread_bps: float
    low_vol_annualized: float
    high_vol_annualized: float
    minimum_history_days_for_screening: int
    minimum_history_days_for_extended_validation: int
    minimum_trades_per_regime: int
    transition_policy: Mapping[str, Any]


class RegimeDetectorCatalog:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def load(self, artifact_key: str) -> RegimeDetectorConfig:
        path = (self.root / artifact_key).resolve()
        path.relative_to(self.root)
        if not artifact_key.startswith("configs/regimes/") or path.suffix != ".yaml":
            raise ValueError("regime detector must be a project configs/regimes YAML")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid regime detector configuration")
        if raw["input"].get("require_complete_bars") is not True:
            raise ValueError("regime detector must require complete bars")
        lag = int(raw["input"].get("decision_lag_bars", 0))
        if lag < 1:
            raise ValueError("regime labels must become effective after a closed bar")
        return RegimeDetectorConfig(
            detector_id=str(raw["detector_id"]),
            detector_version=str(raw["detector_version"]),
            market_profile=str(raw["market_profile"]),
            timeframe=str(raw["input"]["timeframe"]),
            decision_lag_bars=lag,
            fast_ema_bars=int(raw["features"]["fast_ema_bars"]),
            slow_ema_bars=int(raw["features"]["slow_ema_bars"]),
            realized_vol_window_bars=int(
                raw["features"]["realized_vol_window_bars"]
            ),
            annualization_bars=int(raw["features"]["annualization_bars"]),
            trend_spread_bps=float(raw["thresholds"]["trend_spread_bps"]),
            low_vol_annualized=float(raw["thresholds"]["low_vol_annualized"]),
            high_vol_annualized=float(raw["thresholds"]["high_vol_annualized"]),
            minimum_history_days_for_screening=int(
                raw["validation"]["minimum_history_days_for_screening"]
            ),
            minimum_history_days_for_extended_validation=int(
                raw["validation"]["minimum_history_days_for_extended_validation"]
            ),
            minimum_trades_per_regime=int(
                raw["validation"]["minimum_trades_per_regime"]
            ),
            transition_policy=dict(raw["transition_policy"]),
        )


class ExAnteRegimeDetector:
    """Trend/volatility detector whose label is delayed until the next complete bar."""

    def __init__(self, config: RegimeDetectorConfig) -> None:
        self.config = config

    def detect(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp", "close"}
        if not required.issubset(bars.columns):
            raise ValueError("regime bars require timestamp and close")
        frame = bars.loc[:, ["timestamp", "close"]].copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp").drop_duplicates("timestamp")
        if frame.empty:
            raise ValueError("regime detector received no bars")
        close = frame["close"].astype(float)
        returns = close.pct_change()
        fast = close.ewm(span=self.config.fast_ema_bars, adjust=False).mean()
        slow = close.ewm(span=self.config.slow_ema_bars, adjust=False).mean()
        spread_bps = (fast / slow - 1.0) * 10_000
        realized_vol = (
            returns.rolling(
                self.config.realized_vol_window_bars,
                min_periods=self.config.realized_vol_window_bars,
            ).std()
            * np.sqrt(self.config.annualization_bars)
        )
        trend = np.select(
            [
                spread_bps >= self.config.trend_spread_bps,
                spread_bps <= -self.config.trend_spread_bps,
            ],
            ["trend_up", "trend_down"],
            default="trend_neutral",
        )
        volatility = np.select(
            [
                realized_vol >= self.config.high_vol_annualized,
                realized_vol <= self.config.low_vol_annualized,
            ],
            ["high_vol", "low_vol"],
            default="normal_vol",
        )
        ready = slow.notna() & realized_vol.notna()
        labels = pd.Series(
            np.where(ready, np.char.add(np.char.add(trend, "__"), volatility), "unknown"),
            index=frame.index,
        )
        interval = pd.Timedelta(self.config.timeframe)
        result = pd.DataFrame(
            {
                "source_bar_open_time": frame["timestamp"],
                "effective_from": frame["timestamp"]
                + interval * self.config.decision_lag_bars,
                "regime": labels,
                "trend_spread_bps": spread_bps,
                "realized_vol_annualized": realized_vol,
                "detector_version": self.config.detector_version,
                "ex_ante_observable": True,
            }
        )
        return result.reset_index(drop=True)


def summarize_trades_by_regime(
    trades: pd.DataFrame, labels: pd.DataFrame, *, minimum_trades: int
) -> tuple[dict[str, dict[str, float]], dict[str, tuple[str, ...]]]:
    required = {"entry_time", "net_return"}
    if not required.issubset(trades.columns):
        raise ValueError("trade evidence requires entry_time and net_return")
    trade_frame = trades.copy()
    trade_frame["entry_time"] = pd.to_datetime(
        trade_frame["entry_time"], utc=True
    ).astype("datetime64[ns, UTC]")
    label_frame = labels.loc[:, ["effective_from", "regime"]].copy()
    label_frame["effective_from"] = pd.to_datetime(
        label_frame["effective_from"], utc=True
    ).astype("datetime64[ns, UTC]")
    label_frame = label_frame.sort_values("effective_from")
    joined = pd.merge_asof(
        trade_frame.sort_values("entry_time"),
        label_frame,
        left_on="entry_time",
        right_on="effective_from",
        direction="backward",
    )
    joined["regime"] = joined["regime"].fillna("unknown")
    metrics: dict[str, dict[str, float]] = {}
    suitable: list[str] = []
    conditional: list[str] = []
    blocked: list[str] = []
    unknown: list[str] = []
    for regime, group in joined.groupby("regime", sort=True):
        values = group["net_return"].astype(float)
        gains = float(values[values > 0].sum())
        losses = abs(float(values[values < 0].sum()))
        profit_factor = gains / losses if losses else (999999.0 if gains else 0.0)
        curve = (1.0 + values).cumprod()
        drawdown = curve / curve.cummax() - 1.0
        item = {
            "trade_count": float(len(values)),
            "net_return": float((1.0 + values).prod() - 1.0),
            "profit_factor": float(profit_factor),
            "expectancy": float(values.mean()) if len(values) else 0.0,
            "max_drawdown_abs": abs(float(drawdown.min())) if len(values) else 0.0,
        }
        metrics[str(regime)] = item
        if regime == "unknown" or len(values) < minimum_trades:
            unknown.append(str(regime))
        elif item["net_return"] > 0 and item["profit_factor"] > 1 and item["expectancy"] > 0:
            suitable.append(str(regime))
        elif item["net_return"] < 0 and item["profit_factor"] < 1:
            blocked.append(str(regime))
        else:
            conditional.append(str(regime))
    return metrics, {
        "suitable": tuple(suitable),
        "conditional": tuple(conditional),
        "blocked": tuple(blocked),
        "unknown": tuple(unknown),
    }
