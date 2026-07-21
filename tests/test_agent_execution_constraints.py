import sqlite3
from pathlib import Path

import pytest
import yaml

from quant_lab.application.services import ResearchApplicationService
from quant_lab.application.tool_ports import ALLOWED_RESEARCH_TOOLS
from quant_lab.domain.errors import ApprovalRequiredError, InvalidJobError
from quant_lab.domain.models import Constraint, Objective, ParameterSpace
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository


ROOT = Path(__file__).resolve().parents[1]


def create_baseline(service: ResearchApplicationService) -> tuple[str, str]:
    session = service.create_research_session(title="agent constraint test")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="Enter only after a completed 15m candle.",
    )
    baseline = service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)
    return session.id, baseline.id


def create_plan(
    service: ResearchApplicationService, baseline_version_id: str
):
    return service.create_experiment_plan(
        baseline_version_id=baseline_version_id,
        hypothesis="EMA values from 18 to 24 form a stable validation plateau.",
        parameter_space=(
            ParameterSpace(name="ema", kind="integer", lower=18, upper=24),
        ),
        objectives=(Objective(metric="validation_sharpe", direction="maximize"),),
        constraints=(Constraint(metric="max_drawdown", operator="lte", value=0.2),),
        data_splits={
            "train": "2026-04-21/2026-06-10",
            "validation": "2026-06-10/2026-07-01",
            "locked_test": "2026-07-01/2026-07-20",
        },
        cost_model={"fee_per_side": 0.0005, "slippage_bps_per_side": 2},
        max_trials=12,
        time_budget_seconds=600,
        stopping_conditions=("stop after four non-improving validation trials",),
    )


def test_parameter_search_and_trial_require_approved_plan(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/product.sqlite3")
    service = ResearchApplicationService(repository)
    _session_id, baseline_id = create_baseline(service)
    plan = create_plan(service, baseline_id)

    assert repository.get_experiment_plan(plan.id).status == "draft"
    with pytest.raises(ApprovalRequiredError, match="explicit user approval"):
        service.create_job(
            job_type="parameter_search", payload={"experiment_plan_id": plan.id}
        )
    with pytest.raises(ApprovalRequiredError, match="approved experiment plan"):
        service.record_trial(
            experiment_plan_id=plan.id,
            parameters={"ema": 20},
            data_version="data-v1",
        )

    approved = service.approve_experiment_plan(
        plan_id=plan.id, confirmed_by_user=True
    )
    job = service.create_job(
        job_type="parameter_search", payload={"experiment_plan_id": approved.id}
    )
    trial = service.record_trial(
        experiment_plan_id=approved.id,
        parameters={"ema": 20},
        data_version="data-v1",
        log_artifact_key="experiments/runs/trial_1/log.txt",
    )

    assert approved.status == "approved"
    assert job.status == "queued"
    assert trial.status == "queued"
    assert repository.list_trials(approved.id)[0].parameters == {"ema": 20}


def test_agent_run_tool_call_and_artifact_round_trip(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/product.sqlite3")
    service = ResearchApplicationService(repository)
    session_id, _baseline_id = create_baseline(service)
    agent_run = service.create_agent_run(
        session_id=session_id,
        agent_name="codex",
        plan_summary="Read routing policy and prepare an intake draft.",
    )
    tool_call = service.record_tool_call(
        agent_run_id=agent_run.id,
        tool_name="intake_strategy",
        sanitized_input={"source_type": "natural_language"},
        sanitized_output={"status": "draft"},
        status="completed",
    )
    artifact = service.record_artifact(
        agent_run_id=agent_run.id,
        artifact_type="manifest",
        artifact_key="experiments/runs/run_1/manifest.json",
        checksum="sha256:example",
    )

    persisted = repository.get_agent_run(agent_run.id)
    assert persisted.agent_provider == "external_local_agent"
    assert persisted.execution_target == "local_runtime"
    assert repository.list_tool_calls(agent_run.id) == [tool_call]
    assert repository.list_artifacts(agent_run.id) == [artifact]

    with pytest.raises(InvalidJobError, match="not allowlisted"):
        service.record_tool_call(
            agent_run_id=agent_run.id,
            tool_name="run_shell",
            sanitized_input={},
            sanitized_output=None,
            status="requested",
        )


@pytest.mark.parametrize(
    "invalid_key",
    ["/tmp/report.json", "../outside.json", "file://report.json", "C:/report.json"],
)
def test_artifact_store_rejects_non_project_relative_keys(
    tmp_path: Path, invalid_key: str
) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="artifact_key"):
        store.put(invalid_key, b"unsafe")


def test_artifact_store_round_trip_is_project_rooted(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    key = "reports/backtests/run_1.json"
    store.put(key, b'{"status":"ok"}')
    assert store.exists(key) is True
    assert store.get(key) == b'{"status":"ok"}'
    assert (tmp_path / key).is_file()


def test_document_routing_policy_is_machine_readable_and_complete() -> None:
    policy = yaml.safe_load(
        (ROOT / "configs/agent_policies/document-routing.yaml").read_text(
            encoding="utf-8"
        )
    )
    intents = {item["intent"]: item for item in policy["intents"]}
    assert set(intents) == {
        "strategy_intake",
        "data_download",
        "baseline_backtest",
        "parameter_optimization",
        "stress_test",
        "dry_run",
    }
    for route in intents.values():
        assert route["required_docs"]
        assert route["required_preconditions"]
        assert route["allowed_tools"]
        assert route["required_outputs"]
        assert route["approval_gate"]
        for relative in route["required_docs"]:
            assert (ROOT / relative).is_file(), relative
        assert set(route["allowed_tools"]).issubset(ALLOWED_RESEARCH_TOOLS)


def test_audit_events_cannot_be_updated_or_deleted(tmp_path: Path) -> None:
    database = tmp_path / "runtime/app/product.sqlite3"
    service = ResearchApplicationService(SQLiteProductRepository(database))
    service.create_research_session(title="append only")

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE audit_events SET event_type = 'rewritten'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM audit_events")
