from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional


VALID_STATUSES = {
    "candidate",
    "validated",
    "production",
    "degraded",
    "retired",
    "rejected",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS factors (
                factor_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                formula_path TEXT,
                description TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS experiments (
                run_id TEXT PRIMARY KEY,
                run_type TEXT NOT NULL,
                status TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS factor_metrics (
                factor_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                split TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL,
                PRIMARY KEY (factor_id, run_id, split, metric),
                FOREIGN KEY (factor_id) REFERENCES factors(factor_id),
                FOREIGN KEY (run_id) REFERENCES experiments(run_id)
            );
            """
        )


def register_factor(
    path: Path,
    *,
    factor_id: str,
    name: str,
    category: str,
    status: str = "candidate",
    version: int = 1,
    formula_path: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, object]] = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError("invalid factor status: %s" % status)
    if status == "production":
        raise ValueError("production promotion requires explicit human approval")

    initialize(path)
    now = utc_now()
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO factors (
                factor_id, name, category, version, status, formula_path,
                description, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(factor_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                version = excluded.version,
                status = excluded.status,
                formula_path = excluded.formula_path,
                description = excluded.description,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                factor_id,
                name,
                category,
                version,
                status,
                formula_path,
                description,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )


def list_factors(path: Path) -> Iterable[sqlite3.Row]:
    initialize(path)
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT factor_id, name, category, version, status, updated_at
            FROM factors
            ORDER BY category, factor_id
            """
        ).fetchall()
    return rows


def register_experiment(
    path: Path, *, run_id: str, run_type: str, manifest_path: str
) -> None:
    initialize(path)
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO experiments (
                run_id, run_type, status, manifest_path, started_at
            ) VALUES (?, ?, 'created', ?, ?)
            """,
            (run_id, run_type, manifest_path, utc_now()),
        )
