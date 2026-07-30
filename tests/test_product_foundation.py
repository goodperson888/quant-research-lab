import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import (
    ConflictError,
    ExperimentPlanValidationError,
    InvalidJobError,
)
from quant_lab.domain.models import Constraint, ExperimentPlan, Objective, ParameterSpace
from quant_lab.infrastructure.project_readers import DataSummaryReader
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app
from quant_lab.registry import register_factor


def build_service(tmp_path: Path) -> ResearchApplicationService:
    return ResearchApplicationService(SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3"))


def test_baseline_is_frozen_once_and_never_overwritten(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    session = service.create_research_session(title="immutable baseline")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="Buy when condition A is true.",
    )

    formalized = service.formalize_strategy(
        draft_id=draft.id,
        structured_content={
            "version": "v0.1",
            "entry": {"rule": "confirmed condition A"},
        },
        confirmed_by_user=True,
    )

    baseline = service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)

    assert formalized.status == "awaiting_confirmation"
    assert baseline.version == 0
    assert baseline.immutable is True
    assert baseline.source_snapshot == "Buy when condition A is true."
    assert baseline.content_snapshot == {
        "version": "v0.1",
        "entry": {"rule": "confirmed condition A"},
    }
    with pytest.raises(ConflictError, match="immutable"):
        service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)
    with pytest.raises(ConflictError, match="immutable"):
        service.formalize_strategy(
            draft_id=draft.id,
            structured_content={"version": "v0.2"},
            confirmed_by_user=True,
        )


def test_experiment_plan_requires_budget_splits_cost_and_objective_before_approval() -> None:
    incomplete = ExperimentPlan(
        id="plan_1",
        baseline_version_id="baseline_1",
        hypothesis="EMA length has a stable validation plateau.",
        parameter_space=(),
        objectives=(),
        constraints=(),
        data_splits={},
        cost_model={},
        max_trials=None,
        time_budget_seconds=None,
        stopping_conditions=(),
    )
    with pytest.raises(ExperimentPlanValidationError, match="cannot be approved"):
        incomplete.approve()

    complete = ExperimentPlan(
        id="plan_2",
        baseline_version_id="baseline_1",
        hypothesis="EMA length has a stable validation plateau.",
        parameter_space=(ParameterSpace(name="ema", kind="integer", lower=10, upper=30),),
        objectives=(Objective(metric="validation_sharpe", direction="maximize"),),
        constraints=(Constraint(metric="max_drawdown", operator="lte", value=0.2),),
        data_splits={
            "train": "2026-04-21/2026-06-10",
            "validation": "2026-06-10/2026-07-01",
            "locked_test": "2026-07-01/2026-07-20",
        },
        cost_model={"fee_per_side": 0.0005, "slippage_bps_per_side": 2},
        max_trials=24,
        time_budget_seconds=900,
        stopping_conditions=("stop if validation does not improve after 8 trials",),
    )

    approved = complete.approve()

    assert approved.status == "approved"
    assert approved.approved_by == "user"


