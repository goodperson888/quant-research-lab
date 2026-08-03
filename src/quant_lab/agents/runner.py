from __future__ import annotations

from typing import Any, Mapping

from quant_lab.application.services import ResearchApplicationService
from quant_lab.domain.models import AgentRun


def complete_formalization_run(
    *,
    service: ResearchApplicationService,
    run: AgentRun,
    result: Mapping[str, Any],
    actor_type: str,
) -> None:
    if run.task_type != "strategy_formalization" or not run.subject_id:
        raise RuntimeError("AgentRun 不是可执行的策略形式化任务")
    service.propose_strategy_formalization(
        draft_id=run.subject_id,
        structured_content=result["structured_content"],
        agent_run_id=run.id,
        assistant_message=str(result["assistant_message"]),
        actor_type=actor_type,
    )
    service.update_agent_run_status(
        agent_run_id=run.id,
        status="waiting_approval",
    )


def fail_agent_run(
    *,
    service: ResearchApplicationService,
    run: AgentRun,
    error: BaseException,
    actor_type: str,
) -> None:
    safe_error = str(error).strip()[:1200] or error.__class__.__name__
    service.update_agent_run_status(
        agent_run_id=run.id,
        status="failed",
        error=safe_error,
    )
    service.record_handoff(
        session_id=run.session_id,
        agent_run_id=run.id,
        subject_id=run.subject_id or run.id,
        status="blocked_dependency",
        stop_reason_code="agent_execution_failed",
        stop_reason_text=safe_error,
        completed_actions=("保存原始策略", "创建 AgentRun"),
        not_started_actions=("生成结构化策略提案", "用户确认", "冻结 Baseline"),
        user_action_required=True,
        required_user_action="检查本地助手或网页模型配置后重试。",
        next_recommended_action="修复页面显示的失败原因，再重新发起一次形式化任务。",
        approval_subject_id=run.subject_id,
        safe_to_continue=False,
        actor_type=actor_type,
    )
