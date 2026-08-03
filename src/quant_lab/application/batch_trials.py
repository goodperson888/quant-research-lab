from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from itertools import product
import json
import random
from typing import Any, Mapping, Sequence

from quant_lab.domain.models import Constraint, ExperimentPlan, ParameterSpace, Trial


def parameter_signature(parameters: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(sorted(parameters.items())),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _grid_values(space: ParameterSpace) -> tuple[Any, ...]:
    if space.values:
        return tuple(space.values)
    if space.lower is None or space.upper is None:
        raise ValueError(f"grid parameter {space.name} requires values or bounds")
    if space.lower == space.upper:
        value: Any = int(space.lower) if space.kind == "integer" else float(space.lower)
        return (value,)
    if space.step is None:
        raise ValueError(f"grid parameter {space.name} requires an explicit step")
    lower = Decimal(str(space.lower))
    upper = Decimal(str(space.upper))
    step = Decimal(str(space.step))
    values: list[Any] = []
    current = lower
    while current <= upper:
        values.append(int(current) if space.kind == "integer" else float(current))
        current += step
    return tuple(values)


@dataclass(frozen=True, slots=True)
class ParameterBatch:
    combinations: tuple[Mapping[str, Any], ...]
    generated_count: int
    truncated_by_budget: bool


class DeterministicParameterGenerator:
    """Generate bounded grid or seeded-random parameter combinations."""

    def generate(self, plan: ExperimentPlan) -> ParameterBatch:
        if not plan.max_trials or plan.max_trials <= 0:
            raise ValueError("batch parameter search requires a positive max_trials budget")
        if plan.search_strategy == "grid":
            values = [_grid_values(item) for item in plan.parameter_space]
            names = [item.name for item in plan.parameter_space]
            all_combinations = [
                dict(zip(names, combination, strict=True))
                for combination in product(*values)
            ]
            return ParameterBatch(
                combinations=tuple(all_combinations[: plan.max_trials]),
                generated_count=len(all_combinations),
                truncated_by_budget=len(all_combinations) > plan.max_trials,
            )
        if plan.search_strategy != "random":
            raise ValueError(f"unsupported search strategy: {plan.search_strategy}")

        rng = random.Random(plan.random_seed)
        combinations: list[Mapping[str, Any]] = []
        signatures: set[str] = set()
        attempts = 0
        max_attempts = max(plan.max_trials * 50, 100)
        while len(combinations) < plan.max_trials and attempts < max_attempts:
            attempts += 1
            values: dict[str, Any] = {}
            for space in plan.parameter_space:
                if space.values:
                    values[space.name] = rng.choice(tuple(space.values))
                elif space.kind == "integer":
                    if space.lower is None or space.upper is None:
                        raise ValueError(f"random integer {space.name} requires bounds")
                    values[space.name] = rng.randint(int(space.lower), int(space.upper))
                elif space.kind == "float":
                    if space.lower is None or space.upper is None:
                        raise ValueError(f"random float {space.name} requires bounds")
                    sampled = rng.uniform(float(space.lower), float(space.upper))
                    values[space.name] = round(sampled, 12)
                else:
                    raise ValueError(f"random categorical {space.name} requires values")
            signature = parameter_signature(values)
            if signature in signatures:
                continue
            signatures.add(signature)
            combinations.append(values)
        return ParameterBatch(
            combinations=tuple(combinations),
            generated_count=len(combinations),
            truncated_by_budget=False,
        )


def constraints_pass(
    metrics: Mapping[str, float], constraints: Sequence[Constraint]
) -> bool:
    operators = {
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "eq": lambda left, right: left == right,
    }
    for constraint in constraints:
        value = metrics.get(constraint.metric)
        if value is None or not operators[constraint.operator](value, constraint.value):
            return False
    return True


def summarize_stable_ranges(
    trials: Sequence[Trial], constraints: Sequence[Constraint]
) -> dict[str, Any]:
    succeeded = [item for item in trials if item.status == "succeeded"]
    stable = [item for item in succeeded if constraints_pass(item.metrics, constraints)]
    failed = [item for item in trials if item not in stable]

    def ranges(items: Sequence[Trial]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        names = sorted({name for item in items for name in item.parameters})
        for name in names:
            values = [item.parameters[name] for item in items if name in item.parameters]
            if not values:
                continue
            if all(isinstance(value, (int, float)) for value in values):
                result[name] = {"min": min(values), "max": max(values), "count": len(values)}
            else:
                result[name] = {"values": sorted({str(value) for value in values}), "count": len(values)}
        return result

    metric_names = (
        "train_net_return",
        "validation_net_return",
        "validation_profit_factor",
        "validation_expectancy",
        "validation_trade_count",
        "validation_max_drawdown_abs",
    )
    representative_metrics: dict[str, float] = {}
    for metric in metric_names:
        values = [item.metrics[metric] for item in stable if metric in item.metrics]
        if values:
            representative_metrics[metric] = sum(values) / len(values)
    recommendation = _recommend_stable_trial(stable)
    return {
        "trial_count": len(trials),
        "succeeded_count": len(succeeded),
        "stable_count": len(stable),
        "stable_parameter_ranges": ranges(stable),
        "failed_parameter_ranges": ranges(failed),
        "representative_stable_metrics": representative_metrics,
        "recommendation": recommendation,
        "isolated_best_is_not_a_candidate": True,
        "locked_test_used": False,
    }


def _recommend_stable_trial(stable: Sequence[Trial]) -> dict[str, Any]:
    if not stable:
        return {
            "decision": "stop",
            "headline": "没有参数方案同时满足已批准约束",
            "recommended_trial_id": None,
            "reasons": [
                "未发现稳定参数区间，不能为了历史收益继续扩大搜索。",
                "建议回到亏损归因，重新提出一个可解释的单一假设。",
            ],
        }
    if len(stable) == 1:
        only = stable[0]
        return {
            "decision": "insufficient_plateau",
            "headline": "只有一个孤立方案通过，暂不作为候选",
            "recommended_trial_id": only.id,
            "parameters": dict(only.parameters),
            "metrics": dict(only.metrics),
            "reasons": [
                "单点最优可能是参数偶然性，不代表邻近参数也稳定。",
                "可在不触碰最终保留测试的前提下，做一次小范围参数扰动。",
            ],
        }

    scored: list[tuple[float, int, Trial]] = []
    metric_directions = {
        "validation_net_return": 1.0,
        "validation_profit_factor": 1.0,
        "validation_expectancy": 1.0,
        "validation_max_drawdown_abs": -1.0,
    }
    ranks: dict[str, dict[str, float]] = {}
    for metric, direction in metric_directions.items():
        values = [
            (item.id, float(item.metrics[metric]))
            for item in stable
            if metric in item.metrics
        ]
        ordered = sorted(values, key=lambda pair: pair[1] * direction)
        denominator = max(len(ordered) - 1, 1)
        ranks[metric] = {
            trial_id: index / denominator
            for index, (trial_id, _) in enumerate(ordered)
        }
    for index, item in enumerate(stable):
        performance = [
            metric_ranks[item.id]
            for metric_ranks in ranks.values()
            if item.id in metric_ranks
        ]
        performance_score = (
            sum(performance) / len(performance) if performance else 0.0
        )
        train = item.metrics.get("train_net_return")
        validation = item.metrics.get("validation_net_return")
        gap_penalty = (
            min(abs(float(train) - float(validation)), 1.0)
            if train is not None and validation is not None
            else 1.0
        )
        neighbor_support = _stable_neighbor_support(item, stable)
        score = (
            performance_score * 0.55
            + neighbor_support * 0.30
            + (1.0 - gap_penalty) * 0.15
        )
        scored.append((score, -index, item))
    score, _, selected = max(scored, key=lambda row: (row[0], row[1]))
    support = _stable_neighbor_support(selected, stable)
    return {
        "decision": "candidate_validation",
        "headline": "发现约束内的稳定参数区间，可进入候选验证",
        "recommended_trial_id": selected.id,
        "parameters": dict(selected.parameters),
        "metrics": dict(selected.metrics),
        "stability_score": round(score, 6),
        "neighbor_support": round(support, 6),
        "reasons": [
            "推荐值同时考虑验证期表现、训练与验证差距和邻近参数支持。",
            "没有直接选择孤立的最高收益点。",
            "下一阶段仍需成本敏感性、滚动窗口和参数扰动；最终保留测试继续隔离。",
        ],
    }


def _stable_neighbor_support(
    target: Trial, stable: Sequence[Trial]
) -> float:
    others = [item for item in stable if item.id != target.id]
    if not others:
        return 0.0
    names = sorted(
        {
            name
            for item in stable
            for name, value in item.parameters.items()
            if isinstance(value, (int, float))
        }
    )
    if not names:
        matching = sum(
            1
            for item in others
            if sum(
                target.parameters.get(name) != item.parameters.get(name)
                for name in set(target.parameters) | set(item.parameters)
            )
            <= 1
        )
        return matching / len(others)
    ranges = {
        name: (
            min(
                float(item.parameters[name])
                for item in stable
                if isinstance(item.parameters.get(name), (int, float))
            ),
            max(
                float(item.parameters[name])
                for item in stable
                if isinstance(item.parameters.get(name), (int, float))
            ),
        )
        for name in names
    }
    distances = []
    for item in others:
        components = []
        for name in names:
            left = target.parameters.get(name)
            right = item.parameters.get(name)
            if not isinstance(left, (int, float)) or not isinstance(
                right, (int, float)
            ):
                continue
            lower, upper = ranges[name]
            span = upper - lower
            components.append(
                abs(float(left) - float(right)) / span if span else 0.0
            )
        if components:
            distances.append(sum(components) / len(components))
    if not distances:
        return 0.0
    nearest = min(distances)
    return max(0.0, 1.0 - min(nearest, 1.0))
