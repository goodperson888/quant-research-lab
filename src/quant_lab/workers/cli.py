from __future__ import annotations

import argparse
import json

from quant_lab.infrastructure.baseline_backtest_runner import BaselineBacktestRunner
from quant_lab.infrastructure.candidate_cost_stress_runner import (
    CandidateCostStressRunner,
)
from quant_lab.infrastructure.entry_confirmation_experiment_runner import (
    EntryConfirmationExperimentRunner,
)
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
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
        handlers={
            "backtest": BaselineBacktestRunner(root, repository),
            "parameter_search": EntryConfirmationExperimentRunner(root, repository),
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
