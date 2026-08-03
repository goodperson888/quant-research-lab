from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


SUPPORTED_INDICATORS = frozenset({"ema", "sma", "rsi", "atr", "bollinger"})
SUPPORTED_OPERATORS = frozenset(
    {"gt", "gte", "lt", "lte", "crosses_above", "crosses_below"}
)
SUPPORTED_TIMEFRAMES = frozenset({"15m", "1h", "4h"})


@dataclass(frozen=True, slots=True)
class StrategyDslCapability:
    executable: bool
    reasons: tuple[str, ...]
    normalized: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class StrategyDslResult:
    label: str
    start_utc_inclusive: str
    end_utc_exclusive: str
    metrics: Mapping[str, float]
    funding: Mapping[str, Any]
    execution: Mapping[str, Any]
    signal_funnel: Mapping[str, Any]
    signals: pd.DataFrame
    trades: pd.DataFrame
    equity: pd.DataFrame


def analyze_strategy_dsl(
    structured_content: Mapping[str, Any],
) -> StrategyDslCapability:
    raw = structured_content.get("strategy_dsl")
    if not isinstance(raw, Mapping):
        return StrategyDslCapability(
            executable=False,
            reasons=("缺少 strategy_dsl；当前策略只能形式化，不能自动执行。",),
            normalized=None,
        )
    reasons: list[str] = []
    if raw.get("schema_version") != 1:
        reasons.append("strategy_dsl.schema_version 必须为 1。")
    if raw.get("market_profile") != "crypto_perpetual.binance.eth":
        reasons.append("第一版只支持 crypto_perpetual.binance.eth。")
    if raw.get("execution_timeframe") != "15m":
        reasons.append("第一版只支持 15m 执行周期。")
    parameters = raw.get("parameters")
    if not isinstance(parameters, Mapping):
        reasons.append("strategy_dsl.parameters 必须是参数映射。")
        parameters = {}
    indicators = raw.get("indicators")
    if not isinstance(indicators, Sequence) or isinstance(indicators, (str, bytes)):
        reasons.append("strategy_dsl.indicators 必须是指标数组。")
        indicators = ()
    indicator_ids: set[str] = {"open", "high", "low", "close", "volume"}
    normalized_indicators: list[dict[str, Any]] = []
    for index, item in enumerate(indicators):
        if not isinstance(item, Mapping):
            reasons.append(f"第 {index + 1} 个指标不是对象。")
            continue
        indicator_id = str(item.get("id", "")).strip()
        indicator_type = str(item.get("type", "")).strip()
        timeframe = str(item.get("timeframe", "15m")).strip()
        if not indicator_id or indicator_id in indicator_ids:
            reasons.append(f"第 {index + 1} 个指标 id 缺失或重复。")
            continue
        if indicator_type not in SUPPORTED_INDICATORS:
            reasons.append(f"指标 {indicator_id} 的类型 {indicator_type or '空'} 暂不支持。")
        if timeframe not in SUPPORTED_TIMEFRAMES:
            reasons.append(f"指标 {indicator_id} 的周期 {timeframe or '空'} 暂不支持。")
        source = str(item.get("source", "close"))
        if source not in {"open", "high", "low", "close", "volume"}:
            reasons.append(f"指标 {indicator_id} 的 source 必须是 OHLCV 字段。")
        normalized_item = dict(item)
        normalized_item.update(
            {
                "id": indicator_id,
                "type": indicator_type,
                "timeframe": timeframe,
                "source": source,
            }
        )
        normalized_indicators.append(normalized_item)
        indicator_ids.add(indicator_id)
    entries = raw.get("entries")
    if not isinstance(entries, Mapping):
        reasons.append("strategy_dsl.entries 必须包含 long/short 条件。")
        entries = {}
    for side in ("long", "short"):
        condition = entries.get(side)
        if condition is None:
            continue
        _validate_condition(
            condition,
            path=f"entries.{side}",
            available_series=indicator_ids,
            parameters=parameters,
            reasons=reasons,
        )
    if entries.get("long") is None and entries.get("short") is None:
        reasons.append("至少需要一个 long 或 short 入场条件。")
    exits = raw.get("exits")
    if not isinstance(exits, Mapping):
        reasons.append("strategy_dsl.exits 必须是退出规则对象。")
        exits = {}
    for name in ("stop_loss_fraction", "take_profit_fraction", "max_holding_bars"):
        if name not in exits:
            reasons.append(f"strategy_dsl.exits 缺少 {name}。")
        else:
            _resolve_number(exits[name], parameters, f"exits.{name}", reasons)
    risk = raw.get("risk")
    if not isinstance(risk, Mapping):
        reasons.append("strategy_dsl.risk 必须是风险规则对象。")
        risk = {}
    risk_fraction = _resolve_number(
        risk.get("risk_per_trade_fraction", 0.005),
        parameters,
        "risk.risk_per_trade_fraction",
        reasons,
    )
    leverage = _resolve_number(
        risk.get("leverage", 1.0), parameters, "risk.leverage", reasons
    )
    if risk_fraction is not None and not 0 < risk_fraction <= 0.02:
        reasons.append("每笔风险必须在 (0, 2%] 内。")
    if leverage is not None and leverage != 1.0:
        reasons.append("通用 DSL 第一版只允许 1 倍杠杆。")
    normalized = {
        **dict(raw),
        "schema_version": 1,
        "market_profile": "crypto_perpetual.binance.eth",
        "execution_timeframe": "15m",
        "parameters": dict(parameters),
        "indicators": normalized_indicators,
        "entries": dict(entries),
        "exits": dict(exits),
        "risk": dict(risk),
    }
    return StrategyDslCapability(
        executable=not reasons,
        reasons=tuple(reasons),
        normalized=normalized if not reasons else None,
    )


