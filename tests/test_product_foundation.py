from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import (
    ConflictError,
    ExperimentPlanValidationError,
    InvalidJobError,
)
from quant_lab.domain.models import ExperimentPlan, Objective, ParameterSpace
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app


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

    baseline = service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)

    assert baseline.version == 0
    assert baseline.immutable is True
    assert baseline.source_snapshot == "Buy when condition A is true."
    with pytest.raises(ConflictError, match="immutable"):
        service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)


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
        constraints=(),
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

    frozen = client.post(
        f"/api/strategy-drafts/{draft_id}/freeze-baseline",
        json={"confirmed_by_user": True},
    )
    assert frozen.status_code == 201
    assert frozen.json()["immutable"] is True
    repeated = client.post(
        f"/api/strategy-drafts/{draft_id}/freeze-baseline",
        json={"confirmed_by_user": True},
    )
    assert repeated.status_code == 409

    events = client.get("/api/audit/events").json()
    assert {event["event_type"] for event in events} >= {
        "research_session.created",
        "strategy_intake.created",
        "strategy_baseline.frozen",
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
