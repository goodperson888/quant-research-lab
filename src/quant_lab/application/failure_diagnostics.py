from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quant_lab.domain.models import ComponentHypothesis

from .services import new_id, utc_now


def _profit_factor(values: pd.Series) -> float:
    gains = float(values[values > 0].sum())
    losses = abs(float(values[values < 0].sum()))
    return gains / losses if losses else (999999.0 if gains else 0.0)


def _group_metrics(frame: pd.DataFrame) -> dict[str, float]:
    values = frame["net_return"].astype(float)
    curve = (1.0 + values).cumprod()
    drawdown = curve / curve.cummax() - 1.0 if len(curve) else pd.Series(dtype=float)
    return {
        "trade_count": float(len(frame)),
        "net_return": float((1.0 + values).prod() - 1.0) if len(values) else 0.0,
        "profit_factor": float(_profit_factor(values)),
        "expectancy": float(values.mean()) if len(values) else 0.0,
        "win_rate": float((values > 0).mean()) if len(values) else 0.0,
        "max_drawdown_abs": abs(float(drawdown.min())) if len(drawdown) else 0.0,
    }


def _grouped(frame: pd.DataFrame, column: str) -> dict[str, dict[str, float]]:
    return {
        str(name): _group_metrics(group)
        for name, group in frame.groupby(column, observed=True, dropna=False)
    }


class ArtifactLossAttribution:
    """Explain saved trade/signal evidence without loading market data or rerunning."""

    def analyze(
        self,
        *,
        trades: pd.DataFrame,
        signals: pd.DataFrame,
        metrics: Mapping[str, Any],
    ) -> dict[str, Any]:
        if trades.empty:
            raise ValueError("loss attribution requires saved trades")
        frame = _normalize_trade_columns(trades)
        required = {
            "trade_id",
            "side",
            "entry_time",
            "exit_time",
            "entry_raw_price",
            "initial_stop_price",
            "exit_reason",
            "fees",
            "price_pnl",
            "funding_pnl",
            "net_pnl",
            "holding_minutes",
            "split",
        }
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError("saved trades are missing attribution fields: " + ", ".join(missing))
        return_column = (
            "net_return"
            if "net_return" in frame.columns
            else "net_return_on_entry_equity"
        )
        if return_column not in frame:
            raise ValueError("saved trades do not contain a net-return field")
        frame["net_return"] = frame[return_column].astype(float)
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        frame = frame.sort_values(["split", "entry_time", "trade_id"])
        frame["holding_bucket"] = pd.cut(
            frame["holding_minutes"].astype(float),
            bins=[-0.1, 15, 60, 240, float("inf")],
            labels=["up_to_15m", "15m_to_1h", "1h_to_4h", "over_4h"],
        ).astype(str)
        frame["entry_utc_session"] = pd.cut(
            frame["entry_time"].dt.hour,
            bins=[-1, 5, 11, 17, 23],
            labels=["00-05", "06-11", "12-17", "18-23"],
        ).astype(str)
        frame["entry_weekday"] = frame["entry_time"].dt.day_name()
        if "is_reentry" in frame:
            frame["entry_type"] = np.where(
                frame["is_reentry"], "reentry", "first_entry"
            )
            entry_type_availability = "recorded"
        else:
            frame["entry_type"] = "not_recorded"
            entry_type_availability = "not_recorded"
        frame["stop_distance_fraction"] = (
            (frame["entry_raw_price"] - frame["initial_stop_price"]).abs()
            / frame["entry_raw_price"].abs()
        )
        frame["stop_distance_bucket"] = pd.cut(
            frame["stop_distance_fraction"],
            bins=[-0.000001, 0.003, 0.006, 0.012, float("inf")],
            labels=["up_to_0_3pct", "0_3_to_0_6pct", "0_6_to_1_2pct", "over_1_2pct"],
        ).astype(str)

        split_reports: dict[str, Any] = {}
        for split, split_frame in frame.groupby("split", observed=True):
            gross_positive = float(
                split_frame.loc[split_frame["price_pnl"] > 0, "price_pnl"].sum()
            )
            total_fees = float(split_frame["fees"].sum())
            split_reports[str(split)] = {
                "summary": _group_metrics(split_frame),
                "by_side": _grouped(split_frame, "side"),
                "by_exit_reason": _grouped(split_frame, "exit_reason"),
                "by_holding_period": _grouped(split_frame, "holding_bucket"),
                "by_entry_utc_session": _grouped(split_frame, "entry_utc_session"),
                "by_entry_weekday": _grouped(split_frame, "entry_weekday"),
                "by_stop_distance": _grouped(split_frame, "stop_distance_bucket"),
                "by_first_entry_or_reentry": _grouped(split_frame, "entry_type"),
                "entry_type_availability": entry_type_availability,
                "costs": {
                    "fees": total_fees,
                    "gross_positive_price_pnl": gross_positive,
                    "fees_as_fraction_of_gross_positive_price_pnl": (
                        total_fees / gross_positive if gross_positive > 0 else None
                    ),
                    "funding_pnl": float(split_frame["funding_pnl"].sum()),
                    "price_pnl": float(split_frame["price_pnl"].sum()),
                    "net_pnl": float(split_frame["net_pnl"].sum()),
                },
                "streaks": _streak_summary(split_frame["net_return"]),
            }

        signal_frame = signals.copy()
        if not signal_frame.empty:
            signal_frame["signal_time"] = pd.to_datetime(
                signal_frame["signal_time"], utc=True
            )
        funnel = {
            split: _signal_funnel(
                trades=frame.loc[frame["split"] == split],
                signals=signal_frame.loc[signal_frame["split"] == split],
                split_metrics=_split_metric_context(metrics, split),
            )
            for split in sorted(frame["split"].astype(str).unique())
        }
        return {
            "analysis_type": "artifact_only_loss_attribution",
            "reran_strategy": False,
            "market_data_reloaded": False,
            "causal_claim_allowed": False,
            "validation_reused_for_hypothesis_generation": True,
            "independent_oos_claim_allowed": False,
            "splits": split_reports,
            "signal_funnel": funnel,
            "multi_timeframe": {
                "executed": True,
                "timeframes": ["5m", "15m", "1h"],
                "walk_forward_or_multi_period_executed": False,
            },
            "limitations": [
                "The report reuses saved trades, signals and metrics; it does not rerun the strategy.",
                "Grouped loss attribution is descriptive and cannot prove causality.",
                "Validation was already inspected and is therefore screening evidence for new hypotheses.",
                "Unavailable funnel or first-entry/reentry fields remain explicit and are not inferred.",
            ],
        }


