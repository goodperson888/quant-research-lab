from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quant_lab.application.batch_trials import (
    DeterministicParameterGenerator,
    parameter_signature,
)
from quant_lab.application.component_attribution import DefaultComponentEvidenceAggregator
from quant_lab.application.equity_series import TrialEquityReader
from quant_lab.application.guided_research import GuidedResearchService
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import Constraint, ExperimentPlan, Objective, ParameterSpace
from quant_lab.infrastructure.batch_parameter_search_runner import BatchParameterSearchRunner
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.policy_readers import WorkerResourcePolicy
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.strategy_evaluators import (
    DeterministicFixtureStrategyEvaluator,
    StrategyEvaluatorRegistry,
)
from quant_lab.infrastructure.trial_execution import InProcessTrialExecutor
from quant_lab.infrastructure.trial_metrics import ParquetTrialMetricsSink
from quant_lab.application.ports import TrialEvaluationResult
from quant_lab.interfaces.api.app import create_app


class MemoryMetricsSink:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def append_batch(self, *, experiment_plan_id: str, rows):
        self.batches.append([dict(item) for item in rows])
        return (f"experiments/runs/{experiment_plan_id}/trial_metrics/batch.parquet",)


class ResourceLimitFixtureEvaluator:
    evaluator_id = "resource_limit_fixture"

    def evaluate(self, request):
        return TrialEvaluationResult(
            trial_id=request.trial_id,
            status="succeeded",
            metrics={"validation_trade_count": 30.0},
            elapsed_seconds=120.0,
            peak_rss_mb=9999.0,
        )


def _baseline(repository: SQLiteProductRepository):
    service = ResearchApplicationService(repository)
    session = service.create_research_session(title="batch")
    draft = service.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        source_name=None,
        raw_content="frozen baseline",
    )
    return service, session, service.freeze_baseline(
        draft_id=draft.id, confirmed_by_user=True
    )


def _approved_plan(repository: SQLiteProductRepository):
    service, session, baseline = _baseline(repository)
    guided = GuidedResearchService(repository)
    direction = guided.create_direction(
        baseline_version_id=baseline.id,
        subject_id=baseline.id,
        hypothesis="one explicit filter improves validation stability",
        rule_diff={"add_filter": "closed_1h_trend"},
        evidence_refs=(),
        parameter_space=(
            ParameterSpace(name="window", kind="integer", values=(10, 20, 30)),
        ),
        data_splits={
            "train": "2025-07/2025-12",
            "validation": "2026-01/2026-04",
            "locked_test": "2026-05/2026-07",
        },
        cost_model={"taker_fee_per_side": 0.0005, "slippage_per_side": 0.0002},
        objectives=(Objective(metric="validation_net_return", direction="maximize"),),
        constraints=(
            Constraint(metric="validation_trade_count", operator="gte", value=20),
            Constraint(metric="validation_max_drawdown_abs", operator="lte", value=0.3),
        ),
        estimated_trials=3,
        estimated_minutes=5,
        failure_conditions=("no stable range",),
        stopping_conditions=("stop after approved budget",),
        rollback_plan="discard candidate and retain baseline",
    )
    submitted = guided.submit_for_approval(direction.id)
    approved, candidate = guided.approve(
        proposal_id=submitted.id,
        subject_id=submitted.id,
        confirmed_by_user=True,
    )
    assert approved.candidate_version_id == candidate.id
    plan = service.create_experiment_plan(
        baseline_version_id=baseline.id,
        proposal_id=approved.id,
        candidate_version_id=candidate.id,
        hypothesis=approved.hypothesis,
        parameter_space=approved.parameter_space,
        objectives=approved.objectives,
        constraints=approved.constraints,
        data_splits=approved.data_splits,
        cost_model=approved.cost_model,
        max_trials=3,
        time_budget_seconds=300,
        stopping_conditions=approved.stopping_conditions,
        search_strategy="grid",
        random_seed=7,
    )
    return service, session, baseline, candidate, service.approve_experiment_plan(
        plan_id=plan.id, confirmed_by_user=True
    )


