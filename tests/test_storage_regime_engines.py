from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quant_lab.application.backtest_engines import BacktestEngineRegistry
from quant_lab.application.pipeline import PipelineApplicationService, PipelineProfileCatalog
from quant_lab.application.regime import (
    ExAnteRegimeDetector,
    RegimeDetectorCatalog,
    summarize_trades_by_regime,
)
from quant_lab.application.services import ResearchApplicationService
from quant_lab.application.storage import StoragePolicyCatalog, StorageReporter
from quant_lab.domain.errors import GatePolicyError, InvalidJobError
from quant_lab.domain.models import Job
from quant_lab.infrastructure.backtest_engines import (
    FreqtradeBacktestEngineAdapter,
    NativeBacktestEngineAdapter,
)
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.interfaces.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def _baseline(tmp_path: Path):
    repository = SQLiteProductRepository(tmp_path / "runtime/app/test.sqlite3")
    research = ResearchApplicationService(repository)
    session = research.create_research_session(title="governance")
    draft = research.create_strategy_intake(
        session_id=session.id,
        source_type="natural_language",
        raw_content="test",
    )
    baseline = research.freeze_baseline(draft_id=draft.id, confirmed_by_user=True)
    return repository, research, baseline


def test_storage_policy_is_read_only_and_classifies_project_areas(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    shutil.copy(ROOT / "configs/storage-policy.yaml", tmp_path / "configs/storage-policy.yaml")
    (tmp_path / "data/raw").mkdir(parents=True)
    (tmp_path / "data/raw/source.zip").write_bytes(b"official")
    report = StorageReporter(tmp_path).read()
    assert report["automatic_deletion"] is False
    assert "data/raw" in report["summary"]["authoritative_areas"]
    assert "data/catalog" in report["summary"]["rebuildable_areas"]
    assert report["summary"]["file_count"] == 1
    with pytest.raises(ValueError, match="project-relative"):
        StoragePolicyCatalog(tmp_path).resolve_artifact_key("../outside")


def test_regime_detector_is_delayed_and_does_not_rewrite_past_labels() -> None:
    config = RegimeDetectorCatalog(ROOT).load(
        "configs/regimes/eth_perpetual_1h_v1.yaml"
    )
    detector = ExAnteRegimeDetector(config)
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01T00:00:00Z", periods=120, freq="1h"
            ),
            "close": [2000 + index * 2 for index in range(120)],
        }
    )
    original = detector.detect(bars)
    extended = pd.concat(
        [
            bars,
            pd.DataFrame(
                {
                    "timestamp": [pd.Timestamp("2026-01-06T00:00:00Z")],
                    "close": [9999.0],
                }
            ),
        ],
        ignore_index=True,
    )
    rerun = detector.detect(extended)
    assert (
        original["effective_from"] - original["source_bar_open_time"]
    ).eq(pd.Timedelta("1h")).all()
    pd.testing.assert_series_equal(
        original["regime"], rerun.iloc[: len(original)]["regime"], check_names=False
    )
    assert original["ex_ante_observable"].all()


def test_regime_summary_normalizes_datetime_precision() -> None:
    trades = pd.DataFrame(
        {
            "entry_time": pd.Series(
                [pd.Timestamp("2026-01-01T02:00:00Z")],
                dtype="datetime64[ms, UTC]",
            ),
            "net_return": [0.01],
        }
    )
    labels = pd.DataFrame(
        {
            "effective_from": pd.Series(
                [pd.Timestamp("2026-01-01T01:00:00Z")],
                dtype="datetime64[us, UTC]",
            ),
            "regime": ["trend_up__low_vol"],
        }
    )
    metrics, groups = summarize_trades_by_regime(
        trades,
        labels,
        minimum_trades=1,
    )
    assert metrics["trend_up__low_vol"]["trade_count"] == 1.0
    assert groups["suitable"] == ("trend_up__low_vol",)


