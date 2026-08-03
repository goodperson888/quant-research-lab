from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
from fastapi.testclient import TestClient

from quant_lab.application.strategy_dsl import (
    GenericStrategyDslBacktester,
    analyze_strategy_dsl,
    attach_execution_readiness,
)
from quant_lab.application.services import ResearchApplicationService, utc_now
from quant_lab.domain.models import ComponentHypothesis, Report
from quant_lab.infrastructure.builtin_strategy_plugins import (
    build_builtin_evaluator_registry,
    build_builtin_strategy_plugins,
)
from quant_lab.infrastructure.sqlite_product_repository import (
    SQLiteProductRepository,
)
from quant_lab.interfaces.api.app import create_app


def _dsl(*, timeframe: str = "15m", leverage: float = 1.0) -> dict:
    return {
        "schema_version": 1,
        "market_profile": "crypto_perpetual.binance.eth",
        "execution_timeframe": "15m",
        "parameters": {
            "sma_period": 2,
            "stop": 0.05,
            "take": 0.05,
            "hold": 4,
        },
        "indicators": [
            {
                "id": "trend",
                "type": "sma",
                "timeframe": timeframe,
                "source": "close",
                "period": "$sma_period",
            }
        ],
        "entries": {
            "long": {
                "operator": "gt",
                "left": "close",
                "right": "trend",
            },
            "short": None,
        },
        "exits": {
            "stop_loss_fraction": "$stop",
            "take_profit_fraction": "$take",
            "max_holding_bars": "$hold",
        },
        "risk": {
            "risk_per_trade_fraction": 0.005,
            "leverage": leverage,
        },
    }


def _ohlcv(
    timestamps: pd.DatetimeIndex,
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * len(timestamps),
        }
    )


def _funding() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex([], tz="UTC"),
            "last_funding_rate": pd.Series(dtype=float),
        }
    )


def test_dsl_capability_and_execution_readiness() -> None:
    structured = attach_execution_readiness({"strategy_dsl": _dsl()})
    assert structured["execution_readiness"]["status"] == "executable"
    assert structured["strategy_spec_id"] == "generic_strategy_dsl_v1"

    unsupported = _dsl()
    unsupported["indicators"][0]["type"] = "macd"
    capability = analyze_strategy_dsl({"strategy_dsl": unsupported})
    assert capability.executable is False
    assert any("macd" in reason for reason in capability.reasons)

    leveraged = analyze_strategy_dsl(
        {"strategy_dsl": _dsl(leverage=2.0)}
    )
    assert leveraged.executable is False
    assert any("1 倍杠杆" in reason for reason in leveraged.reasons)


def test_next_bar_open_execution_and_same_bar_stop_precedence() -> None:
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=6, freq="15min"
    )
    frame = _ohlcv(
        timestamps,
        opens=[100, 100, 100, 100, 100, 100],
        highs=[101, 101, 102, 110, 101, 101],
        lows=[99, 99, 99, 90, 99, 99],
        closes=[100, 100, 102, 100, 100, 100],
    )
    result = GenericStrategyDslBacktester(
        strategy_version_id="version_test",
        dsl=_dsl(),
        fee_per_side=0.0,
        slippage_bps_per_side=0.0,
    ).run(
        label="validation",
        ohlcv_by_timeframe={"15m": frame},
        funding=_funding(),
        warmup_start_utc_inclusive=timestamps[0].isoformat(),
        start_utc_inclusive=timestamps[0].isoformat(),
        end_utc_exclusive=(timestamps[-1] + pd.Timedelta(minutes=15)).isoformat(),
    )

    first_signal = pd.Timestamp(result.signals.iloc[0]["signal_time"])
    first_trade = result.trades.iloc[0]
    assert pd.Timestamp(first_trade["entry_time"]) == first_signal + pd.Timedelta(
        minutes=15
    )
    assert first_trade["entry_raw_price"] == 100.0
    assert first_trade["exit_reason"] == "protective_stop"


