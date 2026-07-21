from __future__ import annotations

import json

from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.paths import app_database_path
from quant_lab.workers.runner import LocalWorker


def main() -> int:
    repository = SQLiteProductRepository(app_database_path())
    repository.initialize()
    worker = LocalWorker(repository)
    print(json.dumps(worker.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
