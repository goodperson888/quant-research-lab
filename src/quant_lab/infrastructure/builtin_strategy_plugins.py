from __future__ import annotations

from pathlib import Path

from quant_lab.domain.repositories import ProductRepository

from .baseline_backtest_runner import BaselineBacktestRunner
from .selective_reentry_smoke_runner import (
    SelectiveReentryFastScreenRunner,
    SelectiveReentrySmokeRunner,
)
from .strategy_evaluators import (
    DeterministicFixtureStrategyEvaluator,
    SelectiveReentryComponentEvaluator,
    StrategyEvaluatorRegistry,
    StrategyPluginRegistry,
    StrategySpec,
)


def build_builtin_strategy_plugins(
    root: Path, repository: ProductRepository
) -> StrategyPluginRegistry:
    """Single reviewed discovery point; Worker routing stays strategy-agnostic."""

    return StrategyPluginRegistry(
        (
            StrategySpec(
                strategy_spec_id="trend_follow_selective_reentry_v1",
                backtest_handlers={
                    "candidate_smoke": SelectiveReentrySmokeRunner(root, repository),
                    "candidate_fast_screen": SelectiveReentryFastScreenRunner(
                        root, repository
                    ),
                },
                evaluator_ids=("selective_reentry_component_v1",),
            ),
        ),
        fallback=BaselineBacktestRunner(root, repository),
    )


def build_builtin_evaluator_registry(
    root: Path, repository: ProductRepository
) -> StrategyEvaluatorRegistry:
    return StrategyEvaluatorRegistry(
        (
            DeterministicFixtureStrategyEvaluator(),
            SelectiveReentryComponentEvaluator(
                root,
                repository,
                config_artifact_key=(
                    "configs/research/"
                    "trend_follow_selective_reentry_candidate_v1_fast_screen.yaml"
                ),
            ),
        )
    )
