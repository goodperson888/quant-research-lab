from __future__ import annotations

import argparse
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import InvalidJobError
from quant_lab.infrastructure.policy_readers import VersioningPolicyReader
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app
from quant_lab.interfaces.cli.governance_commands import handle_governance_command
from quant_lab.workers.runner import LocalWorker


def test_versioning_policy_keeps_research_actions_git_independent() -> None:
    policy = VersioningPolicyReader(Path.cwd()).read()

    assert policy.authoritative_strategy_state == (
        "sqlite_product_state",
        "project_relative_artifact",
        "artifact_checksum",
        "append_only_audit",
    )
    assert policy.git_required_for_local_product is False
    assert policy.remote_required is False
    assert policy.auto_commit_on_intake is False
    assert policy.auto_commit_on_formalization is False
    assert policy.auto_commit_on_freeze is False
    assert policy.auto_push is False
    assert policy.research_actions_must_not_invoke_git is True


def test_api_freezes_without_git_repository_and_exposes_latest_handoff(
    tmp_path: Path,
) -> None:
    root = tmp_path / "customer-local-product"
    root.mkdir()
    app = create_app(root=root, database_path=root / "runtime/app/local.sqlite3")
    client = TestClient(app)

    session = client.post("/api/research/sessions", json={"title": "No Git"}).json()
    draft = client.post(
        f"/api/research/sessions/{session['id']}/intakes",
        json={"source_type": "natural_language", "raw_content": "closed bars only"},
    ).json()
    intake_handoff = client.get(
        f"/api/research/sessions/{session['id']}/handoff"
    ).json()
    assert intake_handoff["status"] == "waiting_required_input"
    assert intake_handoff["subject_id"] == draft["id"]

    formalized = client.post(
        f"/api/strategy-drafts/{draft['id']}/formalize",
        json={
            "subject_id": draft["id"],
            "confirmed_by_user": True,
            "structured_content": {"entry": "confirmed closed-bar structure"},
        },
    )
    assert formalized.status_code == 200
    waiting = client.get(
        f"/api/research/sessions/{session['id']}/handoff"
    ).json()
    assert waiting["status"] == "waiting_user_approval"
    assert waiting["approval_subject_id"] == draft["id"]

    frozen = client.post(
        f"/api/strategy-drafts/{draft['id']}/freeze-baseline",
        json={"subject_id": draft["id"], "confirmed_by_user": True},
    )
    assert frozen.status_code == 201
    baseline_id = frozen.json()["id"]
    handoff = client.get(
        f"/api/research/sessions/{session['id']}/handoff"
    ).json()
    assert handoff["status"] == "completed_scope"
    assert handoff["subject_id"] == baseline_id
    assert handoff["approval_subject_id"] == baseline_id
    assert handoff["user_action_required"] is True
    assert "correctness/fast-screen" in handoff["required_user_action"]
    assert not (root / ".git").exists()

    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])
    assert f"/api/research/sessions/{{session_id}}/handoff" in paths
    assert "/api/agent-runs/{agent_run_id}/handoff" in paths
    assert "/api/versioning/policy" in paths
    assert all("/git" not in path for path in paths)
    assert all("/trade" not in path for path in paths)
    assert all("/live" not in path for path in paths)
    assert all("/shell" not in path for path in paths)
    assert all("credential" not in path for path in paths)


def test_cli_intake_formalize_and_freeze_do_not_invoke_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "local-cli"
    root.mkdir()
    database = root / "runtime/app/cli.sqlite3"
    service = ResearchApplicationService(SQLiteProductRepository(database))
    session = service.create_research_session(title="CLI without Git")
    structured = root / "strategies/research/structured.yaml"
    structured.parent.mkdir(parents=True)
    structured.write_text("entry: confirmed structure\n", encoding="utf-8")

    def forbid_subprocess(*_args, **_kwargs):
        raise AssertionError("research lifecycle must not invoke Git or any subprocess")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)
    monkeypatch.setattr(subprocess, "check_output", forbid_subprocess)
    monkeypatch.setattr(subprocess, "Popen", forbid_subprocess)

    assert handle_governance_command(
        argparse.Namespace(
            command="intake-strategy",
            session_id=session.id,
            source_type="natural_language",
            raw_content="trend follows confirmed structure",
            source_name=None,
        ),
        root=root,
        database_path=database,
    ) == 0
    draft = service.list_strategy_drafts(session.id)[0]

    assert handle_governance_command(
        argparse.Namespace(
            command="formalize-strategy",
            draft_id=draft.id,
            subject_id=draft.id,
            structured_artifact_key="strategies/research/structured.yaml",
            confirmed_by_user=True,
        ),
        root=root,
        database_path=database,
    ) == 0
    assert handle_governance_command(
        argparse.Namespace(
            command="freeze-baseline",
            draft_id=draft.id,
            subject_id=draft.id,
            confirmed_by_user=True,
        ),
        root=root,
        database_path=database,
    ) == 0
    assert service.get_latest_session_handoff(session.id).status == "completed_scope"
    assert not (root / ".git").exists()


def test_handoff_is_append_only_and_live_trade_refusal_is_normalized(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime/app/test.sqlite3"
    service = ResearchApplicationService(SQLiteProductRepository(database))
    session = service.create_research_session(title="Safety handoff")

    with pytest.raises(InvalidJobError, match="live trade is forbidden"):
        service.create_job(
            job_type="trade",
            payload={"session_id": session.id, "subject_id": session.id},
        )
    handoff = service.get_latest_session_handoff(session.id)
    assert handoff.status == "safety_refusal"
    assert handoff.stop_reason_text == (
        "Live trade safety guard: PASS (expected rejection, exit code 3)."
    )
    assert handoff.safe_to_continue is True

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute(
                "UPDATE research_handoffs SET stop_reason_text = 'rewritten' WHERE id = ?",
                (handoff.id,),
            )


def test_worker_handoff_failure_does_not_mask_original_job_failure(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service = ResearchApplicationService(repository)
    job = service.create_job(
        job_type="data_quality",
        payload={"session_id": "session_missing_for_handoff"},
    )

    def fail_handler(_job):
        raise RuntimeError("primary worker failure")

    worker = LocalWorker(repository, handlers={"data_quality": fail_handler})
    with pytest.raises(RuntimeError, match="primary worker failure"):
        worker.run(job.id)

    assert repository.get_job(job.id).status == "failed"
