from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from quant_lab.application.research_authorization import (
    CORE_STAGE_ORDER,
    ResearchAuthorizationPolicyReader,
    ResearchAuthorizationService,
)
from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.errors import ApprovalRequiredError, NotFoundError
from quant_lab.domain.models import (
    AgentProviderKind,
    AssistantEntryMode,
    ExecutionTargetKind,
)
from quant_lab.infrastructure.offline_licensing import OfflineLicenseService
from quant_lab.infrastructure.policy_readers import ResearchBudgetPolicyReader
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.paths import app_database_path, project_root


T = TypeVar("T")


class McpGatewayError(RuntimeError):
    """A safe, user-facing MCP boundary error."""


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _budget_policy(root: Path) -> dict[str, Any] | None:
    path = root / "configs" / "research_budgets" / "default.yaml"
    if not path.is_file():
        return None
    policy = ResearchBudgetPolicyReader(root).read()
    return {
        "max_hypotheses": policy.max_hypotheses,
        "max_trials_total": policy.max_trials_total,
        "max_compute_minutes": policy.max_compute_minutes,
        "max_locked_test_uses": policy.max_locked_test_uses,
        "require_user_approval_for_new_hypothesis": (
            policy.require_user_approval_for_new_hypothesis
        ),
    }


class McpResearchGateway:
    """Expose a small, audited research subset to local MCP clients.

    The adapter calls the same application services as FastAPI and the CLI. It does
    not expose shell execution, arbitrary paths, secrets, live trading, locked-test
    access or automatic production promotion.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        database_path: Path | None = None,
        license_service: OfflineLicenseService | None = None,
    ) -> None:
        self.root = (root or project_root()).resolve()
        self.database_path = (database_path or app_database_path()).resolve()
        try:
            self.database_path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("MCP product database must remain inside project root") from exc
        self.repository = SQLiteProductRepository(self.database_path)
        self.service = ResearchApplicationService(
            self.repository,
            budget_policy=_budget_policy(self.root),
        )
        self.authorization_service = ResearchAuthorizationService(self.repository)
        self.authorization_policy = ResearchAuthorizationPolicyReader(self.root)
        self.license_service = license_service or OfflineLicenseService.from_environment(
            self.root
        )

    def _license_status(self) -> Any:
        return self.license_service.status()

    def _require_local_ai_write(self) -> None:
        current = self._license_status()
        if not current.write_allowed:
            raise McpGatewayError(current.message)
        if (
            current.enforcement_mode == "commercial_required"
            and "all" not in current.features
            and "local_ai" not in current.features
        ):
            raise McpGatewayError("当前许可证不包含“本地 AI 直连”功能。")

    def _new_agent_run(
        self,
        *,
        session_id: str,
        subject_id: str | None,
        plan_summary: str,
    ) -> Any:
        run = self.service.create_agent_run(
            session_id=session_id,
            agent_name="本地 AI · MCP",
            agent_provider=AgentProviderKind.EXTERNAL_LOCAL_AGENT,
            execution_target=ExecutionTargetKind.LOCAL_RUNTIME,
            plan_summary=plan_summary,
            subject_id=subject_id,
            task_type="research_orchestration",
        )
        return self.service.update_agent_run_status(
            agent_run_id=run.id,
            status="running",
        )

    def _record_success(
        self,
        *,
        run: Any,
        tool_name: str,
        sanitized_input: Mapping[str, Any],
        sanitized_output: Mapping[str, Any],
        final_status: str,
    ) -> Any:
        self.service.record_tool_call(
            agent_run_id=run.id,
            tool_name=tool_name,
            sanitized_input=sanitized_input,
            sanitized_output=sanitized_output,
            status="completed",
        )
        return self.service.update_agent_run_status(
            agent_run_id=run.id,
            status=final_status,
        )

    def _record_failure(
        self,
        *,
        run: Any,
        tool_name: str,
        sanitized_input: Mapping[str, Any],
        error: BaseException,
    ) -> None:
        self.service.record_tool_call(
            agent_run_id=run.id,
            tool_name=tool_name,
            sanitized_input=sanitized_input,
            sanitized_output={"error_type": error.__class__.__name__},
            status="failed",
        )
        self.service.update_agent_run_status(
            agent_run_id=run.id,
            status="failed",
            error=str(error)[:1200],
        )

    def _execute(
        self,
        *,
        run: Any,
        tool_name: str,
        sanitized_input: Mapping[str, Any],
        action: Callable[[], T],
        result_summary: Callable[[T], Mapping[str, Any]],
        final_status: str = "waiting_approval",
    ) -> tuple[T, Any]:
        try:
            result = action()
            completed_run = self._record_success(
                run=run,
                tool_name=tool_name,
                sanitized_input=sanitized_input,
                sanitized_output=result_summary(result),
                final_status=final_status,
            )
            return result, completed_run
        except BaseException as error:
            self._record_failure(
                run=run,
                tool_name=tool_name,
                sanitized_input=sanitized_input,
                error=error,
            )
            raise

    def status(self) -> dict[str, Any]:
        current = self._license_status()
        feature_allowed = (
            current.enforcement_mode == "development_disabled"
            or "all" in current.features
            or "local_ai" in current.features
        )
        return {
            "product": "Quant Research Lab",
            "transport": "stdio",
            "database_ready": self.database_path.is_file(),
            "data_home": str(self.root),
            "license": _jsonable(current),
            "local_ai_feature_allowed": feature_allowed,
            "write_available": bool(current.write_allowed and feature_allowed),
            "safety": {
                "live_trading": False,
                "arbitrary_shell": False,
                "credential_access": False,
                "automatic_production_promotion": False,
                "locked_test_without_separate_approval": False,
            },
        }
    def list_research_sessions(self, *, limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise McpGatewayError("limit 必须在 1 到 100 之间。")
        sessions = list(self.service.list_research_sessions())[:limit]
        return {
            "count": len(sessions),
            "sessions": [_jsonable(item) for item in sessions],
        }

    def get_research_session(self, *, session_id: str) -> dict[str, Any]:
        session = self.service.get_research_session(session_id)
        drafts = list(self.service.list_strategy_drafts(session_id))
        versions: list[Any] = []
        for draft in drafts:
            versions.extend(self.service.list_strategy_versions(draft.id))
        jobs = [
            item
            for item in self.service.list_jobs()
            if item.payload.get("session_id") == session_id
        ]
        try:
            handoff: Any = self.service.get_latest_session_handoff(session_id)
        except NotFoundError:
            handoff = None
        return {
            "session": _jsonable(session),
            "messages": _jsonable(self.service.list_messages(session_id)),
            "drafts": _jsonable(drafts),
            "strategy_versions": _jsonable(versions),
            "agent_runs": _jsonable(self.service.list_session_agent_runs(session_id)),
            "budget": _jsonable(self.service.get_research_budget(session_id)),
            "jobs": _jsonable(jobs),
            "latest_handoff": _jsonable(handoff),
        }

    def get_job(self, *, job_id: str) -> dict[str, Any]:
        return {
            "job": _jsonable(self.service.get_job(job_id)),
            "logs": _jsonable(self.service.list_job_logs(job_id)),
        }

    def start_strategy_research(
        self,
        *,
        title: str,
        strategy_text: str,
        source_type: str = "natural_language",
        source_name: str | None = None,
    ) -> dict[str, Any]:
        self._require_local_ai_write()
        if source_type not in {"natural_language", "pine", "file"}:
            raise McpGatewayError("source_type 只能是 natural_language、pine 或 file。")
        if not title.strip() or not strategy_text.strip():
            raise McpGatewayError("策略标题和策略原文不能为空。")
        session = self.service.create_research_session(
            title=title,
            assistant_entry_mode=AssistantEntryMode.EXTERNAL_AGENT_DIRECT,
        )
        run = self._new_agent_run(
            session_id=session.id,
            subject_id=None,
            plan_summary="保存用户原始策略并建立待形式化研究会话",
        )

        def action() -> Any:
            self.service.add_user_message(
                session_id=session.id,
                content=strategy_text,
            )
            return self.service.create_strategy_intake(
                session_id=session.id,
                source_type=source_type,
                source_name=source_name,
                raw_content=strategy_text,
            )

        draft, completed_run = self._execute(
            run=run,
            tool_name="intake_strategy",
            sanitized_input={
                "session_id": session.id,
                "source_type": source_type,
                "source_name": source_name,
                "strategy_text_chars": len(strategy_text),
            },
            action=action,
            result_summary=lambda value: {
                "draft_id": value.id,
                "status": value.status,
            },
        )
        return {
            "session": _jsonable(session),
            "draft": _jsonable(draft),
            "agent_run": _jsonable(completed_run),
            "next_action": (
                "分析原始策略、列出歧义，然后调用 propose_strategy_formalization；"
                "不要直接冻结 Baseline。"
            ),
        }

    def propose_strategy_formalization(
        self,
        *,
        session_id: str,
        draft_id: str,
        structured_content: Mapping[str, Any],
        assistant_message: str,
    ) -> dict[str, Any]:
        self._require_local_ai_write()
        draft = self.repository.get_draft(draft_id)
        if draft.session_id != session_id:
            raise McpGatewayError("Draft 不属于指定研究会话。")
        if not structured_content:
            raise McpGatewayError("结构化策略提案不能为空。")
        run = self._new_agent_run(
            session_id=session_id,
            subject_id=draft_id,
            plan_summary="提交本地 AI 生成的结构化策略提案，等待用户确认",
        )
        proposed, completed_run = self._execute(
            run=run,
            tool_name="formalize_strategy",
            sanitized_input={
                "session_id": session_id,
                "draft_id": draft_id,
                "structured_keys": sorted(str(key) for key in structured_content),
            },
            action=lambda: self.service.propose_strategy_formalization(
                draft_id=draft_id,
                structured_content=structured_content,
                agent_run_id=run.id,
                assistant_message=assistant_message,
                actor_type="external_agent",
            ),
            result_summary=lambda value: {
                "draft_id": value.id,
                "status": value.status,
                "requires_user_confirmation": True,
            },
        )
        return {
            "draft": _jsonable(proposed),
            "agent_run": _jsonable(completed_run),
            "next_action": (
                "向用户解释关键规则和歧义；只有用户明确确认后，"
                "才能调用 confirm_strategy_formalization。"
            ),
        }

    def confirm_strategy_formalization(
        self,
        *,
        session_id: str,
        draft_id: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise ApprovalRequiredError("确认结构化策略需要用户明确同意。")
        self._require_local_ai_write()
        draft = self.repository.get_draft(draft_id)
        if draft.session_id != session_id:
            raise McpGatewayError("Draft 不属于指定研究会话。")
        if not draft.structured_content:
            raise McpGatewayError("当前 Draft 尚无可确认的结构化策略提案。")
        run = self._new_agent_run(
            session_id=session_id,
            subject_id=draft_id,
            plan_summary="记录用户对结构化策略规则的明确确认",
        )
        confirmed, completed_run = self._execute(
            run=run,
            tool_name="formalize_strategy",
            sanitized_input={
                "session_id": session_id,
                "draft_id": draft_id,
                "confirmed_by_user": True,
            },
            action=lambda: self.service.formalize_strategy(
                draft_id=draft_id,
                structured_content=draft.structured_content,
                confirmed_by_user=True,
            ),
            result_summary=lambda value: {
                "draft_id": value.id,
                "status": value.status,
                "baseline_frozen": False,
            },
        )
        return {
            "draft": _jsonable(confirmed),
            "agent_run": _jsonable(completed_run),
            "next_action": (
                "Baseline 仍未冻结。只有用户再次明确批准冻结当前 Draft，"
                "才能调用 freeze_strategy_baseline。"
            ),
        }

    def freeze_strategy_baseline(
        self,
        *,
        session_id: str,
        draft_id: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise ApprovalRequiredError("冻结 Baseline 需要用户明确同意。")
        self._require_local_ai_write()
        draft = self.repository.get_draft(draft_id)
        if draft.session_id != session_id:
            raise McpGatewayError("Draft 不属于指定研究会话。")
        run = self._new_agent_run(
            session_id=session_id,
            subject_id=draft_id,
            plan_summary="按用户精确批准冻结不可覆盖的 Baseline v0",
        )
        baseline, completed_run = self._execute(
            run=run,
            tool_name="freeze_baseline",
            sanitized_input={
                "session_id": session_id,
                "draft_id": draft_id,
                "confirmed_by_user": True,
            },
            action=lambda: self.service.freeze_baseline(
                draft_id=draft_id,
                confirmed_by_user=True,
            ),
            result_summary=lambda value: {
                "strategy_version_id": value.id,
                "version": value.version,
                "immutable": value.immutable,
            },
        )
        return {
            "baseline": _jsonable(baseline),
            "agent_run": _jsonable(completed_run),
            "next_action": (
                "Baseline 已冻结但尚未开始回测。向用户展示 exact subject_id，"
                "获得授权后再调用 authorize_research_to_viability。"
            ),
        }

    def authorize_research_to_viability(
        self,
        *,
        session_id: str,
        baseline_version_id: str,
        user_confirmed: bool,
        include_failure_diagnostics: bool = True,
        max_time_minutes: int | None = None,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise ApprovalRequiredError("启动研究到 Viability 需要用户明确同意。")
        self._require_local_ai_write()
        actual_session = self.repository.get_session_id_for_strategy_version(
            baseline_version_id
        )
        if actual_session != session_id:
            raise McpGatewayError("Baseline 不属于指定研究会话。")
        template = self.authorization_policy.read()
        resolved_minutes = (
            template.max_time_minutes
            if max_time_minutes is None
            else max_time_minutes
        )
        if not 1 <= resolved_minutes <= template.max_time_minutes:
            raise McpGatewayError(
                f"max_time_minutes 必须在 1 到 {template.max_time_minutes} 之间。"
            )
        allowed_stages = (
            template.allowed_stages
            if include_failure_diagnostics
            else CORE_STAGE_ORDER
        )
        mcp_run = self._new_agent_run(
            session_id=session_id,
            subject_id=baseline_version_id,
            plan_summary="按用户批准创建有边界的研究授权到 Viability",
        )

        try:
            authorization = self.authorization_service.create(
                subject_id=baseline_version_id,
                session_id=session_id,
                allowed_stages=tuple(allowed_stages),
                auto_continue=template.auto_continue,
                max_cost_usdt=template.max_cost_usdt,
                max_time_minutes=resolved_minutes,
                max_trials=template.max_trials,
                locked_test_allowed=False,
                stop_conditions=template.stop_conditions,
                expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(minutes=template.expires_after_minutes)
                ).isoformat(),
                confirmed_by_user=True,
            )
            self._record_success(
                run=mcp_run,
                tool_name="create_research_authorization",
                sanitized_input={
                    "session_id": session_id,
                    "subject_id": baseline_version_id,
                    "allowed_stages": list(allowed_stages),
                    "max_time_minutes": resolved_minutes,
                    "locked_test_allowed": False,
                },
                sanitized_output={
                    "authorization_id": authorization.id,
                    "status": authorization.status,
                },
                final_status="completed",
            )
        except BaseException as error:
            self._record_failure(
                run=mcp_run,
                tool_name="create_research_authorization",
                sanitized_input={
                    "session_id": session_id,
                    "subject_id": baseline_version_id,
                },
                error=error,
            )
            raise

        pipeline_run = self.service.create_agent_run(
            session_id=session_id,
            agent_name="授权研究 Worker",
            agent_provider=AgentProviderKind.EXTERNAL_LOCAL_AGENT,
            execution_target=ExecutionTargetKind.LOCAL_RUNTIME,
            plan_summary=(
                "correctness → smoke → fast_screen → viability；"
                "失败后只执行授权内廉价诊断"
            ),
            subject_id=baseline_version_id,
            task_type="research_orchestration",
        )
        try:
            job = self.service.create_job(
                job_type="pipeline_execution",
                payload={
                    "authorization_id": authorization.id,
                    "session_id": session_id,
                    "subject_id": baseline_version_id,
                    "agent_run_id": pipeline_run.id,
                    "pipeline_profile": "fast_screen",
                    "locked_test_used": False,
                },
            )
            self.service.record_tool_call(
                agent_run_id=pipeline_run.id,
                tool_name="run_authorized_pipeline",
                sanitized_input={
                    "authorization_id": authorization.id,
                    "subject_id": baseline_version_id,
                    "pipeline_profile": "fast_screen",
                    "locked_test_used": False,
                },
                sanitized_output={"job_id": job.id, "status": job.status},
                status="completed",
            )
        except BaseException as error:
            self.service.update_agent_run_status(
                agent_run_id=pipeline_run.id,
                status="failed",
                error=str(error)[:1200],
            )
            raise
        return {
            "authorization": _jsonable(authorization),
            "job": _jsonable(job),
            "agent_run": _jsonable(pipeline_run),
            "next_action": (
                "研究 Job 已排队。保持 Quant Research Lab 主程序运行，并用 get_job "
                "或 get_research_session 查看停止原因、证据和下一步。"
            ),
            "not_authorized": [
                "参数批量搜索",
                "最终保留测试",
                "dry-run",
                "live trade",
                "Git 提交或推送",
            ],
        }
