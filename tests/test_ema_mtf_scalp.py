from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab.application.ema_mtf_scalp import EmaMtfScalpBacktester
from quant_lab.application.ports import TrialEvaluationRequest
from quant_lab.application.failure_diagnostics import ArtifactLossAttribution
from quant_lab.infrastructure.builtin_strategy_plugins import (
    build_builtin_evaluator_registry,
    build_builtin_strategy_plugins,
)
from quant_lab.infrastructure.execution_models import ExecutionModelCatalog
from quant_lab.infrastructure.research_diagnostics_runner import (
    _manifest_strategy_subject,
    _manifest_validation_end,
)
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository


ROOT = Path(__file__).resolve().parents[1]


def _ohlcv(index: pd.DatetimeIndex, close: np.ndarray) -> pd.DataFrame:
    previous = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "timestamp": index,
            "open": previous,
            "high": np.maximum(previous, close) + 7.0,
            "low": np.minimum(previous, close) - 7.0,
            "close": close,
            "volume": 1000.0,
        }
    )


def _resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    source = frame.set_index("timestamp")
    return (
        source.resample(rule, label="left", closed="left")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
        .reset_index()
    )


def test_ema_mtf_scalp_uses_closed_multitimeframe_bars_and_finite_metrics() -> None:
    index = pd.date_range("2025-01-01", periods=12 * 24 * 10, freq="5min", tz="UTC")
    steps = np.arange(len(index), dtype=float)
    close = 3000.0 + steps * 0.035 + 12.0 * np.sin(steps / 11.0)
    bars_5m = _ohlcv(index, close)
    bars_15m = _resample(bars_5m, "15min")
    bars_1h = _resample(bars_5m, "1h")
    funding_index = pd.date_range(
        "2025-01-01", "2025-01-11", freq="8h", inclusive="left", tz="UTC"
    )
    funding = pd.DataFrame(
        {
            "timestamp": funding_index,
            "last_funding_rate": 0.0001,
        }
    )
    result = EmaMtfScalpBacktester(
        strategy_version_id="version_fixture",
        execution_model=ExecutionModelCatalog(ROOT).get(venue="binance"),
        leverage=20.0,
    ).run(
        ohlcv_5m=bars_5m,
        ohlcv_15m=bars_15m,
        ohlcv_1h=bars_1h,
        funding=funding,
        warmup_start_utc_inclusive="2025-01-01T00:00:00Z",
        start_utc_inclusive="2025-01-04T00:00:00Z",
        end_utc_exclusive="2025-01-10T00:00:00Z",
    )

    assert result.data_quality["missing_5m_rows"] == 0
    assert result.execution["leverage"] == 20.0
    assert result.execution["leverage_safety_proven"] is False
    assert result.funding["silent_zero_allowed"] is False
    assert result.signal_funnel["trend_bars_1h"]["long"] > 0
    assert set(result.metrics) >= {
        "total_return",
        "profit_factor",
        "expectancy",
        "trade_count",
        "max_drawdown",
    }
    assert np.isfinite(list(result.metrics.values())).all()
    if not result.signals.empty:
        assert (
            pd.to_datetime(result.signals["signal_time"], utc=True)
            > pd.to_datetime(result.signals["setup_event_time"], utc=True)
        ).all()


def test_ema_mtf_scalp_is_deterministic_and_never_reads_locked_test() -> None:
    config = (
        ROOT / "configs/research/ema_mtf_scalp_20x_v0.yaml"
    ).read_text(encoding="utf-8")
    assert "use_in_this_authorization: false" in config
    assert "leverage_explicitly_requested: true" in config
    assert "live trade" in config


