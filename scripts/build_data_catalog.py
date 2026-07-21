#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "catalog" / "quant.duckdb"
SQL_PATH = ROOT / "data" / "catalog" / "views_eth_perpetual.sql"
SUMMARY_PATH = ROOT / "data" / "catalog" / "catalog_summary.json"
VIEWS = (
    "binance_ethusdt_futures_5m",
    "binance_ethusdt_futures_15m",
    "binance_ethusdt_futures_1h",
    "binance_ethusdt_futures_4h",
    "binance_ethusdt_mark_15m",
    "binance_ethusdt_index_15m",
    "binance_ethusdt_funding",
    "binance_ethusdt_open_interest_5m",
)


def utc_iso(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


def main() -> int:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DATABASE)) as connection:
        connection.execute(SQL_PATH.read_text(encoding="utf-8"))
        summary = {}
        for view in VIEWS:
            row = connection.execute(
                f'SELECT count(*) AS rows, min(timestamp) AS first_timestamp, '
                f'max(timestamp) AS last_timestamp FROM "{view}"'
            ).fetchone()
            summary[view] = {
                "rows": row[0],
                "first_timestamp": utc_iso(row[1]),
                "last_timestamp": utc_iso(row[2]),
            }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"database={DATABASE}")
    print(f"summary={SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