def test_grid_and_seeded_random_generation_are_deterministic() -> None:
    generator = DeterministicParameterGenerator()
    common = dict(
        id="plan",
        baseline_version_id="baseline",
        hypothesis="bounded",
        objectives=(Objective(metric="return", direction="maximize"),),
        constraints=(Constraint(metric="dd", operator="lte", value=0.2),),
        data_splits={"train": "a", "validation": "b", "locked_test": "c"},
        cost_model={"fee": 0.001},
        max_trials=4,
        time_budget_seconds=60,
        stopping_conditions=("budget",),
    )
    grid = ExperimentPlan(
        **common,
        parameter_space=(
            ParameterSpace(name="a", kind="integer", lower=1, upper=3, step=1),
            ParameterSpace(name="b", kind="categorical", values=("x", "y")),
        ),
        search_strategy="grid",
    )
    generated = generator.generate(grid)
    assert generated.generated_count == 6
    assert generated.truncated_by_budget is True
    assert generated.combinations[0] == {"a": 1, "b": "x"}

    random_plan = ExperimentPlan(
        **common,
        parameter_space=(
            ParameterSpace(name="a", kind="integer", lower=1, upper=100),
            ParameterSpace(name="b", kind="categorical", values=("x", "y")),
        ),
        search_strategy="random",
        random_seed=42,
    )
    assert generator.generate(random_plan) == generator.generate(random_plan)


