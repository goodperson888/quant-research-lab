from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import ALLOWED_JOB_TYPES
from quant_lab.domain.models import AuditEvent, Job
from quant_lab.domain.repositories import ProductRepository


JobHandler = Callable[[Job], Mapping[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalWorker:
    """Deterministic local worker shell with injected, allowlisted handlers only."""

    def __init__(
        self,
        repository: ProductRepository,
        *,
        handlers: Mapping[str, JobHandler] | None = None,
    ) -> None:
        self.repository = repository
        self.service = ResearchApplicationService(repository)
        self.handlers = dict(handlers or {})
        unknown = set(self.handlers) - ALLOWED_JOB_TYPES
        if unknown:
            raise ValueError(f"worker handler is not allowlisted: {sorted(unknown)}")

    def status(self) -> dict[str, Any]:
        return {
            "worker": "local_sqlite",
            "execution_mode": "one_shot",
            "process_running": False,
            "requires_job_id": True,
            "registered_handlers": sorted(self.handlers),
            "note": (
                "Without --job-id this command prints status and exits; it is not a "
                "resident queue consumer."
            ),
            "arbitrary_shell_enabled": False,
            "live_trading_enabled": False,
        }

    def run(self, job_id: str) -> Mapping[str, Any]:
        job = self.repository.get_job(job_id)
        if job.status != "queued":
            raise ValueError(f"job must be queued, got: {job.status}")
        handler = self.handlers.get(job.job_type)
        if handler is None:
            raise ValueError(f"no allowlisted handler registered for: {job.job_type}")
        if job.job_type == "stress_test" and job.payload.get("stress_level") == "full":
            gate_id = job.payload.get("viability_gate_result_id")
            subject_id = job.payload.get("strategy_version_id") or job.payload.get(
                "candidate_version_id"
            )
            if not isinstance(gate_id, str) or not isinstance(subject_id, str):
                raise ValueError("full stress requires an explicit passed viability gate")
            gate = self.repository.get_gate_evaluation(gate_id)
            if (
                gate.subject_id != subject_id
                or gate.gate_name != "viability"
                or gate.status != "passed"
            ):
                raise ValueError("full stress is blocked until viability passes")

        agent_run_id = job.payload.get("agent_run_id")
        tool_name = (
            "run_parameter_search"
            if job.job_type == "parameter_search"
            else "run_backtest"
        )
        now = _utc_now()
        self.repository.update_job(job.id, status="running", updated_at=now)
        self.repository.append_job_log(
            job.id,
            level="info",
            message=f"Allowlisted {job.job_type} handler started.",
            created_at=now,
        )
        if isinstance(agent_run_id, str):
            self.repository.update_agent_run_status(agent_run_id, status="running")
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type="job.running",
                aggregate_type="job",
                aggregate_id=job.id,
                actor_type="system",
                payload={"job_type": job.job_type},
                created_at=now,
            )
        )

        try:
            result = dict(handler(job))
        except Exception as exc:
            failed_at = _utc_now()
            self.repository.update_job(
                job.id, status="failed", updated_at=failed_at, error=str(exc)
            )
            self.repository.append_job_log(
                job.id,
                level="error",
                message=str(exc),
                created_at=failed_at,
            )
            if isinstance(agent_run_id, str):
                self.repository.update_agent_run_status(agent_run_id, status="failed")
                self.service.record_tool_call(
                    agent_run_id=agent_run_id,
                    tool_name=tool_name,
                    sanitized_input={"job_id": job.id},
                    sanitized_output={"error": str(exc)},
                    status="failed",
                )
            self.repository.append_event(
                AuditEvent(
                    id=None,
                    event_type="job.failed",
                    aggregate_type="job",
                    aggregate_id=job.id,
                    actor_type="system",
                    payload={"job_type": job.job_type, "error": str(exc)},
                    created_at=failed_at,
                )
            )
            raise

        completed_at = _utc_now()
        self.repository.update_job(job.id, status="succeeded", updated_at=completed_at)
        self.repository.append_job_log(
            job.id,
            level="info",
            message=f"Handler completed; run_id={result.get('run_id')}",
            created_at=completed_at,
        )
        if isinstance(agent_run_id, str):
            self.repository.update_agent_run_status(agent_run_id, status="completed")
            self.service.record_tool_call(
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                sanitized_input={"job_id": job.id},
                sanitized_output={
                    "run_id": result.get("run_id"),
                    "manifest_artifact_key": result.get("manifest_artifact_key"),
                    "report_artifact_key": result.get("report_artifact_key"),
                },
                status="completed",
            )
            for artifact_key in result.get("artifact_keys", []):
                artifact_type = "experiment"
                if artifact_key.endswith("manifest.json"):
                    artifact_type = "manifest"
                elif artifact_key.endswith(".md"):
                    artifact_type = "report"
                elif artifact_key.endswith(".jsonl"):
                    artifact_type = "log"
                self.service.record_artifact(
                    agent_run_id=agent_run_id,
                    artifact_type=artifact_type,
                    artifact_key=artifact_key,
                )
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type="job.succeeded",
                aggregate_type="job",
                aggregate_id=job.id,
                actor_type="system",
                payload={
                    "job_type": job.job_type,
                    "run_id": result.get("run_id"),
                },
                created_at=completed_at,
            )
        )
        return result
