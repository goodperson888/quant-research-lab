from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from quant_lab.domain.models import Constraint, Objective, ParameterSpace


PARAMETER_OPTIMIZATION_DOCS = (
    "docs/02-回测与验收规范.md",
    "docs/04-压力测试清单.md",
    "docs/07-个人量化策略研究工作法.md",
    "docs/15-产品需求规格-v1.md",
    "docs/16-产品化目标架构-v1.md",
    "docs/18-Agent与Skill执行架构.md",
)

PARAMETER_OPTIMIZATION_PRECONDITIONS = (
    "baseline_frozen",
    "single_falsifiable_hypothesis",
    "parameter_space_defined",
    "objective_and_constraints_defined",
    "train_validation_locked_test_defined",
    "cost_model_complete",
    "max_trials_or_time_budget_defined",
    "stopping_conditions_defined",
)


@dataclass(frozen=True, slots=True)
class ExperimentPlanDraftConfig:
    session_id: str
    baseline_version_id: str
    market_profile: str
    evidence_artifact_key: str
    hypothesis: str
    single_rule_change: str
    parameter_space: tuple[ParameterSpace, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...]
    data_splits: Mapping[str, str]
    cost_model: Mapping[str, Any]
    max_trials: int | None
    time_budget_seconds: int | None
    stopping_conditions: tuple[str, ...]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _list(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer or null")
    return value


def parse_experiment_plan_draft(
    raw: Mapping[str, Any],
) -> ExperimentPlanDraftConfig:
    """Parse a safe, unapproved ExperimentPlan config.

    This parser deliberately rejects approval or execution flags. Registering a
    config may only create a draft plan and append audit records; it cannot queue a
    parameter-search Job.
    """

    if raw.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if raw.get("intent") != "parameter_optimization":
        raise ValueError("intent must be parameter_optimization")
    if raw.get("status") != "draft":
        raise ValueError("status must remain draft during config registration")

    approval = _mapping(raw.get("approval"), "approval")
    if approval.get("required_before_execution") is not True:
        raise ValueError("approval.required_before_execution must be true")
    if approval.get("confirmed_by_user") is not False:
        raise ValueError("approval.confirmed_by_user must be false for a draft")
    if approval.get("parameter_search_job_allowed") is not False:
        raise ValueError("approval.parameter_search_job_allowed must be false for a draft")

    guardrails = _mapping(raw.get("guardrails"), "guardrails")
    required_true = (
        "baseline_immutable",
        "previous_locked_test_excluded",
    )
    required_false = (
        "validation_used_for_hypothesis_selection",
        "live_trading_enabled",
        "automatic_production_promotion",
    )
    for field in required_true:
        if guardrails.get(field) is not True:
            raise ValueError(f"guardrails.{field} must be true")
    for field in required_false:
        if guardrails.get(field) is not False:
            raise ValueError(f"guardrails.{field} must be false")

    parameter_space: list[ParameterSpace] = []
    for index, item in enumerate(_list(raw.get("parameter_space"), "parameter_space")):
        entry = _mapping(item, f"parameter_space[{index}]")
        values = entry.get("values")
        parameter_space.append(
            ParameterSpace(
                name=_text(entry.get("name"), f"parameter_space[{index}].name"),
                kind=_text(entry.get("kind"), f"parameter_space[{index}].kind"),  # type: ignore[arg-type]
                values=(
                    tuple(_list(values, f"parameter_space[{index}].values"))
                    if values is not None
                    else ()
                ),
                lower=entry.get("lower"),
                upper=entry.get("upper"),
            )
        )
    if not parameter_space:
        raise ValueError("parameter_space must not be empty")

    objectives: list[Objective] = []
    for index, item in enumerate(_list(raw.get("objectives"), "objectives")):
        entry = _mapping(item, f"objectives[{index}]")
        objectives.append(
            Objective(
                metric=_text(entry.get("metric"), f"objectives[{index}].metric"),
                direction=_text(
                    entry.get("direction"), f"objectives[{index}].direction"
                ),  # type: ignore[arg-type]
            )
        )
    if not objectives:
        raise ValueError("objectives must not be empty")

    constraints: list[Constraint] = []
    for index, item in enumerate(_list(raw.get("constraints"), "constraints")):
        entry = _mapping(item, f"constraints[{index}]")
        value = entry.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"constraints[{index}].value must be numeric")
        constraints.append(
            Constraint(
                metric=_text(entry.get("metric"), f"constraints[{index}].metric"),
                operator=_text(
                    entry.get("operator"), f"constraints[{index}].operator"
                ),  # type: ignore[arg-type]
                value=float(value),
            )
        )
    if not constraints:
        raise ValueError("constraints must not be empty")

    data_splits_raw = _mapping(raw.get("data_splits"), "data_splits")
    data_splits = {
        str(key): _text(value, f"data_splits.{key}")
        for key, value in data_splits_raw.items()
    }
    for split in ("train", "validation", "locked_test"):
        if split not in data_splits:
            raise ValueError(f"data_splits.{split} is required")

    cost_model = dict(_mapping(raw.get("cost_model"), "cost_model"))
    required_costs = (
        "fee_per_side",
        "slippage_bps_per_side",
        "leverage",
        "funding_rate",
        "zero_funding_fallback_allowed",
    )
    missing_costs = [field for field in required_costs if field not in cost_model]
    if missing_costs:
        raise ValueError("cost_model is missing: " + ", ".join(missing_costs))
    if cost_model["leverage"] != 1.0:
        raise ValueError("cost_model.leverage must remain 1.0")
    if cost_model["funding_rate"] != "required":
        raise ValueError("cost_model.funding_rate must be required")
    if cost_model["zero_funding_fallback_allowed"] is not False:
        raise ValueError("zero funding fallback must remain disabled")

    max_trials = _positive_int_or_none(raw.get("max_trials"), "max_trials")
    time_budget_seconds = _positive_int_or_none(
        raw.get("time_budget_seconds"), "time_budget_seconds"
    )
    if max_trials is None and time_budget_seconds is None:
        raise ValueError("max_trials or time_budget_seconds is required")

    stopping_conditions = tuple(
        _text(item, f"stopping_conditions[{index}]")
        for index, item in enumerate(
            _list(raw.get("stopping_conditions"), "stopping_conditions")
        )
    )
    if not stopping_conditions:
        raise ValueError("stopping_conditions must not be empty")

    return ExperimentPlanDraftConfig(
        session_id=_text(raw.get("session_id"), "session_id"),
        baseline_version_id=_text(
            raw.get("baseline_version_id"), "baseline_version_id"
        ),
        market_profile=_text(raw.get("market_profile"), "market_profile"),
        evidence_artifact_key=_text(
            raw.get("evidence_artifact_key"), "evidence_artifact_key"
        ),
        hypothesis=_text(raw.get("hypothesis"), "hypothesis"),
        single_rule_change=_text(
            raw.get("single_rule_change"), "single_rule_change"
        ),
        parameter_space=tuple(parameter_space),
        objectives=tuple(objectives),
        constraints=tuple(constraints),
        data_splits=data_splits,
        cost_model=cost_model,
        max_trials=max_trials,
        time_budget_seconds=time_budget_seconds,
        stopping_conditions=stopping_conditions,
    )
