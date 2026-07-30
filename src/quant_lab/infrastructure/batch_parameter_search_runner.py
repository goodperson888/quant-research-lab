from __future__ import annotations

import os
import time
from typing import Any, Mapping

from quant_lab.application.batch_trials import (
    DeterministicParameterGenerator,
    parameter_signature,
    summarize_stable_ranges,
)
from quant_lab.application.ports import TrialEvaluationRequest, TrialExecutor, TrialMetricsSink
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import JobCancelledError
from quant_lab.domain.models import Job, Trial
from quant_lab.domain.repositories import ProductRepository
from quant_lab.infrastructure.policy_readers import WorkerResourcePolicy
from quant_lab.infrastructure.strategy_evaluators import StrategyEvaluatorRegistry


def detected_memory_gb() -> float:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024**3)
    except (AttributeError, OSError, ValueError):
        return 8.0


class BatchParameterSearchRunner:
    """Run an approved plan in bounded, resumable Trial batches."""

    def __init__(
        self,
        repository: ProductRepository,
        *,
        evaluator_registry: StrategyEvaluatorRegistry,
        executor: TrialExecutor,
        metrics_sink: TrialMetricsSink,
        resource_policy: WorkerResourcePolicy,
        component_aggregator=None,
    ) -> None:
        self.repository = repository
        self.service = ResearchApplicationService(repository)
        self.evaluators = evaluator_registry
        self.executor = executor
        self.metrics_sink = metrics_sink
        self.policy = resource_policy
        self.generator = DeterministicParameterGenerator()
        self.component_aggregator = component_aggregator

    def __call__(self, job: Job) -> Mapping[str, Any]:
        if job.payload.get("batch_mode") is not True:
            raise ValueError("batch runner requires batch_mode=true")
        if job.payload.get("locked_test_used") is not False:
            raise ValueError("batch parameter search cannot use locked-test data")
        plan_id = job.payload.get("experiment_plan_id")
        if not isinstance(plan_id, str):
            raise ValueError("batch parameter search requires experiment_plan_id")
        plan = self.repository.get_experiment_plan(plan_id)
        if plan.status != "approved" or plan.approved_by != "user":
            raise ValueError("batch parameter search requires an approved plan")
        if plan.candidate_version_id:
            candidate = self.repository.get_strategy_version(plan.candidate_version_id)
            if candidate.status != "candidate" or not candidate.immutable:
                raise ValueError("batch plan candidate snapshot must be immutable")
        requested_trials = int(job.payload.get("max_trials", plan.max_trials or 0))
        if requested_trials <= 0 or not plan.max_trials or requested_trials > plan.max_trials:
            raise ValueError("job Trial budget must be positive and within the approved plan")

        evaluator_id = str(job.payload.get("evaluator_id", ""))
        if evaluator_id == "deterministic_fixture" and job.payload.get("evidence_mode") != "fixture":
            raise ValueError("fixture evaluator must be explicitly labelled evidence_mode=fixture")
        evaluator = self.evaluators.get(evaluator_id)
        parameter_batch = self.generator.generate(plan)
        combinations = parameter_batch.combinations[:requested_trials]

        memory_gb = detected_memory_gb()
        recommended = (
            self.policy.recommended_concurrency(memory_gb)
            if self.policy.auto_concurrency
            else self.policy.default_concurrent_trials
        )
        requested_concurrency = int(job.payload.get("max_concurrent_trials", recommended))
        concurrency = max(
            1, min(requested_concurrency, recommended, self.policy.max_concurrent_trials)
        )
        executor_limit = getattr(self.executor, "max_supported_concurrency", None)
        if isinstance(executor_limit, int):
            concurrency = min(concurrency, executor_limit)
        per_trial_timeout = int(
            job.payload.get("max_trial_seconds", self.policy.max_job_minutes * 60)
        )
        if per_trial_timeout <= 0 or per_trial_timeout > self.policy.max_job_minutes * 60:
            raise ValueError("per-Trial timeout exceeds Worker policy")

        existing = {
            item.parameter_signature: item
            for item in self.repository.list_trials(plan.id)
            if item.parameter_signature
        }
        runnable: list[Trial] = []
        skipped_completed = 0
        for parameters in combinations:
            signature = parameter_signature(parameters)
            previous = existing.get(signature)
            if previous is not None and previous.status in {"succeeded", "failed"}:
                skipped_completed += 1
                continue
            if previous is None:
                previous = self.service.record_trial(
                    experiment_plan_id=plan.id,
                    parameters=parameters,
                    data_version=str(job.payload.get("data_version", "unspecified")),
                    candidate_version_id=plan.candidate_version_id,
                    parameter_signature=signature,
                    split="train_validation",
                    cost_model=plan.cost_model,
                    seed=plan.random_seed,
                )
            runnable.append(previous)

        started = time.monotonic()
        metrics_artifacts: list[str] = []
        pending_metric_rows: list[Mapping[str, Any]] = []
        stop_reason: str | None = None

        def flush_metric_rows() -> None:
            if not pending_metric_rows:
                return
            rows = list(pending_metric_rows)
            pending_metric_rows.clear()
            artifact_keys = self.metrics_sink.append_batch(
                experiment_plan_id=plan.id, rows=rows
            )
            metrics_artifacts.extend(artifact_keys)
            if not artifact_keys:
                return
            artifact_key = artifact_keys[0]
            for row in rows:
                current = next(
                    item
                    for item in self.repository.list_trials(plan.id)
                    if item.id == row["trial_id"]
                )
                self.service.update_trial(
                    trial_id=current.id,
                    status=current.status,
                    metrics=current.metrics,
                    log_artifact_key=current.log_artifact_key,
                    error=current.error,
                    elapsed_seconds=current.elapsed_seconds,
                    peak_rss_mb=current.peak_rss_mb,
                    result_artifact_key=current.result_artifact_key,
                    metrics_artifact_key=artifact_key,
                )

        for offset in range(0, len(runnable), concurrency):
            current_job = self.repository.get_job(job.id)
            if current_job.status == "cancelled":
                stop_reason = "cancelled_by_user"
                flush_metric_rows()
                for item in runnable[offset:]:
                    self.service.update_trial(
                        trial_id=item.id,
                        status="cancelled",
                        metrics=item.metrics,
                        log_artifact_key=item.log_artifact_key,
                        error="batch cancelled before Trial execution",
                    )
                raise JobCancelledError("batch parameter search cancelled by user")
            chunk = runnable[offset : offset + concurrency]
            requests: list[TrialEvaluationRequest] = []
            for item in chunk:
                self.service.update_trial(
                    trial_id=item.id,
                    status="running",
                    metrics=item.metrics,
                    log_artifact_key=item.log_artifact_key,
                )
                requests.append(
                    TrialEvaluationRequest(
                        trial_id=item.id,
                        experiment_plan_id=plan.id,
                        baseline_version_id=plan.baseline_version_id,
                        candidate_version_id=plan.candidate_version_id,
                        parameters=item.parameters,
                        data_version=item.data_version,
                        data_splits={
                            "train": plan.data_splits["train"],
                            "validation": plan.data_splits["validation"],
                        },
                        cost_model=plan.cost_model,
                        seed=plan.random_seed,
                    )
                )
            results = self.executor.execute(
                evaluator=evaluator,
                requests=requests,
                max_concurrency=concurrency,
                timeout_seconds=per_trial_timeout,
                max_rss_mb=self.policy.max_rss_mb,
                kill_on_memory_limit=self.policy.kill_on_memory_limit,
            )
            rows = []
            for result in results:
                trial = next(item for item in chunk if item.id == result.trial_id)
                metrics = dict(result.metrics)
                self.service.update_trial(
                    trial_id=result.trial_id,
                    status=result.status,
                    metrics=metrics,
                    log_artifact_key=None,
                    error=result.error,
                    elapsed_seconds=result.elapsed_seconds,
                    peak_rss_mb=result.peak_rss_mb,
                    result_artifact_key=result.result_artifact_key,
                )
                rows.append(
                    {
                        "trial_id": result.trial_id,
                        "experiment_plan_id": plan.id,
                        "candidate_version_id": plan.candidate_version_id,
                        "parameter_signature": trial.parameter_signature,
                        "parameters": dict(trial.parameters),
                        "data_version": trial.data_version,
                        "split": "train_validation",
                        "seed": trial.seed,
                        "status": result.status,
                        "metrics": metrics,
                        "error": result.error,
                        "elapsed_seconds": result.elapsed_seconds,
                        "peak_rss_mb": result.peak_rss_mb,
                        "evidence_mode": job.payload.get("evidence_mode", "research"),
                    }
                )
            pending_metric_rows.extend(rows)
            if len(pending_metric_rows) >= self.policy.parquet_batch_rows:
                flush_metric_rows()

        flush_metric_rows()

        trials = list(self.repository.list_trials(plan.id))
        summary = summarize_stable_ranges(trials, plan.constraints)
        peak_rss = max((item.peak_rss_mb or 0.0 for item in trials), default=0.0)
        summary.update(
            {
                "experiment_plan_id": plan.id,
                "correction_of_plan_id": plan.correction_of_plan_id,
                "baseline_version_id": plan.baseline_version_id,
                "candidate_version_id": plan.candidate_version_id,
                "search_strategy": plan.search_strategy,
                "seed": plan.random_seed,
                "generated_combinations": parameter_batch.generated_count,
                "truncated_by_plan_budget": parameter_batch.truncated_by_budget,
                "skipped_completed_trials": skipped_completed,
                "concurrency": concurrency,
                "recommended_concurrency": recommended,
                "detected_memory_gb": round(memory_gb, 2),
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "peak_rss_mb": round(peak_rss, 3),
                "stop_reason": stop_reason,
                "metrics_artifact_keys": metrics_artifacts,
                "evidence_mode": job.payload.get("evidence_mode", "research"),
                "research_conclusion_allowed": job.payload.get("evidence_mode") != "fixture",
            }
        )
        attribution = job.payload.get("component_attribution")
        if attribution is not None:
            if self.component_aggregator is None or not isinstance(attribution, Mapping):
                raise ValueError("component attribution is not configured")
            aggregated = self.component_aggregator.aggregate(
                experiment_plan_id=plan.id, attribution=attribution
            )
            summary["component_evidence"] = {
                "evidence_id": aggregated["evidence"].id,
                "candidate_id": aggregated["candidate"].id,
                "candidate_status": aggregated["candidate"].status,
                "deduplicated": aggregated["deduplicated"],
                "automatic_validation": False,
            }
        return summary


class ParameterSearchRouter:
    """Preserve the legacy one-Trial Handler while routing new batch jobs explicitly."""

    def __init__(self, *, batch_runner, legacy_runner) -> None:
        self.batch_runner = batch_runner
        self.legacy_runner = legacy_runner

    def __call__(self, job: Job) -> Mapping[str, Any]:
        if job.payload.get("batch_mode") is True:
            return self.batch_runner(job)
        return self.legacy_runner(job)