def test_component_triage_requires_explicit_ablation_lineage(tmp_path: Path) -> None:
    repository, _research, baseline = _baseline(tmp_path)
    pipeline = PipelineApplicationService(repository, PipelineProfileCatalog(ROOT))
    with pytest.raises(GatePolicyError, match="lineage fields"):
        pipeline.triage_component(
            source_strategy_version_id=baseline.id,
            lineage={"hypothesis": "one change"},
            component_type="filter",
            target_market_profile="crypto_perpetual.binance.eth",
            incremental_metrics={"validation_net_return_delta": 0.01},
            out_of_sample_status="screening",
            failure_conditions=(),
            name="filter",
            status="diagnostic_improvement",
        )
    evidence, candidate = pipeline.triage_component(
        source_strategy_version_id=baseline.id,
        lineage={
            "hypothesis": "one change",
            "baseline_version_id": baseline.id,
            "source_run_ids": ["run_fixture"],
            "ablation": "baseline versus one filter",
        },
        component_type="filter",
        target_market_profile="crypto_perpetual.binance.eth",
        incremental_metrics={"validation_net_return_delta": 0.01},
        out_of_sample_status="screening",
        failure_conditions=(),
        name="filter",
        status="diagnostic_improvement",
    )
    assert evidence.out_of_sample_status == "screening"
    assert candidate.status == "diagnostic_improvement"


def test_regime_job_requires_ex_ante_and_never_uses_locked_test(tmp_path: Path) -> None:
    _repository, research, baseline = _baseline(tmp_path)
    base = {
        "subject_type": "strategy_version",
        "subject_id": baseline.id,
        "market_profile": "crypto_perpetual.binance.eth",
        "detector_config_artifact_key": "configs/regimes/eth_perpetual_1h_v1.yaml",
        "data_manifest_artifact_key": "data/manifests/data.json",
        "trades_artifact_key": "experiments/runs/run/trades.parquet",
        "locked_test_used": False,
        "mode": "regime_diagnostic",
    }
    with pytest.raises(InvalidJobError, match="ex-ante"):
        research.create_job(
            job_type="regime_validation",
            payload={**base, "ex_ante_observable": False},
        )
    queued = research.create_job(
        job_type="regime_validation",
        payload={**base, "ex_ante_observable": True},
    )
    assert queued.status == "queued"


def test_backtest_engine_registry_and_freqtrade_adapter_are_non_live() -> None:
    job = Job(
        id="job_fixture",
        job_type="backtest",
        status="queued",
        payload={"live": False},
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    native = NativeBacktestEngineAdapter(lambda current: {"job_id": current.id})
    freqtrade = FreqtradeBacktestEngineAdapter(
        lambda current: {"job_id": current.id, "engine": "fixture"}
    )
    registry = BacktestEngineRegistry((native, freqtrade))
    assert registry.get("native_research").run(job)["job_id"] == job.id
    assert registry.get("freqtrade_2026_6").run(job)["engine"] == "fixture"
    with pytest.raises(ValueError, match="live trade"):
        freqtrade.run(
            Job(
                id="unsafe",
                job_type="backtest",
                status="queued",
                payload={"live": True},
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )


def test_storage_and_engine_read_only_api(tmp_path: Path) -> None:
    (tmp_path / "configs/pipelines").mkdir(parents=True)
    for source in (ROOT / "configs/pipelines").glob("*.yaml"):
        shutil.copy(source, tmp_path / "configs/pipelines" / source.name)
    shutil.copy(ROOT / "configs/storage-policy.yaml", tmp_path / "configs/storage-policy.yaml")
    client = TestClient(
        create_app(root=tmp_path, database_path=tmp_path / "runtime/app/api.sqlite3")
    )
    assert client.get("/api/storage/report").status_code == 200
    engines = client.get("/api/backtest-engines")
    assert engines.status_code == 200
    assert {item["engine_id"] for item in engines.json()} == {
        "native_research",
        "freqtrade_2026_6",
    }