def test_job_registry_rejects_shell_or_non_allowlisted_jobs(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    with pytest.raises(InvalidJobError):
        service.create_job(job_type="shell", payload={})
    with pytest.raises(InvalidJobError):
        service.create_job(job_type="backtest", payload={"command": "rm -rf /"})


def test_api_smoke_and_agent_first_status(tmp_path: Path) -> None:
    app = create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["live_trading_enabled"] is False
    api_root = client.get("/")
    assert api_root.status_code == 200
    assert api_root.json()["web_studio"] == "http://127.0.0.1:3100/studio"

    agent = client.get("/api/agent/status").json()
    assert agent["active_configuration"] == {
        "agent_provider": "external_local_agent",
        "execution_target": "local_runtime",
        "data_location": "local_project",
        "privacy_note": "Research inputs and product state remain local in phase 0.",
    }
    assert agent["embedded_provider"]["configured"] is False

    session = client.post("/api/research/sessions", json={"title": "API session"})
    assert session.status_code == 201
    session_id = session.json()["id"]
    draft = client.post(
        f"/api/research/sessions/{session_id}/intakes",
        json={"source_type": "pine", "raw_content": "//@version=6"},
    )
    assert draft.status_code == 201
    draft_id = draft.json()["id"]

    mismatched = client.post(
        f"/api/strategy-drafts/{draft_id}/formalize",
        json={
            "subject_id": "draft_wrong",
            "confirmed_by_user": True,
            "structured_content": {"version": "v0.1"},
        },
    )
    assert mismatched.status_code == 400
    formalized = client.post(
        f"/api/strategy-drafts/{draft_id}/formalize",
        json={
            "subject_id": draft_id,
            "confirmed_by_user": True,
            "structured_content": {"version": "v0.1", "source": "confirmed"},
        },
    )
    assert formalized.status_code == 200
    assert formalized.json()["raw_content"] == "//@version=6"
    assert formalized.json()["status"] == "awaiting_confirmation"

    frozen = client.post(
        f"/api/strategy-drafts/{draft_id}/freeze-baseline",
        json={"subject_id": draft_id, "confirmed_by_user": True},
    )
    assert frozen.status_code == 201
    assert frozen.json()["immutable"] is True
    assert frozen.json()["content_snapshot"] == {
        "version": "v0.1",
        "source": "confirmed",
    }
    baseline_id = frozen.json()["id"]
    versions = client.get("/api/strategy-versions").json()
    assert versions == [frozen.json()]
    assert (
        client.get(f"/api/strategy-versions?strategy_id={draft_id}").json()
        == versions
    )

    register_factor(
        tmp_path / "factor_library" / "registry.sqlite3",
        factor_id="trend.ema_slope.20",
        name="EMA slope 20",
        category="trend",
        formula_path="factor_library/trend/ema_slope_20.py",
        description="Twenty-period EMA slope.",
        metadata={"market_profile": "crypto_perpetual.binance.eth"},
    )
    factors = client.get("/api/factors")
    assert factors.status_code == 200
    assert factors.json()[0] == {
        "factor_id": "trend.ema_slope.20",
        "name": "EMA slope 20",
        "category": "trend",
        "version": 1,
        "status": "candidate",
        "formula_path": "factor_library/trend/ema_slope_20.py",
        "description": "Twenty-period EMA slope.",
        "metadata": {"market_profile": "crypto_perpetual.binance.eth"},
        "created_at": factors.json()[0]["created_at"],
        "updated_at": factors.json()[0]["updated_at"],
    }
    repeated = client.post(
        f"/api/strategy-drafts/{draft_id}/freeze-baseline",
        json={"confirmed_by_user": True},
    )
    assert repeated.status_code == 409

    plan = client.post(
        "/api/experiment-plans",
        json={
            "baseline_version_id": baseline_id,
            "hypothesis": "EMA has a stable validation plateau.",
            "parameter_space": [
                {"name": "ema", "kind": "integer", "lower": 18, "upper": 24}
            ],
            "objectives": [
                {"metric": "validation_sharpe", "direction": "maximize"}
            ],
            "constraints": [
                {"metric": "max_drawdown", "operator": "lte", "value": 0.2}
            ],
            "data_splits": {
                "train": "a/b",
                "validation": "b/c",
                "locked_test": "c/d",
            },
            "cost_model": {"fee_per_side": 0.0005},
            "max_trials": 10,
            "time_budget_seconds": 600,
            "stopping_conditions": ["stop after budget"],
        },
    )
    assert plan.status_code == 201
    plan_id = plan.json()["id"]
    blocked_search = client.post(
        "/api/jobs",
        json={
            "job_type": "parameter_search",
            "payload": {"experiment_plan_id": plan_id},
        },
    )
    assert blocked_search.status_code == 400
    approved = client.post(
        f"/api/experiment-plans/{plan_id}/approve",
        json={"confirmed_by_user": True},
    )
    assert approved.status_code == 200
    queued_search = client.post(
        "/api/jobs",
        json={
            "job_type": "parameter_search",
            "payload": {"experiment_plan_id": plan_id},
        },
    )
    assert queued_search.status_code == 201
    assert queued_search.json()["status"] == "queued"

    events = client.get("/api/audit/events").json()
    assert {event["event_type"] for event in events} >= {
        "research_session.created",
        "strategy_intake.created",
        "strategy_baseline.frozen",
        "experiment_plan.approved",
    }


def test_openapi_has_no_live_trade_or_arbitrary_execution_endpoint(tmp_path: Path) -> None:
    app = create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    schema = TestClient(app).get("/openapi.json").json()
    paths = set(schema["paths"])

    assert "/health" in paths
    assert "/api/jobs" in paths
    assert "/api/data/summary" in paths
    assert all("/trade" not in path for path in paths)
    assert all("/live" not in path for path in paths)
    assert all("/shell" not in path for path in paths)
    assert all("credential" not in path for path in paths)


def test_data_summary_prefers_two_year_manifest(tmp_path: Path) -> None:
    catalog = tmp_path / "data/catalog"
    manifests = tmp_path / "data/manifests"
    catalog.mkdir(parents=True)
    manifests.mkdir(parents=True)
    (catalog / "catalog_summary.json").write_text(
        '{"binance_ethusdt_futures_1h":{"rows":17520}}'
    )
    manifest = {
        "market_profile": "crypto_perpetual.binance.eth",
        "source": {"effective_source": "binance_official_archive"},
        "range": {"complete_utc_days": 730},
        "symbol": {"native": "ETHUSDT"},
        "data_version": "two-year-v1",
        "cost_model": {"baseline_fee_per_side": 0.0005},
        "processed_datasets": [
            {
                "dataset": "funding_rate",
                "timeframe": "native",
                "quality": {"missing_intervals": 57},
            }
        ],
    }
    (manifests / "binance_ethusdt_perpetual_20240720_20260720_v2.json").write_text(
        json.dumps(manifest)
    )

    summary = DataSummaryReader(tmp_path).read()

    assert summary["available"] is True
    assert summary["data_version"] == "two-year-v1"
    assert summary["range"]["complete_utc_days"] == 730
    assert summary["quality_gaps"][0]["missing_intervals"] == 57
    assert summary["research_limit"].startswith("730 complete UTC days")
