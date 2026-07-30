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
    ComponentCandidate,
    ComponentEvidence,
    ComponentHypothesis,
    ExecutionTargetKind,
    ExperimentPlan,
    GateEvaluation,
    Job,
    Message,
    Objective,
    ParameterSpace,
    Proposal,
    Report,
    ResearchSession,
    ResearchBudget,
    ResearchAuthorization,
    ResearchAuthorizationStage,
    ResearchHandoff,
    RESEARCH_MODE_DEFINITIONS,
    StrategyDraft,
    StrategyOutcome,
    StrategyVersion,
    ToolCall,
    Trial,
    RegimeValidation,
)


SCHEMA_VERSION = 10


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
                    updated_at TEXT NOT NULL,
                    research_mode TEXT NOT NULL DEFAULT 'guided',
                    mode_config_json TEXT NOT NULL DEFAULT '{}',
                    mode_revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS research_budgets (
                    session_id TEXT PRIMARY KEY,
                    max_hypotheses INTEGER NOT NULL,
                    max_trials_total INTEGER NOT NULL,
                    max_compute_minutes INTEGER NOT NULL,
                    max_locked_test_uses INTEGER NOT NULL,
                    require_user_approval_for_new_hypothesis INTEGER NOT NULL,
                    used_hypotheses INTEGER NOT NULL DEFAULT 0,
                    reserved_trials INTEGER NOT NULL DEFAULT 0,
                    reserved_compute_minutes INTEGER NOT NULL DEFAULT 0,
                    used_locked_test_uses INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS research_authorizations (
                    id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    allowed_stages_json TEXT NOT NULL,
                    auto_continue INTEGER NOT NULL,
                    max_cost_usdt REAL NOT NULL,
                    max_time_minutes INTEGER NOT NULL,
                    max_trials INTEGER NOT NULL,
                    locked_test_allowed INTEGER NOT NULL,
                    stop_conditions_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    used_cost_usdt REAL NOT NULL DEFAULT 0,
                    used_time_minutes REAL NOT NULL DEFAULT 0,
                    used_trials INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE INDEX IF NOT EXISTS research_authorizations_subject_created
                    ON research_authorizations(subject_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS research_authorization_stages (
                    id TEXT PRIMARY KEY,
                    authorization_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    elapsed_minutes REAL NOT NULL,
                    cost_usdt REAL NOT NULL,
                    trials_used INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (authorization_id) REFERENCES research_authorizations(id)
                );

                CREATE INDEX IF NOT EXISTS research_authorization_stages_created
                    ON research_authorization_stages(authorization_id, created_at);

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
                    baseline_version_id TEXT,
                    subject_id TEXT,
                    hypothesis TEXT NOT NULL DEFAULT '',
                    rule_diff_json TEXT NOT NULL DEFAULT '{}',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    parameter_space_json TEXT NOT NULL DEFAULT '[]',
                    data_splits_json TEXT NOT NULL DEFAULT '{}',
                    cost_model_json TEXT NOT NULL DEFAULT '{}',
                    objectives_json TEXT NOT NULL DEFAULT '[]',
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    estimated_trials INTEGER,
                    estimated_minutes INTEGER,
                    failure_conditions_json TEXT NOT NULL DEFAULT '[]',
                    stopping_conditions_json TEXT NOT NULL DEFAULT '[]',
                    rollback_plan TEXT NOT NULL DEFAULT '',
                    candidate_version_id TEXT,
                    created_at TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS research_handoffs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_run_id TEXT,
                    subject_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stop_reason_code TEXT NOT NULL,
                    stop_reason_text TEXT NOT NULL,
                    completed_actions_json TEXT NOT NULL,
                    not_started_actions_json TEXT NOT NULL,
                    user_action_required INTEGER NOT NULL,
                    required_user_action TEXT,
                    next_recommended_action TEXT NOT NULL,
                    approval_subject_id TEXT,
                    safe_to_continue INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id),
                    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id)
                );

                CREATE INDEX IF NOT EXISTS research_handoffs_session_created
                    ON research_handoffs(session_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS research_handoffs_agent_run_created
                    ON research_handoffs(agent_run_id, created_at DESC);

                CREATE TRIGGER IF NOT EXISTS research_handoffs_no_update
                BEFORE UPDATE ON research_handoffs
                BEGIN
                    SELECT RAISE(ABORT, 'research handoffs are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS research_handoffs_no_delete
                BEFORE DELETE ON research_handoffs
                BEGIN
                    SELECT RAISE(ABORT, 'research handoffs are append-only');
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
                    proposal_id TEXT,
                    candidate_version_id TEXT,
                    correction_of_plan_id TEXT,
                    search_strategy TEXT NOT NULL DEFAULT 'grid',
                    random_seed INTEGER NOT NULL DEFAULT 0,
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
                    candidate_version_id TEXT,
                    parameter_signature TEXT,
                    split TEXT NOT NULL DEFAULT 'train_validation',
                    cost_model_json TEXT NOT NULL DEFAULT '{}',
                    seed INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    elapsed_seconds REAL,
                    peak_rss_mb REAL,
                    result_artifact_key TEXT,
                    metrics_artifact_key TEXT,
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

                CREATE TABLE IF NOT EXISTS gate_evaluations (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    gate_name TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    market_profile TEXT NOT NULL,
                    strategy_objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_outcomes (
                    id TEXT PRIMARY KEY,
                    strategy_version_id TEXT NOT NULL,
                    market_profile TEXT NOT NULL,
                    pipeline_profile_id TEXT NOT NULL,
                    outcome_type TEXT NOT NULL,
                    viability_gate_result_id TEXT,
                    evidence_artifact_keys_json TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_version_id) REFERENCES strategy_versions(id),
                    FOREIGN KEY (viability_gate_result_id) REFERENCES gate_evaluations(id)
                );

                CREATE TABLE IF NOT EXISTS component_evidence (
                    id TEXT PRIMARY KEY,
                    source_strategy_version_id TEXT NOT NULL,
                    lineage_json TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    target_market_profile TEXT NOT NULL,
                    incremental_metrics_json TEXT NOT NULL,
                    out_of_sample_status TEXT NOT NULL,
                    failure_conditions_json TEXT NOT NULL,
                    logic_signature TEXT NOT NULL DEFAULT '',
                    timeframe TEXT NOT NULL DEFAULT '',
                    source_experiment_plan_id TEXT,
                    source_trial_ids_json TEXT NOT NULL DEFAULT '[]',
                    stable_parameter_ranges_json TEXT NOT NULL DEFAULT '{}',
                    failed_parameter_ranges_json TEXT NOT NULL DEFAULT '{}',
                    regimes_json TEXT NOT NULL DEFAULT '[]',
                    evidence_level TEXT NOT NULL DEFAULT 'diagnostic',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (source_strategy_version_id) REFERENCES strategy_versions(id)
                );

                CREATE TABLE IF NOT EXISTS component_candidates (
                    id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    logic_signature TEXT NOT NULL DEFAULT '',
                    target_market_profile TEXT NOT NULL DEFAULT '',
                    timeframe TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    archive_reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (evidence_id) REFERENCES component_evidence(id)
                );

                CREATE TABLE IF NOT EXISTS component_hypotheses (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    expected_improvement TEXT NOT NULL,
                    parameter_space_json TEXT NOT NULL,
                    suggested_trials INTEGER NOT NULL,
                    failure_conditions_json TEXT NOT NULL,
                    evidence_level TEXT NOT NULL,
                    contamination_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS regime_validations (
                    id TEXT PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'regime_diagnostic',
                    market_profile TEXT NOT NULL,
                    detector_version TEXT NOT NULL,
                    ex_ante_observable INTEGER NOT NULL,
                    target_regimes_json TEXT NOT NULL,
                    suitable_regimes_json TEXT NOT NULL,
                    conditional_regimes_json TEXT NOT NULL,
                    blocked_regimes_json TEXT NOT NULL,
                    unknown_regimes_json TEXT NOT NULL,
                    regime_metrics_json TEXT NOT NULL,
                    transition_policy_json TEXT NOT NULL,
                    history_days INTEGER NOT NULL,
                    evidence_status TEXT NOT NULL,
                    viability_gate_result_id TEXT,
                    created_at TEXT NOT NULL
                );

                """
            )
            regime_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(regime_validations)"
                ).fetchall()
            }
            if "mode" not in regime_columns:
                connection.execute(
                    "ALTER TABLE regime_validations "
                    "ADD COLUMN mode TEXT NOT NULL DEFAULT 'regime_diagnostic'"
                )
            if "viability_gate_result_id" not in regime_columns:
                connection.execute(
                    "ALTER TABLE regime_validations "
                    "ADD COLUMN viability_gate_result_id TEXT"
                )
            migrations = {
                "research_sessions": {
                    "research_mode": "TEXT NOT NULL DEFAULT 'guided'",
                    "mode_config_json": "TEXT NOT NULL DEFAULT '{}'",
                    "mode_revision": "INTEGER NOT NULL DEFAULT 1",
                },
                "proposals": {
                    "baseline_version_id": "TEXT",
                    "subject_id": "TEXT",
                    "hypothesis": "TEXT NOT NULL DEFAULT ''",
                    "rule_diff_json": "TEXT NOT NULL DEFAULT '{}'",
                    "evidence_refs_json": "TEXT NOT NULL DEFAULT '[]'",
                    "parameter_space_json": "TEXT NOT NULL DEFAULT '[]'",
                    "data_splits_json": "TEXT NOT NULL DEFAULT '{}'",
                    "cost_model_json": "TEXT NOT NULL DEFAULT '{}'",
                    "objectives_json": "TEXT NOT NULL DEFAULT '[]'",
                    "constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                    "estimated_trials": "INTEGER",
                    "estimated_minutes": "INTEGER",
                    "failure_conditions_json": "TEXT NOT NULL DEFAULT '[]'",
                    "stopping_conditions_json": "TEXT NOT NULL DEFAULT '[]'",
                    "rollback_plan": "TEXT NOT NULL DEFAULT ''",
                    "candidate_version_id": "TEXT",
                    "created_at": "TEXT NOT NULL DEFAULT ''",
                },
                "experiment_plans": {
                    "proposal_id": "TEXT",
                    "candidate_version_id": "TEXT",
                    "correction_of_plan_id": "TEXT",
                    "search_strategy": "TEXT NOT NULL DEFAULT 'grid'",
                    "random_seed": "INTEGER NOT NULL DEFAULT 0",
                },
                "trials": {
                    "candidate_version_id": "TEXT",
                    "parameter_signature": "TEXT",
                    "split": "TEXT NOT NULL DEFAULT 'train_validation'",
                    "cost_model_json": "TEXT NOT NULL DEFAULT '{}'",
                    "seed": "INTEGER NOT NULL DEFAULT 0",
                    "error": "TEXT",
                    "elapsed_seconds": "REAL",
                    "peak_rss_mb": "REAL",
                    "result_artifact_key": "TEXT",
                    "metrics_artifact_key": "TEXT",
                },
                "component_evidence": {
                    "logic_signature": "TEXT NOT NULL DEFAULT ''",
                    "timeframe": "TEXT NOT NULL DEFAULT ''",
                    "source_experiment_plan_id": "TEXT",
                    "source_trial_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                    "stable_parameter_ranges_json": "TEXT NOT NULL DEFAULT '{}'",
                    "failed_parameter_ranges_json": "TEXT NOT NULL DEFAULT '{}'",
                    "regimes_json": "TEXT NOT NULL DEFAULT '[]'",
                    "evidence_level": "TEXT NOT NULL DEFAULT 'diagnostic'",
                },
                "component_candidates": {
                    "logic_signature": "TEXT NOT NULL DEFAULT ''",
                    "target_market_profile": "TEXT NOT NULL DEFAULT ''",
                    "timeframe": "TEXT NOT NULL DEFAULT ''",
                    "archived_at": "TEXT",
                    "archive_reason": "TEXT",
                },
            }
            for table, columns in migrations.items():
                existing = {
                    row[1]
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                for column, definition in columns.items():
                    if column not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
            for mode, config in RESEARCH_MODE_DEFINITIONS.items():
                connection.execute(
                    """
                    UPDATE research_sessions
                    SET mode_config_json = ?
                    WHERE research_mode = ?
                      AND (mode_config_json = '{}' OR mode_config_json = '')
                    """,
                    (
                        json.dumps(config, ensure_ascii=False, sort_keys=True),
                        mode,
                    ),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_trial_per_parameter_signature "
                "ON trials(experiment_plan_id, parameter_signature) "
                "WHERE parameter_signature IS NOT NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_component_candidate_per_logic_scope "
                "ON component_candidates(logic_signature, target_market_profile, timeframe) "
                "WHERE logic_signature != ''"
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_session(self, session: ResearchSession) -> ResearchSession:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_sessions (
                    id, title, status, created_at, updated_at,
                    research_mode, mode_config_json, mode_revision
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.status,
                    session.created_at,
                    session.updated_at,
                    session.research_mode,
                    json.dumps(
                        session.mode_config, ensure_ascii=False, sort_keys=True
                    ),
                    session.mode_revision,
                ),
            )
        return session

    def update_session_research_mode(
        self,
        session_id: str,
        *,
        research_mode: str,
        mode_config: Mapping[str, Any],
        mode_revision: int,
        updated_at: str,
    ) -> ResearchSession:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_sessions
                SET research_mode = ?, mode_config_json = ?,
                    mode_revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    research_mode,
                    json.dumps(mode_config, ensure_ascii=False, sort_keys=True),
                    mode_revision,
                    updated_at,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"research session not found: {session_id}")
        return self.get_session(session_id)

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

    def create_research_budget(self, budget: ResearchBudget) -> ResearchBudget:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_budgets (
                    session_id, max_hypotheses, max_trials_total,
                    max_compute_minutes, max_locked_test_uses,
                    require_user_approval_for_new_hypothesis, used_hypotheses,
                    reserved_trials, reserved_compute_minutes, used_locked_test_uses
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget.session_id,
                    budget.max_hypotheses,
                    budget.max_trials_total,
                    budget.max_compute_minutes,
                    budget.max_locked_test_uses,
                    int(budget.require_user_approval_for_new_hypothesis),
                    budget.used_hypotheses,
                    budget.reserved_trials,
                    budget.reserved_compute_minutes,
                    budget.used_locked_test_uses,
                ),
            )
        return budget

    def get_research_budget(self, session_id: str) -> ResearchBudget:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_budgets WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"research budget not found for session: {session_id}")
        return self._research_budget(row)

    def create_research_authorization(
        self, authorization: ResearchAuthorization
    ) -> ResearchAuthorization:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT id FROM research_sessions WHERE id = ?",
                (authorization.session_id,),
            ).fetchone()
            if session is None:
                raise NotFoundError(
                    f"research session not found: {authorization.session_id}"
                )
            connection.execute(
                """
                INSERT INTO research_authorizations (
                    id, subject_id, session_id, allowed_stages_json, auto_continue,
                    max_cost_usdt, max_time_minutes, max_trials, locked_test_allowed,
                    stop_conditions_json, expires_at, approved_by, status,
                    used_cost_usdt, used_time_minutes, used_trials, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization.id,
                    authorization.subject_id,
                    authorization.session_id,
                    json.dumps(authorization.allowed_stages, ensure_ascii=False),
                    int(authorization.auto_continue),
                    authorization.max_cost_usdt,
                    authorization.max_time_minutes,
                    authorization.max_trials,
                    int(authorization.locked_test_allowed),
                    json.dumps(authorization.stop_conditions, ensure_ascii=False),
                    authorization.expires_at,
                    authorization.approved_by,
                    authorization.status,
                    authorization.used_cost_usdt,
                    authorization.used_time_minutes,
                    authorization.used_trials,
                    authorization.created_at,
                ),
            )
        return authorization

    def get_research_authorization(
        self, authorization_id: str
    ) -> ResearchAuthorization:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(
                f"research authorization not found: {authorization_id}"
            )
        return self._research_authorization(row)

    def list_research_authorizations(
        self, *, session_id: str | None = None, subject_id: str | None = None
    ) -> Sequence[ResearchAuthorization]:
        conditions: list[str] = []
        parameters: list[str] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            parameters.append(session_id)
        if subject_id is not None:
            conditions.append("subject_id = ?")
            parameters.append(subject_id)
        query = "SELECT * FROM research_authorizations"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [self._research_authorization(row) for row in rows]

    def update_research_authorization_usage(
        self,
        authorization_id: str,
        *,
        status: str,
        used_cost_usdt: float,
        used_time_minutes: float,
        used_trials: int,
    ) -> ResearchAuthorization:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_authorizations
                SET status = ?, used_cost_usdt = ?, used_time_minutes = ?,
                    used_trials = ?
                WHERE id = ?
                """,
                (
                    status,
                    used_cost_usdt,
                    used_time_minutes,
                    used_trials,
                    authorization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(
                    f"research authorization not found: {authorization_id}"
                )
        return self.get_research_authorization(authorization_id)

    def create_research_authorization_stage(
        self, stage: ResearchAuthorizationStage
    ) -> ResearchAuthorizationStage:
        with self._connect() as connection:
            authorization = connection.execute(
                "SELECT id FROM research_authorizations WHERE id = ?",
                (stage.authorization_id,),
            ).fetchone()
            if authorization is None:
                raise NotFoundError(
                    f"research authorization not found: {stage.authorization_id}"
                )
            connection.execute(
                """
                INSERT INTO research_authorization_stages (
                    id, authorization_id, stage, status, evidence_refs_json,
                    reason, elapsed_minutes, cost_usdt, trials_used, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage.id,
                    stage.authorization_id,
                    stage.stage,
                    stage.status,
                    json.dumps(stage.evidence_refs, ensure_ascii=False),
                    stage.reason,
                    stage.elapsed_minutes,
                    stage.cost_usdt,
                    stage.trials_used,
                    stage.created_at,
                ),
            )
        return stage

    def list_research_authorization_stages(
        self, authorization_id: str
    ) -> Sequence[ResearchAuthorizationStage]:
        self.get_research_authorization(authorization_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_authorization_stages
                WHERE authorization_id = ?
                ORDER BY created_at, rowid
                """,
                (authorization_id,),
            ).fetchall()
        return [self._research_authorization_stage(row) for row in rows]

    def reserve_hypothesis(self, session_id: str) -> ResearchBudget:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_budgets
                SET used_hypotheses = used_hypotheses + 1
                WHERE session_id = ? AND used_hypotheses < max_hypotheses
                """,
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise ConflictError("research budget max_hypotheses exceeded")
        return self.get_research_budget(session_id)

    def reserve_experiment_resources(
        self, session_id: str, *, trials: int, compute_minutes: int
    ) -> ResearchBudget:
        if trials < 0 or compute_minutes < 0:
            raise ValueError("research resource reservation must be non-negative")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_budgets
                SET reserved_trials = reserved_trials + ?,
                    reserved_compute_minutes = reserved_compute_minutes + ?
                WHERE session_id = ?
                  AND reserved_trials + ? <= max_trials_total
                  AND reserved_compute_minutes + ? <= max_compute_minutes
                """,
                (trials, compute_minutes, session_id, trials, compute_minutes),
            )
            if cursor.rowcount != 1:
                raise ConflictError("research budget trial or compute limit exceeded")
        return self.get_research_budget(session_id)

    def reserve_locked_test_use(self, session_id: str) -> ResearchBudget:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE research_budgets
                SET used_locked_test_uses = used_locked_test_uses + 1
                WHERE session_id = ?
                  AND used_locked_test_uses < max_locked_test_uses
                """,
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise ConflictError("research budget max_locked_test_uses exceeded")
        return self.get_research_budget(session_id)

    def get_session_id_for_strategy_version(self, version_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT d.session_id
                FROM strategy_versions v
                JOIN strategy_drafts d ON d.id = v.strategy_id
                WHERE v.id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"strategy version not found: {version_id}")
        return str(row["session_id"])

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

    def update_draft_formalization(
        self,
        *,
        draft_id: str,
        structured_content: Mapping[str, Any],
    ) -> StrategyDraft:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM strategy_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"strategy draft not found: {draft_id}")
            if row["baseline_version_id"] is not None or row["status"] == "baseline_frozen":
                raise ConflictError(
                    "baseline is immutable; a frozen draft cannot be formalized again"
                )
            if row["status"] not in {"draft", "awaiting_confirmation"}:
                raise ConflictError(
                    f"strategy draft cannot be formalized from status: {row['status']}"
                )
            connection.execute(
                """
                UPDATE strategy_drafts
                SET structured_json = ?, status = 'awaiting_confirmation'
                WHERE id = ?
                """,
                (
                    json.dumps(structured_content, ensure_ascii=False, sort_keys=True),
                    draft_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM strategy_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        assert updated is not None
        return self._draft(updated)

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

    def get_strategy_version(self, version_id: str) -> StrategyVersion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"strategy version not found: {version_id}")
        return self._strategy_version(row)

    def create_proposal(self, proposal: Proposal) -> Proposal:
        with self._connect() as connection:
            draft = connection.execute(
                "SELECT id FROM strategy_drafts WHERE id = ?", (proposal.draft_id,)
            ).fetchone()
            if draft is None:
                raise NotFoundError(f"strategy draft not found: {proposal.draft_id}")
            connection.execute(
                """
                INSERT INTO proposals (
                    id, draft_id, proposal_type, content_json, status,
                    baseline_version_id, subject_id, hypothesis, rule_diff_json,
                    evidence_refs_json, parameter_space_json, data_splits_json,
                    cost_model_json, objectives_json, constraints_json,
                    estimated_trials, estimated_minutes, failure_conditions_json,
                    stopping_conditions_json, rollback_plan, candidate_version_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.id,
                    proposal.draft_id,
                    proposal.proposal_type,
                    json.dumps(proposal.content, ensure_ascii=False, sort_keys=True),
                    proposal.status,
                    proposal.baseline_version_id,
                    proposal.subject_id,
                    proposal.hypothesis,
                    json.dumps(proposal.rule_diff, ensure_ascii=False, sort_keys=True),
                    json.dumps(proposal.evidence_refs, ensure_ascii=False),
                    json.dumps([asdict(item) for item in proposal.parameter_space]),
                    json.dumps(proposal.data_splits, sort_keys=True),
                    json.dumps(proposal.cost_model, sort_keys=True),
                    json.dumps([asdict(item) for item in proposal.objectives]),
                    json.dumps([asdict(item) for item in proposal.constraints]),
                    proposal.estimated_trials,
                    proposal.estimated_minutes,
                    json.dumps(proposal.failure_conditions, ensure_ascii=False),
                    json.dumps(proposal.stopping_conditions, ensure_ascii=False),
                    proposal.rollback_plan,
                    proposal.candidate_version_id,
                    proposal.created_at,
                ),
            )
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"proposal not found: {proposal_id}")
        return self._proposal(row)

    def list_proposals(self, draft_id: str | None = None) -> Sequence[Proposal]:
        query = "SELECT * FROM proposals"
        parameters: tuple[str, ...] = ()
        if draft_id is not None:
            query += " WHERE draft_id = ?"
            parameters = (draft_id,)
        query += " ORDER BY rowid DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._proposal(row) for row in rows]

    def update_proposal(self, proposal: Proposal) -> Proposal:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proposals
                SET status = ?, candidate_version_id = ?, content_json = ?,
                    estimated_trials = ?, estimated_minutes = ?
                WHERE id = ?
                """,
                (
                    proposal.status,
                    proposal.candidate_version_id,
                    json.dumps(proposal.content, ensure_ascii=False, sort_keys=True),
                    proposal.estimated_trials,
                    proposal.estimated_minutes,
                    proposal.id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"proposal not found: {proposal.id}")
        return proposal

    def approve_proposal_candidate(
        self,
        proposal: Proposal,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion:
        if proposal.status != "approved":
            raise ApprovalRequiredError("proposal approval must come from the user")
        if not proposal.baseline_version_id:
            raise ConflictError("proposal is missing its baseline identity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal.id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"proposal not found: {proposal.id}")
            if current["status"] != "waiting_approval":
                raise ConflictError("proposal is not waiting for approval")
            baseline = connection.execute(
                """
                SELECT * FROM strategy_versions
                WHERE id = ? AND strategy_id = ?
                  AND status = 'baseline' AND immutable = 1
                """,
                (proposal.baseline_version_id, proposal.draft_id),
            ).fetchone()
            if baseline is None:
                raise NotFoundError("proposal requires its original immutable baseline")
            latest = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS value FROM strategy_versions WHERE strategy_id = ?",
                (proposal.draft_id,),
            ).fetchone()
            version = StrategyVersion(
                id=version_id,
                strategy_id=proposal.draft_id,
                version=int(latest["value"]) + 1,
                status="candidate",
                content_snapshot={
                    "baseline_version_id": proposal.baseline_version_id,
                    "proposal_id": proposal.id,
                    "hypothesis": proposal.hypothesis,
                    "rule_diff": dict(proposal.rule_diff),
                    "automatic_validation": False,
                    "locked_test_used": False,
                    "production_enabled": False,
                },
                source_snapshot=baseline["source_snapshot"],
                created_at=created_at,
                immutable=True,
            )
            connection.execute(
                """
                INSERT INTO strategy_versions (
                    id, strategy_id, version, status, content_json,
                    source_snapshot, immutable, created_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, 1, ?)
                """,
                (
                    version.id,
                    version.strategy_id,
                    version.version,
                    json.dumps(version.content_snapshot, ensure_ascii=False, sort_keys=True),
                    version.source_snapshot,
                    version.created_at,
                ),
            )
            connection.execute(
                "UPDATE proposals SET status = 'approved', candidate_version_id = ? WHERE id = ?",
                (version.id, proposal.id),
            )
            connection.execute(
                """
                INSERT INTO approvals (
                    id, subject_type, subject_id, decision, actor, created_at
                ) VALUES (?, 'improvement_proposal', ?, 'approved', 'user', ?)
                """,
                (approval_id, proposal.id, created_at),
            )
        return version

    def accept_proposal(
        self,
        proposal: Proposal,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion:
        if proposal.status != "accepted":
            raise ApprovalRequiredError("proposal acceptance must come from the user")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal.id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"proposal not found: {proposal.id}")
            if current["status"] != "draft":
                raise ConflictError("proposal has already left draft state")
            if current["draft_id"] != proposal.draft_id:
                raise ConflictError("proposal draft identity changed")

            baseline_id = proposal.content.get("baseline_version_id")
            baseline = connection.execute(
                """
                SELECT * FROM strategy_versions
                WHERE id = ? AND strategy_id = ?
                  AND status = 'baseline' AND immutable = 1
                """,
                (baseline_id, proposal.draft_id),
            ).fetchone()
            if baseline is None:
                raise NotFoundError("proposal requires its original immutable baseline")
            latest_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS value FROM strategy_versions WHERE strategy_id = ?",
                (proposal.draft_id,),
            ).fetchone()
            version_number = int(latest_version["value"]) + 1
            content_snapshot = {
                "baseline_version_id": baseline_id,
                "proposal_id": proposal.id,
                "proposal": dict(proposal.content),
                "automatic_validation": False,
                "dry_run_enabled": False,
                "production_enabled": False,
            }
            version = StrategyVersion(
                id=version_id,
                strategy_id=proposal.draft_id,
                version=version_number,
                status="candidate",
                content_snapshot=content_snapshot,
                source_snapshot=baseline["source_snapshot"],
                created_at=created_at,
                immutable=True,
            )
            connection.execute(
                """
                INSERT INTO strategy_versions (
                    id, strategy_id, version, status, content_json,
                    source_snapshot, immutable, created_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, 1, ?)
                """,
                (
                    version.id,
                    version.strategy_id,
                    version.version,
                    json.dumps(
                        version.content_snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    version.source_snapshot,
                    version.created_at,
                ),
            )
            connection.execute(
                "UPDATE proposals SET status = 'accepted' WHERE id = ?",
                (proposal.id,),
            )
            connection.execute(
                """
                INSERT INTO approvals (
                    id, subject_type, subject_id, decision, actor, created_at
                ) VALUES (?, 'strategy_proposal', ?, 'approved', 'user', ?)
                """,
                (approval_id, proposal.id, created_at),
            )
            connection.execute(
                """
                UPDATE research_sessions
                SET status = 'candidate', updated_at = ?
                WHERE id = (
                    SELECT session_id FROM strategy_drafts WHERE id = ?
                )
                """,
                (created_at, proposal.draft_id),
            )
        return version

    def reject_candidate(
        self,
        *,
        version_id: str,
        approval_id: str,
        created_at: str,
    ) -> StrategyVersion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM strategy_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"strategy version not found: {version_id}")
            if row["status"] != "candidate":
                raise ConflictError("only candidate status can transition to rejected")
            if not bool(row["immutable"]):
                raise ConflictError("candidate strategy version must remain immutable")

            content = json.loads(row["content_json"])
            baseline_id = content.get("baseline_version_id")
            baseline = connection.execute(
                """
                SELECT id FROM strategy_versions
                WHERE id = ? AND strategy_id = ?
                  AND status = 'baseline' AND immutable = 1
                """,
                (baseline_id, row["strategy_id"]),
            ).fetchone()
            if baseline is None:
                raise NotFoundError("candidate requires its original immutable baseline")

            changed = connection.execute(
                """
                UPDATE strategy_versions SET status = 'rejected'
                WHERE id = ? AND status = 'candidate' AND immutable = 1
                """,
                (version_id,),
            )
            if changed.rowcount != 1:
                raise ConflictError("candidate status changed before rejection completed")
            connection.execute(
                """
                INSERT INTO approvals (
                    id, subject_type, subject_id, decision, actor, created_at
                ) VALUES (?, 'strategy_candidate', ?, 'rejected', 'user', ?)
                """,
                (approval_id, version_id, created_at),
            )
            connection.execute(
                """
                UPDATE research_sessions
                SET status = 'rejected', updated_at = ?
                WHERE id = (
                    SELECT session_id FROM strategy_drafts WHERE id = ?
                )
                """,
                (created_at, row["strategy_id"]),
            )

        return StrategyVersion(
            id=row["id"],
            strategy_id=row["strategy_id"],
            version=row["version"],
            status="rejected",
            content_snapshot=content,
            source_snapshot=row["source_snapshot"],
            immutable=True,
            created_at=row["created_at"],
        )

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

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        updated_at: str,
        error: str | None = None,
    ) -> Job:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, error = ?
                WHERE id = ?
                """,
                (status, updated_at, error, job_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"job not found: {job_id}")
        return self.get_job(job_id)

    def append_job_log(
        self,
        job_id: str,
        *,
        level: str,
        message: str,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError(f"job not found: {job_id}")
            connection.execute(
                """
                INSERT INTO job_logs (job_id, level, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, level, message, created_at),
            )

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

    def create_report(self, report: Report) -> Report:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reports (
                    id, job_id, report_type, path, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    report.job_id,
                    report.report_type,
                    report.artifact_key,
                    json.dumps(report.summary, ensure_ascii=False, sort_keys=True),
                    report.created_at,
                ),
            )
        return report

    def list_reports(self, job_id: str | None = None) -> Sequence[Report]:
        query = "SELECT * FROM reports"
        parameters: tuple[str, ...] = ()
        if job_id is not None:
            query += " WHERE job_id = ?"
            parameters = (job_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._report(row) for row in rows]

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

    def create_research_handoff(
        self, handoff: ResearchHandoff
    ) -> ResearchHandoff:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT id FROM research_sessions WHERE id = ?", (handoff.session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError(f"research session not found: {handoff.session_id}")
            if handoff.agent_run_id is not None:
                agent_run = connection.execute(
                    "SELECT session_id FROM agent_runs WHERE id = ?",
                    (handoff.agent_run_id,),
                ).fetchone()
                if agent_run is None:
                    raise NotFoundError(
                        f"agent run not found: {handoff.agent_run_id}"
                    )
                if agent_run["session_id"] != handoff.session_id:
                    raise ConflictError("handoff agent run belongs to another session")
            connection.execute(
                """
                INSERT INTO research_handoffs (
                    id, session_id, agent_run_id, subject_id, status,
                    stop_reason_code, stop_reason_text, completed_actions_json,
                    not_started_actions_json, user_action_required,
                    required_user_action, next_recommended_action,
                    approval_subject_id, safe_to_continue, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff.id,
                    handoff.session_id,
                    handoff.agent_run_id,
                    handoff.subject_id,
                    handoff.status,
                    handoff.stop_reason_code,
                    handoff.stop_reason_text,
                    json.dumps(handoff.completed_actions, ensure_ascii=False),
                    json.dumps(handoff.not_started_actions, ensure_ascii=False),
                    int(handoff.user_action_required),
                    handoff.required_user_action,
                    handoff.next_recommended_action,
                    handoff.approval_subject_id,
                    int(handoff.safe_to_continue),
                    handoff.created_at,
                ),
            )
        return handoff

    def get_latest_session_handoff(self, session_id: str) -> ResearchHandoff:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM research_handoffs
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"research handoff not found for session: {session_id}")
        return self._research_handoff(row)

    def get_latest_agent_run_handoff(self, agent_run_id: str) -> ResearchHandoff:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM research_handoffs
                WHERE agent_run_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (agent_run_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"research handoff not found for agent run: {agent_run_id}")
        return self._research_handoff(row)

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
                    stopping_conditions_json, proposal_id, candidate_version_id,
                    correction_of_plan_id, search_strategy, random_seed, status,
                    approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    plan.proposal_id,
                    plan.candidate_version_id,
                    plan.correction_of_plan_id,
                    plan.search_strategy,
                    plan.random_seed,
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
                    status, metrics_json, log_artifact_key, candidate_version_id,
                    parameter_signature, split, cost_model_json, seed, error,
                    elapsed_seconds, peak_rss_mb, result_artifact_key,
                    metrics_artifact_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trial.id,
                    trial.experiment_plan_id,
                    json.dumps(trial.parameters, sort_keys=True),
                    trial.data_version,
                    trial.status,
                    json.dumps(trial.metrics, sort_keys=True),
                    trial.log_artifact_key,
                    trial.candidate_version_id,
                    trial.parameter_signature,
                    trial.split,
                    json.dumps(trial.cost_model, sort_keys=True),
                    trial.seed,
                    trial.error,
                    trial.elapsed_seconds,
                    trial.peak_rss_mb,
                    trial.result_artifact_key,
                    trial.metrics_artifact_key,
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

    def update_trial(
        self,
        trial_id: str,
        *,
        status: str,
        metrics: Mapping[str, float],
        log_artifact_key: str | None,
        error: str | None = None,
        elapsed_seconds: float | None = None,
        peak_rss_mb: float | None = None,
        result_artifact_key: str | None = None,
        metrics_artifact_key: str | None = None,
    ) -> Trial:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE trials
                SET status = ?, metrics_json = ?, log_artifact_key = ?,
                    error = ?, elapsed_seconds = ?, peak_rss_mb = ?,
                    result_artifact_key = ?, metrics_artifact_key = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(metrics, sort_keys=True),
                    log_artifact_key,
                    error,
                    elapsed_seconds,
                    peak_rss_mb,
                    result_artifact_key,
                    metrics_artifact_key,
                    trial_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"trial not found: {trial_id}")
            row = connection.execute(
                "SELECT * FROM trials WHERE id = ?", (trial_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"trial not found: {trial_id}")
        return self._trial(row)

    def get_trial_by_signature(
        self, plan_id: str, parameter_signature: str
    ) -> Trial | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM trials
                WHERE experiment_plan_id = ? AND parameter_signature = ?
                """,
                (plan_id, parameter_signature),
            ).fetchone()
        return self._trial(row) if row is not None else None

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

    def update_agent_run_status(self, agent_run_id: str, *, status: str) -> AgentRun:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE agent_runs SET status = ? WHERE id = ?",
                (status, agent_run_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"agent run not found: {agent_run_id}")
        return self.get_agent_run(agent_run_id)

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

    def create_gate_evaluation(self, evaluation: GateEvaluation) -> GateEvaluation:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gate_evaluations (
                    id, profile_id, gate_name, subject_type, subject_id,
                    market_profile, strategy_objective, status, metrics_json,
                    reasons_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation.id,
                    evaluation.profile_id,
                    evaluation.gate_name,
                    evaluation.subject_type,
                    evaluation.subject_id,
                    evaluation.market_profile,
                    evaluation.strategy_objective,
                    evaluation.status,
                    json.dumps(evaluation.metrics, sort_keys=True),
                    json.dumps(evaluation.reasons, ensure_ascii=False),
                    evaluation.created_at,
                ),
            )
        return evaluation

    def get_gate_evaluation(self, evaluation_id: str) -> GateEvaluation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gate_evaluations WHERE id = ?", (evaluation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"gate evaluation not found: {evaluation_id}")
        return self._gate_evaluation(row)

    def list_gate_evaluations(
        self, *, subject_id: str | None = None
    ) -> Sequence[GateEvaluation]:
        query = "SELECT * FROM gate_evaluations"
        parameters: tuple[str, ...] = ()
        if subject_id is not None:
            query += " WHERE subject_id = ?"
            parameters = (subject_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._gate_evaluation(row) for row in rows]

    def create_strategy_outcome(self, outcome: StrategyOutcome) -> StrategyOutcome:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_outcomes (
                    id, strategy_version_id, market_profile, pipeline_profile_id,
                    outcome_type, viability_gate_result_id,
                    evidence_artifact_keys_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.id,
                    outcome.strategy_version_id,
                    outcome.market_profile,
                    outcome.pipeline_profile_id,
                    outcome.outcome_type,
                    outcome.viability_gate_result_id,
                    json.dumps(outcome.evidence_artifact_keys),
                    outcome.notes,
                    outcome.created_at,
                ),
            )
        return outcome

    def list_strategy_outcomes(self) -> Sequence[StrategyOutcome]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_outcomes ORDER BY created_at DESC"
            ).fetchall()
        return [self._strategy_outcome(row) for row in rows]

    def create_component_evidence(
        self, evidence: ComponentEvidence
    ) -> ComponentEvidence:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO component_evidence (
                    id, source_strategy_version_id, lineage_json, component_type,
                    target_market_profile, incremental_metrics_json,
                    out_of_sample_status, failure_conditions_json, logic_signature,
                    timeframe, source_experiment_plan_id, source_trial_ids_json,
                    stable_parameter_ranges_json, failed_parameter_ranges_json,
                    regimes_json, evidence_level, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.id,
                    evidence.source_strategy_version_id,
                    json.dumps(evidence.lineage, ensure_ascii=False, sort_keys=True),
                    evidence.component_type,
                    evidence.target_market_profile,
                    json.dumps(evidence.incremental_metrics, sort_keys=True),
                    evidence.out_of_sample_status,
                    json.dumps(evidence.failure_conditions, ensure_ascii=False),
                    evidence.logic_signature,
                    evidence.timeframe,
                    evidence.source_experiment_plan_id,
                    json.dumps(evidence.source_trial_ids),
                    json.dumps(evidence.stable_parameter_ranges, sort_keys=True),
                    json.dumps(evidence.failed_parameter_ranges, sort_keys=True),
                    json.dumps(evidence.regimes),
                    evidence.evidence_level,
                    evidence.created_at,
                ),
            )
        return evidence

    def get_component_evidence(self, evidence_id: str) -> ComponentEvidence:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM component_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"component evidence not found: {evidence_id}")
        return self._component_evidence(row)

    def list_component_evidence(self) -> Sequence[ComponentEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM component_evidence ORDER BY created_at DESC"
            ).fetchall()
        return [self._component_evidence(row) for row in rows]

    def create_component_candidate(
        self, candidate: ComponentCandidate
    ) -> ComponentCandidate:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO component_candidates (
                    id, evidence_id, name, status, logic_signature,
                    target_market_profile, timeframe, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.id,
                    candidate.evidence_id,
                    candidate.name,
                    candidate.status,
                    candidate.logic_signature,
                    candidate.target_market_profile,
                    candidate.timeframe,
                    candidate.created_at,
                ),
            )
        return candidate

    def list_component_candidates(self) -> Sequence[ComponentCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM component_candidates ORDER BY created_at DESC"
            ).fetchall()
        return [self._component_candidate(row) for row in rows]

    def get_component_candidate(self, candidate_id: str) -> ComponentCandidate:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM component_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"component candidate not found: {candidate_id}")
        return self._component_candidate(row)

    def set_component_candidate_archive(
        self,
        *,
        candidate_id: str,
        archived_at: str | None,
        archive_reason: str | None,
    ) -> ComponentCandidate:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE component_candidates
                SET archived_at = ?, archive_reason = ?
                WHERE id = ?
                """,
                (archived_at, archive_reason, candidate_id),
            )
            if updated.rowcount != 1:
                raise NotFoundError(
                    f"component candidate not found: {candidate_id}"
                )
        return self.get_component_candidate(candidate_id)

    def create_component_hypothesis(
        self, hypothesis: ComponentHypothesis
    ) -> ComponentHypothesis:
        with self._connect() as connection:
            session = connection.execute(
                "SELECT id FROM research_sessions WHERE id = ?",
                (hypothesis.session_id,),
            ).fetchone()
            if session is None:
                raise NotFoundError(
                    f"research session not found: {hypothesis.session_id}"
                )
            existing_count = connection.execute(
                """
                SELECT COUNT(*) FROM component_hypotheses
                WHERE subject_id = ? AND status IN ('draft', 'approved')
                """,
                (hypothesis.subject_id,),
            ).fetchone()[0]
            if existing_count >= 3:
                raise ConflictError(
                    "a subject may have at most three active component hypotheses"
                )
            connection.execute(
                """
                INSERT INTO component_hypotheses (
                    id, session_id, subject_id, title, hypothesis, component_type,
                    source, evidence_refs_json, expected_improvement,
                    parameter_space_json, suggested_trials,
                    failure_conditions_json, evidence_level,
                    contamination_status, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hypothesis.id,
                    hypothesis.session_id,
                    hypothesis.subject_id,
                    hypothesis.title,
                    hypothesis.hypothesis,
                    hypothesis.component_type,
                    hypothesis.source,
                    json.dumps(hypothesis.evidence_refs, ensure_ascii=False),
                    hypothesis.expected_improvement,
                    json.dumps(hypothesis.parameter_space, sort_keys=True),
                    hypothesis.suggested_trials,
                    json.dumps(hypothesis.failure_conditions, ensure_ascii=False),
                    hypothesis.evidence_level,
                    hypothesis.contamination_status,
                    hypothesis.status,
                    hypothesis.created_at,
                ),
            )
        return hypothesis

    def list_component_hypotheses(
        self, *, subject_id: str | None = None
    ) -> Sequence[ComponentHypothesis]:
        query = "SELECT * FROM component_hypotheses"
        parameters: tuple[str, ...] = ()
        if subject_id is not None:
            query += " WHERE subject_id = ?"
            parameters = (subject_id,)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._component_hypothesis(row) for row in rows]

    def create_regime_validation(
        self, validation: RegimeValidation
    ) -> RegimeValidation:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO regime_validations (
                    id, subject_type, subject_id, mode, market_profile, detector_version,
                    ex_ante_observable, target_regimes_json, suitable_regimes_json,
                    conditional_regimes_json, blocked_regimes_json,
                    unknown_regimes_json, regime_metrics_json,
                    transition_policy_json, history_days, evidence_status,
                    viability_gate_result_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation.id,
                    validation.subject_type,
                    validation.subject_id,
                    validation.mode,
                    validation.market_profile,
                    validation.detector_version,
                    int(validation.ex_ante_observable),
                    json.dumps(validation.target_regimes),
                    json.dumps(validation.suitable_regimes),
                    json.dumps(validation.conditional_regimes),
                    json.dumps(validation.blocked_regimes),
                    json.dumps(validation.unknown_regimes),
                    json.dumps(validation.regime_metrics, sort_keys=True),
                    json.dumps(validation.transition_policy, ensure_ascii=False, sort_keys=True),
                    validation.history_days,
                    validation.evidence_status,
                    validation.viability_gate_result_id,
                    validation.created_at,
                ),
            )
        return validation

    def list_regime_validations(self) -> Sequence[RegimeValidation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM regime_validations ORDER BY created_at DESC"
            ).fetchall()
        return [self._regime_validation(row) for row in rows]

    @staticmethod
    def _session(row: sqlite3.Row) -> ResearchSession:
        mode_config = json.loads(row["mode_config_json"] or "{}")
        return ResearchSession(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            research_mode=row["research_mode"],
            mode_config=mode_config,
            mode_revision=row["mode_revision"],
        )

    @staticmethod
    def _research_budget(row: sqlite3.Row) -> ResearchBudget:
        return ResearchBudget(
            session_id=row["session_id"],
            max_hypotheses=row["max_hypotheses"],
            max_trials_total=row["max_trials_total"],
            max_compute_minutes=row["max_compute_minutes"],
            max_locked_test_uses=row["max_locked_test_uses"],
            require_user_approval_for_new_hypothesis=bool(
                row["require_user_approval_for_new_hypothesis"]
            ),
            used_hypotheses=row["used_hypotheses"],
            reserved_trials=row["reserved_trials"],
            reserved_compute_minutes=row["reserved_compute_minutes"],
            used_locked_test_uses=row["used_locked_test_uses"],
        )

    @staticmethod
    def _research_authorization(row: sqlite3.Row) -> ResearchAuthorization:
        return ResearchAuthorization(
            id=row["id"],
            subject_id=row["subject_id"],
            session_id=row["session_id"],
            allowed_stages=tuple(json.loads(row["allowed_stages_json"])),
            auto_continue=bool(row["auto_continue"]),
            max_cost_usdt=float(row["max_cost_usdt"]),
            max_time_minutes=int(row["max_time_minutes"]),
            max_trials=int(row["max_trials"]),
            locked_test_allowed=bool(row["locked_test_allowed"]),
            stop_conditions=tuple(json.loads(row["stop_conditions_json"])),
            expires_at=row["expires_at"],
            approved_by=row["approved_by"],
            status=row["status"],
            created_at=row["created_at"],
            used_cost_usdt=float(row["used_cost_usdt"]),
            used_time_minutes=float(row["used_time_minutes"]),
            used_trials=int(row["used_trials"]),
        )

    @staticmethod
    def _research_authorization_stage(
        row: sqlite3.Row,
    ) -> ResearchAuthorizationStage:
        return ResearchAuthorizationStage(
            id=row["id"],
            authorization_id=row["authorization_id"],
            stage=row["stage"],
            status=row["status"],
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            reason=row["reason"],
            elapsed_minutes=float(row["elapsed_minutes"]),
            cost_usdt=float(row["cost_usdt"]),
            trials_used=int(row["trials_used"]),
            created_at=row["created_at"],
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
    def _strategy_version(row: sqlite3.Row) -> StrategyVersion:
        return StrategyVersion(
            id=row["id"],
            strategy_id=row["strategy_id"],
            version=row["version"],
            status=row["status"],
            content_snapshot=json.loads(row["content_json"]),
            source_snapshot=row["source_snapshot"],
            immutable=bool(row["immutable"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _proposal(row: sqlite3.Row) -> Proposal:
        items = json.loads(row["parameter_space_json"])
        return Proposal(
            id=row["id"],
            draft_id=row["draft_id"],
            proposal_type=row["proposal_type"],
            content=json.loads(row["content_json"]),
            status=row["status"],
            baseline_version_id=row["baseline_version_id"],
            subject_id=row["subject_id"],
            hypothesis=row["hypothesis"],
            rule_diff=json.loads(row["rule_diff_json"]),
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            parameter_space=tuple(
                ParameterSpace(
                    name=item["name"],
                    kind=item["kind"],
                    values=tuple(item.get("values", ())),
                    lower=item.get("lower"),
                    upper=item.get("upper"),
                    step=item.get("step"),
                )
                for item in items
            ),
            data_splits=json.loads(row["data_splits_json"]),
            cost_model=json.loads(row["cost_model_json"]),
            objectives=tuple(
                Objective(**item) for item in json.loads(row["objectives_json"])
            ),
            constraints=tuple(
                Constraint(**item) for item in json.loads(row["constraints_json"])
            ),
            estimated_trials=row["estimated_trials"],
            estimated_minutes=row["estimated_minutes"],
            failure_conditions=tuple(json.loads(row["failure_conditions_json"])),
            stopping_conditions=tuple(json.loads(row["stopping_conditions_json"])),
            rollback_plan=row["rollback_plan"],
            candidate_version_id=row["candidate_version_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _gate_evaluation(row: sqlite3.Row) -> GateEvaluation:
        return GateEvaluation(
            id=row["id"],
            profile_id=row["profile_id"],
            gate_name=row["gate_name"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            market_profile=row["market_profile"],
            strategy_objective=row["strategy_objective"],
            status=row["status"],
            metrics=json.loads(row["metrics_json"]),
            reasons=tuple(json.loads(row["reasons_json"])),
            created_at=row["created_at"],
        )

    @staticmethod
    def _strategy_outcome(row: sqlite3.Row) -> StrategyOutcome:
        return StrategyOutcome(
            id=row["id"],
            strategy_version_id=row["strategy_version_id"],
            market_profile=row["market_profile"],
            pipeline_profile_id=row["pipeline_profile_id"],
            outcome_type=row["outcome_type"],
            viability_gate_result_id=row["viability_gate_result_id"],
            evidence_artifact_keys=tuple(
                json.loads(row["evidence_artifact_keys_json"])
            ),
            notes=row["notes"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _component_evidence(row: sqlite3.Row) -> ComponentEvidence:
        return ComponentEvidence(
            id=row["id"],
            source_strategy_version_id=row["source_strategy_version_id"],
            lineage=json.loads(row["lineage_json"]),
            component_type=row["component_type"],
            target_market_profile=row["target_market_profile"],
            incremental_metrics=json.loads(row["incremental_metrics_json"]),
            out_of_sample_status=row["out_of_sample_status"],
            failure_conditions=tuple(json.loads(row["failure_conditions_json"])),
            created_at=row["created_at"],
            logic_signature=row["logic_signature"],
            timeframe=row["timeframe"],
            source_experiment_plan_id=row["source_experiment_plan_id"],
            source_trial_ids=tuple(json.loads(row["source_trial_ids_json"])),
            stable_parameter_ranges=json.loads(row["stable_parameter_ranges_json"]),
            failed_parameter_ranges=json.loads(row["failed_parameter_ranges_json"]),
            regimes=tuple(json.loads(row["regimes_json"])),
            evidence_level=row["evidence_level"],
        )

    @staticmethod
    def _component_candidate(row: sqlite3.Row) -> ComponentCandidate:
        return ComponentCandidate(
            id=row["id"],
            evidence_id=row["evidence_id"],
            name=row["name"],
            status=row["status"],
            created_at=row["created_at"],
            logic_signature=row["logic_signature"],
            target_market_profile=row["target_market_profile"],
            timeframe=row["timeframe"],
            archived_at=row["archived_at"],
            archive_reason=row["archive_reason"],
        )

    @staticmethod
    def _component_hypothesis(row: sqlite3.Row) -> ComponentHypothesis:
        return ComponentHypothesis(
            id=row["id"],
            session_id=row["session_id"],
            subject_id=row["subject_id"],
            title=row["title"],
            hypothesis=row["hypothesis"],
            component_type=row["component_type"],
            source=row["source"],
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
            expected_improvement=row["expected_improvement"],
            parameter_space=json.loads(row["parameter_space_json"]),
            suggested_trials=int(row["suggested_trials"]),
            failure_conditions=tuple(
                json.loads(row["failure_conditions_json"])
            ),
            evidence_level=row["evidence_level"],
            contamination_status=row["contamination_status"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _regime_validation(row: sqlite3.Row) -> RegimeValidation:
        return RegimeValidation(
            id=row["id"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            mode=row["mode"],
            market_profile=row["market_profile"],
            detector_version=row["detector_version"],
            ex_ante_observable=bool(row["ex_ante_observable"]),
            target_regimes=tuple(json.loads(row["target_regimes_json"])),
            suitable_regimes=tuple(json.loads(row["suitable_regimes_json"])),
            conditional_regimes=tuple(json.loads(row["conditional_regimes_json"])),
            blocked_regimes=tuple(json.loads(row["blocked_regimes_json"])),
            unknown_regimes=tuple(json.loads(row["unknown_regimes_json"])),
            regime_metrics=json.loads(row["regime_metrics_json"]),
            transition_policy=json.loads(row["transition_policy_json"]),
            history_days=row["history_days"],
            evidence_status=row["evidence_status"],
            viability_gate_result_id=row["viability_gate_result_id"],
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
    def _report(row: sqlite3.Row) -> Report:
        return Report(
            id=row["id"],
            job_id=row["job_id"],
            report_type=row["report_type"],
            artifact_key=row["path"],
            summary=json.loads(row["summary_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _research_handoff(row: sqlite3.Row) -> ResearchHandoff:
        return ResearchHandoff(
            id=row["id"],
            session_id=row["session_id"],
            agent_run_id=row["agent_run_id"],
            subject_id=row["subject_id"],
            status=row["status"],
            stop_reason_code=row["stop_reason_code"],
            stop_reason_text=row["stop_reason_text"],
            completed_actions=tuple(json.loads(row["completed_actions_json"])),
            not_started_actions=tuple(json.loads(row["not_started_actions_json"])),
            user_action_required=bool(row["user_action_required"]),
            required_user_action=row["required_user_action"],
            next_recommended_action=row["next_recommended_action"],
            approval_subject_id=row["approval_subject_id"],
            safe_to_continue=bool(row["safe_to_continue"]),
            created_at=row["created_at"],
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
                    step=item.get("step"),
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
            proposal_id=row["proposal_id"],
            candidate_version_id=row["candidate_version_id"],
            correction_of_plan_id=row["correction_of_plan_id"],
            search_strategy=row["search_strategy"],
            random_seed=row["random_seed"],
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
            candidate_version_id=row["candidate_version_id"],
            parameter_signature=row["parameter_signature"],
            split=row["split"],
            cost_model=json.loads(row["cost_model_json"]),
            seed=row["seed"],
            error=row["error"],
            elapsed_seconds=row["elapsed_seconds"],
            peak_rss_mb=row["peak_rss_mb"],
            result_artifact_key=row["result_artifact_key"],
            metrics_artifact_key=row["metrics_artifact_key"],
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
