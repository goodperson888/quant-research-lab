import argparse
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import ApprovalRequiredError
from quant_lab.infrastructure.sqlite_product_repository import (
    SCHEMA_VERSION,
    SQLiteProductRepository,
)
from quant_lab.interfaces.api.app import create_app
from quant_lab.interfaces.cli.governance_commands import handle_governance_command


def build_service(tmp_path: Path) -> ResearchApplicationService:
    return ResearchApplicationService(
        SQLiteProductRepository(tmp_path / "runtime/app/modes.sqlite3")
    )


def test_session_defaults_to_guided_and_agent_run_inherits_mode(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    session = service.create_research_session(title="shared mode")

    assert session.research_mode == "guided"
    assert session.mode_config["agent_run_mode"] == "guided"
    assert session.mode_revision == 1
    first_run = service.create_agent_run(
        session_id=session.id, agent_name="fixture"
    )
    assert first_run.mode == "guided"
    service.update_agent_run_status(agent_run_id=first_run.id, status="completed")


def test_mode_update_requires_confirmation_is_audited_and_changes_agent_pacing(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    session = service.create_research_session(title="quick mode")

    with pytest.raises(ApprovalRequiredError, match="explicit user confirmation"):
        service.update_research_mode(
            session_id=session.id,
            research_mode="quick",
            mode_config=None,
            confirmed_by_user=False,
        )

    updated = service.update_research_mode(
        session_id=session.id,
        research_mode="quick",
        mode_config={"default_trial_budget": 12},
        confirmed_by_user=True,
    )

    assert updated.research_mode == "quick"
    assert updated.mode_config["agent_run_mode"] == "bounded_autonomous"
    assert updated.mode_config["default_trial_budget"] == 12
    assert updated.mode_revision == 2
    assert service.create_agent_run(
        session_id=session.id, agent_name="fixture"
    ).mode == "bounded_autonomous"
    mode_events = [
        item
        for item in service.list_audit_events(limit=20)
        if item.event_type == "research_session.mode_updated"
    ]
    assert mode_events[0].payload["safety_boundaries_unchanged"] is True


def test_api_lists_and_updates_research_modes(tmp_path: Path) -> None:
    app = create_app(
        root=tmp_path, database_path=tmp_path / "runtime/app/api-modes.sqlite3"
    )
    client = TestClient(app)
    session = client.post("/api/research/sessions", json={"title": "API mode"}).json()

    modes = client.get("/api/research-modes")
    assert modes.status_code == 200
    assert [item["mode"] for item in modes.json()] == ["quick", "guided", "expert"]

    updated = client.put(
        f"/api/research/sessions/{session['id']}/mode",
        json={"mode": "expert", "confirmed_by_user": True},
    )
    assert updated.status_code == 200
    assert updated.json()["research_mode"] == "expert"
    assert updated.json()["mode_config"]["agent_run_mode"] == "supervised"


def test_api_exposes_current_session_agent_occupancy(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime/app/api-occupancy.sqlite3"
    app = create_app(root=tmp_path, database_path=database_path)
    client = TestClient(app)
    session = client.post(
        "/api/research/sessions", json={"title": "occupancy"}
    ).json()
    service = ResearchApplicationService(SQLiteProductRepository(database_path))
    run = service.create_agent_run(
        session_id=session["id"],
        agent_name="codex-window-a",
        plan_summary="Research the selected strategy.",
    )

    response = client.get(
        f"/api/research/sessions/{session['id']}/agent-occupancy"
    )
    assert response.status_code == 200
    assert response.json() == {
        "session_id": session["id"],
        "occupied": True,
        "agent_run_id": run.id,
        "agent_name": "codex-window-a",
        "status": "queued",
        "plan_summary": "Research the selected strategy.",
        "started_at": run.created_at,
        "lease_expires_at": run.lease_expires_at,
    }


def test_schema_v7_session_migrates_without_losing_existing_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime/app/legacy.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE research_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO research_sessions
            VALUES ('session_legacy', 'legacy title', 'inbox', 't0', 't1')
            """
        )
        connection.execute("PRAGMA user_version = 7")

    repository = SQLiteProductRepository(path)
    repository.initialize()
    migrated = repository.get_session("session_legacy")

    assert migrated.title == "legacy title"
    assert migrated.status == "inbox"
    assert migrated.research_mode == "guided"
    assert migrated.mode_config["agent_run_mode"] == "guided"
    assert migrated.mode_revision == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_cli_show_and_set_mode_use_the_same_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "runtime/app/cli-modes.sqlite3"
    service = ResearchApplicationService(SQLiteProductRepository(database_path))
    session = service.create_research_session(title="CLI mode")

    result = handle_governance_command(
        argparse.Namespace(
            command="set-research-mode",
            session_id=session.id,
            mode="quick",
            mode_config_json='{"default_trial_budget": 9}',
            confirmed_by_user=True,
        ),
        root=tmp_path,
        database_path=database_path,
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["research_mode"] == "quick"

    result = handle_governance_command(
        argparse.Namespace(command="show-research-mode", session_id=session.id),
        root=tmp_path,
        database_path=database_path,
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["research_mode"] == "quick"
    assert payload["mode_config"]["default_trial_budget"] == 9
