import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_eth_perpetual_profiles_keep_research_safety_defaults() -> None:
    for name in (
        "crypto_perpetual.okx.eth.yaml",
        "crypto_perpetual.binance.eth.yaml",
    ):
        profile = yaml.safe_load(
            (ROOT / "configs" / "market_profiles" / name).read_text(encoding="utf-8")
        )
        assert profile["enabled"] is True
        assert profile["symbols"][0]["unified"] == "ETH/USDT:USDT"
        assert profile["timeframes"]["download"] == ["5m", "15m", "1h", "4h"]
        assert profile["risk_defaults"]["leverage"] == 1.0
        assert profile["risk_defaults"]["live_trading_enabled"] is False
        assert profile["cost_model"]["funding_rate"] == "required_never_silently_zero"


def test_committed_data_manifest_records_quality_and_missing_funding() -> None:
    manifest = json.loads(
        (
            ROOT
            / "data"
            / "manifests"
            / "binance_ethusdt_perpetual_20260421_20260720.json"
        ).read_text(encoding="utf-8")
    )
    datasets = {
        (item["dataset"], item["timeframe"]): item
        for item in manifest["processed_datasets"]
    }
    for timeframe, rows in (("5m", 25920), ("15m", 8640), ("1h", 2160), ("4h", 540)):
        quality = datasets[("futures_ohlcv", timeframe)]["quality"]
        assert quality["rows"] == rows
        assert quality["missing_intervals"] == 0
        assert quality["duplicate_timestamps"] == 0
        assert quality["invalid_ohlc_rows"] == 0

    funding = datasets[("funding_rate", "native")]["quality"]
    assert funding["missing_intervals"] == 57
    assert manifest["source"]["credentials_used"] is False
    assert all(not item["raw_path"].startswith("/") for item in manifest["raw_archives"])