def test_ema_structure_exit_variants_are_explicit_and_single_component() -> None:
    execution_model = ExecutionModelCatalog(ROOT).get(venue="binance")
    current = EmaMtfScalpBacktester(
        strategy_version_id="version_fixture",
        execution_model=execution_model,
        leverage=20.0,
        structure_exit_variant="current",
    )
    tighter = EmaMtfScalpBacktester(
        strategy_version_id="version_fixture",
        execution_model=execution_model,
        leverage=20.0,
        structure_exit_variant="tighter",
    )
    looser = EmaMtfScalpBacktester(
        strategy_version_id="version_fixture",
        execution_model=execution_model,
        leverage=20.0,
        structure_exit_variant="looser",
    )

    assert current._structure_exit_update(
        side="long",
        close=99.0,
        ema9=98.0,
        ema21=100.0,
        adverse_ema21_closes=0,
    ) == ("ema21_structure_exit", 1)
    assert tighter._structure_exit_update(
        side="long",
        close=99.0,
        ema9=100.0,
        ema21=98.0,
        adverse_ema21_closes=0,
    ) == ("ema9_structure_exit", 0)
    assert looser._structure_exit_update(
        side="short",
        close=101.0,
        ema9=102.0,
        ema21=100.0,
        adverse_ema21_closes=1,
    ) == ("ema21_two_close_structure_exit", 2)


def test_ema_component_evaluator_is_registered_and_rejects_locked_test(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "product.sqlite3")
    repository.initialize()
    evaluator = build_builtin_evaluator_registry(ROOT, repository).get(
        "ema_mtf_scalp_exit_component_v1"
    )
    specs = {
        item.strategy_spec_id: item
        for item in build_builtin_strategy_plugins(ROOT, repository).list_specs()
    }
    assert specs["ema_mtf_pullback_scalp_20x_v0"].evaluator_ids == (
        "ema_mtf_scalp_exit_component_v1",
    )

    result = evaluator.evaluate(
        TrialEvaluationRequest(
            trial_id="trial_locked",
            experiment_plan_id="plan_fixture",
            baseline_version_id="version_baseline",
            candidate_version_id="version_candidate",
            parameters={"structure_exit_variant": "current"},
            data_version="data_fixture",
            data_splits={
                "train": "train",
                "validation": "screening_contaminated",
                "locked_test": "forbidden",
            },
            cost_model={},
            seed=1,
        )
    )
    assert result.status == "failed"
    assert "locked-test" in str(result.error)


def test_ema_fast_screen_artifacts_fit_shared_diagnostic_contract() -> None:
    trades = pd.DataFrame(
        {
            "side": ["long", "short"],
            "entry_time": pd.to_datetime(
                ["2026-03-21T01:00:00Z", "2026-03-22T13:00:00Z"]
            ),
            "exit_time": pd.to_datetime(
                ["2026-03-21T01:15:00Z", "2026-03-22T13:20:00Z"]
            ),
            "entry_price": [2000.0, 2100.0],
            "stop_price": [1990.0, 2112.0],
            "exit_reason": ["protective_stop", "ema21_structure_exit"],
            "fees_and_liquidation_cost": [2.0, 2.1],
            "price_pnl": [-10.0, 5.0],
            "funding_pnl": [0.0, -0.1],
            "net_pnl": [-12.0, 2.8],
            "return_on_initial_equity": [-0.0012, 0.00028],
            "holding_minutes": [15.0, 20.0],
            "split": ["validation", "validation"],
        }
    )
    signals = pd.DataFrame(
        {
            "signal_time": trades["entry_time"],
            "side": trades["side"],
            "status": ["entered", "entered"],
            "reason": ["next_bar_market_entry", "next_bar_market_entry"],
            "split": trades["split"],
        }
    )
    report = ArtifactLossAttribution().analyze(
        trades=trades,
        signals=signals,
        metrics={
            "validation": {
                "metrics": {},
                "signal_funnel": {
                    "trend_bars_1h": {"long": 10, "short": 12},
                    "pullback_candidates_15m": {"long": 4, "short": 5},
                    "triggers_5m": 3,
                    "entry_candidates": 2,
                    "entered": 2,
                    "skipped": {"cooldown": 1},
                },
            }
        },
    )

    assert report["reran_strategy"] is False
    assert report["splits"]["validation"]["entry_type_availability"] == "not_recorded"
    assert (
        report["signal_funnel"]["validation"]["pullback_candidates_15m"]["count"]
        == 9
    )
    assert report["signal_funnel"]["validation"]["filled_entries"] == 2

    manifest = {
        "strategy": {"strategy_version_id": "version_fixture"},
        "time_range_or_splits": {
            "validation": {"end_utc_exclusive": "2026-06-20T00:00:00Z"}
        },
    }
    assert _manifest_strategy_subject(manifest) == "version_fixture"
    assert _manifest_validation_end(manifest) == "2026-06-20T00:00:00Z"
