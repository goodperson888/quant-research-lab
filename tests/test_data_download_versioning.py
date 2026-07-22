from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_downloader():
    path = ROOT / "scripts/download_binance_vision.py"
    spec = importlib.util.spec_from_file_location("download_binance_vision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_annual_download_plan_is_explicit_and_project_scoped(tmp_path: Path) -> None:
    downloader = _load_downloader()
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, tzinfo=timezone.utc)
    plan = downloader.build_plan(
        tmp_path,
        "ETHUSDT",
        start,
        end,
        timeframes=("15m", "1h"),
    )
    assert len(plan) == 12
    assert {item.timeframe for item in plan if item.dataset == "klines"} == {
        "15m",
        "1h",
    }
    assert all(item.raw_path.is_relative_to(tmp_path / "data/raw") for item in plan)


def test_processed_parquet_is_versioned_and_never_silently_overwritten(
    tmp_path: Path,
) -> None:
    downloader = _load_downloader()
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z"]
            ),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1.0, 2.0],
        }
    )
    outputs = downloader.write_partitioned_parquet(
        frame,
        root=tmp_path,
        symbol="ETHUSDT",
        dataset="klines",
        timeframe="15m",
        data_version="fixture-v1",
    )
    path = Path(outputs[0]["path"])
    assert "version=fixture-v1" in path.as_posix()
    assert path.is_file()

    downloader.write_partitioned_parquet(
        frame,
        root=tmp_path,
        symbol="ETHUSDT",
        dataset="klines",
        timeframe="15m",
        data_version="fixture-v1",
    )
    changed = frame.copy()
    changed.loc[0, "close"] = 999.0
    with pytest.raises(FileExistsError, match="different content"):
        downloader.write_partitioned_parquet(
            changed,
            root=tmp_path,
            symbol="ETHUSDT",
            dataset="klines",
            timeframe="15m",
            data_version="fixture-v1",
        )


def test_manifest_uses_relative_keys_and_is_immutable(tmp_path: Path) -> None:
    downloader = _load_downloader()
    raw_path = tmp_path / "data/raw/binance_vision/fixture.zip"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"fixture")
    processed_path = tmp_path / "data/processed/version=v1/fixture.parquet"
    processed_path.parent.mkdir(parents=True)
    processed_path.write_bytes(b"parquet-fixture")
    kwargs = {
        "root": tmp_path,
        "profile": {"profile_id": "fixture", "cost_model": {"funding_rate": "required"}},
        "start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "end": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "records": [{
            "dataset": "klines",
            "timeframe": "15m",
            "archive_kind": "daily",
            "period": "2026-01-01",
            "url": "https://data.binance.vision/fixture.zip",
            "raw_path": str(raw_path),
            "status": "cached_immutable",
        }],
        "processed": [{
            "dataset": "futures_ohlcv",
            "timeframe": "15m",
            "status": "processed",
            "quality": {"rows": 1},
            "outputs": [{"path": str(processed_path), "rows": 1}],
        }],
        "dataset_id": "fixture",
        "data_version": "fixture-v1",
        "manifest_name": "fixture.json",
        "timeframes": ("15m",),
        "proxy_used": False,
    }
    path = downloader.write_manifest(**kwargs)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["raw_archives"][0]["raw_path"].startswith("data/raw/")
    assert manifest["processed_datasets"][0]["outputs"][0]["path"].startswith(
        "data/processed/"
    )
    with pytest.raises(FileExistsError, match="immutable"):
        downloader.write_manifest(**kwargs)
