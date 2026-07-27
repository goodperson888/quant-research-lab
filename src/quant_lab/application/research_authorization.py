from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from quant_lab.domain.errors import ApprovalRequiredError, ConflictError, GatePolicyError
from quant_lab.domain.models import (
    AuditEvent,
    ResearchAuthorization,
    ResearchAuthorizationStage,
)
from quant_lab.domain.repositories import ProductRepository

from .services import (
    ResearchApplicationService,
    new_id,
    strategy_is_rejected,
    utc_now,
)


CORE_STAGE_ORDER = ("correctness", "smoke", "fast_screen", "viability")
DIAGNOSTIC_STAGE_ORDER = (
    "loss_attribution",
    "regime_diagnostic",
    "component_hypothesis_generation",
)
ALLOWED_AUTHORIZATION_STAGES = frozenset(CORE_STAGE_ORDER + DIAGNOSTIC_STAGE_ORDER)


@dataclass(frozen=True, slots=True)
class ResearchAuthorizationTemplate:
    template_id: str
    allowed_stages: tuple[str, ...]
    auto_continue: bool
    max_cost_usdt: float
    max_time_minutes: int
    max_trials: int
    locked_test_allowed: bool
    stop_conditions: tuple[str, ...]
    expires_after_minutes: int


class ResearchAuthorizationPolicyReader:
    def __init__(self, project_root: Path) -> None:
        self.path = project_root / "configs/research_authorizations/guided-to-viability.yaml"

    def read(self) -> ResearchAuthorizationTemplate:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise GatePolicyError("invalid research authorization policy")
        scope = raw.get("scope", {})
        template = ResearchAuthorizationTemplate(
            template_id=str(raw["template_id"]),
            allowed_stages=tuple(str(item) for item in scope["allowed_stages"]),
            auto_continue=bool(scope["auto_continue"]),
            max_cost_usdt=float(scope["max_cost_usdt"]),
            max_time_minutes=int(scope["max_time_minutes"]),
            max_trials=int(scope["max_trials"]),
            locked_test_allowed=bool(scope["locked_test_allowed"]),
            stop_conditions=tuple(str(item) for item in scope["stop_conditions"]),
            expires_after_minutes=int(scope["expires_after_minutes"]),
        )
        _validate_stage_sequence(template.allowed_stages)
        if template.locked_test_allowed:
            raise GatePolicyError("guided-to-viability policy must forbid locked test")
        return template


