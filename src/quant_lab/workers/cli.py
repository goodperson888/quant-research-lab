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
    worker = LocalWorker(
        repository,
        resource_policy=WorkerResourcePolicyReader(root).read(),
        handlers={
            "backtest": NativeBacktestEngineAdapter(
                BaselineBacktestRunner(root, repository)
            ).run,
            "parameter_search": EntryConfirmationExperimentRunner(root, repository),
            "regime_validation": RegimeValidationRunner(root, repository),
            "correctness_diagnostic": FreqtradeCorrectnessDiagnosticRunner(
                root,
                repository,
                timeout_seconds=WorkerResourcePolicyReader(root).read().max_job_minutes
                * 60,
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