class DeterministicComponentHypothesisGenerator:
    """Rank transparent diagnostic drafts without rerunning the strategy.

    Directions are scored by observed impact, sample support and how directly one
    isolated component can be changed.  Only one direction per component category
    survives, so a weak strategy does not receive three near-identical exit tweaks.
    """

    def generate(
        self,
        *,
        session_id: str,
        subject_id: str,
        attribution: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
    ) -> tuple[ComponentHypothesis, ...]:
        validation = attribution["splits"].get("validation", {})
        funnel = attribution.get("signal_funnel", {}).get("validation", {})
        directions: list[_DiagnosticDirection] = []

        filter_reasons = funnel.get("filter_or_cancel_reasons", {})
        if isinstance(filter_reasons, Mapping) and filter_reasons:
            reason, count = max(
                ((str(name), int(value)) for name, value in filter_reasons.items()),
                key=lambda item: item[1],
            )
            denominator = max(
                int(funnel.get("entry_candidates") or 0),
                int(funnel.get("trigger_records_5m") or 0),
                sum(int(value) for value in filter_reasons.values()),
                1,
            )
            ratio = count / denominator
            parameter_name, title, explanation = _filter_direction(reason)
            directions.append(
                _DiagnosticDirection(
                    category="filter",
                    score=_diagnostic_score(ratio, count, 0.95),
                    title=title,
                    hypothesis=(
                        f"验证样本中“{explanation}”拦截 {count} 次，"
                        "只调整这一项可判断入场筛选是否过严或是否在保护策略。"
                    ),
                    component_type="filter",
                    expected_improvement=(
                        "增加有效入场机会，同时检查新增交易是否只是放大费用和低质量信号。"
                    ),
                    parameter_space={parameter_name: ["current", "tighter", "looser"]},
                    failure_conditions=(
                        "交易数增加但验证净收益、盈亏效率和单笔期望未改善",
                        "新增交易主要集中在同一时段或少数异常样本",
                        "最大回撤或费用占比明显恶化",
                    ),
                )
            )

        by_side = validation.get("by_side", {})
        if len(by_side) >= 2:
            worst_side = min(
                by_side,
                key=lambda name: float(by_side[name].get("net_return", 0.0)),
            )
            metrics = by_side[worst_side]
            trade_count = int(metrics.get("trade_count", 0))
            impact = abs(float(metrics.get("net_return", 0.0)))
            directions.append(
                _DiagnosticDirection(
                    category="direction",
                    score=_diagnostic_score(impact, trade_count, 0.65),
                    title=f"诊断性 {worst_side} 方向开关",
                    hypothesis=(
                        f"暂时关闭验证样本中净收益更弱的 {worst_side} 方向，"
                        "仅用于判断多空不对称是否是主要损失来源。"
                    ),
                    component_type="filter",
                    expected_improvement="减少弱方向损失，但必须警惕交易数下降和样本选择偏差。",
                    parameter_space={"enabled_side": ["both", f"exclude_{worst_side}"]},
                    failure_conditions=(
                        "验证净收益未改善",
                        "交易数低于诊断最低样本",
                        "改善只来自单笔异常交易",
                    ),
                )
            )

        reentry = validation.get("by_first_entry_or_reentry", {})
        if "reentry" in reentry:
            metrics = reentry["reentry"]
            trade_count = int(metrics.get("trade_count", 0))
            impact = abs(min(0.0, float(metrics.get("net_return", 0.0))))
            directions.append(
                _DiagnosticDirection(
                    category="entry",
                    score=_diagnostic_score(impact, trade_count, 0.9),
                    title="再入组件开关消融",
                    hypothesis="关闭同一趋势腿再入，可能减少连续止损和手续费拖累。",
                    component_type="entry",
                    expected_improvement="降低再入损失与成本；若错失主要盈利段则假设失败。",
                    parameter_space={"max_reentries_per_trend_leg": [0, 1, 2]},
                    failure_conditions=(
                        "验证盈亏效率不改善",
                        "净收益改善但单笔期望继续为负",
                        "改善来自不足 30 笔交易",
                    ),
                )
            )

        exits = validation.get("by_exit_reason", {})
        if exits:
            worst_exit = min(
                exits,
                key=lambda name: float(exits[name].get("net_return", 0.0)),
            )
            metrics = exits[worst_exit]
            trade_count = int(metrics.get("trade_count", 0))
            impact = abs(min(0.0, float(metrics.get("net_return", 0.0))))
            directions.append(
                _DiagnosticDirection(
                    category="exit",
                    score=_diagnostic_score(impact, trade_count, 0.8),
                    title=f"{worst_exit} 退出规则小范围消融",
                    hypothesis=(
                        f"仅调整 {worst_exit} 对应的退出组件，"
                        "可能改善持仓时间与损失尾部而不改变入场。"
                    ),
                    component_type="exit",
                    expected_improvement="减少该退出类别的损失，同时保持其他规则固定。",
                    parameter_space={"exit_rule_variant": ["current", "tighter", "looser"]},
                    failure_conditions=(
                        "训练与验证改善方向不一致",
                        "最大回撤恶化",
                        "费用占毛盈利比例上升",
                    ),
                )
            )

        costs = validation.get("costs", {})
        fee_fraction = costs.get("fees_as_fraction_of_gross_positive_price_pnl")
        validation_trade_count = int(
            validation.get("summary", {}).get("trade_count", 0)
        )
        if isinstance(fee_fraction, (int, float)) and fee_fraction > 0:
            directions.append(
                _DiagnosticDirection(
                    category="cost",
                    score=_diagnostic_score(
                        min(float(fee_fraction), 2.0),
                        validation_trade_count,
                        0.85,
                    ),
                    title="交易频率与成本负担消融",
                    hypothesis=(
                        f"费用约占毛正收益的 {float(fee_fraction):.1%}；"
                        "减少边缘入场或重复入场，可能比继续微调退出更有效。"
                    ),
                    component_type="execution",
                    expected_improvement="降低成本侵蚀，同时保留主要盈利交易。",
                    parameter_space={"entry_selectivity": ["current", "higher"]},
                    failure_conditions=(
                        "费用下降但净收益和单笔期望未改善",
                        "交易数下降后结果被少数交易主导",
                        "主要盈利段被过滤",
                    ),
                )
            )

        selected: list[_DiagnosticDirection] = []
        used_categories: set[str] = set()
        used_component_types: set[str] = set()
        for direction in sorted(directions, key=lambda item: item.score, reverse=True):
            if direction.category in used_categories:
                continue
            if (
                direction.component_type in used_component_types
                and direction.component_type == "filter"
            ):
                continue
            selected.append(direction)
            used_categories.add(direction.category)
            used_component_types.add(direction.component_type)
            if len(selected) == 3:
                break

        return tuple(
            self._draft(
                session_id=session_id,
                subject_id=subject_id,
                title=item.title,
                hypothesis=item.hypothesis,
                component_type=item.component_type,
                evidence_refs=evidence_refs,
                expected_improvement=item.expected_improvement,
                parameter_space=item.parameter_space,
                suggested_trials=3,
                failure_conditions=item.failure_conditions,
            )
            for item in selected
        )

    @staticmethod
    def _draft(
        *,
        session_id: str,
        subject_id: str,
        title: str,
        hypothesis: str,
        component_type: str,
        evidence_refs: tuple[str, ...],
        expected_improvement: str,
        parameter_space: Mapping[str, Any],
        suggested_trials: int,
        failure_conditions: tuple[str, ...],
    ) -> ComponentHypothesis:
        return ComponentHypothesis(
            id=new_id("component_hypothesis"),
            session_id=session_id,
            subject_id=subject_id,
            title=title,
            hypothesis=hypothesis,
            component_type=component_type,  # type: ignore[arg-type]
            source="deterministic_rule_analyzer",
            evidence_refs=evidence_refs,
            expected_improvement=expected_improvement,
            parameter_space=dict(parameter_space),
            suggested_trials=suggested_trials,
            failure_conditions=failure_conditions,
            evidence_level="screening",
            contamination_status="screening_contaminated",
            status="draft",
            created_at=utc_now(),
        )