class ResearchAuthorizationService:
    """Persist and enforce one explicit approval across safe pipeline boundaries."""

    def __init__(self, repository: ProductRepository) -> None:
        self.repository = repository
        self.repository.initialize()

    def create(
        self,
        *,
        subject_id: str,
        session_id: str,
        allowed_stages: tuple[str, ...],
        auto_continue: bool,
        max_cost_usdt: float,
        max_time_minutes: int,
        max_trials: int,
        locked_test_allowed: bool,
        stop_conditions: tuple[str, ...],
        expires_at: str,
        confirmed_by_user: bool,
        approved_by: str = "user",
    ) -> ResearchAuthorization:
        if not confirmed_by_user or approved_by != "user":
            raise ApprovalRequiredError(
                "research authorization requires explicit user approval"
            )
        _validate_stage_sequence(allowed_stages)
        if locked_test_allowed or "locked_test" in allowed_stages:
            raise GatePolicyError("research-to-viability authorization forbids locked test")
        version = self.repository.get_strategy_version(subject_id)
        actual_session = self.repository.get_session_id_for_strategy_version(subject_id)
        if actual_session != session_id:
            raise ConflictError("authorization subject belongs to another session")
        if strategy_is_rejected(self.repository, version.id) and any(
            stage in CORE_STAGE_ORDER for stage in allowed_stages
        ):
            raise GatePolicyError(
                "a rejected strategy may authorize diagnostics, not rerun core stages"
            )
        expiry = _parse_utc(expires_at)
        if expiry <= datetime.now(timezone.utc):
            raise GatePolicyError("research authorization expiry must be in the future")
        authorization = ResearchAuthorization(
            id=new_id("authorization"),
            subject_id=subject_id,
            session_id=session_id,
            allowed_stages=allowed_stages,
            auto_continue=auto_continue,
            max_cost_usdt=max_cost_usdt,
            max_time_minutes=max_time_minutes,
            max_trials=max_trials,
            locked_test_allowed=False,
            stop_conditions=stop_conditions,
            expires_at=expiry.isoformat(),
            approved_by="user",
            status="active",
            created_at=utc_now(),
        )
        created = self.repository.create_research_authorization(authorization)
        self._audit(
            event_type="research_authorization.approved",
            aggregate_id=created.id,
            payload={
                "subject_id": created.subject_id,
                "session_id": created.session_id,
                "allowed_stages": created.allowed_stages,
                "auto_continue": created.auto_continue,
                "locked_test_allowed": False,
                "budgets": {
                    "max_cost_usdt": created.max_cost_usdt,
                    "max_time_minutes": created.max_time_minutes,
                    "max_trials": created.max_trials,
                },
            },
            actor_type="user",
        )
        return created

    def get(self, authorization_id: str) -> ResearchAuthorization:
        return self.repository.get_research_authorization(authorization_id)

    def list(
        self, *, session_id: str | None = None, subject_id: str | None = None
    ) -> tuple[ResearchAuthorization, ...]:
        return tuple(
            self.repository.list_research_authorizations(
                session_id=session_id, subject_id=subject_id
            )
        )

    def stages(self, authorization_id: str) -> tuple[ResearchAuthorizationStage, ...]:
        return tuple(self.repository.list_research_authorization_stages(authorization_id))

    def assert_scope_covers(
        self,
        authorization_id: str,
        *,
        subject_id: str,
        stages: tuple[str, ...],
    ) -> ResearchAuthorization:
        authorization = self.repository.get_research_authorization(authorization_id)
        if authorization.subject_id != subject_id:
            raise ApprovalRequiredError("authorization does not cover this subject")
        outside = [stage for stage in stages if stage not in authorization.allowed_stages]
        if outside:
            raise ApprovalRequiredError(
                "stages are outside the approved research scope: "
                + ", ".join(outside)
            )
        if authorization.locked_test_allowed or "locked_test" in stages:
            raise GatePolicyError("locked test is not allowed by this authorization")
        if authorization.status != "active":
            raise ApprovalRequiredError(
                f"research authorization is not active: {authorization.status}"
            )
        if _parse_utc(authorization.expires_at) <= datetime.now(timezone.utc):
            self.repository.update_research_authorization_usage(
                authorization.id,
                status="expired",
                used_cost_usdt=authorization.used_cost_usdt,
                used_time_minutes=authorization.used_time_minutes,
                used_trials=authorization.used_trials,
            )
            raise ApprovalRequiredError("research authorization has expired")
        return authorization

    def assert_stage_allowed(
        self, authorization_id: str, *, subject_id: str, stage: str
    ) -> ResearchAuthorization:
        authorization = self.assert_scope_covers(
            authorization_id,
            subject_id=subject_id,
            stages=(stage,),
        )
        stage_events = self.repository.list_research_authorization_stages(
            authorization.id
        )
        terminal_failures = [
            item for item in stage_events if item.status in {"failed", "blocked"}
        ]
        diagnostic_after_failed_viability = (
            stage in DIAGNOSTIC_STAGE_ORDER
            and terminal_failures
            and all(item.stage == "viability" for item in terminal_failures)
        )
        if terminal_failures and not diagnostic_after_failed_viability:
            raise GatePolicyError(
                f"authorization stopped at {terminal_failures[-1].stage}: "
                f"{terminal_failures[-1].reason}"
            )
        completed = {
            item.stage
            for item in stage_events
            if item.status in {"passed", "failed", "blocked", "skipped"}
        }
        index = authorization.allowed_stages.index(stage)
        missing = [
            item
            for item in authorization.allowed_stages[:index]
            if item not in completed
        ]
        if missing:
            raise GatePolicyError(
                "authorization stage prerequisites are incomplete: " + ", ".join(missing)
            )
        return authorization

    def record_stage(
        self,
        *,
        authorization_id: str,
        subject_id: str,
        stage: str,
        status: str,
        evidence_refs: tuple[str, ...] = (),
        reason: str,
        elapsed_minutes: float = 0.0,
        cost_usdt: float = 0.0,
        trials_used: int = 0,
    ) -> ResearchAuthorizationStage:
        authorization = self.assert_stage_allowed(
            authorization_id, subject_id=subject_id, stage=stage
        )
        if status not in {"running", "passed", "failed", "blocked", "skipped"}:
            raise GatePolicyError("invalid authorization stage status")
        previous = [
            item
            for item in self.repository.list_research_authorization_stages(
                authorization_id
            )
            if item.stage == stage and item.status in {"passed", "failed", "blocked"}
        ]
        if previous:
            raise ConflictError("authorization stage already has immutable terminal evidence")
        used_cost = authorization.used_cost_usdt + cost_usdt
        used_time = authorization.used_time_minutes + elapsed_minutes
        used_trials = authorization.used_trials + trials_used
        budget_failure = None
        if used_cost > authorization.max_cost_usdt:
            budget_failure = "authorization cost budget exceeded"
        elif used_time > authorization.max_time_minutes:
            budget_failure = "authorization time budget exceeded"
        elif used_trials > authorization.max_trials:
            budget_failure = "authorization Trial budget exceeded"
        if budget_failure:
            status = "blocked"
            reason = budget_failure

        item = self.repository.create_research_authorization_stage(
            ResearchAuthorizationStage(
                id=new_id("authorization_stage"),
                authorization_id=authorization.id,
                stage=stage,
                status=status,  # type: ignore[arg-type]
                evidence_refs=evidence_refs,
                reason=reason,
                elapsed_minutes=elapsed_minutes,
                cost_usdt=cost_usdt,
                trials_used=trials_used,
                created_at=utc_now(),
            )
        )
        terminal_status = authorization.status
        if status in {"failed", "blocked"}:
            diagnostics_follow = (
                stage == "viability"
                and status == "failed"
                and any(
                    item in authorization.allowed_stages
                    for item in DIAGNOSTIC_STAGE_ORDER
                )
            )
            terminal_status = "active" if diagnostics_follow else "stopped"
        if status in {"passed", "failed", "blocked", "skipped"}:
            terminal = {
                value.stage
                for value in self.repository.list_research_authorization_stages(
                    authorization.id
                )
                if value.status in {"passed", "failed", "blocked", "skipped"}
            }
            failures = [
                value
                for value in self.repository.list_research_authorization_stages(
                    authorization.id
                )
                if value.status in {"failed", "blocked"}
            ]
            allowed_terminal_failure = failures and all(
                value.stage == "viability" and value.status == "failed"
                for value in failures
            )
            if all(
                stage_name in terminal for stage_name in authorization.allowed_stages
            ) and (not failures or allowed_terminal_failure):
                terminal_status = "completed"
        self.repository.update_research_authorization_usage(
            authorization.id,
            status=terminal_status,
            used_cost_usdt=used_cost,
            used_time_minutes=used_time,
            used_trials=used_trials,
        )
        self._audit(
            event_type="research_authorization.stage_recorded",
            aggregate_id=authorization.id,
            payload=asdict(item),
            actor_type="system",
        )
        return item

    def next_stage(self, authorization_id: str) -> str | None:
        authorization = self.repository.get_research_authorization(authorization_id)
        if authorization.status != "active":
            return None
        completed = {
            item.stage
            for item in self.repository.list_research_authorization_stages(
                authorization_id
            )
            if item.status in {"passed", "failed", "blocked", "skipped"}
        }
        return next(
            (
                stage
                for stage in authorization.allowed_stages
                if stage not in completed
            ),
            None,
        )

    def _audit(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        actor_type: str,
    ) -> None:
        self.repository.append_event(
            AuditEvent(
                id=None,
                event_type=event_type,
                aggregate_type="research_authorization",
                aggregate_id=aggregate_id,
                actor_type=actor_type,  # type: ignore[arg-type]
                payload=dict(payload),
                created_at=utc_now(),
            )
        )


