import sqlite3
import json
from pathlib import Path

import pytest
import yaml

from quant_lab.application.services import ResearchApplicationService
from quant_lab.application.tool_ports import ALLOWED_RESEARCH_TOOLS
from quant_lab.domain.errors import ApprovalRequiredError, ConflictError, InvalidJobError
from quant_lab.domain.models import Constraint, Objective, ParameterSpace
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.cli.main import register_experiment_plan_draft


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


def test_session_allows_only_one_active_writing_agent_run(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/product.sqlite3")
    service = ResearchApplicationService(repository)
    session_id, _baseline_id = create_baseline(service)

    first = service.create_agent_run(
        session_id=session_id,
        agent_name="codex-window-a",
        plan_summary="Research strategy A in this session.",
    )
    occupancy = service.get_session_agent_occupancy(session_id)
    assert occupancy["occupied"] is True
    assert occupancy["agent_run_id"] == first.id
    assert occupancy["agent_name"] == "codex-window-a"

    with pytest.raises(ConflictError, match="正由另一个 AI 任务写入"):
        service.create_agent_run(
            session_id=session_id,
            agent_name="codex-window-b",
            plan_summary="Competing write in the same session.",
        )

    service.update_agent_run_status(
        agent_run_id=first.id, status="waiting_approval"
    )
    assert service.get_session_agent_occupancy(session_id)["occupied"] is False

    second = service.create_agent_run(
        session_id=session_id,
        agent_name="codex-window-b",
        plan_summary="Continue in the now-idle session.",
    )
    with pytest.raises(ConflictError, match="原任务只能在会话空闲后继续"):
        service.update_agent_run_status(agent_run_id=first.id, status="queued")

    service.update_agent_run_status(agent_run_id=second.id, status="completed")
    resumed = service.update_agent_run_status(agent_run_id=first.id, status="queued")
    assert resumed.lease_expires_at is not None


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
        "pipeline_evaluation",
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


def test_register_experiment_plan_config_stops_at_draft_approval_gate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime/app/product.sqlite3"
    repository = SQLiteProductRepository(database)
    service = ResearchApplicationService(repository)
    session_id, baseline_id = create_baseline(service)
    store = LocalArtifactStore(tmp_path)
    evidence_key = "reports/backtests/train-loss-attribution.json"
    store.put(evidence_key, json.dumps({"scope": "train_only"}).encode())
    store.put(
        "configs/market_profiles/crypto_perpetual.binance.eth.yaml",
        b"enabled: false\n",
    )
    config_key = "configs/research/entry-confirmation-plan.yaml"
    store.put(
        config_key,
        yaml.safe_dump(
            {
                "schema_version": 1,
                "intent": "parameter_optimization",
                "status": "draft",
                "session_id": session_id,
                "baseline_version_id": baseline_id,
                "market_profile": "crypto_perpetual.binance.eth",
                "evidence_artifact_key": evidence_key,
                "hypothesis": "A confirmation-candle breakout reduces false entries.",
                "single_rule_change": "Replace next-open with confirmation breakout.",
                "parameter_space": [
                    {
                        "name": "entry_mode",
                        "kind": "categorical",
                        "values": ["confirmation_candle_breakout"],
                    }
                ],
                "objectives": [
                    {
                        "metric": "validation_net_return_delta_vs_baseline",
                        "direction": "maximize",
                    }
                ],
                "constraints": [
                    {
                        "metric": "validation_max_drawdown_abs",
                        "operator": "lte",
                        "value": 0.2,
                    }
                ],
                "data_splits": {
                    "train": "[2026-04-21, 2026-06-20)",
                    "validation": "[2026-06-20, 2026-07-10)",
                    "locked_test": "[2026-07-22, 2026-08-21)",
                },
                "cost_model": {
                    "fee_per_side": 0.0005,
                    "slippage_bps_per_side": 2.0,
                    "leverage": 1.0,
                    "funding_rate": "required",
                    "zero_funding_fallback_allowed": False,
                },
                "max_trials": 1,
                "time_budget_seconds": 300,
                "stopping_conditions": ["Stop after the single declared trial."],
                "approval": {
                    "required_before_execution": True,
                    "confirmed_by_user": False,
                    "parameter_search_job_allowed": False,
                },
                "guardrails": {
                    "baseline_immutable": True,
                    "validation_used_for_hypothesis_selection": False,
                    "previous_locked_test_excluded": True,
                    "new_locked_data_available": False,
                    "live_trading_enabled": False,
                    "automatic_production_promotion": False,
                },
            },
            sort_keys=False,
        ).encode(),
    )

    result = register_experiment_plan_draft(
        root=tmp_path,
        database_path=database,
        config_artifact_key=config_key,
    )

    plan = repository.get_experiment_plan(str(result["experiment_plan_id"]))
    agent_run = repository.get_agent_run(str(result["agent_run_id"]))
    assert plan.status == "draft"
    assert plan.approved_by is None
    assert agent_run.status == "waiting_approval"
    assert repository.list_jobs() == []
    assert repository.list_trials(plan.id) == []
    assert [call.tool_name for call in repository.list_tool_calls(agent_run.id)] == [
        "create_experiment_plan"
    ]
    assert {artifact.artifact_key for artifact in repository.list_artifacts(agent_run.id)} == {
        config_key,
        evidence_key,
    }


def test_experiment_plan_config_cannot_self_approve(tmp_path: Path) -> None:
    database = tmp_path / "runtime/app/product.sqlite3"
    repository = SQLiteProductRepository(database)
    service = ResearchApplicationService(repository)
    session_id, baseline_id = create_baseline(service)
    store = LocalArtifactStore(tmp_path)
    evidence_key = "reports/backtests/evidence.json"
    store.put(evidence_key, b"{}")
    store.put(
        "configs/market_profiles/crypto_perpetual.binance.eth.yaml",
        b"enabled: false\n",
    )
    config_key = "configs/research/unsafe-plan.yaml"
    unsafe = {
        "schema_version": 1,
        "intent": "parameter_optimization",
        "status": "draft",
        "session_id": session_id,
        "baseline_version_id": baseline_id,
        "market_profile": "crypto_perpetual.binance.eth",
        "evidence_artifact_key": evidence_key,
        "hypothesis": "One hypothesis.",
        "single_rule_change": "One change.",
        "parameter_space": [
            {"name": "mode", "kind": "categorical", "values": ["candidate"]}
        ],
        "objectives": [{"metric": "return", "direction": "maximize"}],
        "constraints": [{"metric": "drawdown", "operator": "lte", "value": 0.2}],
        "data_splits": {"train": "a", "validation": "b", "locked_test": "c"},
        "cost_model": {
            "fee_per_side": 0.0005,
            "slippage_bps_per_side": 2.0,
            "leverage": 1.0,
            "funding_rate": "required",
            "zero_funding_fallback_allowed": False,
        },
        "max_trials": 1,
        "time_budget_seconds": 300,
        "stopping_conditions": ["stop"],
        "approval": {
            "required_before_execution": True,
            "confirmed_by_user": True,
            "parameter_search_job_allowed": True,
        },
        "guardrails": {
            "baseline_immutable": True,
            "validation_used_for_hypothesis_selection": False,
            "previous_locked_test_excluded": True,
            "new_locked_data_available": False,
            "live_trading_enabled": False,
            "automatic_production_promotion": False,
        },
    }
    store.put(config_key, yaml.safe_dump(unsafe).encode())

    with pytest.raises(ValueError, match="confirmed_by_user must be false"):
        register_experiment_plan_draft(
            root=tmp_path,
            database_path=database,
            config_artifact_key=config_key,
        )
    assert repository.list_experiment_plans() == []
    assert repository.list_jobs() == []


def test_accepting_proposal_creates_candidate_v1_without_overwriting_baseline(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/product.sqlite3")
    service = ResearchApplicationService(repository)
    _session_id, baseline_id = create_baseline(service)
    baseline_before = repository.get_strategy_version(baseline_id)
    plan = create_plan(service, baseline_id)
    service.approve_experiment_plan(plan_id=plan.id, confirmed_by_user=True)
    service.record_trial(
        experiment_plan_id=plan.id,
        parameters={"entry_mode": "confirmation_candle_breakout"},
        data_version="data-v1",
        status="succeeded",
        metrics={"all_constraints_passed": 1.0, "objective_improved": 1.0},
        log_artifact_key="experiments/runs/trial/log.jsonl",
    )
    proposal = service.propose_strategy_version(
        baseline_version_id=baseline_id,
        content={
            "experiment_plan_id": plan.id,
            "baseline_version_id": baseline_id,
            "status": "awaiting_user_acceptance",
            "baseline_immutable": True,
            "strategy_version_created": False,
            "future_locked_test_required": True,
            "production_promotion_requested": False,
        },
    )

    with pytest.raises(ApprovalRequiredError, match="explicit user confirmation"):
        service.accept_proposal(
            proposal_id=proposal.id,
            confirmed_by_user=False,
        )

    candidate = service.accept_proposal(
        proposal_id=proposal.id,
        confirmed_by_user=True,
    )
    baseline_after = repository.get_strategy_version(baseline_id)

    assert candidate.version == 1
    assert candidate.status == "candidate"
    assert candidate.immutable is True
    assert candidate.content_snapshot["baseline_version_id"] == baseline_id
    assert candidate.content_snapshot["automatic_validation"] is False
    assert candidate.content_snapshot["dry_run_enabled"] is False
    assert candidate.content_snapshot["production_enabled"] is False
    assert baseline_after == baseline_before
    assert baseline_after.status == "baseline"
    assert repository.get_proposal(proposal.id).status == "accepted"
    with pytest.raises(ConflictError, match="already left draft state"):
        service.accept_proposal(
            proposal_id=proposal.id,
            confirmed_by_user=True,
        )

    with pytest.raises(ApprovalRequiredError, match="explicit user confirmation"):
        service.reject_candidate(
            version_id=candidate.id,
            confirmed_by_user=False,
            stress_manifest_artifact_key="experiments/runs/stress/manifest.json",
            failure_conditions=({"metric": "validation_max_drawdown_abs"},),
        )

    rejected = service.reject_candidate(
        version_id=candidate.id,
        confirmed_by_user=True,
        stress_manifest_artifact_key="experiments/runs/stress/manifest.json",
        failure_conditions=(
            {
                "metric": "validation_total_return_delta_vs_candidate",
                "operator": "lt",
                "value": -0.05,
            },
            {
                "metric": "validation_max_drawdown_abs",
                "operator": "gt",
                "value": 0.2,
            },
        ),
    )

    assert rejected.id == candidate.id
    assert rejected.status == "rejected"
    assert rejected.content_snapshot == candidate.content_snapshot
    assert rejected.source_snapshot == candidate.source_snapshot
    assert rejected.immutable is True
    assert repository.get_strategy_version(baseline_id) == baseline_before
    assert repository.get_session(_session_id).status == "rejected"
    events = repository.list_events(limit=20)
    rejection_event = next(
        event for event in events if event.event_type == "strategy_candidate.rejected"
    )
    assert rejection_event.payload["baseline_overwritten"] is False
    assert rejection_event.payload["locked_test_used"] is False

    with sqlite3.connect(repository.path) as connection:
        approval = connection.execute(
            """
            SELECT subject_type, subject_id, decision, actor
            FROM approvals
            WHERE subject_type = 'strategy_candidate' AND subject_id = ?
            """,
            (candidate.id,),
        ).fetchone()
    assert approval == ("strategy_candidate", candidate.id, "rejected", "user")

    with pytest.raises(ConflictError, match="only candidate status"):
        repository.reject_candidate(
            version_id=candidate.id,
            approval_id="approval_second_rejection",
            created_at="2026-07-22T00:00:00+00:00",
        )


def test_candidate_cost_stress_config_is_fixed_and_cannot_change_status() -> None:
    config = yaml.safe_load(
        (
            ROOT
            / "configs/research/price_structure_entry_confirmation_cost_stress.yaml"
        ).read_text(encoding="utf-8")
    )

    assert config["intent"] == "stress_test"
    assert {
        item["id"]: (
            item["fee_per_side"],
            item["slippage_bps_per_side"],
        )
        for item in config["scenarios"]
    } == {
        "high_slippage_5bps": (0.0005, 5.0),
        "doubled_fee_and_slippage": (0.001, 4.0),
    }
    assert config["scope"]["previous_locked_test_used"] is False
    assert config["scope"]["future_locked_test_used"] is False
    assert all(value is False for value in config["guardrails"].values())