def test_guided_proposal_creates_immutable_candidate_and_caps_directions(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    _, _, baseline = _baseline(repository)
    guided = GuidedResearchService(repository, max_directions=1)
    values = dict(
        baseline_version_id=baseline.id,
        subject_id=baseline.id,
        hypothesis="single hypothesis",
        rule_diff={"filter": "trend"},
        evidence_refs=(),
        parameter_space=(ParameterSpace(name="x", kind="integer", values=(1, 2)),),
        data_splits={"train": "a", "validation": "b", "locked_test": "c"},
        cost_model={"fee": 0.001},
        objectives=(Objective(metric="return", direction="maximize"),),
        constraints=(Constraint(metric="dd", operator="lte", value=0.2),),
        estimated_trials=2,
        estimated_minutes=2,
        failure_conditions=("fails validation",),
        stopping_conditions=("budget",),
        rollback_plan="keep baseline",
    )
    proposal = guided.create_direction(**values)
    try:
        guided.create_direction(**values)
        raise AssertionError("second active direction should be blocked")
    except Exception as exc:
        assert "at most 1" in str(exc)
    submitted = guided.submit_for_approval(proposal.id)
    approved, candidate = guided.approve(
        proposal_id=submitted.id,
        subject_id=submitted.id,
        confirmed_by_user=True,
    )
    assert approved.status == "approved"
    assert candidate.immutable is True
    assert candidate.content_snapshot["baseline_version_id"] == baseline.id
    assert repository.get_strategy_version(baseline.id).status == "baseline"


def test_batch_runner_persists_trials_batches_and_deduplicates_components(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service, _, _, _, plan = _approved_plan(repository)
    job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "max_concurrent_trials": 1,
            "max_trial_seconds": 30,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    sink = MemoryMetricsSink()
    aggregator = DefaultComponentEvidenceAggregator(repository)
    runner = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry(
            (DeterministicFixtureStrategyEvaluator(),)
        ),
        executor=InProcessTrialExecutor(),
        metrics_sink=sink,
        resource_policy=WorkerResourcePolicy(
            policy_id="test",
            max_rss_mb=4096,
            max_concurrent_trials=4,
            max_job_minutes=1,
            parquet_batch_rows=100,
            kill_on_memory_limit=True,
        ),
        component_aggregator=aggregator,
    )
    result = runner(job)
    trials = repository.list_trials(plan.id)
    assert len(trials) == 3
    assert all(item.status == "succeeded" for item in trials)
    assert len(sink.batches) == 1
    assert len(sink.batches[0]) == 3
    assert result["locked_test_used"] is False
    assert result["research_conclusion_allowed"] is False
    assert all(item.metrics_artifact_key for item in trials)

    attribution = {
        "name": "closed 1h trend filter",
        "component_type": "filter",
        "logic": {"rule": "closed_1h_trend"},
        "market_profile": "crypto_perpetual.binance.eth",
        "timeframe": "15m",
        "out_of_sample_status": "screening",
        "evidence_level": "fixture",
        "min_incremental_net_return": 0.0,
        "min_validation_trades": 20,
        "failure_conditions": [{"regime": "chop"}],
        "regimes": ["trend"],
    }
    first = aggregator.aggregate(experiment_plan_id=plan.id, attribution=attribution)
    second = aggregator.aggregate(experiment_plan_id=plan.id, attribution=attribution)
    assert first["candidate"].status == "diagnostic_improvement"
    assert second["deduplicated"] is True
    assert len(repository.list_component_candidates()) == 1
    assert len(repository.list_component_evidence()) == 2
    assert first["evidence"].logic_signature == second["evidence"].logic_signature
    assert "window" not in first["evidence"].logic_signature


def test_batch_runner_persists_shared_trial_equity_and_api_reads_real_curves(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime/app/test.sqlite3"
    repository = SQLiteProductRepository(database_path)
    service, _, _, _, plan = _approved_plan(repository)
    job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "max_concurrent_trials": 1,
            "max_trial_seconds": 30,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    sink = ParquetTrialMetricsSink(tmp_path)
    result = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry(
            (DeterministicFixtureStrategyEvaluator(),)
        ),
        executor=InProcessTrialExecutor(),
        metrics_sink=sink,
        resource_policy=WorkerResourcePolicy(
            policy_id="test",
            max_rss_mb=4096,
            max_concurrent_trials=1,
            max_job_minutes=1,
            parquet_batch_rows=100,
            kill_on_memory_limit=True,
        ),
    )(job)

    trials = repository.list_trials(plan.id)
    equity_keys = {item.equity_artifact_key for item in trials}
    assert None not in equity_keys
    assert len(equity_keys) == 1
    assert len(result["trial_equity_artifact_keys"]) == 1
    artifact_key = next(iter(equity_keys))
    assert artifact_key is not None
    assert artifact_key.startswith(
        f"experiments/runs/{plan.id}/trial_equity/"
    )

    comparison = TrialEquityReader(
        repository, LocalArtifactStore(tmp_path)
    ).read(experiment_plan_id=plan.id)
    assert comparison["available"] is True
    assert comparison["comparable"] is True
    assert len(comparison["series"]) == 3
    assert all(
        item["evidence_mode"] == "fixture"
        for item in comparison["series"]
    )
    assert 1 <= len(comparison["recommended_trial_ids"]) <= 6
    assert comparison["series"][0]["points"][0]["normalized_equity"] == 1.0

    app = create_app(root=tmp_path, database_path=database_path)
    response = TestClient(app).get(
        f"/api/experiment-plans/{plan.id}/trial-equity"
    )
    assert response.status_code == 200
    assert response.json()["series"][0]["source_artifact_key"] == artifact_key


def test_batch_retry_preserves_completed_trials_and_resource_failures(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service, _, _, _, plan = _approved_plan(repository)
    original = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "max_concurrent_trials": 99,
            "max_trial_seconds": 30,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    completed = service.record_trial(
        experiment_plan_id=plan.id,
        parameters={"window": 10},
        data_version="fixture-v1",
        candidate_version_id=plan.candidate_version_id,
        parameter_signature=parameter_signature({"window": 10}),
        status="succeeded",
        metrics={"validation_trade_count": 30.0},
    )
    repository.update_job(
        original.id,
        status="failed",
        updated_at=original.updated_at,
        error="interrupted before remaining Trials",
    )
    retry = service.retry_job(
        job_id=original.id,
        subject_id=original.id,
        confirmed_by_user=True,
    )
    runner = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry(
            (DeterministicFixtureStrategyEvaluator(),)
        ),
        executor=InProcessTrialExecutor(),
        metrics_sink=MemoryMetricsSink(),
        resource_policy=WorkerResourcePolicy(
            policy_id="test",
            max_rss_mb=4096,
            max_concurrent_trials=4,
            max_job_minutes=1,
            parquet_batch_rows=100,
            kill_on_memory_limit=True,
        ),
    )
    result = runner(retry)
    assert repository.get_trial_by_signature(
        plan.id, parameter_signature({"window": 10})
    ) == completed
    assert result["concurrency"] <= 4
    assert service.get_research_budget(
        repository.get_session_id_for_strategy_version(plan.baseline_version_id)
    ).reserved_trials == 3

    limited_plan = service.create_experiment_plan(
        baseline_version_id=plan.baseline_version_id,
        hypothesis="resource bound fixture",
        parameter_space=(ParameterSpace(name="x", kind="integer", values=(1,)),),
        objectives=(Objective(metric="return", direction="maximize"),),
        constraints=(Constraint(metric="validation_trade_count", operator="gte", value=1),),
        data_splits=plan.data_splits,
        cost_model=plan.cost_model,
        max_trials=1,
        time_budget_seconds=60,
        stopping_conditions=("resource stop",),
    )
    limited_plan = service.approve_experiment_plan(
        plan_id=limited_plan.id, confirmed_by_user=True
    )
    limited_job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": limited_plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 1,
            "max_concurrent_trials": 1,
            "max_trial_seconds": 30,
            "evaluator_id": "resource_limit_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    limited_runner = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry((ResourceLimitFixtureEvaluator(),)),
        executor=InProcessTrialExecutor(),
        metrics_sink=MemoryMetricsSink(),
        resource_policy=WorkerResourcePolicy(
            policy_id="limit-test",
            max_rss_mb=128,
            max_concurrent_trials=1,
            max_job_minutes=1,
            parquet_batch_rows=100,
            kill_on_memory_limit=True,
        ),
    )
    limited_runner(limited_job)
    failed = repository.list_trials(limited_plan.id)
    assert failed[0].status == "failed"
    assert failed[0].error == "trial time budget exceeded"
    assert failed[0].elapsed_seconds == 120.0
    assert failed[0].peak_rss_mb >= 9999.0


