from __future__ import annotations

from datetime import datetime, timezone
import resource
import signal
import sys
import time
from typing import Any, Callable, Mapping

from quant_lab.application.services import (
    ResearchApplicationService,
    strategy_is_rejected,
)
from quant_lab.application.research_authorization import ResearchAuthorizationService
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
        self.authorizations = ResearchAuthorizationService(repository)
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

    def _session_id_for_job(self, job: Job) -> str | None:
        session_id = job.payload.get("session_id")
        if isinstance(session_id, str):
            return session_id
        agent_run_id = job.payload.get("agent_run_id")
        if isinstance(agent_run_id, str):
            return self.repository.get_agent_run(agent_run_id).session_id
        for key in ("strategy_version_id", "candidate_version_id"):
            version_id = job.payload.get(key)
            if isinstance(version_id, str):
                return self.repository.get_session_id_for_strategy_version(version_id)
        plan_id = job.payload.get("experiment_plan_id")
        if isinstance(plan_id, str):
            plan = self.repository.get_experiment_plan(plan_id)
            return self.repository.get_session_id_for_strategy_version(
                plan.baseline_version_id
            )
        return None

    def _record_job_handoff(self, job: Job, **handoff: Any) -> None:
        """Best-effort handoff recording that never masks the Job outcome."""
        try:
            session_id = self._session_id_for_job(job)
            if session_id is None:
                return
            self.service.record_handoff(session_id=session_id, **handoff)
        except Exception:
            # The Job status, result, and original failure remain authoritative even
            # if an optional handoff cannot be linked or persisted.
            return

    @staticmethod
    def _subject_id_for_job(job: Job) -> str:
        for key in (
            "subject_id",
            "strategy_version_id",
            "candidate_version_id",
            "baseline_version_id",
            "experiment_plan_id",
        ):
            value = job.payload.get(key)
            if isinstance(value, str):
                return value
        return job.id

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
        if job.job_type == "backtest" and job.payload.get("intent") == "candidate_smoke":
            version_id = job.payload.get("strategy_version_id")
            gate_id = job.payload.get("correctness_gate_result_id")
            if (
                job.payload.get("subject_id") != version_id
                or job.payload.get("confirmed_by_user") is not True
                or job.payload.get("pipeline_profile") != "smoke"
                or job.payload.get("locked_test_used") is not False
                or job.payload.get("run_fast_screen") is not False
                or not isinstance(version_id, str)
                or not isinstance(gate_id, str)
            ):
                raise ValueError("candidate smoke Worker gate is incomplete")
            version = self.repository.get_strategy_version(version_id)
            gate = self.repository.get_gate_evaluation(gate_id)
            if (
                version.status != "candidate"
                or not version.immutable
                or gate.subject_id != version_id
                or gate.gate_name != "correctness"
                or gate.status != "passed"
                or gate.profile_id != "smoke"
            ):
                raise ValueError("candidate smoke Worker gate rejected the Job")
        if job.job_type == "backtest" and job.payload.get("intent") == "candidate_fast_screen":
            version_id = job.payload.get("strategy_version_id")
            gate_id = job.payload.get("correctness_gate_result_id")
            if (
                job.payload.get("subject_id") != version_id
                or job.payload.get("confirmed_by_user") is not True
                or job.payload.get("pipeline_profile") != "fast_screen"
                or job.payload.get("locked_test_used") is not False
                or job.payload.get("run_viability") is not False
                or not isinstance(version_id, str)
                or not isinstance(gate_id, str)
            ):
                raise ValueError("candidate fast-screen Worker gate is incomplete")
            version = self.repository.get_strategy_version(version_id)
            gate = self.repository.get_gate_evaluation(gate_id)
            if (
                version.status != "candidate"
                or not version.immutable
                or gate.subject_id != version_id
                or gate.gate_name != "correctness"
                or gate.status != "passed"
            ):
                raise ValueError("candidate fast-screen Worker gate rejected the Job")
        if job.job_type == "research_diagnostic":
            subject_id = job.payload.get("subject_id")
            authorization_id = job.payload.get("authorization_id")
            if (
                not isinstance(subject_id, str)
                or not isinstance(authorization_id, str)
                or job.payload.get("locked_test_used") is not False
            ):
                raise ValueError("research diagnostic Worker gate is incomplete")
            if not strategy_is_rejected(self.repository, subject_id):
                raise ValueError("research diagnostics cannot alter a non-rejected strategy")
            self.authorizations.assert_scope_covers(
                authorization_id,
                subject_id=subject_id,
                stages=(
                    "loss_attribution",
                    "regime_diagnostic",
                    "component_hypothesis_generation",
                ),
            )

        agent_run_id = job.payload.get("agent_run_id")
        tool_name = {
            "parameter_search": "run_parameter_search",
            "regime_validation": "record_regime_validation",
            "correctness_diagnostic": "run_correctness_diagnostic",
            "research_diagnostic": "generate_report",
            "report": "generate_report",
        }.get(job.job_type, "run_backtest")
        now = _utc_now()
        if isinstance(agent_run_id, str):
            self.repository.update_agent_run_status(agent_run_id, status="running")
        self.repository.update_job(job.id, status="running", updated_at=now)
        self.repository.append_job_log(
            job.id,
            level="info",
            message=f"Allowlisted {job.job_type} handler started.",
            created_at=now,
        )
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
            self._record_job_handoff(
                job,
                agent_run_id=agent_run_id if isinstance(agent_run_id, str) else None,
                subject_id=self._subject_id_for_job(job),
                status="completed_scope",
                stop_reason_code="job_cancelled_at_safe_boundary",
                stop_reason_text=str(exc),
                completed_actions=("停止 Job", "保留已完成 Trial 与失败证据"),
                not_started_actions=("恢复未完成 Trial", "候选晋升"),
                user_action_required=False,
                required_user_action=None,
                next_recommended_action=f"如需继续，显式重试 Job {job.id} 的未完成组合。",
                safe_to_continue=True,
                actor_type="system",
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
            dependency_blocked = job.job_type == "correctness_diagnostic"
            self._record_job_handoff(
                job,
                agent_run_id=agent_run_id if isinstance(agent_run_id, str) else None,
                subject_id=self._subject_id_for_job(job),
                status="blocked_dependency" if dependency_blocked else "failed",
                stop_reason_code=(
                    "optional_freqtrade_unavailable"
                    if dependency_blocked
                    else "worker_job_failed"
                ),
                stop_reason_text=str(exc),
                completed_actions=("保存 Job 失败状态", "保留日志、manifest 与已有 Trial"),
                not_started_actions=("依赖该 Job 的后续 Gate", "候选晋升"),
                user_action_required=False,
                required_user_action=None,
                next_recommended_action=(
                    "继续使用 Native correctness 能力，或稍后安装/修复可选 Freqtrade external engine。"
                    if dependency_blocked
                    else "审阅失败证据后决定修复依赖或停止当前研究分支。"
                ),
                safe_to_continue=dependency_blocked,
                actor_type="system",
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
        if job.job_type == "research_diagnostic":
            hypothesis_ids = tuple(result.get("component_hypothesis_ids", ()))
            next_action = (
                "审阅诊断报告与 ComponentHypothesis "
                + ", ".join(str(item) for item in hypothesis_ids)
                + "；若要继续，另行创建并批准一次有 Trial/时间预算的 Diagnostic Batch。"
            )
            handoff_values = {
                "stop_reason_code": "post_viability_diagnostics_scope_completed",
                "stop_reason_text": (
                    "已复用保存的 fast_screen trades/signals/metrics 完成失败归因、"
                    "ex-ante Regime screening 和确定性组件假设；授权不包含组件 Trial。"
                ),
                "completed_actions": (
                    "完成 artifact-only loss attribution，未重跑策略",
                    "完成 closed-1h ex-ante regime_diagnostic，仅形成 screening 证据",
                    f"创建 {len(hypothesis_ids)} 个 deterministic ComponentHypothesis 草案",
                    "保存 Run Bundle、Report、资源指标和 append-only audit",
                ),
                "not_started_actions": (
                    "组件消融 Batch Trials 或参数搜索",
                    "Walk-forward/multi-period、正式 Regime validation",
                    "locked test、full stress、dry-run、production 或 live trade",
                ),
                "next_recommended_action": next_action,
            }
        else:
            handoff_values = {
                "stop_reason_code": "worker_job_scope_completed",
                "stop_reason_text": (
                    f"Job {job.id} 已完成已批准范围，未自动启动后续阶段。"
                ),
                "completed_actions": (
                    f"完成 {job.job_type} Job",
                    "保存结果、资源指标与审计事件",
                ),
                "not_started_actions": (
                    "自动接受策略修改",
                    "自动晋升",
                    "live trade",
                ),
                "next_recommended_action": (
                    "审阅结果和 Gate 证据，再决定是否批准下一阶段。"
                ),
            }
        self._record_job_handoff(
            job,
            agent_run_id=agent_run_id if isinstance(agent_run_id, str) else None,
            subject_id=self._subject_id_for_job(job),
            status="completed_scope",
            user_action_required=False,
            required_user_action=None,
            safe_to_continue=True,
            actor_type="system",
            **handoff_values,
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
