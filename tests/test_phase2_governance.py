import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.application.pipeline import PipelineApplicationService, PipelineProfileCatalog
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import ApprovalRequiredError, ConflictError, GatePolicyError
from quant_lab.domain.models import GateEvaluation, Job
from quant_lab.infrastructure.freqtrade_correctness_runner import (
    FreqtradeCorrectnessDiagnosticRunner,
)
from quant_lab.infrastructure.policy_readers import WorkerResourcePolicy
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app
from quant_lab.workers.runner import LocalWorker


ROOT = Path(__file__).resolve().parents[1]


def build_baseline(
    tmp_path: Path, *, budget_policy: dict | None = None
) -> tuple[SQLiteProductRepository, ResearchApplicationService, object, object]:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service = ResearchApplicationService(repository, budget_policy=budget_policy)
    session = service.create_research_session(title="phase 2")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="A frozen test strategy.",
    )
    baseline = service.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)
    return repository, service, session, baseline


def passed_viability(repository: SQLiteProductRepository, subject_id: str) -> GateEvaluation:
    evaluation = GateEvaluation(
        id="gate_viability_passed",
        profile_id="full_validation",
        gate_name="viability",
        subject_type="strategy_version",
        subject_id=subject_id,
        market_profile="crypto_perpetual.binance.eth",
        strategy_objective="standalone",
        status="passed",
        metrics={"validation_net_return": 0.01},
        reasons=("fixture gate",),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return repository.create_gate_evaluation(evaluation)


def regime_values(baseline_id: str) -> dict:
    return {
        "subject_type": "strategy_version",
        "subject_id": baseline_id,
        "market_profile": "crypto_perpetual.binance.eth",
        "detector_version": "ex-ante-fixture-v1",
        "ex_ante_observable": True,
        "target_regimes": ["trend"],
        "suitable_regimes": [],
        "conditional_regimes": [],
        "blocked_regimes": [],
        "unknown_regimes": ["trend"],
        "regime_metrics": {},
        "transition_policy": {"on_unknown": "block_promotion"},
        "history_days": 365,
    }


def test_regime_diagnostic_and_formal_validation_are_separate(tmp_path: Path) -> None:
    for source in (ROOT / "configs/pipelines").glob("*.yaml"):
        target = tmp_path / "configs/pipelines" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
    repository, _service, _session, baseline = build_baseline(tmp_path)
    pipeline = PipelineApplicationService(repository, PipelineProfileCatalog(tmp_path))

    diagnostic = pipeline.create_regime_validation(
        **regime_values(baseline.id),
        mode="regime_diagnostic",
        evidence_status="screening",
        viability_gate_result_id=None,
    )
    assert diagnostic.mode == "regime_diagnostic"
    assert diagnostic.evidence_status == "screening"
    assert diagnostic.viability_gate_result_id is None

    with pytest.raises(GatePolicyError, match="passed viability"):
        pipeline.create_regime_validation(
            **regime_values(baseline.id),
            mode="regime_validation",
            evidence_status="extended_validation",
            viability_gate_result_id=None,
        )

    gate = passed_viability(repository, baseline.id)
    formal = pipeline.create_regime_validation(
        **regime_values(baseline.id),
        mode="regime_validation",
        evidence_status="extended_validation",
        viability_gate_result_id=gate.id,
    )
    assert formal.mode == "regime_validation"
    assert formal.viability_gate_result_id == gate.id


def test_regime_validation_job_requires_same_subject_viability(tmp_path: Path) -> None:
    repository, service, _session, baseline = build_baseline(tmp_path)
    payload = {
        "mode": "regime_validation",
        "subject_type": "strategy_version",
        "subject_id": baseline.id,
        "market_profile": "crypto_perpetual.binance.eth",
        "detector_config_artifact_key": "configs/regimes/eth.yaml",
        "data_manifest_artifact_key": "data/manifests/eth.json",
        "trades_artifact_key": "experiments/runs/run/trades.parquet",
        "ex_ante_observable": True,
        "locked_test_used": False,
    }
    with pytest.raises(ApprovalRequiredError, match="passed viability"):
        service.create_job(job_type="regime_validation", payload=payload)
    gate = passed_viability(repository, baseline.id)
    queued = service.create_job(
        job_type="regime_validation",
        payload={**payload, "viability_gate_result_id": gate.id},
    )
    assert queued.status == "queued"


def _plan(service: ResearchApplicationService, baseline_id: str, hypothesis: str):
    from quant_lab.domain.models import Constraint, Objective, ParameterSpace

    return service.create_experiment_plan(
        baseline_version_id=baseline_id,
        hypothesis=hypothesis,
        parameter_space=(ParameterSpace(name="x", kind="integer", lower=1, upper=2),),
        objectives=(Objective(metric="validation_pf", direction="maximize"),),
        constraints=(Constraint(metric="drawdown", operator="lte", value=0.2),),
        data_splits={"train": "a", "validation": "b", "locked_test": "c"},
        cost_model={"fee": 0.0005},
        max_trials=2,
        time_budget_seconds=120,
        stopping_conditions=("stop at budget",),
    )


def test_research_budget_blocks_hypotheses_trials_and_locked_test(tmp_path: Path) -> None:
    repository, service, session, baseline = build_baseline(
        tmp_path,
        budget_policy={
            "max_hypotheses": 1,
            "max_trials_total": 1,
            "max_compute_minutes": 1,
            "max_locked_test_uses": 1,
            "require_user_approval_for_new_hypothesis": True,
        },
    )
    first = _plan(service, baseline.id, "first hypothesis")
    service.approve_experiment_plan(plan_id=first.id, confirmed_by_user=True)
    assert service.get_research_budget(session.id).remaining_hypotheses == 0

    second = _plan(service, baseline.id, "second hypothesis")
    with pytest.raises(ConflictError, match="max_hypotheses"):
        service.approve_experiment_plan(plan_id=second.id, confirmed_by_user=True)
    with pytest.raises(ConflictError, match="trial or compute"):
        service.create_job(
            job_type="parameter_search",
            payload={"experiment_plan_id": first.id},
        )

    service.create_job(
        job_type="report",
        payload={"session_id": session.id, "locked_test_used": True},
    )
    with pytest.raises(ConflictError, match="max_locked_test_uses"):
        service.create_job(
            job_type="report",
            payload={"session_id": session.id, "locked_test_used": True},
        )
    assert any(
        event.event_type == "research_budget.blocked"
        for event in repository.list_events(limit=20)
    )


def test_freqtrade_correctness_runner_uses_fixed_wrapper_and_records_evidence(
    tmp_path: Path,
) -> None:
    repository, service, _session, baseline = build_baseline(tmp_path)
    strategy = tmp_path / "strategies/freqtrade/SafeFixture.py"
    config = tmp_path / "configs/freqtrade.fixture.json"
    strategy.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_text("class SafeFixture: pass\n", encoding="utf-8")
    config.write_text("{}\n", encoding="utf-8")
    job = service.create_job(
        job_type="correctness_diagnostic",
        payload={
            "strategy_version_id": baseline.id,
            "analysis_type": "lookahead-analysis",
            "strategy_name": "SafeFixture",
            "strategy_artifact_key": "strategies/freqtrade/SafeFixture.py",
            "config_artifact_key": "configs/freqtrade.fixture.json",
            "timerange": "20250720-20250721",
            "locked_test_used": False,
            "live_trading": False,
        },
    )
    commands: list[list[str]] = []

    def fixture(command: list[str], _timeout: int) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "No lookahead bias detected", ""

    result = FreqtradeCorrectnessDiagnosticRunner(
        tmp_path, repository, command_runner=fixture
    )(job)
    assert commands[0][0].endswith("scripts/freqtrade.sh")
    assert commands[0][1] == "lookahead-analysis"
    assert "trade" not in commands[0]
    report = json.loads(
        (tmp_path / result["report_artifact_key"]).read_text(encoding="utf-8")
    )
    assert report["evidence_scope"] == "correctness_only_no_strategy_performance_claim"
    assert report["locked_test_used"] is False
    assert repository.list_reports(job.id)[0].artifact_key == result["report_artifact_key"]


def test_unavailable_freqtrade_diagnostic_fails_but_keeps_agent_artifacts(
    tmp_path: Path,
) -> None:
    repository, service, session, baseline = build_baseline(tmp_path)
    strategy = tmp_path / "strategies/freqtrade/SafeFixture.py"
    config = tmp_path / "configs/freqtrade.fixture.json"
    strategy.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_text("class SafeFixture: pass\n", encoding="utf-8")
    config.write_text("{}\n", encoding="utf-8")
    agent_run = service.create_agent_run(session_id=session.id, agent_name="fixture")
    job = service.create_job(
        job_type="correctness_diagnostic",
        payload={
            "strategy_version_id": baseline.id,
            "analysis_type": "recursive-analysis",
            "strategy_name": "SafeFixture",
            "strategy_artifact_key": "strategies/freqtrade/SafeFixture.py",
            "config_artifact_key": "configs/freqtrade.fixture.json",
            "locked_test_used": False,
            "live_trading": False,
            "agent_run_id": agent_run.id,
        },
    )
    runner = FreqtradeCorrectnessDiagnosticRunner(
        tmp_path,
        repository,
        command_runner=lambda _command, _timeout: (127, "", "not installed"),
    )
    worker = LocalWorker(repository, handlers={"correctness_diagnostic": runner})
    with pytest.raises(RuntimeError, match="unavailable or failed"):
        worker.run(job.id)
    assert repository.get_job(job.id).status == "failed"
    artifact_keys = {item.artifact_key for item in repository.list_artifacts(agent_run.id)}
    assert any(key.endswith("manifest.json") for key in artifact_keys)
    assert any(key.startswith("reports/correctness/") for key in artifact_keys)


def test_worker_resource_limit_fails_job_and_preserves_metrics(tmp_path: Path) -> None:
    repository, service, _session, _baseline = build_baseline(tmp_path)
    job = service.create_job(job_type="report", payload={})
    policy = WorkerResourcePolicy(
        policy_id="tiny-memory-fixture",
        max_rss_mb=1,
        max_concurrent_trials=1,
        max_job_minutes=1,
        parquet_batch_rows=10,
        kill_on_memory_limit=True,
    )
    worker = LocalWorker(
        repository,
        handlers={"report": lambda _job: {"run_id": "never-accepted"}},
        resource_policy=policy,
    )
    with pytest.raises(MemoryError, match="max_rss_mb"):
        worker.run(job.id)
    assert repository.get_job(job.id).status == "failed"
    assert any("peak_rss_mb" in item["message"] for item in repository.list_job_logs(job.id))


def test_worker_watch_selection_uses_oldest_supported_queued_job(
    tmp_path: Path,
) -> None:
    repository, service, _session, _baseline = build_baseline(tmp_path)
    unsupported = service.create_job(job_type="report", payload={"order": 1})
    supported = service.create_job(job_type="data_quality", payload={"order": 2})
    worker = LocalWorker(
        repository,
        handlers={"data_quality": lambda job: {"run_id": job.id}},
    )

    assert worker.can_handle("data_quality") is True
    assert worker.can_handle("report") is False
    assert worker.next_queued_job() == supported
    assert repository.get_job(unsupported.id).status == "queued"


def test_phase2_read_only_policy_api(tmp_path: Path) -> None:
    for relative in (
        "configs/research_budgets/default.yaml",
        "configs/workers/local.yaml",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    assert client.get("/api/research-budget/default").json()["available"] is True
    assert client.get("/api/worker/resource-policy").json()["available"] is True
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/correctness-diagnostics/jobs" in paths
    assert all("/trade" not in path and "/live" not in path for path in paths)