def test_correction_attempt_preserves_failed_trials_and_reuses_budget(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service, session, _, _, plan = _approved_plan(repository)
    original_job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    original_trials = (
        service.record_trial(
            experiment_plan_id=plan.id,
            parameters={"window": 10},
            data_version="fixture-v1",
            candidate_version_id=plan.candidate_version_id,
            parameter_signature=parameter_signature({"window": 10}),
            status="succeeded",
            metrics={"validation_trade_count": 30.0},
        ),
        service.record_trial(
            experiment_plan_id=plan.id,
            parameters={"window": 20},
            data_version="fixture-v1",
            candidate_version_id=plan.candidate_version_id,
            parameter_signature=parameter_signature({"window": 20}),
            status="failed",
            metrics={},
        ),
        service.record_trial(
            experiment_plan_id=plan.id,
            parameters={"window": 30},
            data_version="fixture-v1",
            candidate_version_id=plan.candidate_version_id,
            parameter_signature=parameter_signature({"window": 30}),
            status="failed",
            metrics={},
        ),
    )
    repository.update_job(
        original_job.id,
        status="succeeded",
        updated_at=original_job.updated_at,
    )
    budget_before = service.get_research_budget(session.id)

    correction = service.create_correction_experiment_plan(
        original_plan_id=plan.id,
        subject_id=plan.id,
        confirmed_by_user=True,
        parameter_space=(
            ParameterSpace(name="window", kind="integer", values=(20, 30)),
        ),
        max_trials=2,
        time_budget_seconds=300,
        correction_reason="reviewed evaluator contract mismatch",
    )
    correction_job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": correction.id,
            "correction_of_plan_id": plan.id,
            "correction_of_job_id": original_job.id,
            "reuse_reserved_budget": True,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 2,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    result = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry(
            (DeterministicFixtureStrategyEvaluator(),)
        ),
        executor=InProcessTrialExecutor(),
        metrics_sink=MemoryMetricsSink(),
        resource_policy=WorkerResourcePolicy(
            policy_id="test",
            max_rss_mb=4096,
            max_concurrent_trials=1,
            max_job_minutes=5,
            parquet_batch_rows=100,
            kill_on_memory_limit=True,
        ),
    )(correction_job)

    assert correction.correction_of_plan_id == plan.id
    assert result["correction_of_plan_id"] == plan.id
    assert [item.status for item in repository.list_trials(plan.id)] == [
        "succeeded",
        "failed",
        "failed",
    ]
    assert tuple(repository.list_trials(plan.id)) == original_trials
    corrected_trials = repository.list_trials(correction.id)
    assert [dict(item.parameters) for item in corrected_trials] == [
        {"window": 20},
        {"window": 30},
    ]
    assert all(item.status == "succeeded" for item in corrected_trials)
    budget_after = service.get_research_budget(session.id)
    assert budget_after.used_hypotheses == budget_before.used_hypotheses
    assert budget_after.reserved_trials == budget_before.reserved_trials
    assert (
        budget_after.reserved_compute_minutes
        == budget_before.reserved_compute_minutes
    )
    assert budget_after.used_locked_test_uses == 0


