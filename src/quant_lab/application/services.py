from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from quant_lab.domain.errors import ApprovalRequiredError, InvalidJobError
from quant_lab.domain.models import (
    ALLOWED_JOB_TYPES,
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
from quant_lab.domain.repositories import ProductRepository

from .tool_ports import ALLOWED_RESEARCH_TOOLS


FORBIDDEN_JOB_PAYLOAD_KEYS = frozenset(
    {"cmd", "command", "shell", "executable", "api_key", "secret", "password"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_JOB_PAYLOAD_KEYS:
                return True
            if _contains_forbidden_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


class ResearchApplicationService:
    def __init__(self, repository: ProductRepository) -> None:
        self.repository = repository
        self.repository.initialize()

    def _audit(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        actor_type: str = "user",
    ) -> None:
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                actor_type=actor_type,  # type: ignore[arg-type]
                payload=dict(payload),
                created_at=utc_now(),
            )
        )

    def create_research_session(self, *, title: str) -> ResearchSession:
        now = utc_now()
        session = ResearchSession(
            id=new_id("session"),
            title=title.strip(),
            status="inbox",
            created_at=now,
            updated_at=now,
        )
        created = self.repository.create_session(session)
        self._audit(
            event_type="research_session.created",
            aggregate_type="research_session",
            aggregate_id=created.id,
            payload={"title": created.title, "status": created.status},
        )
        return created

    def list_research_sessions(self) -> Sequence[ResearchSession]:
        return self.repository.list_sessions()

    def get_research_session(self, session_id: str) -> ResearchSession:
        return self.repository.get_session(session_id)

    def add_user_message(self, *, session_id: str, content: str) -> Message:
        self.repository.get_session(session_id)
        message = Message(
            id=new_id("message"),
            session_id=session_id,
            role="user",
            content=content.strip(),
            created_at=utc_now(),
        )
        created = self.repository.add_message(message)
        self._audit(
            event_type="message.created",
            aggregate_type="research_session",
            aggregate_id=session_id,
            payload={"message_id": created.id, "role": created.role},
        )
        return created

    def list_messages(self, session_id: str) -> Sequence[Message]:
        self.repository.get_session(session_id)
        return self.repository.list_messages(session_id)

    def create_strategy_intake(
        self,
        *,
        session_id: str,
        source_type: str,
        raw_content: str,
        source_name: str | None = None,
    ) -> StrategyDraft:
        self.repository.get_session(session_id)
        draft = StrategyDraft(
            id=new_id("draft"),
            session_id=session_id,
            source_type=source_type,  # type: ignore[arg-type]
            source_name=source_name.strip() if source_name else None,
            raw_content=raw_content,
            structured_content={},
            status="draft",
            baseline_version_id=None,
            created_at=utc_now(),
        )
        created = self.repository.create_draft(draft)
        self._audit(
            event_type="strategy_intake.created",
            aggregate_type="strategy_draft",
            aggregate_id=created.id,
            payload={
                "session_id": session_id,
                "source_type": created.source_type,
                "source_name": created.source_name,
            },
        )
        return created

    def list_strategy_drafts(
        self, session_id: str | None = None
    ) -> Sequence[StrategyDraft]:
        return self.repository.list_drafts(session_id)

    def freeze_baseline(
        self, *, draft_id: str, confirmed_by_user: bool
    ) -> StrategyVersion:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "baseline freeze requires explicit user confirmation"
            )
        now = utc_now()
        version = self.repository.freeze_baseline(
            draft_id=draft_id,
            version_id=new_id("version"),
            approval_id=new_id("approval"),
            created_at=now,
        )
        self._audit(
            event_type="strategy_baseline.frozen",
            aggregate_type="strategy_version",
            aggregate_id=version.id,
            payload={
                "strategy_id": version.strategy_id,
                "version": version.version,
                "immutable": True,
                "approved_by": "user",
            },
        )
        return version

    def create_job(self, *, job_type: str, payload: Mapping[str, Any]) -> Job:
        if job_type not in ALLOWED_JOB_TYPES:
            allowed = ", ".join(sorted(ALLOWED_JOB_TYPES))
            raise InvalidJobError(f"job type must be one of: {allowed}")
        if _contains_forbidden_key(payload):
            raise InvalidJobError("job payload contains a forbidden execution or secret key")
        if job_type == "parameter_search":
            plan_id = payload.get("experiment_plan_id")
            if not isinstance(plan_id, str) or not plan_id:
                raise ApprovalRequiredError(
                    "parameter search requires an approved experiment_plan_id"
                )
            plan = self.repository.get_experiment_plan(plan_id)
            if plan.status != "approved" or plan.approved_by != "user":
                raise ApprovalRequiredError(
                    "parameter search requires explicit user approval of the experiment plan"
                )
        now = utc_now()
        job = Job(
            id=new_id("job"),
            job_type=job_type,
            status="queued",
            payload=dict(payload),
            created_at=now,
            updated_at=now,
        )
        created = self.repository.create_job(job)
        self._audit(
            event_type="job.queued",
            aggregate_type="job",
            aggregate_id=created.id,
            payload={"job_type": created.job_type, "status": created.status},
        )
        return created

    def list_jobs(self) -> Sequence[Job]:
        return self.repository.list_jobs()

    def get_job(self, job_id: str) -> Job:
        return self.repository.get_job(job_id)

    def list_job_logs(self, job_id: str) -> Sequence[Mapping[str, Any]]:
        self.repository.get_job(job_id)
        return self.repository.list_job_logs(job_id)

    def list_audit_events(self, *, limit: int = 100) -> Sequence[AuditEvent]:
        return self.repository.list_events(limit=limit)

    def create_experiment_plan(
        self,
        *,
        baseline_version_id: str,
        hypothesis: str,
        parameter_space: Sequence[ParameterSpace],
        objectives: Sequence[Objective],
        constraints: Sequence[Constraint],
        data_splits: Mapping[str, str],
        cost_model: Mapping[str, Any],
        max_trials: int | None,
        time_budget_seconds: int | None,
        stopping_conditions: Sequence[str],
    ) -> ExperimentPlan:
        plan = ExperimentPlan(
            id=new_id("plan"),
            baseline_version_id=baseline_version_id,
            hypothesis=hypothesis.strip(),
            parameter_space=tuple(parameter_space),
            objectives=tuple(objectives),
            constraints=tuple(constraints),
            data_splits=dict(data_splits),
            cost_model=dict(cost_model),
            max_trials=max_trials,
            time_budget_seconds=time_budget_seconds,
            stopping_conditions=tuple(stopping_conditions),
            status="draft",
            approved_by=None,
            created_at=utc_now(),
        )
        created = self.repository.create_experiment_plan(plan)
        self._audit(
            event_type="experiment_plan.created",
            aggregate_type="experiment_plan",
            aggregate_id=created.id,
            payload={
                "baseline_version_id": created.baseline_version_id,
                "max_trials": created.max_trials,
                "time_budget_seconds": created.time_budget_seconds,
            },
        )
        return created

    def list_experiment_plans(self) -> Sequence[ExperimentPlan]:
        return self.repository.list_experiment_plans()

    def approve_experiment_plan(
        self, *, plan_id: str, confirmed_by_user: bool
    ) -> ExperimentPlan:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "experiment plan approval requires explicit user confirmation"
            )
        current = self.repository.get_experiment_plan(plan_id)
        approved = current.approve(actor="user")
        saved = self.repository.approve_experiment_plan(
            approved,
            approval_id=new_id("approval"),
            created_at=utc_now(),
        )
        self._audit(
            event_type="experiment_plan.approved",
            aggregate_type="experiment_plan",
            aggregate_id=saved.id,
            payload={"approved_by": "user", "status": saved.status},
        )
        return saved

    def create_agent_run(
        self,
        *,
        session_id: str,
        agent_name: str,
        mode: str = "guided",
        agent_provider: AgentProviderKind = AgentProviderKind.EXTERNAL_LOCAL_AGENT,
        execution_target: ExecutionTargetKind = ExecutionTargetKind.LOCAL_RUNTIME,
        plan_summary: str | None = None,
    ) -> AgentRun:
        self.repository.get_session(session_id)
        agent_run = AgentRun(
            id=new_id("agent_run"),
            session_id=session_id,
            agent_name=agent_name,
            agent_provider=agent_provider,
            execution_target=execution_target,
            mode=mode,  # type: ignore[arg-type]
            status="queued",
            plan_summary=plan_summary,
            created_at=utc_now(),
        )
        created = self.repository.create_agent_run(agent_run)
        self._audit(
            event_type="agent_run.created",
            aggregate_type="agent_run",
            aggregate_id=created.id,
            payload={
                "agent_provider": created.agent_provider,
                "execution_target": created.execution_target,
                "mode": created.mode,
            },
            actor_type="external_agent",
        )
        return created

    def record_tool_call(
        self,
        *,
        agent_run_id: str,
        tool_name: str,
        sanitized_input: Mapping[str, Any],
        sanitized_output: Mapping[str, Any] | None,
        status: str,
        agent_step_id: str | None = None,
    ) -> ToolCall:
        self.repository.get_agent_run(agent_run_id)
        if tool_name not in ALLOWED_RESEARCH_TOOLS:
            raise InvalidJobError(f"research tool is not allowlisted: {tool_name}")
        if _contains_forbidden_key(sanitized_input) or _contains_forbidden_key(
            sanitized_output
        ):
            raise InvalidJobError("tool call contains a forbidden execution or secret key")
        tool_call = ToolCall(
            id=new_id("tool_call"),
            agent_run_id=agent_run_id,
            agent_step_id=agent_step_id,
            tool_name=tool_name,
            sanitized_input=dict(sanitized_input),
            sanitized_output=(
                dict(sanitized_output) if sanitized_output is not None else None
            ),
            status=status,  # type: ignore[arg-type]
            created_at=utc_now(),
        )
        created = self.repository.create_tool_call(tool_call)
        self._audit(
            event_type="tool_call.recorded",
            aggregate_type="agent_run",
            aggregate_id=agent_run_id,
            payload={"tool_call_id": created.id, "tool_name": tool_name, "status": status},
            actor_type="external_agent",
        )
        return created

    def record_artifact(
        self,
        *,
        agent_run_id: str,
        artifact_type: str,
        artifact_key: str,
        checksum: str | None = None,
    ) -> Artifact:
        self.repository.get_agent_run(agent_run_id)
        artifact = Artifact(
            id=new_id("artifact"),
            agent_run_id=agent_run_id,
            artifact_type=artifact_type,  # type: ignore[arg-type]
            artifact_key=artifact_key,
            checksum=checksum,
            created_at=utc_now(),
        )
        created = self.repository.create_artifact(artifact)
        self._audit(
            event_type="artifact.created",
            aggregate_type="agent_run",
            aggregate_id=agent_run_id,
            payload={
                "artifact_id": created.id,
                "artifact_type": created.artifact_type,
                "artifact_key": created.artifact_key,
            },
            actor_type="external_agent",
        )
        return created

    def record_trial(
        self,
        *,
        experiment_plan_id: str,
        parameters: Mapping[str, Any],
        data_version: str,
        status: str = "queued",
        metrics: Mapping[str, float] | None = None,
        log_artifact_key: str | None = None,
    ) -> Trial:
        trial = Trial(
            id=new_id("trial"),
            experiment_plan_id=experiment_plan_id,
            parameters=dict(parameters),
            data_version=data_version,
            status=status,  # type: ignore[arg-type]
            metrics=dict(metrics or {}),
            log_artifact_key=log_artifact_key,
            created_at=utc_now(),
        )
        created = self.repository.create_trial(trial)
        self._audit(
            event_type="trial.created",
            aggregate_type="experiment_plan",
            aggregate_id=experiment_plan_id,
            payload={"trial_id": created.id, "status": created.status},
            actor_type="system",
        )
        return created
