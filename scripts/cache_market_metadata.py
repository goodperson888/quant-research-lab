#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SOURCES = {
    "binance_usdt_futures_exchange_info": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "okx_eth_usdt_swap_instrument": (
        "https://www.okx.com/api/v5/public/instruments"
        "?instType=SWAP&instId=ETH-USDT-SWAP"
    ),
}


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"metadata cache is immutable and already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(path)


def binance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    instrument = next(
        item for item in payload["symbols"] if item.get("symbol") == "ETHUSDT"
    )
    filters = {item["filterType"]: item for item in instrument.get("filters", [])}
    return {
        "symbol": instrument["symbol"],
        "pair": instrument.get("pair"),
        "contract_type": instrument.get("contractType"),
        "status": instrument.get("status"),
        "base_asset": instrument.get("baseAsset"),
        "quote_asset": instrument.get("quoteAsset"),
        "margin_asset": instrument.get("marginAsset"),
        "price_precision": instrument.get("pricePrecision"),
        "quantity_precision": instrument.get("quantityPrecision"),
        "price_filter": filters.get("PRICE_FILTER"),
        "lot_size": filters.get("LOT_SIZE"),
        "market_lot_size": filters.get("MARKET_LOT_SIZE"),
        "min_notional": filters.get("MIN_NOTIONAL"),
    }


def okx_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("OKX instrument response is not successful")
    instrument = payload["data"][0]
    return {
        key: instrument.get(key)
        for key in (
            "instId",
            "instType",
            "state",
            "ctType",
            "ctVal",
            "ctValCcy",
            "settleCcy",
            "tickSz",
            "lotSz",
            "minSz",
            "lever",
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--proxy-used", action="store_true")
    parser.add_argument("--manifest-name")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    fetched_at = datetime.now(timezone.utc)
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    records = []
    for source_id, url in SOURCES.items():
        response = requests.get(
            url,
            timeout=(15, args.timeout),
            headers={"User-Agent": "quant-research-lab/0.1 public-market-research"},
        )
        response.raise_for_status()
        payload = response.json()
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        raw_key = f"data/external/market_metadata/{source_id}/{stamp}.json"
        atomic_write(root / raw_key, content)
        summary = (
            binance_summary(payload)
            if source_id.startswith("binance")
            else okx_summary(payload)
        )
        records.append(
            {
                "source_id": source_id,
                "source_url": url,
                "fetched_at": fetched_at.isoformat(),
                "http_status": response.status_code,
                "credentials_used": False,
                "proxy_used": args.proxy_used,
                "raw_artifact_key": raw_key,
                "bytes": len(content),
                "sha256": sha256(content),
                "summary": summary,
            }
        )

    manifest_name = args.manifest_name or f"market_metadata_eth_perpetual_{stamp}.json"
    manifest_path = root / "data" / "manifests" / manifest_name
    manifest = {
        "manifest_version": 1,
        "metadata_version": f"official-public-metadata-{stamp}",
        "market_profiles": [
            "crypto_perpetual.binance.eth",
            "crypto_perpetual.okx.eth",
        ],
        "created_at": fetched_at.isoformat(),
        "records": records,
        "limitations": [
            "This is a point-in-time metadata cache, not historical leverage-tier data.",
            "No API key or account-specific fee tier was used.",
        ],
    }
    atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
