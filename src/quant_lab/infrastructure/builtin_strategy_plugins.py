from __future__ import annotations

from pathlib import Path

from quant_lab.domain.repositories import ProductRepository

from .baseline_backtest_runner import BaselineBacktestRunner
from .boll_rsi_slope_runner import BollRsiSlopeRunner
from .ema_mtf_scalp_runner import EmaMtfScalpRunner
from .generic_strategy_dsl_runner import (
    GenericStrategyDslEvaluator,
    GenericStrategyDslRunner,
)
from .selective_reentry_smoke_runner import (
    SelectiveReentryFastScreenRunner,
    SelectiveReentrySmokeRunner,
)
from .strategy_evaluators import (
    BollRsiSlopeExitComponentEvaluator,
    BollRsiSlopeMinimumRewardComponentEvaluator,
    BollRsiSlopeStopDistanceComponentEvaluator,
    DeterministicFixtureStrategyEvaluator,
    EmaMtfScalpComponentEvaluator,
    SelectiveReentryComponentEvaluator,
    StrategyEvaluatorRegistry,
    StrategyPluginRegistry,
    StrategySpec,
)


def build_builtin_strategy_plugins(
    root: Path, repository: ProductRepository
) -> StrategyPluginRegistry:
    """Single reviewed discovery point; Worker routing stays strategy-agnostic."""

    ema_runner = EmaMtfScalpRunner(root, repository)
    boll_runner = BollRsiSlopeRunner(root, repository)
    generic_runner = GenericStrategyDslRunner(root, repository)
    return StrategyPluginRegistry(
        (
            StrategySpec(
                strategy_spec_id="generic_strategy_dsl_v1",
                backtest_handlers={
                    "generic_strategy_dsl_smoke": generic_runner,
                    "generic_strategy_dsl_fast_screen": generic_runner,
                },
                evaluator_ids=("generic_strategy_dsl_v1",),
                config_resolver=generic_runner.prepare_config,
                smoke_intent="generic_strategy_dsl_smoke",
                fast_screen_intent="generic_strategy_dsl_fast_screen",
                market_profile="crypto_perpetual.binance.eth",
            ),
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
            StrategySpec(
                strategy_spec_id="ema_mtf_pullback_scalp_20x_v0",
                backtest_handlers={
                    "ema_mtf_scalp_smoke": ema_runner,
                    "ema_mtf_scalp_fast_screen": ema_runner,
                },
                evaluator_ids=("ema_mtf_scalp_exit_component_v1",),
                snapshot_strategy_id="ema_mtf_pullback_scalp_20x",
                config_artifact_key="configs/research/ema_mtf_scalp_20x_v0.yaml",
                smoke_intent="ema_mtf_scalp_smoke",
                fast_screen_intent="ema_mtf_scalp_fast_screen",
                market_profile="crypto_perpetual.binance.eth",
            ),
            StrategySpec(
                strategy_spec_id="boll_rsi_slope_deep_pullback_v0",
                backtest_handlers={
                    "boll_rsi_slope_smoke": boll_runner,
                    "boll_rsi_slope_fast_screen": boll_runner,
                },
                evaluator_ids=(
                    "boll_rsi_slope_exit_component_v1",
                    "boll_rsi_slope_stop_distance_component_v1",
                    "boll_rsi_slope_minimum_reward_component_v1",
                ),
                snapshot_strategy_id=(
                    "boll_rsi_slope_deep_pullback_continuation_v0"
                ),
                config_artifact_key=(
                    "configs/research/boll_rsi_slope_deep_pullback_v0.yaml"
                ),
                smoke_intent="boll_rsi_slope_smoke",
                fast_screen_intent="boll_rsi_slope_fast_screen",
                market_profile="crypto_perpetual.binance.eth",
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
            GenericStrategyDslEvaluator(root, repository),
            EmaMtfScalpComponentEvaluator(
                root,
                repository,
                config_artifact_key="configs/research/ema_mtf_scalp_20x_v0.yaml",
            ),
            BollRsiSlopeExitComponentEvaluator(
                root,
                repository,
                config_artifact_key=(
                    "configs/research/boll_rsi_slope_deep_pullback_v0.yaml"
                ),
            ),
            BollRsiSlopeStopDistanceComponentEvaluator(
                root,
                repository,
                config_artifact_key=(
                    "configs/research/boll_rsi_slope_deep_pullback_v0.yaml"
                ),
            ),
            BollRsiSlopeMinimumRewardComponentEvaluator(
                root,
                repository,
                config_artifact_key=(
                    "configs/research/boll_rsi_slope_deep_pullback_v0.yaml"
                ),
            ),
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
