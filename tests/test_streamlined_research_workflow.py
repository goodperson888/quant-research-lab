from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quant_lab.application.failure_diagnostics import (
    ArtifactLossAttribution,
    DeterministicComponentHypothesisGenerator,
)
from quant_lab.application.research_authorization import (
    AuthorizedPipelineOrchestrator,
    ResearchAuthorizationService,
)
from quant_lab.application.services import ResearchApplicationService, new_id, utc_now
from quant_lab.domain.errors import ApprovalRequiredError, GatePolicyError
from quant_lab.domain.models import Job, StrategyOutcome
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.strategy_evaluators import (
    SelectiveReentryComponentEvaluator,
    StrategyPluginRegistry,
    StrategySpec,
)
from quant_lab.interfaces.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _baseline(tmp_path: Path):
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    research = ResearchApplicationService(repository)
    session = research.create_research_session(title="streamlined workflow")
    draft = research.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="closed-candle fixture",
    )
    baseline = research.freeze_baseline(
        draft_id=draft.id, confirmed_by_user=True
    )
    return repository, session, baseline


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_one_authorization_advances_to_viability_without_stage_approvals(
    tmp_path: Path,
) -> None:
    repository, session, baseline = _baseline(tmp_path)
    service = ResearchAuthorizationService(repository)
    authorization = service.create(
        subject_id=baseline.id,
        session_id=session.id,
        allowed_stages=("correctness", "smoke", "fast_screen", "viability"),
        auto_continue=True,
        max_cost_usdt=0,
        max_time_minutes=10,
        max_trials=0,
        locked_test_allowed=False,
        stop_conditions=("gate_failed", "scope_completed"),
        expires_at=_expiry(),
        confirmed_by_user=True,
    )
    calls: list[str] = []

    def execute(stage, _authorization):
        calls.append(stage)
        return {
            "status": "passed",
            "reason": "fixture passed",
            "reran_backtest": False,
        }

    result = AuthorizedPipelineOrchestrator(
        service, stage_executor=execute
    ).run(authorization.id)

    assert calls == ["correctness", "smoke", "fast_screen", "viability"]
    assert result["status"] == "completed"
    assert result["locked_test_used"] is False
    assert [item.stage for item in service.stages(authorization.id)] == calls


def test_authorization_stops_on_gate_failure_and_blocks_scope_escape(
    tmp_path: Path,
) -> None:
    repository, session, baseline = _baseline(tmp_path)
    service = ResearchAuthorizationService(repository)
    authorization = service.create(
        subject_id=baseline.id,
        session_id=session.id,
        allowed_stages=("correctness", "smoke", "fast_screen", "viability"),
        auto_continue=True,
        max_cost_usdt=0,
        max_time_minutes=10,
        max_trials=0,
        locked_test_allowed=False,
        stop_conditions=("gate_failed",),
        expires_at=_expiry(),
        confirmed_by_user=True,
    )

    def execute(stage, _authorization):
        return {
            "status": "failed" if stage == "smoke" else "passed",
            "reason": f"{stage} fixture result",
            "reran_backtest": False,
        }

    result = AuthorizedPipelineOrchestrator(
        service, stage_executor=execute
    ).run(authorization.id)
    assert result["status"] == "stopped"
    assert result["completed_stages"] == ["correctness", "smoke"]
    with pytest.raises(ApprovalRequiredError, match="outside"):
        service.assert_stage_allowed(
            authorization.id, subject_id=baseline.id, stage="regime_diagnostic"
        )
    with pytest.raises(GatePolicyError, match="locked test"):
        service.create(
            subject_id=baseline.id,
            session_id=session.id,
            allowed_stages=("correctness",),
            auto_continue=True,
            max_cost_usdt=0,
            max_time_minutes=10,
            max_trials=0,
            locked_test_allowed=True,
            stop_conditions=("gate_failed",),
            expires_at=_expiry(),
            confirmed_by_user=True,
        )


