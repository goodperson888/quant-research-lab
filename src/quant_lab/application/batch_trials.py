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
    return {
        "trial_count": len(trials),
        "succeeded_count": len(succeeded),
        "stable_count": len(stable),
        "stable_parameter_ranges": ranges(stable),
        "failed_parameter_ranges": ranges(failed),
        "representative_stable_metrics": representative_metrics,
        "isolated_best_is_not_a_candidate": True,
        "locked_test_used": False,
    }
