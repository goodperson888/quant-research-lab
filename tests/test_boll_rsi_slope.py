from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

from quant_lab.application.boll_rsi_slope import BollRsiSlopeBacktester
from quant_lab.application.ports import TrialEvaluationRequest
from quant_lab.application.guided_research import GuidedResearchService
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import (
    Constraint,
    Objective,
    ParameterSpace,
    StrategyVersion,
)
from quant_lab.infrastructure.builtin_strategy_plugins import (
    build_builtin_evaluator_registry,
    build_builtin_strategy_plugins,
)
from quant_lab.infrastructure.execution_models import ExecutionModelCatalog
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.strategy_evaluators import (
    BollRsiSlopeExitComponentEvaluator,
    BollRsiSlopeMinimumRewardComponentEvaluator,
)


ROOT = Path(__file__).resolve().parents[1]


def _ohlcv(
    index: pd.DatetimeIndex,
    close: np.ndarray,
    *,
    low_overrides: dict[int, float] | None = None,
) -> pd.DataFrame:
    previous = np.r_[close[0], close[:-1]]
    high = np.maximum(previous, close) + 0.005
    low = np.minimum(previous, close) - 0.005
    for offset, value in (low_overrides or {}).items():
        low[offset] = value
    return pd.DataFrame(
        {
            "timestamp": index,
            "open": previous,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000.0,
        }
    )


def _representative_inputs() -> dict[str, pd.DataFrame]:
    index_15m = pd.date_range(
        "2025-01-01", periods=400, freq="15min", tz="UTC"
    )
    close_15m = 100 + np.arange(len(index_15m), dtype=float) * 0.003
    setup_offset = 200
    for offset in range(setup_offset - 6, setup_offset):
        close_15m[offset] = (
            close_15m[setup_offset - 7]
            - 0.04 * (offset - (setup_offset - 7))
        )
    close_15m[setup_offset] = close_15m[setup_offset - 1] + 0.10
    bars_15m = _ohlcv(
        index_15m,
        close_15m,
        low_overrides={setup_offset: 100.20},
    )

    index_1h = pd.date_range("2025-01-01", periods=120, freq="1h", tz="UTC")
    bars_1h = _ohlcv(
        index_1h,
        100 + np.arange(len(index_1h), dtype=float) * 0.04,
    )

    index_5m = pd.date_range("2025-01-01", periods=1_200, freq="5min", tz="UTC")
    close_5m = 100 + np.arange(len(index_5m), dtype=float) * 0.001
    setup_event = index_15m[setup_offset] + pd.Timedelta(minutes=15)
    trigger_offset = index_5m.get_loc(setup_event)
    close_5m[trigger_offset - 1 : trigger_offset + 4] = (
        100.20,
        100.24,
        100.28,
        100.34,
        100.40,
    )
    bars_5m = _ohlcv(index_5m, close_5m)
    funding = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2025-01-01", periods=20, freq="8h", tz="UTC"
            ),
            "last_funding_rate": 0.0001,
        }
    )
    return {
        "ohlcv_5m": bars_5m,
        "ohlcv_15m": bars_15m,
        "ohlcv_1h": bars_1h,
        "funding": funding,
    }


def _run():
    return BollRsiSlopeBacktester(
        strategy_version_id="version_fixture",
        execution_model=ExecutionModelCatalog(ROOT).get(venue="binance"),
    ).run(
        **_representative_inputs(),
        warmup_start_utc_inclusive="2025-01-01T00:00:00Z",
        start_utc_inclusive="2025-01-02T00:00:00Z",
        end_utc_exclusive="2025-01-04T12:00:00Z",
    )


def test_boll_rsi_slope_uses_closed_bars_and_executes_representative_trade() -> None:
    result = _run()

    assert result.metrics["trade_count"] == 1
    assert result.execution["leverage"] == 1.0
    assert result.funding["silent_zero_allowed"] is False
    assert result.data_quality["missing_5m_rows"] == 0
    assert result.signal_funnel["pullback_candidates_15m"]["long"] >= 1
    entered = result.signals.loc[result.signals["status"] == "entered"]
    assert len(entered) == 1
    assert (
        pd.to_datetime(entered["signal_time"], utc=True)
        > pd.to_datetime(entered["setup_event_time"], utc=True)
    ).all()
    trade = result.trades.iloc[0]
    assert trade["stop_price"] < trade["entry_price"] < trade["take_profit_price"]


