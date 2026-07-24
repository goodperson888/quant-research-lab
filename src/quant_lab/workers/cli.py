from __future__ import annotations

import argparse
import json

from quant_lab.infrastructure.baseline_backtest_runner import BaselineBacktestRunner
from quant_lab.infrastructure.backtest_engines import NativeBacktestEngineAdapter
from quant_lab.infrastructure.candidate_cost_stress_runner import (
    CandidateCostStressRunner,
)
from quant_lab.infrastructure.entry_confirmation_experiment_runner import (
    EntryConfirmationExperimentRunner,
)
from quant_lab.infrastructure.batch_parameter_search_runner import (
    BatchParameterSearchRunner,
    ParameterSearchRouter,
)
from quant_lab.infrastructure.strategy_evaluators import (
    DeterministicFixtureStrategyEvaluator,
    StrategyEvaluatorRegistry,
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
from quant_lab.paths import app_database_path, project_root
from quant_lab.workers.runner import LocalWorker


def main() -> int:
    parser = argparse.ArgumentParser(prog="quant-lab-worker")
    parser.add_argument("--job-id")
    args = parser.parse_args()
    repository = SQLiteProductRepository(app_database_path())
    repository.initialize()
    root = project_root()
    resource_policy = WorkerResourcePolicyReader(root).read()
    legacy_parameter_runner = EntryConfirmationExperimentRunner(root, repository)
    batch_parameter_runner = BatchParameterSearchRunner(
        repository,
        evaluator_registry=StrategyEvaluatorRegistry(
            (DeterministicFixtureStrategyEvaluator(),)
        ),
        executor=ProcessTrialExecutor(),
        metrics_sink=ParquetTrialMetricsSink(root),
        resource_policy=resource_policy,
        component_aggregator=DefaultComponentEvidenceAggregator(repository),
    )
    worker = LocalWorker(
        repository,
        resource_policy=resource_policy,
        handlers={
            "backtest": NativeBacktestEngineAdapter(
                BaselineBacktestRunner(root, repository)
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
            "stress_test": CandidateCostStressRunner(root, repository),
        },
    )
    if args.job_id:
        result = worker.run(args.job_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(worker.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
