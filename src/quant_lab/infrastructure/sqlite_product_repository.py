from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from quant_lab.domain.errors import ApprovalRequiredError, ConflictError, NotFoundError
from quant_lab.domain.models import (
    AgentProviderKind,
    AgentRun,
    Artifact,
    AuditEvent,
    Constraint,
    ExecutionTargetKind,
    ExperimentPlan,
    Job,
    Message,
    Objective,
    ParameterSpace,
    ResearchSession,
    StrategyDraft,
    StrategyVersion,
    ToolCall,
    Trial,
)


SCHEMA_VERSION = 2


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

                CREATE TABLE IF NOT EXISTS experiment_plans (
                    id TEXT PRIMARY KEY,
                    baseline_version_id TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    parameter_space_json TEXT NOT NULL,
                    objectives_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    data_splits_json TEXT NOT NULL,
                    cost_model_json TEXT NOT NULL,
                    max_trials INTEGER,
                    time_budget_seconds INTEGER,
                    stopping_conditions_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approved_by TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trials (
                    id TEXT PRIMARY KEY,
                    experiment_plan_id TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    log_artifact_key TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_plan_id) REFERENCES experiment_plans(id)
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_provider TEXT NOT NULL,
                    execution_target TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_summary TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    agent_run_id TEXT NOT NULL,
                    agent_step_id TEXT,
                    tool_name TEXT NOT NULL,
                    sanitized_input_json TEXT NOT NULL,
                    sanitized_output_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    agent_run_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    artifact_key TEXT NOT NULL,
                    checksum TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (agent_run_id, artifact_key),
                    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id)
                );
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

    def create_experiment_plan(self, plan: ExperimentPlan) -> ExperimentPlan:
        with self._connect() as connection:
            baseline = connection.execute(
                """
                SELECT id FROM strategy_versions
                WHERE id = ? AND status = 'baseline' AND immutable = 1
                """,
                (plan.baseline_version_id,),
            ).fetchone()
            if baseline is None:
                raise NotFoundError(
                    "experiment plan requires an existing immutable baseline"
                )
            connection.execute(
                """
                INSERT INTO experiment_plans (
                    id, baseline_version_id, hypothesis, parameter_space_json,
                    objectives_json, constraints_json, data_splits_json,
                    cost_model_json, max_trials, time_budget_seconds,
                    stopping_conditions_json, status, approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.baseline_version_id,
                    plan.hypothesis,
                    json.dumps([asdict(item) for item in plan.parameter_space]),
                    json.dumps([asdict(item) for item in plan.objectives]),
                    json.dumps([asdict(item) for item in plan.constraints]),
                    json.dumps(plan.data_splits, sort_keys=True),
                    json.dumps(plan.cost_model, sort_keys=True),
                    plan.max_trials,
                    plan.time_budget_seconds,
                    json.dumps(plan.stopping_conditions),
                    plan.status,
                    plan.approved_by,
                    plan.created_at,
                ),
            )
        return plan

    def get_experiment_plan(self, plan_id: str) -> ExperimentPlan:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_plans WHERE id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"experiment plan not found: {plan_id}")
        return self._experiment_plan(row)

    def list_experiment_plans(self) -> Sequence[ExperimentPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiment_plans ORDER BY created_at DESC"
            ).fetchall()
        return [self._experiment_plan(row) for row in rows]

    def approve_experiment_plan(
        self,
        plan: ExperimentPlan,
        *,
        approval_id: str,
        created_at: str,
    ) -> ExperimentPlan:
        if plan.status != "approved" or plan.approved_by != "user":
            raise ApprovalRequiredError("experiment plan approval must come from the user")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status FROM experiment_plans WHERE id = ?", (plan.id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"experiment plan not found: {plan.id}")
            if current["status"] != "draft":
                raise ConflictError("experiment plan has already left draft state")
            connection.execute(
                """
                UPDATE experiment_plans
                SET status = 'approved', approved_by = 'user'
                WHERE id = ?
                """,
                (plan.id,),
            )
            connection.execute(
                """
                INSERT INTO approvals (
                    id, subject_type, subject_id, decision, actor, created_at
                ) VALUES (?, 'experiment_plan', ?, 'approved', 'user', ?)
                """,
                (approval_id, plan.id, created_at),
            )
        return plan

    def create_trial(self, trial: Trial) -> Trial:
        with self._connect() as connection:
            plan = connection.execute(
                "SELECT status FROM experiment_plans WHERE id = ?",
                (trial.experiment_plan_id,),
            ).fetchone()
            if plan is None:
                raise NotFoundError(
                    f"experiment plan not found: {trial.experiment_plan_id}"
                )
            if plan["status"] != "approved":
                raise ApprovalRequiredError(
                    "trial creation requires an approved experiment plan"
                )
            connection.execute(
                """
                INSERT INTO trials (
                    id, experiment_plan_id, parameters_json, data_version,
                    status, metrics_json, log_artifact_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trial.id,
                    trial.experiment_plan_id,
                    json.dumps(trial.parameters, sort_keys=True),
                    trial.data_version,
                    trial.status,
                    json.dumps(trial.metrics, sort_keys=True),
                    trial.log_artifact_key,
                    trial.created_at,
                ),
            )
        return trial

    def list_trials(self, plan_id: str) -> Sequence[Trial]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trials WHERE experiment_plan_id = ? ORDER BY created_at",
                (plan_id,),
            ).fetchall()
        return [self._trial(row) for row in rows]

    def create_agent_run(self, agent_run: AgentRun) -> AgentRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    id, session_id, agent_name, agent_provider, execution_target,
                    mode, status, plan_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_run.id,
                    agent_run.session_id,
                    agent_run.agent_name,
                    agent_run.agent_provider,
                    agent_run.execution_target,
                    agent_run.mode,
                    agent_run.status,
                    agent_run.plan_summary,
                    agent_run.created_at,
                ),
            )
        return agent_run

    def get_agent_run(self, agent_run_id: str) -> AgentRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (agent_run_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"agent run not found: {agent_run_id}")
        return self._agent_run(row)

    def list_agent_runs(self) -> Sequence[AgentRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_runs ORDER BY created_at DESC"
            ).fetchall()
        return [self._agent_run(row) for row in rows]

    def create_tool_call(self, tool_call: ToolCall) -> ToolCall:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls (
                    id, agent_run_id, agent_step_id, tool_name,
                    sanitized_input_json, sanitized_output_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call.id,
                    tool_call.agent_run_id,
                    tool_call.agent_step_id,
                    tool_call.tool_name,
                    json.dumps(tool_call.sanitized_input, sort_keys=True),
                    (
                        json.dumps(tool_call.sanitized_output, sort_keys=True)
                        if tool_call.sanitized_output is not None
                        else None
                    ),
                    tool_call.status,
                    tool_call.created_at,
                ),
            )
        return tool_call

    def list_tool_calls(self, agent_run_id: str) -> Sequence[ToolCall]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE agent_run_id = ? ORDER BY created_at",
                (agent_run_id,),
            ).fetchall()
        return [self._tool_call(row) for row in rows]

    def create_artifact(self, artifact: Artifact) -> Artifact:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, agent_run_id, artifact_type, artifact_key, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.agent_run_id,
                    artifact.artifact_type,
                    artifact.artifact_key,
                    artifact.checksum,
                    artifact.created_at,
                ),
            )
        return artifact

    def list_artifacts(self, agent_run_id: str) -> Sequence[Artifact]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE agent_run_id = ? ORDER BY created_at",
                (agent_run_id,),
            ).fetchall()
        return [self._artifact(row) for row in rows]

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

    @staticmethod
    def _experiment_plan(row: sqlite3.Row) -> ExperimentPlan:
        return ExperimentPlan(
            id=row["id"],
            baseline_version_id=row["baseline_version_id"],
            hypothesis=row["hypothesis"],
            parameter_space=tuple(
                ParameterSpace(
                    name=item["name"],
                    kind=item["kind"],
                    values=tuple(item.get("values", ())),
                    lower=item.get("lower"),
                    upper=item.get("upper"),
                )
                for item in json.loads(row["parameter_space_json"])
            ),
            objectives=tuple(
                Objective(**item) for item in json.loads(row["objectives_json"])
            ),
            constraints=tuple(
                Constraint(**item) for item in json.loads(row["constraints_json"])
            ),
            data_splits=json.loads(row["data_splits_json"]),
            cost_model=json.loads(row["cost_model_json"]),
            max_trials=row["max_trials"],
            time_budget_seconds=row["time_budget_seconds"],
            stopping_conditions=tuple(json.loads(row["stopping_conditions_json"])),
            status=row["status"],
            approved_by=row["approved_by"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _trial(row: sqlite3.Row) -> Trial:
        return Trial(
            id=row["id"],
            experiment_plan_id=row["experiment_plan_id"],
            parameters=json.loads(row["parameters_json"]),
            data_version=row["data_version"],
            status=row["status"],
            metrics=json.loads(row["metrics_json"]),
            log_artifact_key=row["log_artifact_key"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _agent_run(row: sqlite3.Row) -> AgentRun:
        return AgentRun(
            id=row["id"],
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            agent_provider=AgentProviderKind(row["agent_provider"]),
            execution_target=ExecutionTargetKind(row["execution_target"]),
            mode=row["mode"],
            status=row["status"],
            plan_summary=row["plan_summary"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _tool_call(row: sqlite3.Row) -> ToolCall:
        return ToolCall(
            id=row["id"],
            agent_run_id=row["agent_run_id"],
            agent_step_id=row["agent_step_id"],
            tool_name=row["tool_name"],
            sanitized_input=json.loads(row["sanitized_input_json"]),
            sanitized_output=(
                json.loads(row["sanitized_output_json"])
                if row["sanitized_output_json"] is not None
                else None
            ),
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            agent_run_id=row["agent_run_id"],
            artifact_type=row["artifact_type"],
            artifact_key=row["artifact_key"],
            checksum=row["checksum"],
            created_at=row["created_at"],
        )