def test_boll_rsi_slope_is_deterministic_and_locked_test_is_excluded() -> None:
    first = _run()
    second = _run()
    assert first.metrics == second.metrics
    assert first.trades.to_dict("records") == second.trades.to_dict("records")

    config = yaml.safe_load(
        (ROOT / "configs/research/boll_rsi_slope_deep_pullback_v0.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert config["time_splits"]["locked_test"]["use_in_this_authorization"] is False
    assert config["execution"]["leverage"] == 1.0
    assert config["execution"]["leverage_explicitly_requested"] is False
    assert any("volume filter" in item for item in config["limitations"])


def test_boll_rsi_slope_strategy_spec_is_registered(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "product.sqlite3")
    repository.initialize()
    specs = {
        item.strategy_spec_id: item
        for item in build_builtin_strategy_plugins(ROOT, repository).list_specs()
    }
    spec = specs["boll_rsi_slope_deep_pullback_v0"]
    assert set(spec.backtest_handlers) == {
        "boll_rsi_slope_smoke",
        "boll_rsi_slope_fast_screen",
    }
    assert spec.evaluator_ids == (
        "boll_rsi_slope_exit_component_v1",
        "boll_rsi_slope_stop_distance_component_v1",
        "boll_rsi_slope_minimum_reward_component_v1",
    )
    assert (
        build_builtin_evaluator_registry(ROOT, repository)
        .get("boll_rsi_slope_exit_component_v1")
        .evaluator_id
        == "boll_rsi_slope_exit_component_v1"
    )
    assert (
        build_builtin_evaluator_registry(ROOT, repository)
        .get("boll_rsi_slope_stop_distance_component_v1")
        .evaluator_id
        == "boll_rsi_slope_stop_distance_component_v1"
    )
    assert (
        build_builtin_evaluator_registry(ROOT, repository)
        .get("boll_rsi_slope_minimum_reward_component_v1")
        .evaluator_id
        == "boll_rsi_slope_minimum_reward_component_v1"
    )


def test_boll_rsi_exit_component_evaluator_is_single_component_and_locked_safe(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "product.sqlite3")
    service = ResearchApplicationService(repository)
    session = service.create_research_session(title="boll-rsi-component")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        source_name=None,
        raw_content="BOLL RSI slope fixture baseline",
    )
    baseline = service.freeze_baseline(
        draft_id=draft.id,
        confirmed_by_user=True,
    )
    guided = GuidedResearchService(repository)
    proposal = guided.create_direction(
        baseline_version_id=baseline.id,
        subject_id=baseline.id,
        hypothesis="Only early exit timing changes.",
        rule_diff={"exit_rule_variant": ["current", "tighter", "looser"]},
        evidence_refs=(),
        parameter_space=(
            ParameterSpace(
                name="exit_rule_variant",
                kind="categorical",
                values=("current", "tighter", "looser"),
            ),
        ),
        data_splits={
            "train": "[2025-01-02T00:00:00Z, 2025-01-03T00:00:00Z)",
            "validation": (
                "[2025-01-03T00:00:00Z, 2025-01-04T00:00:00Z) "
                "screening_contaminated"
            ),
            "locked_test": "forbidden_reserved_uninspected",
        },
        cost_model={
            "taker_fee_per_side": 0.0005,
            "slippage_bps_per_side": 2.0,
            "leverage": 1.0,
            "funding_required": True,
            "zero_funding_fallback_allowed": False,
        },
        objectives=(Objective(metric="validation_net_return", direction="maximize"),),
        constraints=(
            Constraint(
                metric="validation_max_drawdown_abs",
                operator="lte",
                value=0.01,
            ),
        ),
        estimated_trials=3,
        estimated_minutes=10,
        failure_conditions=("no improvement",),
        stopping_conditions=("three variants complete",),
        rollback_plan="retain immutable baseline",
    )
    proposal = guided.submit_for_approval(proposal.id)
    proposal, candidate = guided.approve(
        proposal_id=proposal.id,
        subject_id=proposal.id,
        confirmed_by_user=True,
    )
    plan = service.create_experiment_plan(
        baseline_version_id=baseline.id,
        proposal_id=proposal.id,
        candidate_version_id=candidate.id,
        hypothesis=proposal.hypothesis,
        parameter_space=proposal.parameter_space,
        objectives=proposal.objectives,
        constraints=proposal.constraints,
        data_splits=proposal.data_splits,
        cost_model=proposal.cost_model,
        max_trials=3,
        time_budget_seconds=600,
        stopping_conditions=proposal.stopping_conditions,
        search_strategy="grid",
        random_seed=20260728,
    )
    evaluator = build_builtin_evaluator_registry(ROOT, repository).get(
        "boll_rsi_slope_exit_component_v1"
    )

    assert evaluator._early_exit_bars({"exit_rule_variant": "current"}) == 3
    assert evaluator._early_exit_bars({"exit_rule_variant": "tighter"}) == 2
    assert evaluator._early_exit_bars({"exit_rule_variant": "looser"}) == 4
    try:
        evaluator._early_exit_bars(
            {"exit_rule_variant": "current", "early_exit_min_mfe_r": 0.2}
        )
        raise AssertionError("compound parameter change must be rejected")
    except ValueError as exc:
        assert "only exit_rule_variant" in str(exc)

    result = evaluator.evaluate(
        TrialEvaluationRequest(
            trial_id="trial_locked_forbidden",
            experiment_plan_id=plan.id,
            baseline_version_id=baseline.id,
            candidate_version_id=candidate.id,
            parameters={"exit_rule_variant": "current"},
            data_version="fixture",
            data_splits={
                "train": proposal.data_splits["train"],
                "validation": proposal.data_splits["validation"],
                "locked_test": proposal.data_splits["locked_test"],
            },
            cost_model=proposal.cost_model,
            seed=20260728,
        )
    )
    assert result.status == "failed"
    assert "must not receive locked-test data" in str(result.error)


def test_boll_rsi_stop_distance_evaluator_preserves_fixed_risk(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "product.sqlite3")
    repository.initialize()
    evaluator = build_builtin_evaluator_registry(ROOT, repository).get(
        "boll_rsi_slope_stop_distance_component_v1"
    )
    assert evaluator._max_stop_distance({"max_stop_distance_fraction": 0.0045}) == 0.0045
    assert evaluator._max_stop_distance({"max_stop_distance_fraction": 0.0060}) == 0.0060
    assert evaluator._max_stop_distance({"max_stop_distance_fraction": 0.0075}) == 0.0075
    try:
        evaluator._max_stop_distance(
            {
                "max_stop_distance_fraction": 0.0060,
                "risk_per_trade_fraction": 0.005,
            }
        )
        raise AssertionError("compound risk change must be rejected")
    except ValueError as exc:
        assert "only max_stop_distance_fraction" in str(exc)

    candidate = StrategyVersion(
        id="candidate",
        strategy_id="strategy",
        version=1,
        status="candidate",
        content_snapshot={
            "baseline_version_id": "baseline",
            "locked_test_used": False,
            "rule_diff": {
                "component_type": "risk",
                "single_component_only": True,
                "fixed_rules": {
                    "risk_per_trade_equity_fraction": 0.0025,
                    "entry_rules": "unchanged",
                    "early_exit_and_max_holding_rules": "unchanged",
                },
                "parameter_mapping": {
                    "current": {"max_stop_distance_fraction": 0.0045},
                    "medium": {"max_stop_distance_fraction": 0.0060},
                    "wide": {"max_stop_distance_fraction": 0.0075},
                },
            },
        },
        source_snapshot="fixture",
        created_at="2026-07-28T00:00:00Z",
        immutable=True,
    )
    evaluator._validate_component_candidate(candidate)
    invalid = StrategyVersion(
        id="invalid",
        strategy_id="strategy",
        version=2,
        status="candidate",
        content_snapshot={
            **candidate.content_snapshot,
            "rule_diff": {
                **candidate.content_snapshot["rule_diff"],
                "fixed_rules": {
                    "risk_per_trade_equity_fraction": 0.005,
                    "entry_rules": "unchanged",
                    "early_exit_and_max_holding_rules": "unchanged",
                },
            },
        },
        source_snapshot="fixture",
        created_at="2026-07-28T00:00:00Z",
        immutable=True,
    )
    try:
        evaluator._validate_component_candidate(invalid)
        raise AssertionError("fixed account risk must be preserved")
    except ValueError as exc:
        assert "fixed-risk" in str(exc)


def test_boll_rsi_stop_distance_diagnostics_explain_admission_and_sizing() -> None:
    result = SimpleNamespace(
        signal_funnel={
            "entry_candidates": 7,
            "entered": 2,
            "skipped": {"stop_distance": 3, "insufficient_reward": 2},
        },
        trades=pd.DataFrame(
            {
                "entry_price": [100.0, 200.0],
                "stop_price": [99.5, 198.0],
                "quantity": [4.0, 2.0],
            }
        ),
    )

    metrics = BollRsiSlopeExitComponentEvaluator._admission_diagnostics(
        "validation", result
    )

    assert metrics["validation_entry_candidates"] == 7.0
    assert metrics["validation_stop_distance_skip_count"] == 3.0
    assert metrics["validation_insufficient_reward_skip_count"] == 2.0
    assert metrics["validation_entered_count"] == 2.0
    assert metrics["validation_average_stop_distance_fraction"] == 0.0075
    assert metrics["validation_average_position_quantity"] == 3.0


def test_boll_rsi_minimum_reward_evaluator_is_single_component_and_fixed_risk(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "product.sqlite3")
    repository.initialize()
    evaluator = build_builtin_evaluator_registry(ROOT, repository).get(
        "boll_rsi_slope_minimum_reward_component_v1"
    )

    assert evaluator._minimum_reward({"minimum_reward_r": 1.0}) == 1.0
    assert evaluator._minimum_reward({"minimum_reward_r": 0.8}) == 0.8
    assert evaluator._minimum_reward({"minimum_reward_r": 0.6}) == 0.6
    try:
        evaluator._minimum_reward(
            {"minimum_reward_r": 0.8, "leverage": 20.0}
        )
        raise AssertionError("compound leverage change must be rejected")
    except ValueError as exc:
        assert "only minimum_reward_r" in str(exc)

    candidate = StrategyVersion(
        id="candidate",
        strategy_id="strategy",
        version=1,
        status="candidate",
        content_snapshot={
            "baseline_version_id": "baseline",
            "locked_test_used": False,
            "rule_diff": {
                "canonical_strategy_parameter": (
                    "minimum_reward_to_middle_band_r"
                ),
                "component_type": "filter",
                "single_component_only": True,
                "fixed_rules": {
                    "trend_setup_confirmation_trigger": "unchanged_1h_15m_5m",
                    "max_stop_distance_fraction": 0.0045,
                    "risk_per_trade_equity_fraction": 0.0025,
                    "leverage_for_this_ablation": 1.0,
                    "exit_rules": "unchanged",
                    "cost_and_funding_model": "unchanged",
                },
                "parameter_mapping": {
                    "current": {"minimum_reward_r": 1.0},
                    "moderate": {"minimum_reward_r": 0.8},
                    "loose": {"minimum_reward_r": 0.6},
                },
                "deferred_separate_hypothesis": {
                    "exchange_leverage": 20.0,
                    "approved_for_current_trials": False,
                },
            },
        },
        source_snapshot="fixture",
        created_at="2026-07-29T00:00:00Z",
        immutable=True,
    )
    evaluator._validate_component_candidate(candidate)
    settings = evaluator._component_settings(
        {"minimum_reward_r": 0.8},
        {
            "max_stop_distance_fraction": 0.0045,
            "minimum_reward_r": 1.0,
            "early_exit_bars_5m": 3,
        },
    )
    assert settings == {
        "max_stop_distance_fraction": 0.0045,
        "minimum_reward_r": 0.8,
        "early_exit_bars": 3,
    }

    invalid = StrategyVersion(
        id="invalid",
        strategy_id="strategy",
        version=2,
        status="candidate",
        content_snapshot={
            **candidate.content_snapshot,
            "rule_diff": {
                **candidate.content_snapshot["rule_diff"],
                "fixed_rules": {
                    **candidate.content_snapshot["rule_diff"]["fixed_rules"],
                    "leverage_for_this_ablation": 20.0,
                },
            },
        },
        source_snapshot="fixture",
        created_at="2026-07-29T00:00:00Z",
        immutable=True,
    )
    try:
        evaluator._validate_component_candidate(invalid)
        raise AssertionError("20x must remain a separate execution hypothesis")
    except ValueError as exc:
        assert "fixed-risk" in str(exc)

    assert isinstance(evaluator, BollRsiSlopeMinimumRewardComponentEvaluator)


def test_boll_rsi_backtester_accepts_reviewed_sub_one_r_thresholds() -> None:
    execution_model = ExecutionModelCatalog(ROOT).get(venue="binance")

    for minimum_reward_r in (0.6, 0.8, 1.0):
        backtester = BollRsiSlopeBacktester(
            strategy_version_id="version_fixture",
            execution_model=execution_model,
            minimum_reward_r=minimum_reward_r,
        )
        assert backtester.minimum_reward_r == minimum_reward_r

    for invalid_value in (0.0, -0.1):
        try:
            BollRsiSlopeBacktester(
                strategy_version_id="version_fixture",
                execution_model=execution_model,
                minimum_reward_r=invalid_value,
            )
            raise AssertionError("non-positive minimum reward must be rejected")
        except ValueError as exc:
            assert "must be positive" in str(exc)