@dataclass(frozen=True, slots=True)
class _DiagnosticDirection:
    category: str
    score: float
    title: str
    hypothesis: str
    component_type: str
    expected_improvement: str
    parameter_space: Mapping[str, Any]
    failure_conditions: tuple[str, ...]


def _diagnostic_score(impact: float, sample_count: int, actionability: float) -> float:
    sample_support = min(max(sample_count, 0) / 30.0, 1.0)
    return max(float(impact), 0.0) * sample_support * actionability


def _filter_direction(reason: str) -> tuple[str, str, str]:
    normalized = reason.lower()
    if "reward" in normalized or "target_space" in normalized:
        return (
            "minimum_reward_r",
            "最低目标空间小范围消融",
            "目标收益风险空间不足",
        )
    if "stop_distance" in normalized or "stop" in normalized:
        return (
            "max_stop_distance_fraction",
            "最大止损距离小范围消融",
            "止损距离不符合上限",
        )
    return (
        f"filter_{normalized}",
        f"{reason} 过滤规则小范围消融",
        reason,
    )


def _streak_summary(values: pd.Series) -> dict[str, float]:
    signs = np.where(values.astype(float).to_numpy() > 0, 1, -1)
    longest_win = longest_loss = current = 0
    previous = 0
    for sign in signs:
        current = current + 1 if sign == previous else 1
        if sign > 0:
            longest_win = max(longest_win, current)
        else:
            longest_loss = max(longest_loss, current)
        previous = int(sign)
    return {
        "longest_win_streak": float(longest_win),
        "longest_loss_streak": float(longest_loss),
    }