def test_batch_api_is_structured_and_openapi_remains_non_live(tmp_path: Path) -> None:
    app = create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/improvement-directions" in paths
    assert "/api/experiment-plans/{plan_id}/trials" in paths
    forbidden = ("trade", "live", "shell", "credential", "secret")
    assert not any(word in path.lower() for path in paths for word in forbidden)


def test_batch_summary_exposes_fixture_boundary(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/api.sqlite3")
    service, _, _, _, plan = _approved_plan(repository)
    service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    app = create_app(
        root=tmp_path,
        database_path=tmp_path / "runtime/app/api.sqlite3",
    )
    summary = TestClient(app).get(
        f"/api/experiment-plans/{plan.id}/batch-summary"
    ).json()
    assert summary["evidence_mode"] == "fixture"
    assert summary["research_conclusion_allowed"] is False


def test_proposal_execution_state_machine_and_locked_search_gate(tmp_path: Path) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    service, _, _, _, plan = _approved_plan(repository)
    guided = GuidedResearchService(repository)
    proposal = repository.get_proposal(plan.proposal_id or "")
    assert proposal.status == "approved"
    try:
        service.create_job(
            job_type="parameter_search",
            payload={
                "experiment_plan_id": plan.id,
                "batch_mode": True,
                "locked_test_used": True,
                "max_trials": 3,
                "evaluator_id": "deterministic_fixture",
                "evidence_mode": "fixture",
            },
        )
        raise AssertionError("locked test must be rejected from search")
    except Exception as exc:
        assert "locked-test" in str(exc)

    job = service.create_job(
        job_type="parameter_search",
        payload={
            "experiment_plan_id": plan.id,
            "batch_mode": True,
            "locked_test_used": False,
            "max_trials": 3,
            "max_concurrent_trials": 1,
            "max_trial_seconds": 30,
            "evaluator_id": "deterministic_fixture",
            "evidence_mode": "fixture",
            "data_version": "fixture-v1",
        },
    )
    assert repository.get_proposal(proposal.id).status == "executing"
    service.record_trial(
        experiment_plan_id=plan.id,
        parameters={"window": 10},
        data_version="fixture-v1",
        status="succeeded",
        candidate_version_id=plan.candidate_version_id,
        parameter_signature=parameter_signature({"window": 10}),
        metrics={"validation_trade_count": 30.0},
    )
    evaluated = guided.transition(
        proposal_id=proposal.id,
        target="evaluated",
        subject_id=proposal.id,
    )
    accepted = guided.transition(
        proposal_id=evaluated.id,
        target="accepted",
        subject_id=evaluated.id,
        confirmed_by_user=True,
    )
    assert accepted.status == "accepted"
    assert repository.get_strategy_version(plan.baseline_version_id).status == "baseline"
    assert job.status == "queued"