def test_higher_timeframe_indicator_uses_only_prior_closed_bar() -> None:
    execution_times = pd.date_range(
        "2026-01-01T00:00:00Z", periods=13, freq="15min"
    )
    execution = _ohlcv(
        execution_times,
        opens=[20.0] * 13,
        highs=[21.0] * 13,
        lows=[19.0] * 13,
        closes=[20.0] * 13,
    )
    hourly_times = pd.date_range(
        "2026-01-01T00:00:00Z", periods=4, freq="1h"
    )
    hourly = _ohlcv(
        hourly_times,
        opens=[10.0, 100.0, 200.0, 300.0],
        highs=[11.0, 101.0, 201.0, 301.0],
        lows=[9.0, 99.0, 199.0, 299.0],
        closes=[10.0, 100.0, 200.0, 300.0],
    )
    strategy = GenericStrategyDslBacktester(
        strategy_version_id="version_test",
        dsl=_dsl(timeframe="1h"),
        fee_per_side=0.0,
        slippage_bps_per_side=0.0,
    )
    features = strategy._prepare_features({"15m": execution, "1h": hourly})
    assert pd.isna(features.loc[pd.Timestamp("2026-01-01T00:45:00Z"), "trend"])
    assert features.loc[pd.Timestamp("2026-01-01T02:15:00Z"), "trend"] == 55.0


