from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from quant_lab.domain.errors import ConflictError, NotFoundError
from quant_lab.domain.models import (
    AuditEvent,
    Job,
    Message,
    ResearchSession,
    StrategyDraft,
    StrategyVersion,
)


SCHEMA_VERSION = 1


class SQLiteProductRepository:
    """Small local-state adapter with explicit, in-repository schema migrations."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"product database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS strategy_drafts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_name TEXT,
                    raw_content TEXT NOT NULL,
                    structured_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    baseline_version_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS ambiguities (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution TEXT,
                    FOREIGN KEY (draft_id) REFERENCES strategy_drafts(id)
                );

                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    proposal_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY (draft_id) REFERENCES strategy_drafts(id)
                );

                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    source_snapshot TEXT NOT NULL,
                    immutable INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE (strategy_id, version),
                    FOREIGN KEY (strategy_id) REFERENCES strategy_drafts(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_baseline_per_strategy
                    ON strategy_versions(strategy_id)
                    WHERE status = 'baseline';

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, 'audit events are append-only');
                END;
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_session(self, session: ResearchSession) -> ResearchSession:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_sessions (id, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.status,
                    session.created_at,
                    session.updated_at,
                ),
            )
        return session

    def list_sessions(self) -> Sequence[ResearchSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_sessions ORDER BY created_at DESC"
            ).fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> ResearchSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"research session not found: {session_id}")
        return self._session(row)

    def add_message(self, message: Message) -> Message:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (id, session_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.created_at,
                ),
            )
        return message

    def list_messages(self, session_id: str) -> Sequence[Message]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def create_draft(self, draft: StrategyDraft) -> StrategyDraft:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_drafts (
                    id, session_id, source_type, source_name, raw_content,
                    structured_json, status, baseline_version_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.session_id,
                    draft.source_type,
                    draft.source_name,
                    draft.raw_content,
                    json.dumps(draft.structured_content, ensure_ascii=False, sort_keys=True),
                    draft.status,
                    draft.baseline_version_id,
                    draft.created_at,
                ),
            )
        return draft

    def list_drafts(self, session_id: str | None = None) -> Sequence[StrategyDraft]:
        query = "SELECT * FROM strategy_drafts"
        parameters: tuple[str, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            parameters = (session_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._draft(row) for row in rows]

    def get_draft(self, draft_id: str) -> StrategyDraft:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"strategy draft not found: {draft_id}")
        return self._draft(row)

    def freeze_baseline(
        self,
        *,
        draft_id: str,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM strategy_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"strategy draft not found: {draft_id}")
            if row["baseline_version_id"] is not None:
                raise ConflictError(
                    "baseline is immutable and already frozen; create a new proposal/version"
                )

            snapshot = json.loads(row["structured_json"])
            if not snapshot:
                snapshot = {
                    "formalization_status": "source_snapshot_only",
                    "ai_provider_configured": False,
                }
            version = StrategyVersion(
                id=version_id,
                strategy_id=draft_id,
                version=0,
                status="baseline",
                content_snapshot=snapshot,
                source_snapshot=row["raw_content"],
                created_at=created_at,
                immutable=True,
            )
            connection.execute(
                """
                INSERT INTO strategy_versions (
                    id, strategy_id, version, status, content_json,
                    source_snapshot, immutable, created_at
                ) VALUES (?, ?, 0, 'baseline', ?, ?, 1, ?)
                """,
                (
                    version.id,
                    version.strategy_id,
                    json.dumps(version.content_snapshot, ensure_ascii=False, sort_keys=True),
                    version.source_snapshot,
                    version.created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO approvals (
                    id, subject_type, subject_id, decision, actor, created_at
                ) VALUES (?, 'strategy_baseline', ?, 'approved', 'user', ?)
                """,
                (approval_id, version_id, created_at),
            )
            connection.execute(
                """
                UPDATE strategy_drafts
                SET baseline_version_id = ?, status = 'baseline_frozen'
                WHERE id = ?
                """,
                (version_id, draft_id),
            )
            connection.execute(
                """
                UPDATE research_sessions
                SET status = 'baseline', updated_at = ?
                WHERE id = ?
                """,
                (created_at, row["session_id"]),
            )
        return version

    def create_job(self, job: Job) -> Job:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, job_type, status, payload_json, created_at, updated_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.job_type,
                    job.status,
                    json.dumps(job.payload, ensure_ascii=False, sort_keys=True),
                    job.created_at,
                    job.updated_at,
                    job.error,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_logs (job_id, level, message, created_at)
                VALUES (?, 'info', 'Job queued; no phase-0 handler has executed.', ?)
                """,
                (job.id, job.created_at),
            )
        return job

    def list_jobs(self) -> Sequence[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._job(row) for row in rows]

    def get_job(self, job_id: str) -> Job:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"job not found: {job_id}")
        return self._job(row)

    def list_job_logs(self, job_id: str) -> Sequence[Mapping[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, level, message, created_at
                FROM job_logs WHERE job_id = ? ORDER BY id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_event(self, event: AuditEvent) -> AuditEvent:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_events (
                    event_type, aggregate_type, aggregate_id, actor_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_type,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.actor_type,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    event.created_at,
                ),
            )
        return AuditEvent(
            id=cursor.lastrowid,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            actor_type=event.actor_type,
            payload=event.payload,
            created_at=event.created_at,
        )

    def list_events(self, *, limit: int = 100) -> Sequence[AuditEvent]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                event_type=row["event_type"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                actor_type=row["actor_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _session(row: sqlite3.Row) -> ResearchSession:
        return ResearchSession(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _draft(row: sqlite3.Row) -> StrategyDraft:
        return StrategyDraft(
            id=row["id"],
            session_id=row["session_id"],
            source_type=row["source_type"],
            source_name=row["source_name"],
            raw_content=row["raw_content"],
            structured_content=json.loads(row["structured_json"]),
            status=row["status"],
            baseline_version_id=row["baseline_version_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            job_type=row["job_type"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error=row["error"],
        )