StageExecutor = Callable[[str, ResearchAuthorization], Mapping[str, Any]]


class AuthorizedPipelineOrchestrator:
    """Worker-side deterministic coordinator; HTTP only creates the authorization."""

    def __init__(
        self,
        authorization_service: ResearchAuthorizationService,
        *,
        stage_executor: StageExecutor,
    ) -> None:
        self.authorizations = authorization_service
        self.stage_executor = stage_executor

    def run(self, authorization_id: str) -> Mapping[str, Any]:
        authorization = self.authorizations.get(authorization_id)
        completed: list[str] = []
        try:
            while authorization.auto_continue:
                stage = self.authorizations.next_stage(authorization.id)
                if stage is None:
                    break
                result = dict(self.stage_executor(stage, authorization))
                if stage == "viability" and result.get("reran_backtest") is True:
                    raise GatePolicyError(
                        "viability must evaluate saved fast-screen metrics without rerun"
                    )
                status = str(result.get("status", "failed"))
                self.authorizations.record_stage(
                    authorization_id=authorization.id,
                    subject_id=authorization.subject_id,
                    stage=stage,
                    status=status,
                    evidence_refs=tuple(result.get("evidence_refs", ())),
                    reason=str(result.get("reason", "stage completed")),
                    elapsed_minutes=float(result.get("elapsed_minutes", 0.0)),
                    cost_usdt=float(result.get("cost_usdt", 0.0)),
                    trials_used=int(result.get("trials_used", 0)),
                )
                completed.append(stage)
                diagnostics_follow = (
                    stage == "viability"
                    and status == "failed"
                    and any(
                        item in authorization.allowed_stages
                        for item in DIAGNOSTIC_STAGE_ORDER
                    )
                )
                if status != "passed" and not diagnostics_follow:
                    break
                authorization = self.authorizations.get(authorization.id)
        except Exception as exc:
            ResearchApplicationService(
                self.authorizations.repository
            ).record_handoff(
                session_id=authorization.session_id,
                subject_id=authorization.subject_id,
                status="failed",
                stop_reason_code="authorized_pipeline_execution_failed",
                stop_reason_text=str(exc),
                completed_actions=tuple(f"完成 {item}" for item in completed),
                not_started_actions=tuple(
                    item
                    for item in authorization.allowed_stages
                    if item not in completed
                ),
                user_action_required=False,
                required_user_action=None,
                next_recommended_action=(
                    "审阅失败 Job/阶段证据，修复确定性执行依赖后使用显式 retry。"
                ),
                safe_to_continue=False,
                actor_type="system",
            )
            raise
        final = self.authorizations.get(authorization.id)
        stage_results = self.authorizations.stages(final.id)
        failures = [item for item in stage_results if item.status in {"failed", "blocked"}]
        viability_failed = any(
            item.stage == "viability" and item.status == "failed"
            for item in failures
        )
        stopped = final.status == "stopped"
        ResearchApplicationService(self.authorizations.repository).record_handoff(
            session_id=final.session_id,
            subject_id=final.subject_id,
            status="gate_failed" if stopped else "completed_scope",
            stop_reason_code=(
                "authorized_pipeline_gate_failed"
                if stopped
                else (
                    "authorized_pipeline_completed_after_viability_failure_diagnostics"
                    if viability_failed
                    else "authorized_pipeline_scope_completed"
                )
            ),
            stop_reason_text=(
                f"授权路径在 {failures[-1].stage} 停止：{failures[-1].reason}"
                if stopped and failures
                else (
                    "Viability 失败后已完成授权内廉价诊断；完整策略未恢复。"
                    if viability_failed
                    else "已完成 ResearchAuthorization 的全部批准范围。"
                )
            ),
            completed_actions=tuple(f"完成 {item}" for item in completed),
            not_started_actions=tuple(
                item for item in final.allowed_stages if item not in completed
            )
            + (
                "locked test、参数/组件 Batch、dry-run 和 live trade",
            ),
            user_action_required=False,
            required_user_action=None,
            next_recommended_action=(
                "审阅廉价诊断与组件假设；如需 Trials，另行批准精确 Diagnostic Batch 预算。"
                if viability_failed
                else (
                    "停止当前完整策略分支；只可审阅失败证据或另建假设。"
                    if stopped
                    else "审阅 Gate 与 Run Bundle，再决定是否创建下一阶段 Proposal。"
                )
            ),
            safe_to_continue=not stopped or viability_failed,
            actor_type="system",
        )
        return {
            "authorization_id": final.id,
            "subject_id": final.subject_id,
            "status": final.status,
            "completed_stages": completed,
            "next_stage": self.authorizations.next_stage(final.id),
            "locked_test_used": False,
        }


def _validate_stage_sequence(stages: tuple[str, ...]) -> None:
    if not stages:
        raise GatePolicyError("research authorization requires stages")
    unknown = sorted(set(stages) - ALLOWED_AUTHORIZATION_STAGES)
    if unknown:
        raise GatePolicyError("unsupported research authorization stages: " + ", ".join(unknown))
    canonical = CORE_STAGE_ORDER + DIAGNOSTIC_STAGE_ORDER
    positions = [canonical.index(stage) for stage in stages]
    if positions != sorted(positions):
        raise GatePolicyError("research authorization stages are out of order")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise GatePolicyError("research authorization expiry must include timezone")
    return parsed.astimezone(timezone.utc)
