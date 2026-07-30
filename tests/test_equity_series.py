from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from quant_lab.application.equity_series import RunBundleEquityReader
from quant_lab.domain.models import Job, Report
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app


def test_run_bundle_equity_reader_uses_registered_bundle_not_caller_path(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    repository.initialize()
    repository.create_job(
        Job(
            id="job_equity",
            job_type="report",
            status="succeeded",
            payload={},
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:00+00:00",
        )
    )
    repository.create_report(
        Report(
            id="report_equity",
            job_id="job_equity",
            report_type="baseline_backtest",
            artifact_key="reports/backtests/equity.md",
            summary={"run_id": "run_equity"},
            created_at="2026-07-29T00:00:00+00:00",
        )
    )
    store = LocalArtifactStore(tmp_path)
    equity_key = "experiments/runs/run_equity/equity.parquet"
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=120, freq="5min", tz="UTC"
            ),
            "equity": [1000 + index for index in range(120)],
        }
    )
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    store.put(equity_key, buffer.getvalue())
    store.put(
        "experiments/runs/run_equity/manifest.json",
        json.dumps({"outputs": [{"artifact_key": equity_key}]}).encode(),
    )

    result = RunBundleEquityReader(repository, store).read(
        bundle_id="report_equity", max_points=50
    )

    assert result["available"] is True
    assert len(result["series"]) == 1
    assert len(result["series"][0]["points"]) == 50
    assert result["series"][0]["points"][0]["normalized_equity"] == 1.0
    assert result["series"][0]["source_artifact_key"] == equity_key


def test_run_bundle_equity_reader_reports_missing_curve_without_fabrication(
    tmp_path: Path,
) -> None:
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    repository.initialize()
    repository.create_job(
        Job(
            id="job_missing",
            job_type="report",
            status="succeeded",
            payload={},
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:00+00:00",
        )
    )
    repository.create_report(
        Report(
            id="report_missing",
            job_id="job_missing",
            report_type="batch_summary",
            artifact_key="reports/backtests/missing.md",
            summary={"run_id": "run_missing"},
            created_at="2026-07-29T00:00:00+00:00",
        )
    )

    result = RunBundleEquityReader(
        repository, LocalArtifactStore(tmp_path)
    ).read(bundle_id="report_missing")

    assert result["available"] is False
    assert result["series"] == []
    assert "不存在" in result["reason"]


def test_run_bundle_chart_api_returns_explicit_unavailable(tmp_path: Path) -> None:
    app = create_app(
        root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3"
    )
    repository = app.state.repository
    repository.create_job(
        Job(
            id="job_api_chart",
            job_type="report",
            status="succeeded",
            payload={},
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:00+00:00",
        )
    )
    repository.create_report(
        Report(
            id="report_api_chart",
            job_id="job_api_chart",
            report_type="batch_summary",
            artifact_key="reports/backtests/api-chart.md",
            summary={"run_id": "run_api_chart"},
            created_at="2026-07-29T00:00:00+00:00",
        )
    )

    response = TestClient(app).get(
        "/api/run-bundles/report_api_chart/chart-series"
    )

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["series"] == []