def test_viability_failure_auto_continues_only_into_authorized_diagnostics(
    tmp_path: Path,
) -> None:
    repository, session, baseline = _baseline(tmp_path)
    service = ResearchAuthorizationService(repository)
    authorization = service.create(
        subject_id=baseline.id,
        session_id=session.id,
        allowed_stages=(
            "correctness",
            "smoke",
            "fast_screen",
            "viability",
            "loss_attribution",
            "regime_diagnostic",
            "component_hypothesis_generation",
        ),
        auto_continue=True,
        max_cost_usdt=0,
        max_time_minutes=10,
        max_trials=0,
        locked_test_allowed=False,
        stop_conditions=("gate_failed", "scope_completed"),
        expires_at=_expiry(),
        confirmed_by_user=True,
    )
    calls: list[str] = []

    def execute(stage, _authorization):
        calls.append(stage)
        return {
            "status": "failed" if stage == "viability" else "passed",
            "reason": f"{stage} fixture result",
            "reran_backtest": False,
        }

    result = AuthorizedPipelineOrchestrator(
        service,
        stage_executor=execute,
    ).run(authorization.id)
    assert calls == [
        "correctness",
        "smoke",
        "fast_screen",
        "viability",
        "loss_attribution",
        "regime_diagnostic",
        "component_hypothesis_generation",
    ]
    assert result["status"] == "completed"
    assert {
        item.stage: item.status for item in service.stages(authorization.id)
    }["viability"] == "failed"
    assert result["locked_test_used"] is False
    handoff = repository.get_latest_session_handoff(session.id)
    assert (
        handoff.stop_reason_code
        == "authorized_pipeline_completed_after_viability_failure_diagnostics"
    )
    assert handoff.subject_id == baseline.id
    assert "locked test" in " ".join(handoff.not_started_actions)


def test_rejected_outcome_allows_diagnostics_but_blocks_core_rerun(
    tmp_path: Path,
) -> None:
    repository, session, baseline = _baseline(tmp_path)
    repository.create_strategy_outcome(
        StrategyOutcome(
            id=new_id("outcome"),
            strategy_version_id=baseline.id,
            market_profile="crypto_perpetual.binance.eth",
            pipeline_profile_id="fast_screen",
            outcome_type="rejected",
            viability_gate_result_id=None,
            evidence_artifact_keys=("experiments/runs/fixture/manifest.json",),
            notes="fixture viability rejection",
            created_at=utc_now(),
        )
    )
    service = ResearchAuthorizationService(repository)
    with pytest.raises(GatePolicyError, match="rejected strategy"):
        service.create(
            subject_id=baseline.id,
            session_id=session.id,
            allowed_stages=("correctness", "smoke"),
            auto_continue=True,
            max_cost_usdt=0,
            max_time_minutes=10,
            max_trials=0,
            locked_test_allowed=False,
            stop_conditions=("gate_failed",),
            expires_at=_expiry(),
            confirmed_by_user=True,
        )

    authorization = service.create(
        subject_id=baseline.id,
        session_id=session.id,
        allowed_stages=(
            "loss_attribution",
            "regime_diagnostic",
            "component_hypothesis_generation",
        ),
        auto_continue=True,
        max_cost_usdt=0,
        max_time_minutes=10,
        max_trials=0,
        locked_test_allowed=False,
        stop_conditions=("scope_completed",),
        expires_at=_expiry(),
        confirmed_by_user=True,
    )
    job = ResearchApplicationService(repository).create_job(
        job_type="research_diagnostic",
        payload={
            "authorization_id": authorization.id,
            "session_id": session.id,
            "subject_id": baseline.id,
            "fast_screen_manifest_artifact_key": "experiments/runs/fixture/manifest.json",
            "metrics_artifact_key": "experiments/runs/fixture/metrics.json",
            "trades_artifact_key": "experiments/runs/fixture/trades.parquet",
            "signals_artifact_key": "experiments/runs/fixture/signals.parquet",
            "data_manifest_artifact_key": "data/manifests/fixture.json",
            "detector_config_artifact_key": "configs/regimes/fixture.yaml",
            "locked_test_used": False,
        },
    )
    assert job.status == "queued"


def test_artifact_loss_attribution_is_split_and_non_causal() -> None:
    trades = pd.DataFrame(
        {
            "trade_id": [1, 2, 3, 4],
            "side": ["long", "short", "long", "short"],
            "entry_time": pd.to_datetime(
                [
                    "2026-01-01T00:00:00Z",
                    "2026-01-02T08:00:00Z",
                    "2026-02-01T12:00:00Z",
                    "2026-02-02T20:00:00Z",
                ]
            ),
            "exit_time": pd.to_datetime(
                [
                    "2026-01-01T00:30:00Z",
                    "2026-01-02T09:30:00Z",
                    "2026-02-01T12:15:00Z",
                    "2026-02-02T22:00:00Z",
                ]
            ),
            "entry_raw_price": [100.0, 100.0, 100.0, 100.0],
            "initial_stop_price": [99.5, 100.5, 99.0, 101.0],
            "exit_reason": ["time_stop", "protective_stop", "time_stop", "max_holding_time"],
            "fees": [1.0, 1.0, 1.0, 1.0],
            "price_pnl": [2.0, -3.0, -1.0, -2.0],
            "funding_pnl": [0.0, 0.0, 0.0, 0.0],
            "net_pnl": [1.0, -4.0, -2.0, -3.0],
            "net_return_on_entry_equity": [0.001, -0.004, -0.002, -0.003],
            "is_reentry": [False, True, False, True],
            "holding_minutes": [30.0, 90.0, 15.0, 120.0],
            "trend_leg_number": [1, 2, 3, 4],
            "split": ["train", "train", "validation", "validation"],
        }
    )
    signals = pd.DataFrame(
        {
            "signal_time": trades["entry_time"],
            "side": trades["side"],
            "trend_leg_number": trades["trend_leg_number"],
            "status": ["filled"] * 4,
            "split": trades["split"],
        }
    )
    metrics = {
        "train": {"metrics": {"skipped_setups": {"confirmation_range": 2}}},
        "validation": {"metrics": {"skipped_setups": {"stop_distance_ineligible": 1}}},
    }
    report = ArtifactLossAttribution().analyze(
        trades=trades, signals=signals, metrics=metrics
    )

    assert set(report["splits"]) == {"train", "validation"}
    assert report["reran_strategy"] is False
    assert report["causal_claim_allowed"] is False
    assert report["signal_funnel"]["validation"]["filled_entries"] == 2
    assert (
        report["signal_funnel"]["validation"]["pullback_candidates_15m"]["count"]
        is None
    )
    assert report["multi_timeframe"]["executed"] is True
    assert report["multi_timeframe"]["walk_forward_or_multi_period_executed"] is False