def _signal_funnel(
    *,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    split_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    saved_funnel = split_metrics.get("signal_funnel", {})
    if isinstance(saved_funnel, Mapping) and saved_funnel:
        trend_bars = saved_funnel.get("trend_bars_1h", {})
        pullbacks = saved_funnel.get("pullback_candidates_15m", {})
        skipped = saved_funnel.get("skipped", {})
        return {
            "trend_1h": {
                "bar_counts_by_direction": (
                    dict(trend_bars) if isinstance(trend_bars, Mapping) else {}
                ),
                "directions": sorted(
                    str(key)
                    for key, value in (
                        trend_bars.items()
                        if isinstance(trend_bars, Mapping)
                        else ()
                    )
                    if int(value) > 0
                ),
            },
            "pullback_candidates_15m": {
                "count": (
                    int(sum(int(value) for value in pullbacks.values()))
                    if isinstance(pullbacks, Mapping)
                    else None
                ),
                "by_direction": (
                    dict(pullbacks) if isinstance(pullbacks, Mapping) else {}
                ),
                "availability": (
                    "recorded"
                    if isinstance(pullbacks, Mapping)
                    else "not_recorded_in_existing_artifact"
                ),
            },
            "confirmations_15m": None,
            "confirmations_15m_availability": "not_separately_recorded",
            "trigger_records_5m": int(saved_funnel.get("triggers_5m", len(signals))),
            "entry_candidates": int(
                saved_funnel.get("entry_candidates", len(signals))
            ),
            "filled_entries": int(saved_funnel.get("entered", len(trades))),
            "signal_status_counts": (
                {
                    str(name): int(count)
                    for name, count in signals["status"].value_counts().items()
                }
                if "status" in signals
                else {}
            ),
            "filter_or_cancel_reasons": (
                dict(skipped) if isinstance(skipped, Mapping) else {}
            ),
        }
    trend_legs = (
        int(signals["trend_leg_number"].nunique())
        if "trend_leg_number" in signals and not signals.empty
        else 0
    )
    directions = (
        sorted(str(item) for item in signals["side"].dropna().unique())
        if "side" in signals
        else []
    )
    confirmations = int(len(signals))
    fills = (
        int((signals["status"] == "filled").sum())
        if "status" in signals
        else int(len(trades))
    )
    return {
        "trend_1h": {
            "trend_leg_count": trend_legs,
            "directions": directions,
        },
        "pullback_candidates_15m": {
            "count": None,
            "availability": "not_recorded_in_existing_artifact",
        },
        "confirmations_15m": confirmations,
        "trigger_records_5m": confirmations,
        "filled_entries": fills,
        "signal_status_counts": (
            {
                str(name): int(count)
                for name, count in signals["status"].value_counts().items()
            }
            if "status" in signals
            else {}
        ),
        "filter_or_cancel_reasons": dict(split_metrics.get("skipped_setups", {})),
    }


def _normalize_trade_columns(trades: pd.DataFrame) -> pd.DataFrame:
    """Map reviewed Native runner schemas into the shared diagnostic contract."""

    frame = trades.copy()
    aliases = {
        "entry_raw_price": ("entry_price",),
        "initial_stop_price": ("stop_price",),
        "fees": ("fees_and_liquidation_cost",),
        "net_return_on_entry_equity": ("return_on_initial_equity",),
    }
    for target, sources in aliases.items():
        if target in frame:
            continue
        source = next((name for name in sources if name in frame), None)
        if source is not None:
            frame[target] = frame[source]
    if "trade_id" not in frame:
        frame = frame.reset_index(drop=True)
        frame["trade_id"] = frame.index.astype(int) + 1
    return frame


def _split_metric_context(
    metrics: Mapping[str, Any], split: str
) -> dict[str, Any]:
    raw = metrics.get(split, {})
    if not isinstance(raw, Mapping):
        return {}
    context = dict(raw.get("metrics", {}))
    if isinstance(raw.get("signal_funnel"), Mapping):
        context["signal_funnel"] = dict(raw["signal_funnel"])
    return context