def attach_execution_readiness(
    structured_content: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(structured_content)
    capability = analyze_strategy_dsl(result)
    result["execution_readiness"] = {
        "status": "executable" if capability.executable else "unsupported",
        "engine": "generic_strategy_dsl_v1" if capability.executable else None,
        "reasons": list(capability.reasons),
    }
    if capability.executable and capability.normalized is not None:
        result["strategy_spec_id"] = "generic_strategy_dsl_v1"
        result["strategy_dsl"] = dict(capability.normalized)
    return result


def _validate_condition(
    condition: Any,
    *,
    path: str,
    available_series: set[str],
    parameters: Mapping[str, Any],
    reasons: list[str],
) -> None:
    if not isinstance(condition, Mapping):
        reasons.append(f"{path} 必须是条件对象。")
        return
    logic = condition.get("logic")
    if logic in {"all", "any"}:
        children = condition.get("conditions")
        if (
            not isinstance(children, Sequence)
            or isinstance(children, (str, bytes))
            or not children
        ):
            reasons.append(f"{path}.conditions 必须是非空数组。")
            return
        for index, child in enumerate(children):
            _validate_condition(
                child,
                path=f"{path}.conditions[{index}]",
                available_series=available_series,
                parameters=parameters,
                reasons=reasons,
            )
        return
    operator = str(condition.get("operator", "")).strip()
    if operator not in SUPPORTED_OPERATORS:
        reasons.append(f"{path}.operator={operator or '空'} 暂不支持。")
    left = condition.get("left")
    if not isinstance(left, str) or left not in available_series:
        reasons.append(f"{path}.left 必须引用已定义指标或 OHLCV 字段。")
    right = condition.get("right")
    if isinstance(right, str) and not right.startswith("$"):
        if right not in available_series:
            reasons.append(f"{path}.right 引用了未知序列 {right}。")
    else:
        _resolve_number(right, parameters, f"{path}.right", reasons)


def _resolve_number(
    value: Any,
    parameters: Mapping[str, Any],
    path: str,
    reasons: list[str],
) -> float | None:
    if isinstance(value, (int, float)) and np.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and value.startswith("$"):
        parameter = value[1:]
        resolved = parameters.get(parameter)
        if isinstance(resolved, (int, float)) and np.isfinite(float(resolved)):
            return float(resolved)
        reasons.append(f"{path} 引用了无效参数 {value}。")
        return None
    reasons.append(f"{path} 必须是数字或 $参数引用。")
    return None


class GenericStrategyDslBacktester:
    """Bounded deterministic 15m event backtester for the reviewed DSL subset."""

    def __init__(
        self,
        *,
        strategy_version_id: str,
        dsl: Mapping[str, Any],
        fee_per_side: float,
        slippage_bps_per_side: float,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        merged_parameters = dict(dsl.get("parameters", {}))
        merged_parameters.update(dict(parameters or {}))
        materialized = _materialize(dict(dsl), merged_parameters)
        capability = analyze_strategy_dsl({"strategy_dsl": materialized})
        if not capability.executable or capability.normalized is None:
            raise ValueError(
                "strategy DSL is not executable: " + "；".join(capability.reasons)
            )
        self.strategy_version_id = strategy_version_id
        self.dsl = dict(capability.normalized)
        self.parameters = merged_parameters
        self.fee_per_side = float(fee_per_side)
        self.slippage_fraction = float(slippage_bps_per_side) / 10_000.0

    def run(
        self,
        *,
        label: str,
        ohlcv_by_timeframe: Mapping[str, pd.DataFrame],
        funding: pd.DataFrame,
        warmup_start_utc_inclusive: str,
        start_utc_inclusive: str,
        end_utc_exclusive: str,
    ) -> StrategyDslResult:
        prepared = self._prepare_features(ohlcv_by_timeframe)
        warmup = pd.Timestamp(warmup_start_utc_inclusive)
        start = pd.Timestamp(start_utc_inclusive)
        end = pd.Timestamp(end_utc_exclusive)
        bars = prepared.loc[
            (prepared.index >= warmup) & (prepared.index < end)
        ].copy()
        if bars.empty or not (bars.index >= start).any():
            raise ValueError("strategy DSL backtest slice has no usable bars")
        long_signal = self._condition_series(self.dsl["entries"].get("long"), bars)
        short_signal = self._condition_series(self.dsl["entries"].get("short"), bars)
        funding_series = _prepare_funding(funding, start=start, end=end)
        return self._simulate(
            label=label,
            bars=bars,
            long_signal=long_signal,
            short_signal=short_signal,
            funding=funding_series,
            start=start,
            end=end,
        )

    def _prepare_features(
        self, ohlcv_by_timeframe: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        execution = _prepare_ohlcv(ohlcv_by_timeframe["15m"])
        features = execution.copy()
        by_timeframe: dict[str, pd.DataFrame] = {"15m": execution}
        for timeframe in sorted(
            {str(item["timeframe"]) for item in self.dsl["indicators"]}
        ):
            if timeframe not in by_timeframe:
                by_timeframe[timeframe] = _prepare_ohlcv(
                    ohlcv_by_timeframe[timeframe]
                )
        for indicator in self.dsl["indicators"]:
            timeframe = str(indicator["timeframe"])
            source_frame = by_timeframe[timeframe]
            series = _indicator_series(indicator, source_frame)
            if timeframe == "15m":
                features[str(indicator["id"])] = series.reindex(features.index)
                continue
            safe_series = series.shift(1)
            aligned = pd.merge_asof(
                features.reset_index()[["timestamp"]],
                safe_series.rename(str(indicator["id"])).reset_index(),
                on="timestamp",
                direction="backward",
            ).set_index("timestamp")
            features[str(indicator["id"])] = aligned[str(indicator["id"])]
        return features

    def _condition_series(
        self, condition: Any, frame: pd.DataFrame
    ) -> pd.Series:
        if condition is None:
            return pd.Series(False, index=frame.index)
        logic = condition.get("logic")
        if logic in {"all", "any"}:
            children = [
                self._condition_series(item, frame)
                for item in condition["conditions"]
            ]
            combined = children[0]
            for child in children[1:]:
                combined = combined & child if logic == "all" else combined | child
            return combined.fillna(False)
        left = frame[str(condition["left"])].astype(float)
        right_raw = condition["right"]
        right = (
            frame[str(right_raw)].astype(float)
            if isinstance(right_raw, str)
            else pd.Series(float(right_raw), index=frame.index)
        )
        operator = condition["operator"]
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
        if operator == "crosses_above":
            return (left > right) & (left.shift(1) <= right.shift(1))
        return (left < right) & (left.shift(1) >= right.shift(1))

    def _simulate(
        self,
        *,
        label: str,
        bars: pd.DataFrame,
        long_signal: pd.Series,
        short_signal: pd.Series,
        funding: pd.Series,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> StrategyDslResult:
        stop_fraction = float(self.dsl["exits"]["stop_loss_fraction"])
        take_fraction = float(self.dsl["exits"]["take_profit_fraction"])
        max_holding = int(self.dsl["exits"]["max_holding_bars"])
        if not 0 < stop_fraction <= 0.1 or not 0 < take_fraction <= 0.5:
            raise ValueError("strategy DSL stop/take fractions are outside safe limits")
        if not 1 <= max_holding <= 10_000:
            raise ValueError("strategy DSL max_holding_bars is outside safe limits")
        risk_fraction = float(
            self.dsl["risk"].get("risk_per_trade_fraction", 0.005)
        )
        notional_fraction = min(1.0, risk_fraction / stop_fraction)
        equity = 1.0
        position: dict[str, Any] | None = None
        pending_side: str | None = None
        trade_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        signal_rows: list[dict[str, Any]] = []
        funding_applied = 0
        signal_count = 0
        for timestamp, row in bars.iterrows():
            trading_enabled = timestamp >= start
            if position is None and pending_side is not None and trading_enabled:
                side_sign = 1.0 if pending_side == "long" else -1.0
                raw_entry = float(row["open"])
                entry_price = raw_entry * (
                    1.0 + side_sign * self.slippage_fraction
                )
                position = {
                    "trade_id": (
                        f"{self.strategy_version_id}-{label}-{len(trade_rows) + 1}"
                    ),
                    "side": pending_side,
                    "side_sign": side_sign,
                    "entry_time": timestamp,
                    "entry_raw_price": raw_entry,
                    "entry_price": entry_price,
                    "entry_equity": equity,
                    "bars": 0,
                    "funding_return": 0.0,
                    "stop_price": entry_price
                    * (1.0 - side_sign * stop_fraction),
                    "take_price": entry_price
                    * (1.0 + side_sign * take_fraction),
                }
                pending_side = None
            if position is not None:
                position["bars"] += 1
                seen_after = position.get(
                    "last_funding_time", position["entry_time"]
                )
                new_events = funding.loc[
                    (funding.index > seen_after) & (funding.index <= timestamp)
                ]
                for event_time, rate in new_events.items():
                    position["funding_return"] += (
                        -position["side_sign"]
                        * float(rate)
                        * notional_fraction
                    )
                    position["last_funding_time"] = event_time
                    funding_applied += 1
                stop_hit = (
                    float(row["low"]) <= position["stop_price"]
                    if position["side"] == "long"
                    else float(row["high"]) >= position["stop_price"]
                )
                take_hit = (
                    float(row["high"]) >= position["take_price"]
                    if position["side"] == "long"
                    else float(row["low"]) <= position["take_price"]
                )
                exit_reason: str | None = None
                raw_exit: float | None = None
                if stop_hit:
                    exit_reason = "protective_stop"
                    raw_exit = position["stop_price"]
                elif take_hit:
                    exit_reason = "take_profit"
                    raw_exit = position["take_price"]
                elif position["bars"] >= max_holding:
                    exit_reason = "max_holding_time"
                    raw_exit = float(row["close"])
                if exit_reason is not None and raw_exit is not None:
                    exit_price = raw_exit * (
                        1.0 - position["side_sign"] * self.slippage_fraction
                    )
                    price_return = (
                        position["side_sign"]
                        * (exit_price / position["entry_price"] - 1.0)
                        * notional_fraction
                    )
                    fees_return = 2.0 * self.fee_per_side * notional_fraction
                    net_return = (
                        price_return
                        + position["funding_return"]
                        - fees_return
                    )
                    entry_equity = float(position["entry_equity"])
                    price_pnl = entry_equity * price_return
                    funding_pnl = entry_equity * position["funding_return"]
                    fees = entry_equity * fees_return
                    net_pnl = entry_equity * net_return
                    equity *= 1.0 + net_return
                    trade_rows.append(
                        {
                            "trade_id": position["trade_id"],
                            "strategy_version_id": self.strategy_version_id,
                            "side": position["side"],
                            "entry_time": position["entry_time"],
                            "exit_time": timestamp,
                            "entry_raw_price": position["entry_raw_price"],
                            "entry_price": position["entry_price"],
                            "initial_stop_price": position["stop_price"],
                            "take_profit_price": position["take_price"],
                            "exit_price": exit_price,
                            "exit_reason": exit_reason,
                            "fees": fees,
                            "price_pnl": price_pnl,
                            "funding_pnl": funding_pnl,
                            "net_pnl": net_pnl,
                            "net_return": net_return,
                            "net_return_on_entry_equity": net_return,
                            "holding_minutes": (
                                timestamp - position["entry_time"]
                            ).total_seconds()
                            / 60.0,
                            "is_reentry": False,
                            "split": label,
                        }
                    )
                    position = None
            if position is None and trading_enabled:
                is_long = bool(long_signal.loc[timestamp])
                is_short = bool(short_signal.loc[timestamp])
                if is_long != is_short:
                    pending_side = "long" if is_long else "short"
                    signal_count += 1
                    signal_rows.append(
                        {
                            "signal_time": timestamp,
                            "split": label,
                            "side": pending_side,
                            "stage": "entry_candidate",
                            "status": "accepted",
                            "reason": "dsl_entry_condition",
                        }
                    )
            marked_equity = equity
            if position is not None:
                marked_equity = equity * (
                    1.0
                    + position["side_sign"]
                    * (
                        float(row["close"]) / position["entry_price"] - 1.0
                    )
                    * notional_fraction
                    + position["funding_return"]
                    - self.fee_per_side * notional_fraction
                )
            if trading_enabled:
                equity_rows.append(
                    {
                        "timestamp": timestamp,
                        "equity": marked_equity,
                        "split": label,
                    }
                )
        trades = pd.DataFrame(trade_rows)
        signals = pd.DataFrame(signal_rows)
        curve = pd.DataFrame(equity_rows)
        metrics = _strategy_metrics(trades, curve)
        return StrategyDslResult(
            label=label,
            start_utc_inclusive=start.isoformat(),
            end_utc_exclusive=end.isoformat(),
            metrics=metrics,
            funding={
                "observed_events_applied_while_position_open": funding_applied,
                "missing_policy": "adverse_p99_abs_observed",
                "zero_funding_fallback_used": False,
            },
            execution={
                "engine": "generic_strategy_dsl_v1",
                "entry_timing": "next_bar_open",
                "leverage": 1.0,
                "notional_fraction": notional_fraction,
                "stop_precedes_take_profit_same_bar": True,
            },
            signal_funnel={
                "entry_candidates": signal_count,
                "filled_entries": len(trades),
                "filter_or_cancel_reasons": {},
            },
            signals=signals,
            trades=trades,
            equity=curve,
        )


def _materialize(value: Any, parameters: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        if name not in parameters:
            raise ValueError(f"strategy DSL parameter is missing: {name}")
        return parameters[name]
    if isinstance(value, Mapping):
        return {key: _materialize(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item, parameters) for item in value]
    return value


def _prepare_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            "strategy DSL OHLCV missing: " + ", ".join(sorted(missing))
        )
    result = frame.loc[:, sorted(required)].copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return (
        result.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .set_index("timestamp")
        .astype(float)
    )


def _prepare_funding(
    frame: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp
) -> pd.Series:
    required = {"timestamp", "last_funding_rate"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            "strategy DSL funding missing: " + ", ".join(sorted(missing))
        )
    result = frame.loc[:, ["timestamp", "last_funding_rate"]].copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    series = (
        result.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .set_index("timestamp")["last_funding_rate"]
        .astype(float)
    )
    return series.loc[(series.index >= start) & (series.index < end)]


def _indicator_series(
    indicator: Mapping[str, Any], frame: pd.DataFrame
) -> pd.Series:
    source = frame[str(indicator.get("source", "close"))].astype(float)
    kind = str(indicator["type"])
    period = int(indicator.get("period", 14))
    if period < 2 or period > 500:
        raise ValueError(
            f"indicator {indicator['id']} period must be between 2 and 500"
        )
    if kind == "ema":
        return source.ewm(span=period, adjust=False, min_periods=period).mean()
    if kind == "sma":
        return source.rolling(period, min_periods=period).mean()
    if kind == "rsi":
        delta = source.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        losses = (-delta.clip(upper=0)).ewm(
            alpha=1 / period, adjust=False
        ).mean()
        rs = gains / losses.replace(0, np.nan)
        return 100.0 - 100.0 / (1.0 + rs)
    if kind == "atr":
        previous_close = frame["close"].shift(1)
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(alpha=1 / period, adjust=False).mean()
    middle = source.rolling(period, min_periods=period).mean()
    stddev = source.rolling(period, min_periods=period).std(ddof=0)
    band = str(indicator.get("band", "middle"))
    multiplier = float(indicator.get("stddev", 2.0))
    if band == "upper":
        return middle + multiplier * stddev
    if band == "lower":
        return middle - multiplier * stddev
    return middle


def _strategy_metrics(
    trades: pd.DataFrame, equity: pd.DataFrame
) -> dict[str, float]:
    if equity.empty:
        raise ValueError("strategy DSL equity curve is empty")
    curve = equity["equity"].astype(float)
    drawdown = curve / curve.cummax() - 1.0
    returns = curve.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    trade_returns = (
        trades["net_return"].astype(float)
        if not trades.empty
        else pd.Series(dtype=float)
    )
    gross_profit = float(trade_returns[trade_returns > 0].sum())
    gross_loss = abs(float(trade_returns[trade_returns < 0].sum()))
    return {
        "starting_equity": 1.0,
        "ending_equity": float(curve.iloc[-1]),
        "total_return": float(curve.iloc[-1] - 1.0),
        "max_drawdown": float(drawdown.min()),
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss
            else (999999.0 if gross_profit else 0.0)
        ),
        "expectancy": (
            float(trade_returns.mean()) if len(trade_returns) else 0.0
        ),
        "trade_count": float(len(trades)),
        "win_rate": (
            float((trade_returns > 0).mean()) if len(trade_returns) else 0.0
        ),
        "average_holding_minutes": (
            float(trades["holding_minutes"].mean()) if not trades.empty else 0.0
        ),
        "sharpe_15m_annualized": (
            float(returns.mean() / volatility * sqrt(96 * 365))
            if volatility > 0
            else 0.0
        ),
    }