def test_component_hypotheses_are_bounded_and_contaminated_screening() -> None:
    attribution = {
        "splits": {
            "validation": {
                "by_side": {
                    "long": {"net_return": -0.02},
                    "short": {"net_return": -0.01},
                },
                "by_first_entry_or_reentry": {
                    "first_entry": {"net_return": -0.01},
                    "reentry": {"net_return": -0.02},
                },
                "by_exit_reason": {
                    "time_stop": {"net_return": -0.03},
                    "take_profit": {"net_return": 0.01},
                },
            }
        }
    }
    drafts = DeterministicComponentHypothesisGenerator().generate(
        session_id="session_fixture",
        subject_id="version_fixture",
        attribution=attribution,
        evidence_refs=("reports/diagnostics/fixture.json",),
    )
    assert len(drafts) == 3
    assert all(3 <= item.suggested_trials <= 5 for item in drafts)
    assert all(item.source == "deterministic_rule_analyzer" for item in drafts)
    assert all(item.contamination_status == "screening_contaminated" for item in drafts)
    assert all(item.status == "draft" for item in drafts)


def test_strategy_plugin_registry_and_single_component_guard() -> None:
    routed: list[str] = []

    def handler(job: Job):
        routed.append(str(job.payload["intent"]))
        return {"ok": True}

    registry = StrategyPluginRegistry(
        (
            StrategySpec(
                strategy_spec_id="fixture",
                backtest_handlers={"fixture_smoke": handler},
                evaluator_ids=("fixture_evaluator",),
            ),
        )
    )
    result = registry.route_backtest(
        Job(
            id="job_fixture",
            job_type="backtest",
            status="queued",
            payload={"intent": "fixture_smoke"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    assert result == {"ok": True}
    assert routed == ["fixture_smoke"]
    SelectiveReentryComponentEvaluator._validate_single_component(
        {"max_reentries_per_trend_leg": 1}
    )
    with pytest.raises(ValueError, match="exactly one component"):
        SelectiveReentryComponentEvaluator._validate_single_component(
            {"max_reentries_per_trend_leg": 1, "enabled_side": "both"}
        )


def test_authorization_api_uses_explicit_subject_and_openapi_stays_safe(
    tmp_path: Path,
) -> None:
    shutil.copytree(ROOT / "configs/pipelines", tmp_path / "configs/pipelines")
    target = tmp_path / "configs/research_authorizations/guided-to-viability.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        ROOT / "configs/research_authorizations/guided-to-viability.yaml",
        target,
    )
    repository, session, baseline = _baseline(tmp_path)
    app = create_app(
        root=tmp_path, database_path=tmp_path / "runtime/app/test.sqlite3"
    )
    client = TestClient(app)
    response = client.post(
        "/api/research-authorizations",
        json={
            "subject_id": baseline.id,
            "session_id": session.id,
            "allowed_stages": ["correctness", "smoke", "fast_screen", "viability"],
            "auto_continue": True,
            "max_cost_usdt": 0,
            "max_time_minutes": 10,
            "max_trials": 0,
            "locked_test_allowed": False,
            "stop_conditions": ["gate_failed"],
            "expires_at": _expiry(),
            "confirmed_by_user": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["subject_id"] == baseline.id
    paths = " ".join(client.get("/openapi.json").json()["paths"]).lower()
    for forbidden in ("live_trade", "/trade", "/shell", "credential", "api-key"):
        assert forbidden not in paths