def test_generic_strategy_plugin_and_evaluator_are_registered(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    repository.initialize()
    spec = build_builtin_strategy_plugins(tmp_path, repository).get_spec(
        "generic_strategy_dsl_v1"
    )
    assert spec.config_resolver is not None
    assert spec.smoke_intent == "generic_strategy_dsl_smoke"
    assert (
        build_builtin_evaluator_registry(tmp_path, repository)
        .get("generic_strategy_dsl_v1")
        .evaluator_id
        == "generic_strategy_dsl_v1"
    )


def test_launch_batch_creates_candidate_plan_and_bounded_job(
    tmp_path: Path,
) -> None:
    manifest = (
        tmp_path
        / "data/manifests/binance_ethusdt_perpetual_20240720_20260720_v2.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "fixture",
                "data_version": "fixture-data-v1",
                "range": {
                    "start_utc_inclusive": "2024-07-20T00:00:00+00:00",
                    "end_utc_exclusive": "2026-07-20T00:00:00+00:00",
                },
                "processed_datasets": [],
            }
        ),
        encoding="utf-8",
    )
    database_path = tmp_path / "runtime/app/api.sqlite3"
    client = TestClient(
        create_app(
            root=tmp_path,
            database_path=database_path,
        )
    )
    session = client.post(
        "/api/research/sessions", json={"title": "DSL batch"}
    ).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={
            "source_type": "natural_language",
            "raw_content": "15m close above SMA, next bar open entry",
        },
    ).json()
    formalized = client.post(
        f"/api/strategy-drafts/{draft['id']}/formalize",
        json={
            "subject_id": draft["id"],
            "confirmed_by_user": True,
            "structured_content": {"strategy_name": "DSL fixture", "strategy_dsl": _dsl()},
        },
    )
    assert formalized.status_code == 200
    baseline = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"subject_id": draft["id"], "confirmed_by_user": True},
    ).json()
    direction = client.post(
        "/api/improvement-directions",
        json={
            "baseline_version_id": baseline["id"],
            "subject_id": baseline["id"],
            "hypothesis": "小范围调整止盈可改善验证期稳定性",
            "rule_diff": {"take": "只调整已声明的止盈参数"},
            "evidence_refs": [],
            "parameter_space": [
                {
                    "name": "take",
                    "kind": "float",
                    "values": [0.04, 0.05, 0.06],
                }
            ],
            "data_splits": {
                "train": "2024-08-19/2026-03-22",
                "validation": "2026-03-22/2026-06-20",
                "locked_test": "2026-06-20/2026-07-20",
            },
            "cost_model": {
                "fee_per_side": 0.0005,
                "slippage_bps_per_side": 2.0,
            },
            "objectives": [
                {
                    "metric": "validation_net_return",
                    "direction": "maximize",
                }
            ],
            "constraints": [
                {
                    "metric": "validation_trade_count",
                    "operator": "gte",
                    "value": 20,
                }
            ],
            "estimated_trials": 3,
            "estimated_minutes": 5,
            "failure_conditions": ["没有稳定参数区间"],
            "stopping_conditions": ["达到三个已批准方案后停止"],
            "rollback_plan": "保留冻结 Baseline，放弃候选",
            "source": "manual",
        },
    ).json()
    submitted = client.post(
        f"/api/improvement-directions/{direction['id']}/submit"
    )
    assert submitted.status_code == 200
    launched = client.post(
        f"/api/improvement-directions/{direction['id']}/launch-batch",
        json={
            "subject_id": direction["id"],
            "confirmed_by_user": True,
        },
    )
    assert launched.status_code == 200, launched.text
    payload = launched.json()
    assert payload["proposal"]["status"] == "executing"
    assert payload["candidate"]["immutable"] is True
    assert payload["plan"]["status"] == "approved"
    assert payload["job"]["status"] == "queued"
    assert payload["job"]["payload"]["evaluator_id"] == "generic_strategy_dsl_v1"
    assert payload["job"]["payload"]["locked_test_used"] is False

    repository = SQLiteProductRepository(database_path)
    service = ResearchApplicationService(repository)
    plan_id = payload["plan"]["id"]
    for index, take in enumerate((0.04, 0.05), start=1):
        service.record_trial(
            experiment_plan_id=plan_id,
            parameters={"take": take},
            data_version="fixture-data-v1",
            status="succeeded",
            candidate_version_id=payload["candidate"]["id"],
            metrics={
                "train_net_return": 0.08 + index * 0.01,
                "validation_net_return": 0.06 + index * 0.01,
                "validation_profit_factor": 1.2 + index * 0.05,
                "validation_expectancy": 0.001,
                "validation_trade_count": 30.0,
                "validation_max_drawdown_abs": 0.1,
            },
        )
    batch_job = repository.get_job(payload["job"]["id"])
    repository.update_job(
        batch_job.id,
        status="succeeded",
        updated_at=batch_job.updated_at,
    )
    validation = client.post(
        f"/api/experiment-plans/{plan_id}/launch-candidate-validation",
        json={"subject_id": plan_id, "confirmed_by_user": True},
    )
    assert validation.status_code == 201, validation.text
    assert validation.json()["payload"]["stress_level"] == (
        "bounded_candidate_validation"
    )
    assert validation.json()["payload"]["locked_test_used"] is False
    validation_job = validation.json()
    repository.create_report(
        Report(
            id="report_candidate_validation",
            job_id=validation_job["id"],
            report_type="generic_candidate_validation",
            artifact_key="reports/experiments/candidate-validation.md",
            summary={
                "experiment_plan_id": plan_id,
                "locked_test_used": False,
                "decision": {
                    "status": "needs_revision",
                    "cost_sensitivity_passed": True,
                    "positive_rolling_windows": 1,
                    "rolling_window_count": 3,
                    "perturbation_pass_ratio": 0.5,
                    "regime_evidence_sufficient": True,
                    "locked_test_automatically_started": False,
                    "next_action": "不要扩大搜索；审阅最弱证据并决定停止。",
                },
            },
            created_at=utc_now(),
        )
    )
    repository.update_job(
        validation_job["id"],
        status="succeeded",
        updated_at=utc_now(),
    )
    summary = client.get(
        f"/api/experiment-plans/{plan_id}/candidate-validation-summary"
    )
    assert summary.status_code == 200
    assert summary.json()["decision_status"] == "needs_revision"
    assert summary.json()["weakest_evidence"] == [
        "滚动窗口中的正向区间不足",
        "参数轻微扰动后的稳定性不足",
    ]
    assert summary.json()["locked_test_automatically_started"] is False
    duplicate = client.post(
        f"/api/experiment-plans/{plan_id}/launch-candidate-validation",
        json={"subject_id": plan_id, "confirmed_by_user": True},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == validation_job["id"]

    repository.create_report(
        Report(
            id="report_candidate_validation_ready",
            job_id=validation_job["id"],
            report_type="generic_candidate_validation",
            artifact_key=(
                "reports/experiments/candidate-validation-ready.md"
            ),
            summary={
                "experiment_plan_id": plan_id,
                "locked_test_used": False,
                "decision": {
                    "status": "ready_for_locked_test_review",
                    "cost_sensitivity_passed": True,
                    "positive_rolling_windows": 3,
                    "rolling_window_count": 3,
                    "perturbation_pass_ratio": 1.0,
                    "regime_evidence_sufficient": True,
                    "locked_test_automatically_started": False,
                    "next_action": "单独批准一次最终保留测试。",
                },
            },
            created_at=utc_now(),
        )
    )
    locked = client.post(
        f"/api/experiment-plans/{plan_id}/launch-locked-test",
        json={"subject_id": plan_id, "confirmed_by_user": True},
    )
    assert locked.status_code == 201, locked.text
    assert (
        locked.json()["payload"]["intent"]
        == "generic_strategy_dsl_locked_test"
    )
    assert locked.json()["payload"]["locked_test_used"] is True
    assert (
        service.get_research_budget(session["id"]).used_locked_test_uses
        == 1
    )
    duplicate_locked = client.post(
        f"/api/experiment-plans/{plan_id}/launch-locked-test",
        json={"subject_id": plan_id, "confirmed_by_user": True},
    )
    assert duplicate_locked.status_code == 201
    assert duplicate_locked.json()["id"] == locked.json()["id"]
    assert (
        service.get_research_budget(session["id"]).used_locked_test_uses
        == 1
    )


def test_component_hypothesis_materializes_reviewable_proposal_without_candidate(
    tmp_path: Path,
) -> None:
    manifest = (
        tmp_path
        / "data/manifests/binance_ethusdt_perpetual_20240720_20260720_v2.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "dataset_id": "fixture",
                "data_version": "fixture-data-v1",
                "range": {
                    "start_utc_inclusive": "2024-07-20T00:00:00+00:00",
                    "end_utc_exclusive": "2026-07-20T00:00:00+00:00",
                },
                "processed_datasets": [],
            }
        ),
        encoding="utf-8",
    )
    database_path = tmp_path / "runtime/app/api.sqlite3"
    client = TestClient(
        create_app(root=tmp_path, database_path=database_path)
    )
    session = client.post(
        "/api/research/sessions", json={"title": "Hypothesis proposal"}
    ).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={
            "source_type": "natural_language",
            "raw_content": "15m close above SMA, next bar open entry",
        },
    ).json()
    client.post(
        f"/api/strategy-drafts/{draft['id']}/formalize",
        json={
            "subject_id": draft["id"],
            "confirmed_by_user": True,
            "structured_content": {
                "strategy_name": "DSL hypothesis fixture",
                "strategy_dsl": _dsl(),
            },
        },
    )
    baseline = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"subject_id": draft["id"], "confirmed_by_user": True},
    ).json()
    repository = SQLiteProductRepository(database_path)
    hypothesis = repository.create_component_hypothesis(
        ComponentHypothesis(
            id="component_hypothesis_take",
            session_id=session["id"],
            subject_id=baseline["id"],
            title="止盈参数小范围验证",
            hypothesis="小范围调整止盈，可能改善验证期稳定性。",
            component_type="exit",
            source="deterministic_rule_analyzer",
            evidence_refs=("reports/diagnostics/take.json",),
            expected_improvement="改善验证净收益且不扩大回撤。",
            parameter_space={"take": [0.04, 0.05, 0.06]},
            suggested_trials=3,
            failure_conditions=("没有稳定参数区间",),
            evidence_level="screening",
            contamination_status="screening_contaminated",
            status="draft",
            created_at=utc_now(),
        )
    )

    created = client.post(
        f"/api/component-hypotheses/{hypothesis.id}/materialize-proposal",
        json={
            "subject_id": hypothesis.id,
            "confirmed_by_user": True,
        },
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert proposal["status"] == "draft"
    assert proposal["baseline_version_id"] == baseline["id"]
    assert proposal["candidate_version_id"] is None
    assert (
        proposal["content"]["source_component_hypothesis_id"]
        == hypothesis.id
    )
    assert proposal["parameter_space"][0]["name"] == "take"
    assert proposal["data_splits"]["locked_test"].endswith(
        "2026-07-20T00:00:00+00:00"
    )

    repeated = client.post(
        f"/api/component-hypotheses/{hypothesis.id}/materialize-proposal",
        json={
            "subject_id": hypothesis.id,
            "confirmed_by_user": True,
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == proposal["id"]
    versions = client.get(
        f"/api/strategy-versions?strategy_id={draft['id']}"
    ).json()
    assert [(item["version"], item["status"]) for item in versions] == [
        (0, "baseline")
    ]
