from __future__ import annotations

import argparse
import json
import signal
import time
from typing import Any

from quant_lab.infrastructure.backtest_engines import NativeBacktestEngineAdapter
from quant_lab.infrastructure.authorized_pipeline_runner import (
    AuthorizedPipelineRunner,
)
from quant_lab.infrastructure.candidate_cost_stress_runner import (
    CandidateCostStressRunner,
)
from quant_lab.infrastructure.generic_strategy_dsl_candidate_validation import (
    GenericStrategyDslCandidateValidationRunner,
    StressTestRouter,
)
from quant_lab.infrastructure.generic_strategy_dsl_locked_test import (
    GenericStrategyDslLockedTestRunner,
)
from quant_lab.infrastructure.entry_confirmation_experiment_runner import (
    EntryConfirmationExperimentRunner,
)
from quant_lab.infrastructure.batch_parameter_search_runner import (
    BatchParameterSearchRunner,
    ParameterSearchRouter,
)
from quant_lab.infrastructure.builtin_strategy_plugins import (
    build_builtin_evaluator_registry,
    build_builtin_strategy_plugins,
)
from quant_lab.infrastructure.trial_execution import ProcessTrialExecutor
from quant_lab.infrastructure.trial_metrics import ParquetTrialMetricsSink
from quant_lab.application.component_attribution import DefaultComponentEvidenceAggregator
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.regime_validation_runner import RegimeValidationRunner
from quant_lab.infrastructure.freqtrade_correctness_runner import (
    FreqtradeCorrectnessDiagnosticRunner,
)
from quant_lab.infrastructure.policy_readers import WorkerResourcePolicyReader
from quant_lab.infrastructure.research_diagnostics_runner import (
    ResearchDiagnosticsRunner,
)
from quant_lab.paths import app_database_path, project_root
from quant_lab.workers.runner import LocalWorker


def build_worker() -> LocalWorker:
    repository = SQLiteProductRepository(app_database_path())
    repository.initialize()
    root = project_root()
    resource_policy = WorkerResourcePolicyReader(root).read()
    legacy_parameter_runner = EntryConfirmationExperimentRunner(root, repository)
    strategy_plugins = build_builtin_strategy_plugins(root, repository)
    batch_parameter_runner = BatchParameterSearchRunner(
        repository,
        evaluator_registry=build_builtin_evaluator_registry(root, repository),
        executor=ProcessTrialExecutor(),
        metrics_sink=ParquetTrialMetricsSink(root),
        resource_policy=resource_policy,
        component_aggregator=DefaultComponentEvidenceAggregator(repository),
        artifact_root=root,
    )
    return LocalWorker(
        repository,
        resource_policy=resource_policy,
        handlers={
            "backtest": NativeBacktestEngineAdapter(
                strategy_plugins.route_backtest
            ).run,
            "parameter_search": ParameterSearchRouter(
                batch_runner=batch_parameter_runner,
                legacy_runner=legacy_parameter_runner,
            ),
            "regime_validation": RegimeValidationRunner(root, repository),
            "correctness_diagnostic": FreqtradeCorrectnessDiagnosticRunner(
                root,
                repository,
                timeout_seconds=resource_policy.max_job_minutes * 60,
            ),
            "research_diagnostic": ResearchDiagnosticsRunner(root, repository),
            "stress_test": StressTestRouter(
                generic_validation=(
                    GenericStrategyDslCandidateValidationRunner(
                        root, repository
                    )
                ),
                generic_locked_test=GenericStrategyDslLockedTestRunner(
                    root, repository
                ),
                legacy_cost_stress=CandidateCostStressRunner(
                    root, repository
                ),
            ),
            "pipeline_execution": AuthorizedPipelineRunner(
                root, repository, strategy_plugins
            ),
        },
    )


def run_watch(worker: LocalWorker, *, poll_seconds: float) -> int:
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    print(
        json.dumps(
            {
                **worker.status(),
                "process_running": True,
                "watching_queue": True,
                "poll_seconds": poll_seconds,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        while not stopping:
            job = worker.next_queued_job()
            if job is None:
                time.sleep(poll_seconds)
                continue
            try:
                result = worker.run(job.id)
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "job_id": job.id,
                            "job_type": job.job_type,
                            "status": "failed",
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                continue
            print(
                json.dumps(
                    {
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "status": "succeeded",
                        "run_id": result.get("run_id"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="quant-lab-worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--job-id")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be greater than 0")
    worker = build_worker()
    if args.job_id:
        result = worker.run(args.job_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.watch:
        return run_watch(worker, poll_seconds=args.poll_seconds)
    else:
        print(json.dumps(worker.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
