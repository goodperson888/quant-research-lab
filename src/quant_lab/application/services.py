from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from quant_lab.domain.errors import ApprovalRequiredError, InvalidJobError
from quant_lab.domain.models import (
    ALLOWED_JOB_TYPES,
    AuditEvent,
    Job,
    Message,
    ResearchSession,
    StrategyDraft,
    StrategyVersion,
)
from quant_lab.domain.repositories import ProductRepository


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
