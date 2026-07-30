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
                "2026-01-01", periods=120, freq="1h", tz="UTC"
            ),
            "equity": [1000 + index for index in range(120)],
        }
    )
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    store.put(equity_key, buffer.getvalue())
    market_key = "data/processed/eth-1h.parquet"
    market_buffer = io.BytesIO()
    pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=120, freq="1h", tz="UTC"
            ),
            "open": [1999 + index * 2 for index in range(120)],
            "high": [2003 + index * 2 for index in range(120)],
            "low": [1997 + index * 2 for index in range(120)],
            "close": [2000 + index * 2 for index in range(120)],
            "volume": [100 + index for index in range(120)],
        }
    ).to_parquet(market_buffer, index=False)
    store.put(market_key, market_buffer.getvalue())
    market_5m_key = "data/processed/eth-5m.parquet"
    market_5m_buffer = io.BytesIO()
    pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=120, freq="5min", tz="UTC"
            ),
            "open": [1999 + index * 0.2 for index in range(120)],
            "high": [2001 + index * 0.2 for index in range(120)],
            "low": [1998 + index * 0.2 for index in range(120)],
            "close": [2000 + index * 0.2 for index in range(120)],
            "volume": [10 + index for index in range(120)],
        }
    ).to_parquet(market_5m_buffer, index=False)
    store.put(market_5m_key, market_5m_buffer.getvalue())
    trade_key = "experiments/runs/run_equity/trades.parquet"
    trade_buffer = io.BytesIO()
    pd.DataFrame(
        {
            "trade_id": [1],
            "side": ["long"],
            "entry_time": [pd.Timestamp("2026-01-02T01:00:00Z")],
            "exit_time": [pd.Timestamp("2026-01-02T04:00:00Z")],
            "entry_execution_price": [2050.5],
            "exit_execution_price": [2062.0],
            "initial_stop_price": [2040.0],
            "take_profit_price": [2070.0],
            "net_return": [0.004],
            "net_pnl": [4.0],
            "exit_reason": ["take_profit"],
            "split": ["validation"],
        }
    ).to_parquet(trade_buffer, index=False)
    store.put(trade_key, trade_buffer.getvalue())
    data_manifest_key = "data/manifests/eth-fixture.json"
    store.put(
        data_manifest_key,
        json.dumps(
            {
                "symbol": {"unified": "ETH/USDT:USDT"},
                "processed_datasets": [
                    {
                        "dataset": "futures_ohlcv",
                        "timeframe": "5m",
                        "outputs": [{"path": market_5m_key}],
                    },
                    {
                        "dataset": "futures_ohlcv",
                        "timeframe": "1h",
                        "outputs": [{"path": market_key}],
                    }
                ],
            }
        ).encode(),
    )
    store.put(
        "experiments/runs/run_equity/manifest.json",
        json.dumps(
            {
                "outputs": [
                    {"artifact_key": equity_key},
                    {"artifact_key": trade_key},
                ],
                "data_manifest": {"artifact_key": data_manifest_key},
            }
        ).encode(),
    )

    result = RunBundleEquityReader(repository, store).read(
        bundle_id="report_equity", max_points=50
    )

    assert result["available"] is True
    assert len(result["series"]) == 1
    assert len(result["series"][0]["points"]) == 50
    assert result["series"][0]["points"][0]["normalized_equity"] == 1.0
    assert result["series"][0]["source_artifact_key"] == equity_key
    assert result["market_series"]["label"] == "ETH/USDT:USDT 行情"
    assert result["market_series"]["source_timeframe"] == "1h"
    assert result["available_market_timeframes"] == ["5m", "1h"]
    assert result["market_series"]["aggregated"] is True
    assert "由1小时K线聚合" in result["market_series"]["timeframe"]
    assert len(result["market_series"]["points"]) == 50
    assert result["market_series"]["points"][0]["value"] == 2004.0
    assert len(result["market_series"]["candles"]) == 50
    assert result["market_series"]["candles"][0]["open"] == 1999.0
    assert result["market_series"]["candles"][0]["volume"] > 0
    assert result["trades"][0]["side"] == "long"
    assert result["trades"][0]["entry_price"] == 2050.5
    assert result["trades"][0]["stop_price"] == 2040.0
    assert result["trades"][0]["take_profit_price"] == 2070.0
    assert result["trade_source_artifact_keys"] == [trade_key]

    five_minute_result = RunBundleEquityReader(repository, store).read(
        bundle_id="report_equity",
        max_points=50,
        market_timeframe="5m",
    )
    assert five_minute_result["market_series"]["source_timeframe"] == "5m"
    assert "由5分钟K线聚合" in five_minute_result["market_series"]["timeframe"]
    assert five_minute_result["available_market_timeframes"] == ["5m", "1h"]


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
    assert result["trades"] == []
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
