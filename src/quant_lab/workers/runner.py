from __future__ import annotations

from datetime import datetime, timezone
import resource
import signal
import sys
import time
from typing import Any, Callable, Mapping

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import JobCancelledError
from quant_lab.domain.models import ALLOWED_JOB_TYPES
from quant_lab.domain.models import AuditEvent, Job
from quant_lab.domain.repositories import ProductRepository
from quant_lab.infrastructure.policy_readers import WorkerResourcePolicy


JobHandler = Callable[[Job], Mapping[str, Any]]


DEFAULT_WORKER_RESOURCE_POLICY = WorkerResourcePolicy(
    policy_id="builtin_local_one_shot_v1",
    max_rss_mb=4096,
    max_concurrent_trials=1,
    max_job_minutes=30,
    parquet_batch_rows=1000,
    kill_on_memory_limit=True,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalWorker:
    """Deterministic local worker shell with injected, allowlisted handlers only."""

    def __init__(
        self,
        repository: ProductRepository,
        *,
        handlers: Mapping[str, JobHandler] | None = None,
        resource_policy: WorkerResourcePolicy | None = None,
    ) -> None:
        self.repository = repository
        self.service = ResearchApplicationService(repository)
        self.handlers = dict(handlers or {})
        self.resource_policy = resource_policy or DEFAULT_WORKER_RESOURCE_POLICY
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
            "resource_policy": {
                "policy_id": self.resource_policy.policy_id,
                "max_rss_mb": self.resource_policy.max_rss_mb,
                "max_concurrent_trials": self.resource_policy.max_concurrent_trials,
                "default_concurrent_trials": self.resource_policy.default_concurrent_trials,
                "max_job_minutes": self.resource_policy.max_job_minutes,
                "parquet_batch_rows": self.resource_policy.parquet_batch_rows,
                "kill_on_memory_limit": self.resource_policy.kill_on_memory_limit,
            },
            "note": (
                "Without --job-id this command prints status and exits; it is not a "
                "resident queue consumer."
            ),
            "arbitrary_shell_enabled": False,
            "live_trading_enabled": False,
        }

    def _mark_batch_proposal_evaluated(self, job: Job) -> None:
        if job.job_type != "parameter_search" or job.payload.get("batch_mode") is not True:
            return
        plan_id = job.payload.get("experiment_plan_id")
        if not isinstance(plan_id, str):
            return
        plan = self.repository.get_experiment_plan(plan_id)
        if plan.proposal_id is None:
            return
        proposal = self.repository.get_proposal(plan.proposal_id)
        if proposal.status == "executing":
            self.repository.update_proposal(proposal.transition("evaluated"))

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
        if job.job_type == "regime_validation" and job.payload.get("mode") == "regime_validation":
            gate_id = job.payload.get("viability_gate_result_id")
            subject_id = job.payload.get("subject_id")
            if not isinstance(gate_id, str) or not isinstance(subject_id, str):
                raise ValueError("formal regime validation requires a passed viability gate")
            gate = self.repository.get_gate_evaluation(gate_id)
            if (
                gate.subject_id != subject_id
                or gate.gate_name != "viability"
                or gate.status != "passed"
                or gate.market_profile != job.payload.get("market_profile")
            ):
                raise ValueError("formal regime validation is blocked until viability passes")

        agent_run_id = job.payload.get("agent_run_id")
        tool_name = {
            "parameter_search": "run_parameter_search",
            "regime_validation": "record_regime_validation",
            "correctness_diagnostic": "run_correctness_diagnostic",
            "report": "generate_report",
        }.get(job.job_type, "run_backtest")
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

        started_monotonic = time.monotonic()
        peak_rss_mb = self._peak_rss_mb()
        try:
            result = dict(self._run_with_resource_budget(handler, job))
            peak_rss_mb = self._peak_rss_mb()
            elapsed_seconds = time.monotonic() - started_monotonic
            result["worker_resources"] = {
                "policy_id": self.resource_policy.policy_id,
                "elapsed_seconds": round(elapsed_seconds, 6),
                "peak_rss_mb": round(peak_rss_mb, 3),
                "max_job_minutes": self.resource_policy.max_job_minutes,
                "max_rss_mb": self.resource_policy.max_rss_mb,
                "max_concurrent_trials": self.resource_policy.max_concurrent_trials,
                "parquet_batch_rows": self.resource_policy.parquet_batch_rows,
            }
        except JobCancelledError as exc:
            cancelled_at = _utc_now()
            elapsed_seconds = time.monotonic() - started_monotonic
            peak_rss_mb = self._peak_rss_mb()
            self.repository.update_job(
                job.id, status="cancelled", updated_at=cancelled_at, error=str(exc)
            )
            self.repository.append_job_log(
                job.id,
                level="warning",
                message=(
                    f"{exc}; elapsed_seconds={elapsed_seconds:.3f}; "
                    f"peak_rss_mb={peak_rss_mb:.3f}"
                ),
                created_at=cancelled_at,
            )
            self.repository.append_event(
                AuditEvent(
                    id=None,
                    event_type="job.cancelled",
                    aggregate_type="job",
                    aggregate_id=job.id,
                    actor_type="system",
                    payload={
                        "job_type": job.job_type,
                        "reason": str(exc),
                        "partial_trials_preserved": True,
                    },
                    created_at=cancelled_at,
                )
            )
            raise
        except Exception as exc:
            failed_at = _utc_now()
            elapsed_seconds = time.monotonic() - started_monotonic
            peak_rss_mb = self._peak_rss_mb()
            self.repository.update_job(
                job.id, status="failed", updated_at=failed_at, error=str(exc)
            )
            self.repository.append_job_log(
                job.id,
                level="error",
                message=(
                    f"{exc}; elapsed_seconds={elapsed_seconds:.3f}; "
                    f"peak_rss_mb={peak_rss_mb:.3f}"
                ),
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
                for artifact_key in getattr(exc, "artifact_keys", []):
                    artifact_type = (
                        "manifest" if artifact_key.endswith("manifest.json") else "report"
                    )
                    self.service.record_artifact(
                        agent_run_id=agent_run_id,
                        artifact_type=artifact_type,
                        artifact_key=artifact_key,
                    )
            self.repository.append_event(
                AuditEvent(
                    id=None,
                    event_type="job.failed",
                    aggregate_type="job",
                    aggregate_id=job.id,
                    actor_type="system",
                    payload={
                        "job_type": job.job_type,
                        "error": str(exc),
                        "elapsed_seconds": round(elapsed_seconds, 6),
                        "peak_rss_mb": round(peak_rss_mb, 3),
                        "resource_policy_id": self.resource_policy.policy_id,
                    },
                    created_at=failed_at,
                )
            )
            raise

        completed_at = _utc_now()
        self._mark_batch_proposal_evaluated(job)
        self.repository.update_job(job.id, status="succeeded", updated_at=completed_at)
        self.repository.append_job_log(
            job.id,
            level="info",
            message=f"Handler completed; run_id={result.get('run_id')}",
            created_at=completed_at,
        )
        self.repository.append_job_log(
            job.id,
            level="info",
            message=(
                f"Resource usage: elapsed_seconds={result['worker_resources']['elapsed_seconds']}; "
                f"peak_rss_mb={result['worker_resources']['peak_rss_mb']}"
            ),
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
                    "worker_resources": result["worker_resources"],
                },
                created_at=completed_at,
            )
        )
        return result

    def _run_with_resource_budget(
        self, handler: JobHandler, job: Job
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + self.resource_policy.max_job_minutes * 60
        previous_handler = signal.getsignal(signal.SIGALRM)

        def enforce_limits(_signum: int, _frame: Any) -> None:
            if time.monotonic() >= deadline:
                raise TimeoutError("worker max_job_minutes exceeded")
            if (
                self.resource_policy.kill_on_memory_limit
                and self._peak_rss_mb() > self.resource_policy.max_rss_mb
            ):
                raise MemoryError("worker max_rss_mb exceeded")

        signal.signal(signal.SIGALRM, enforce_limits)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 0.1, 0.1)
        try:
            result = handler(job)
            if (
                self.resource_policy.kill_on_memory_limit
                and self._peak_rss_mb() > self.resource_policy.max_rss_mb
            ):
                raise MemoryError("worker max_rss_mb exceeded")
            return result
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            signal.signal(signal.SIGALRM, previous_handler)

    @staticmethod
    def _peak_rss_mb() -> float:
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            return rss / (1024 * 1024)
        return rss / 1024
