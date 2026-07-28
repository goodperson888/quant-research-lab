from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from quant_lab.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    InvalidJobError,
    NotFoundError,
)
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
    Proposal,
    ResearchBudget,
    ResearchHandoff,
    ResearchSession,
    ResearchMode,
    RESEARCH_MODE_DEFINITIONS,
    StrategyDraft,
    StrategyVersion,
    ToolCall,
    Trial,
    validate_artifact_key,
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


def strategy_is_rejected(
    repository: ProductRepository, strategy_version_id: str
) -> bool:
    """Use the immutable version state and append-only outcome as rejection facts."""
    version = repository.get_strategy_version(strategy_version_id)
    return version.status == "rejected" or any(
        outcome.strategy_version_id == strategy_version_id
        and outcome.outcome_type == "rejected"
        for outcome in repository.list_strategy_outcomes()
    )


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


DEFAULT_RESEARCH_BUDGET = {
    "max_hypotheses": 8,
    "max_trials_total": 200,
    "max_compute_minutes": 240,
    "max_locked_test_uses": 2,
    "require_user_approval_for_new_hypothesis": True,
}

RESEARCH_MODE_CONFIG_KEYS = frozenset(
    {
        "agent_run_mode",
        "pause_policy",
        "stage_visibility",
        "default_trial_budget",
        "auto_failure_diagnostics",
    }
)


class ResearchApplicationService:
    def __init__(
        self,
        repository: ProductRepository,
        *,
        budget_policy: Mapping[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.budget_policy = {
            **DEFAULT_RESEARCH_BUDGET,
            **dict(budget_policy or {}),
        }
        self.repository.initialize()

    def _new_budget(self, session_id: str) -> ResearchBudget:
        return ResearchBudget(session_id=session_id, **self.budget_policy)

    def _ensure_budget(self, session_id: str) -> ResearchBudget:
        try:
            return self.repository.get_research_budget(session_id)
        except NotFoundError:
            return self.repository.create_research_budget(self._new_budget(session_id))

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

    def _authorization_covers(
        self, *, authorization_id: str, subject_id: str, stages: Sequence[str]
    ) -> bool:
        authorization = self.repository.get_research_authorization(authorization_id)
        if (
            authorization.subject_id != subject_id
            or authorization.status != "active"
            or authorization.locked_test_allowed
        ):
            return False
        expires_at = datetime.fromisoformat(
            authorization.expires_at.replace("Z", "+00:00")
        )
        if expires_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            return False
        return all(stage in authorization.allowed_stages for stage in stages)

    def create_research_session(self, *, title: str) -> ResearchSession:
        now = utc_now()
        mode_config = self.research_mode_definition("guided")
        session = ResearchSession(
            id=new_id("session"),
            title=title.strip(),
            status="inbox",
            created_at=now,
            updated_at=now,
            research_mode="guided",
            mode_config=mode_config,
            mode_revision=1,
        )
        created = self.repository.create_session(session)
        self.repository.create_research_budget(self._new_budget(created.id))
        self._audit(
            event_type="research_session.created",
            aggregate_type="research_session",
            aggregate_id=created.id,
            payload={
                "title": created.title,
                "status": created.status,
                "research_mode": created.research_mode,
                "mode_revision": created.mode_revision,
            },
        )
        return created

    @staticmethod
    def list_research_mode_definitions() -> Sequence[Mapping[str, Any]]:
        return tuple(dict(item) for item in RESEARCH_MODE_DEFINITIONS.values())

    @staticmethod
    def research_mode_definition(mode: str) -> dict[str, Any]:
        if mode not in RESEARCH_MODE_DEFINITIONS:
            raise ValueError(f"unsupported research mode: {mode}")
        return dict(RESEARCH_MODE_DEFINITIONS[mode])  # type: ignore[index]

    def update_research_mode(
        self,
        *,
        session_id: str,
        research_mode: str,
        mode_config: Mapping[str, Any] | None,
        confirmed_by_user: bool,
    ) -> ResearchSession:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "research mode update requires explicit user confirmation"
            )
        current = self.repository.get_session(session_id)
        resolved = self.research_mode_definition(research_mode)
        overrides = dict(mode_config or {})
        unknown = sorted(set(overrides) - RESEARCH_MODE_CONFIG_KEYS)
        if unknown:
            raise ValueError(
                "unsupported research mode config keys: " + ", ".join(unknown)
            )
        resolved.update(overrides)
        if resolved["agent_run_mode"] not in {
            "supervised",
            "guided",
            "bounded_autonomous",
        }:
            raise ValueError("invalid agent_run_mode")
        if resolved["pause_policy"] not in {
            "critical_only",
            "key_decisions",
            "every_stage",
        }:
            raise ValueError("invalid pause_policy")
        if resolved["stage_visibility"] not in {"summary", "guided", "full"}:
            raise ValueError("invalid stage_visibility")
        trial_budget = resolved["default_trial_budget"]
        if (
            isinstance(trial_budget, bool)
            or not isinstance(trial_budget, int)
            or not 1 <= trial_budget <= 200
        ):
            raise ValueError("default_trial_budget must be an integer from 1 to 200")
        if not isinstance(resolved["auto_failure_diagnostics"], bool):
            raise ValueError("auto_failure_diagnostics must be boolean")
        updated = self.repository.update_session_research_mode(
            session_id,
            research_mode=research_mode,
            mode_config=resolved,
            mode_revision=current.mode_revision + 1,
            updated_at=utc_now(),
        )
        self._audit(
            event_type="research_session.mode_updated",
            aggregate_type="research_session",
            aggregate_id=session_id,
            payload={
                "previous_mode": current.research_mode,
                "research_mode": updated.research_mode,
                "mode_revision": updated.mode_revision,
                "mode_config": dict(updated.mode_config),
                "safety_boundaries_unchanged": True,
            },
        )
        return updated

    def get_research_budget(self, session_id: str) -> ResearchBudget:
        self.repository.get_session(session_id)
        return self._ensure_budget(session_id)

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
        self.record_handoff(
            session_id=session_id,
            subject_id=created.id,
            status="waiting_required_input",
            stop_reason_code="strategy_intake_requires_formalization",
            stop_reason_text="原始策略已保存，但形式化规则与歧义尚未由用户确认。",
            completed_actions=("保存不可覆盖的原始策略来源", "创建 StrategyDraft"),
            not_started_actions=("形式化规则", "冻结 Baseline", "创建研究 Job"),
            user_action_required=True,
            required_user_action="审阅歧义并确认结构化策略规则。",
            next_recommended_action=f"为 Draft {created.id} 完成形式化并确认。",
            approval_subject_id=created.id,
            safe_to_continue=False,
        )
        return created

    def list_strategy_drafts(
        self, session_id: str | None = None
    ) -> Sequence[StrategyDraft]:
        return self.repository.list_drafts(session_id)

    def formalize_strategy(
        self,
        *,
        draft_id: str,
        structured_content: Mapping[str, Any],
        confirmed_by_user: bool,
    ) -> StrategyDraft:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "strategy formalization requires explicit user confirmation"
            )
        if not structured_content:
            raise ConflictError("confirmed strategy formalization cannot be empty")
        draft = self.repository.update_draft_formalization(
            draft_id=draft_id,
            structured_content=structured_content,
        )
        self._audit(
            event_type="strategy_formalization.confirmed",
            aggregate_type="strategy_draft",
            aggregate_id=draft.id,
            payload={
                "draft_id": draft.id,
                "status": draft.status,
                "baseline_frozen": False,
                "confirmed_by": "user",
            },
        )
        self.record_handoff(
            session_id=draft.session_id,
            subject_id=draft.id,
            status="waiting_user_approval",
            stop_reason_code="formalization_confirmed_baseline_not_frozen",
            stop_reason_text="结构化规则已确认，但 Baseline 冻结仍需要独立明确批准。",
            completed_actions=("保留原始来源", "确认结构化规则"),
            not_started_actions=("冻结 Baseline", "创建 correctness/fast-screen Job"),
            user_action_required=True,
            required_user_action=f"批准冻结 Draft {draft.id} 的 Baseline v0。",
            next_recommended_action=f"使用精确 subject_id {draft.id} 冻结 Baseline。",
            approval_subject_id=draft.id,
            safe_to_continue=False,
        )
        return draft

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
        session_id = self.repository.get_session_id_for_strategy_version(version.id)
        self.record_handoff(
            session_id=session_id,
            subject_id=version.id,
            status="completed_scope",
            stop_reason_code="baseline_frozen_scope_complete",
            stop_reason_text=(
                "Baseline v0 已冻结。当前授权范围仅包含冻结，因此没有创建或运行回测 Job。"
            ),
            completed_actions=(
                "保存并确认结构化策略规则",
                "冻结不可覆盖的 Baseline v0",
                "记录用户 Approval 与审计事件",
            ),
            not_started_actions=(
                "correctness 检查",
                "fast-screen 基准回测",
                "viability 与后续验证",
            ),
            user_action_required=True,
            required_user_action=f"批准为 Baseline {version.id} 创建 correctness/fast-screen 研究。",
            next_recommended_action=f"批准 subject {version.id} 的 correctness/fast-screen 研究计划。",
            approval_subject_id=version.id,
            safe_to_continue=False,
        )
        return version

    def create_job(self, *, job_type: str, payload: Mapping[str, Any]) -> Job:
        if job_type in {"trade", "live_trade"}:
            session_id = payload.get("session_id")
            if isinstance(session_id, str):
                try:
                    self.repository.get_session(session_id)
                except NotFoundError:
                    pass
                else:
                    self.record_handoff(
                        session_id=session_id,
                        subject_id=str(payload.get("subject_id") or session_id),
                        status="safety_refusal",
                        stop_reason_code="live_trade_safety_guard",
                        stop_reason_text=(
                            "Live trade safety guard: PASS (expected rejection, exit code 3)."
                        ),
                        completed_actions=("拒绝 live trade 请求", "保留研究安全边界"),
                        not_started_actions=("启动交易循环", "连接真实账户"),
                        user_action_required=False,
                        required_user_action=None,
                        next_recommended_action="继续历史研究；安全拒绝不是待修复错误。",
                        safe_to_continue=True,
                        actor_type="system",
                    )
            raise InvalidJobError("live trade is forbidden and has no Job capability")
        if job_type not in ALLOWED_JOB_TYPES:
            allowed = ", ".join(sorted(ALLOWED_JOB_TYPES))
            raise InvalidJobError(f"job type must be one of: {allowed}")
        if _contains_forbidden_key(payload):
            raise InvalidJobError("job payload contains a forbidden execution or secret key")
        if job_type == "backtest" and payload.get("intent") == "candidate_smoke":
            required = {
                "session_id",
                "strategy_version_id",
                "subject_id",
                "config_artifact_key",
                "agent_run_id",
                "correctness_gate_result_id",
                "confirmed_by_user",
                "pipeline_profile",
                "locked_test_used",
                "run_fast_screen",
            }
            missing = sorted(required - set(payload))
            if missing:
                raise InvalidJobError(
                    "candidate smoke payload missing: " + ", ".join(missing)
                )
            subject_id = str(payload["subject_id"])
            version_id = str(payload["strategy_version_id"])
            if (
                payload["confirmed_by_user"] is not True
                or subject_id != version_id
            ):
                raise ApprovalRequiredError(
                    "candidate smoke requires explicit approval of the exact subject_id"
                )
            candidate = self.repository.get_strategy_version(version_id)
            if candidate.status != "candidate" or not candidate.immutable:
                raise InvalidJobError(
                    "candidate smoke requires an immutable Candidate version"
                )
            if payload["pipeline_profile"] != "smoke":
                raise InvalidJobError("candidate smoke requires pipeline_profile=smoke")
            if payload["locked_test_used"] is not False:
                raise InvalidJobError("candidate smoke must not use locked-test data")
            if payload["run_fast_screen"] is not False:
                raise InvalidJobError("candidate smoke must stop before fast_screen")
            try:
                validate_artifact_key(str(payload["config_artifact_key"]))
            except ValueError as exc:
                raise InvalidJobError(str(exc)) from exc
            gate = self.repository.get_gate_evaluation(
                str(payload["correctness_gate_result_id"])
            )
            if (
                gate.subject_id != version_id
                or gate.gate_name != "correctness"
                or gate.status != "passed"
                or gate.profile_id != "smoke"
            ):
                raise ApprovalRequiredError(
                    "candidate smoke requires the same subject's passed correctness gate"
                )
        if job_type == "backtest" and payload.get("intent") == "candidate_fast_screen":
            required = {
                "session_id",
                "strategy_version_id",
                "subject_id",
                "config_artifact_key",
                "agent_run_id",
                "correctness_gate_result_id",
                "smoke_manifest_artifact_key",
                "confirmed_by_user",
                "pipeline_profile",
                "locked_test_used",
                "run_viability",
            }
            missing = sorted(required - set(payload))
            if missing:
                raise InvalidJobError(
                    "candidate fast-screen payload missing: " + ", ".join(missing)
                )
            subject_id = str(payload["subject_id"])
            version_id = str(payload["strategy_version_id"])
            if (
                payload["confirmed_by_user"] is not True
                or subject_id != version_id
            ):
                raise ApprovalRequiredError(
                    "fast-screen requires explicit approval of the exact subject_id"
                )
            candidate = self.repository.get_strategy_version(version_id)
            if candidate.status != "candidate" or not candidate.immutable:
                raise InvalidJobError(
                    "fast-screen requires an immutable Candidate version"
                )
            if payload["pipeline_profile"] != "fast_screen":
                raise InvalidJobError(
                    "fast-screen requires pipeline_profile=fast_screen"
                )
            if payload["locked_test_used"] is not False:
                raise InvalidJobError("fast-screen must not use locked-test data")
            if payload["run_viability"] is not False:
                raise InvalidJobError("fast-screen must stop before viability")
            for key in ("config_artifact_key", "smoke_manifest_artifact_key"):
                try:
                    validate_artifact_key(str(payload[key]))
                except ValueError as exc:
                    raise InvalidJobError(str(exc)) from exc
            gate = self.repository.get_gate_evaluation(
                str(payload["correctness_gate_result_id"])
            )
            if (
                gate.subject_id != version_id
                or gate.gate_name != "correctness"
                or gate.status != "passed"
            ):
                raise ApprovalRequiredError(
                    "fast-screen requires the same subject's passed correctness gate"
                )
            duplicate = [
                item
                for item in self.repository.list_jobs()
                if item.job_type == "backtest"
                and item.payload.get("intent") == "candidate_fast_screen"
                and item.payload.get("strategy_version_id") == version_id
            ]
            if duplicate:
                raise ConflictError(
                    "fast-screen already exists for this Candidate; preserve the prior evidence"
                )
        if job_type == "parameter_search":
            if payload.get("locked_test_used") is True:
                raise InvalidJobError("parameter search must not use locked-test data")
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
            if payload.get("batch_mode") is True:
                if payload.get("locked_test_used") is not False:
                    raise InvalidJobError(
                        "batch parameter search must explicitly exclude locked-test data"
                    )
                if payload.get("evidence_mode") != "fixture":
                    if not plan.proposal_id or not plan.candidate_version_id:
                        raise ApprovalRequiredError(
                            "research batch search requires an approved Proposal and immutable Candidate"
                        )
                    proposal = self.repository.get_proposal(plan.proposal_id)
                    allowed_proposal_statuses = (
                        {"executing"}
                        if payload.get("resume_of_job_id") is not None
                        else {"approved"}
                    )
                    if proposal.status not in allowed_proposal_statuses or (
                        proposal.candidate_version_id != plan.candidate_version_id
                    ):
                        raise ApprovalRequiredError(
                            "research batch search requires its approved Proposal/Candidate lineage"
                        )
                requested_trials = int(payload.get("max_trials", plan.max_trials or 0))
                if requested_trials <= 0 or not plan.max_trials or requested_trials > plan.max_trials:
                    raise InvalidJobError(
                        "batch Trial budget must be positive and within the approved plan"
                    )
                resume_of = payload.get("resume_of_job_id")
                related = [
                    item
                    for item in self.repository.list_jobs()
                    if item.job_type == "parameter_search"
                    and item.payload.get("batch_mode") is True
                    and item.payload.get("experiment_plan_id") == plan.id
                ]
                if resume_of is None and related:
                    raise ConflictError(
                        "batch search already exists for this plan; use the explicit retry endpoint"
                    )
                if resume_of is not None:
                    previous = self.repository.get_job(str(resume_of))
                    if (
                        previous.status not in {"failed", "cancelled"}
                        or previous.payload.get("experiment_plan_id") != plan.id
                    ):
                        raise ConflictError(
                            "batch resume requires a failed/cancelled job for the same plan"
                        )
            session_id = self.repository.get_session_id_for_strategy_version(
                plan.baseline_version_id
            )
            self._ensure_budget(session_id)
            if payload.get("resume_of_job_id") is None:
                trials = plan.max_trials or 0
                compute_minutes = ceil((plan.time_budget_seconds or 0) / 60)
                try:
                    self.repository.reserve_experiment_resources(
                        session_id,
                        trials=trials,
                        compute_minutes=compute_minutes,
                    )
                except ConflictError as exc:
                    self._audit(
                        event_type="research_budget.blocked",
                        aggregate_type="research_session",
                        aggregate_id=session_id,
                        payload={
                            "job_type": job_type,
                            "experiment_plan_id": plan.id,
                            "reason": str(exc),
                        },
                        actor_type="system",
                    )
                    self.record_handoff(
                        session_id=session_id,
                        subject_id=plan.id,
                        status="budget_exhausted",
                        stop_reason_code="trial_or_compute_budget_exhausted",
                        stop_reason_text=str(exc),
                        completed_actions=(
                            "校验已批准的 ExperimentPlan",
                            "检查会话 Trial 与计算分钟预算",
                            "保留 blocked 审计证据",
                        ),
                        not_started_actions=("创建 parameter-search Job", "运行 Trials"),
                        user_action_required=True,
                        required_user_action="审阅预算与停止条件；不得通过提示词绕过硬上限。",
                        next_recommended_action="缩小已批准研究范围或结束当前研究会话。",
                        approval_subject_id=plan.id,
                        safe_to_continue=False,
                        actor_type="system",
                    )
                    raise
        if job_type == "stress_test":
            stress_level = payload.get("stress_level")
            if stress_level == "full":
                gate_id = payload.get("viability_gate_result_id")
                subject_id = payload.get("strategy_version_id") or payload.get(
                    "candidate_version_id"
                )
                if not isinstance(gate_id, str) or not isinstance(subject_id, str):
                    raise ApprovalRequiredError(
                        "full stress requires a strategy subject and passed viability gate"
                    )
                gate = self.repository.get_gate_evaluation(gate_id)
                if (
                    gate.subject_id != subject_id
                    or gate.gate_name != "viability"
                    or gate.status != "passed"
                ):
                    raise ApprovalRequiredError(
                        "full stress is forbidden until the subject passes viability"
                    )
            elif stress_level == "cheap_cost_sensitivity":
                gate_id = payload.get("fast_screen_gate_result_id")
                subject_id = payload.get("strategy_version_id") or payload.get(
                    "candidate_version_id"
                )
                if not isinstance(gate_id, str) or not isinstance(subject_id, str):
                    raise ApprovalRequiredError(
                        "cheap cost sensitivity requires a passed fast_screen gate"
                    )
                gate = self.repository.get_gate_evaluation(gate_id)
                if (
                    gate.subject_id != subject_id
                    or gate.gate_name != "fast_screen"
                    or gate.status != "passed"
                ):
                    raise ApprovalRequiredError(
                        "cheap cost sensitivity requires the subject's passed fast_screen gate"
                    )
        if job_type == "regime_validation":
            required = {
                "subject_type",
                "subject_id",
                "market_profile",
                "detector_config_artifact_key",
                "data_manifest_artifact_key",
                "trades_artifact_key",
            }
            missing = sorted(required - set(payload))
            if missing:
                raise InvalidJobError(
                    "regime validation payload missing: " + ", ".join(missing)
                )
            if payload.get("ex_ante_observable") is not True:
                raise InvalidJobError("regime validation must be ex-ante observable")
            if payload.get("locked_test_used") is not False:
                raise InvalidJobError("regime screening must not use locked-test evidence")
            for name in (
                "detector_config_artifact_key",
                "data_manifest_artifact_key",
                "trades_artifact_key",
            ):
                try:
                    validate_artifact_key(str(payload[name]))
                except ValueError as exc:
                    raise InvalidJobError(str(exc)) from exc
            subject_type = payload["subject_type"]
            subject_id = str(payload["subject_id"])
            if subject_type == "strategy_version":
                self.repository.get_strategy_version(subject_id)
            elif subject_type == "component_candidate":
                known = {item.id for item in self.repository.list_component_candidates()}
                if subject_id not in known:
                    raise InvalidJobError("component candidate subject does not exist")
            else:
                raise InvalidJobError("unsupported regime validation subject_type")
            mode = payload.get("mode")
            if mode not in {"regime_diagnostic", "regime_validation"}:
                raise InvalidJobError("regime job requires an explicit diagnostic/validation mode")
            if mode == "regime_validation":
                gate_id = payload.get("viability_gate_result_id")
                if not isinstance(gate_id, str):
                    raise ApprovalRequiredError(
                        "formal regime validation requires a passed viability gate"
                    )
                gate = self.repository.get_gate_evaluation(gate_id)
                if (
                    gate.subject_id != subject_id
                    or gate.gate_name != "viability"
                    or gate.status != "passed"
                    or gate.market_profile != payload["market_profile"]
                ):
                    raise ApprovalRequiredError(
                        "formal regime validation requires the subject's passed viability gate"
                    )
        if job_type == "research_diagnostic":
            required = {
                "authorization_id",
                "session_id",
                "subject_id",
                "fast_screen_manifest_artifact_key",
                "metrics_artifact_key",
                "trades_artifact_key",
                "signals_artifact_key",
                "data_manifest_artifact_key",
                "detector_config_artifact_key",
            }
            missing = sorted(required - set(payload))
            if missing:
                raise InvalidJobError(
                    "research diagnostic payload missing: " + ", ".join(missing)
                )
            subject_id = str(payload["subject_id"])
            if not strategy_is_rejected(self.repository, subject_id):
                raise InvalidJobError(
                    "post-viability research diagnostics require a rejected strategy"
                )
            if (
                self.repository.get_session_id_for_strategy_version(subject_id)
                != payload["session_id"]
            ):
                raise InvalidJobError("research diagnostic subject/session mismatch")
            if payload.get("locked_test_used") is not False:
                raise InvalidJobError("research diagnostics must exclude locked-test data")
            authorization_id = str(payload["authorization_id"])
            if not self._authorization_covers(
                authorization_id=authorization_id,
                subject_id=subject_id,
                stages=(
                    "loss_attribution",
                    "regime_diagnostic",
                    "component_hypothesis_generation",
                ),
            ):
                raise ApprovalRequiredError(
                    "research diagnostics require an active exact-subject authorization"
                )
            for name in (
                "fast_screen_manifest_artifact_key",
                "metrics_artifact_key",
                "trades_artifact_key",
                "signals_artifact_key",
                "data_manifest_artifact_key",
                "detector_config_artifact_key",
            ):
                try:
                    validate_artifact_key(str(payload[name]))
                except ValueError as exc:
                    raise InvalidJobError(str(exc)) from exc
            duplicate = [
                item
                for item in self.repository.list_jobs()
                if item.job_type == "research_diagnostic"
                and item.payload.get("subject_id") == subject_id
            ]
            active_or_complete = [
                item
                for item in duplicate
                if item.status in {"queued", "running", "succeeded"}
            ]
            if active_or_complete:
                raise ConflictError(
                    "research diagnostics already exist for this rejected subject"
                )
            retry_of_job_id = payload.get("retry_of_job_id")
            if duplicate:
                if not isinstance(retry_of_job_id, str):
                    raise ConflictError(
                        "failed research diagnostics require an explicit retry_of_job_id"
                    )
                prior = self.repository.get_job(retry_of_job_id)
                if (
                    prior.job_type != "research_diagnostic"
                    or prior.status != "failed"
                    or prior.payload.get("subject_id") != subject_id
                ):
                    raise InvalidJobError(
                        "research diagnostic retry must reference the subject's failed Job"
                    )
        if job_type == "correctness_diagnostic":
            required = {
                "strategy_version_id",
                "analysis_type",
                "strategy_name",
                "strategy_artifact_key",
                "config_artifact_key",
            }
            missing = sorted(required - set(payload))
            if missing:
                raise InvalidJobError(
                    "correctness diagnostic payload missing: " + ", ".join(missing)
                )
            version = self.repository.get_strategy_version(
                str(payload["strategy_version_id"])
            )
            if version.status == "rejected":
                raise InvalidJobError("correctness diagnostics must not rerun rejected strategies")
            if payload["analysis_type"] not in {
                "lookahead-analysis",
                "recursive-analysis",
            }:
                raise InvalidJobError("unsupported correctness diagnostic type")
            if payload.get("locked_test_used") is not False:
                raise InvalidJobError("correctness diagnostics must not use locked-test data")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(payload["strategy_name"])):
                raise InvalidJobError("strategy_name must be a Python class identifier")
            for name in ("strategy_artifact_key", "config_artifact_key"):
                try:
                    key = validate_artifact_key(str(payload[name]))
                except ValueError as exc:
                    raise InvalidJobError(str(exc)) from exc
                if name == "strategy_artifact_key" and not key.startswith(
                    ("strategies/freqtrade/", "runtime/freqtrade/user_data/strategies/")
                ):
                    raise InvalidJobError("strategy artifact is outside approved strategy roots")
                if name == "config_artifact_key" and not key.startswith("configs/"):
                    raise InvalidJobError("Freqtrade config must be a project config artifact")
        if payload.get("locked_test_used") is True:
            session_id = payload.get("session_id")
            if not isinstance(session_id, str):
                raise ApprovalRequiredError(
                    "locked-test use requires an explicit research session budget"
                )
            self.repository.get_session(session_id)
            self._ensure_budget(session_id)
            try:
                self.repository.reserve_locked_test_use(session_id)
            except ConflictError as exc:
                self._audit(
                    event_type="research_budget.blocked",
                    aggregate_type="research_session",
                    aggregate_id=session_id,
                    payload={"job_type": job_type, "reason": str(exc)},
                    actor_type="system",
                )
                self.record_handoff(
                    session_id=session_id,
                    subject_id=str(payload.get("subject_id") or session_id),
                    status="budget_exhausted",
                    stop_reason_code="locked_test_budget_exhausted",
                    stop_reason_text=str(exc),
                    completed_actions=("检查 locked-test 使用预算", "保留 blocked 审计证据"),
                    not_started_actions=("创建使用 locked-test 的 Job",),
                    user_action_required=True,
                    required_user_action="审阅 locked-test 使用记录与研究范围。",
                    next_recommended_action="停止重复查看锁定测试，或建立新的未污染锁定区间。",
                    approval_subject_id=str(payload.get("subject_id") or session_id),
                    safe_to_continue=False,
                    actor_type="system",
                )
                raise
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
        if job_type == "parameter_search" and payload.get("batch_mode") is True:
            plan = self.repository.get_experiment_plan(str(payload["experiment_plan_id"]))
            if plan.proposal_id is not None:
                proposal = self.repository.get_proposal(plan.proposal_id)
                if proposal.status == "approved":
                    self.repository.update_proposal(proposal.transition("executing"))
        self._audit(
            event_type="job.queued",
            aggregate_type="job",
            aggregate_id=created.id,
            payload={"job_type": created.job_type, "status": created.status},
        )
        return created

    def propose_strategy_version(
        self,
        *,
        baseline_version_id: str,
        content: Mapping[str, Any],
    ) -> Proposal:
        baseline = self.repository.get_strategy_version(baseline_version_id)
        if baseline.status != "baseline" or not baseline.immutable:
            raise ValueError("proposal requires an immutable baseline")
        proposal = Proposal(
            id=new_id("proposal"),
            draft_id=baseline.strategy_id,
            proposal_type="strategy_version",
            content=dict(content),
            status="draft",
        )
        created = self.repository.create_proposal(proposal)
        self._audit(
            event_type="strategy_version.proposed",
            aggregate_type="proposal",
            aggregate_id=created.id,
            payload={
                "baseline_version_id": baseline_version_id,
                "status": created.status,
                "automatic_acceptance": False,
            },
            actor_type="external_agent",
        )
        return created

    def accept_proposal(
        self, *, proposal_id: str, confirmed_by_user: bool
    ) -> StrategyVersion:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "strategy proposal acceptance requires explicit user confirmation"
            )
        current = self.repository.get_proposal(proposal_id)
        if current.proposal_type != "strategy_version":
            raise ValueError("only strategy-version proposals can be accepted")
        content = current.content
        if content.get("status") != "awaiting_user_acceptance":
            raise ValueError("proposal is not awaiting user acceptance")
        if content.get("baseline_immutable") is not True:
            raise ValueError("proposal must preserve the immutable baseline")
        if content.get("strategy_version_created") is not False:
            raise ValueError("proposal already claims a strategy version")
        if content.get("future_locked_test_required") is not True:
            raise ValueError("candidate must retain the future locked-test requirement")
        if content.get("production_promotion_requested") is not False:
            raise ValueError("proposal must not request production promotion")
        plan_id = content.get("experiment_plan_id")
        if not isinstance(plan_id, str):
            raise ValueError("proposal must reference an experiment plan")
        plan = self.repository.get_experiment_plan(plan_id)
        if plan.status != "approved" or plan.approved_by != "user":
            raise ApprovalRequiredError("proposal requires an approved experiment plan")
        trials = list(self.repository.list_trials(plan_id))
        if len(trials) != 1 or trials[0].status != "succeeded":
            raise ValueError("proposal requires exactly one succeeded bounded Trial")
        if trials[0].metrics.get("all_constraints_passed") != 1.0:
            raise ValueError("proposal constraints did not pass")

        accepted = Proposal(
            id=current.id,
            draft_id=current.draft_id,
            proposal_type=current.proposal_type,
            content=current.content,
            status="accepted",
        )
        version = self.repository.accept_proposal(
            accepted,
            version_id=new_id("version"),
            approval_id=new_id("approval"),
            created_at=utc_now(),
        )
        self._audit(
            event_type="strategy_proposal.accepted",
            aggregate_type="strategy_version",
            aggregate_id=version.id,
            payload={
                "proposal_id": proposal_id,
                "version": version.version,
                "status": version.status,
                "baseline_version_id": content["baseline_version_id"],
                "baseline_overwritten": False,
                "validated": False,
                "dry_run": False,
                "production": False,
                "approved_by": "user",
            },
        )
        return version

    def reject_candidate(
        self,
        *,
        version_id: str,
        confirmed_by_user: bool,
        stress_manifest_artifact_key: str,
        failure_conditions: Sequence[Mapping[str, Any]],
    ) -> StrategyVersion:
        if not confirmed_by_user:
            raise ApprovalRequiredError(
                "candidate rejection requires explicit user confirmation"
            )
        current = self.repository.get_strategy_version(version_id)
        if current.status != "candidate":
            raise ConflictError("only a candidate strategy version can be rejected")
        if not current.immutable:
            raise ValueError("candidate rejection requires an immutable strategy version")
        baseline_id = current.content_snapshot.get("baseline_version_id")
        if not isinstance(baseline_id, str):
            raise ValueError("candidate does not reference its immutable baseline")
        baseline = self.repository.get_strategy_version(baseline_id)
        if baseline.status != "baseline" or not baseline.immutable:
            raise ValueError("candidate rejection requires its original immutable baseline")
        if not failure_conditions:
            raise ValueError("candidate rejection requires recorded stress failure conditions")

        rejected = self.repository.reject_candidate(
            version_id=version_id,
            approval_id=new_id("approval"),
            created_at=utc_now(),
        )
        self._audit(
            event_type="strategy_candidate.rejected",
            aggregate_type="strategy_version",
            aggregate_id=rejected.id,
            payload={
                "previous_status": "candidate",
                "status": rejected.status,
                "approved_by": "user",
                "stress_manifest_artifact_key": stress_manifest_artifact_key,
                "failure_conditions": [dict(item) for item in failure_conditions],
                "baseline_version_id": baseline.id,
                "baseline_overwritten": False,
                "locked_test_used": False,
                "dry_run": False,
                "production": False,
                "live_trading": False,
            },
        )
        return rejected

    def list_jobs(self) -> Sequence[Job]:
        return self.repository.list_jobs()

    def cancel_job(
        self, *, job_id: str, subject_id: str, confirmed_by_user: bool
    ) -> Job:
        if not confirmed_by_user or subject_id != job_id:
            raise ApprovalRequiredError("job cancellation requires the exact subject_id")
        current = self.repository.get_job(job_id)
        if current.status not in {"queued", "running"}:
            raise ConflictError("only queued/running jobs can be cancelled")
        cancelled = self.repository.update_job(
            job_id, status="cancelled", updated_at=utc_now(), error="cancelled by user"
        )
        self._audit(
            event_type="job.cancellation_requested",
            aggregate_type="job",
            aggregate_id=job_id,
            payload={"subject_id": subject_id, "partial_trials_preserved": True},
        )
        return cancelled

    def retry_job(
        self, *, job_id: str, subject_id: str, confirmed_by_user: bool
    ) -> Job:
        if not confirmed_by_user or subject_id != job_id:
            raise ApprovalRequiredError("job retry requires the exact subject_id")
        previous = self.repository.get_job(job_id)
        if previous.job_type != "parameter_search" or previous.status not in {
            "failed",
            "cancelled",
        }:
            raise ConflictError("only failed/cancelled parameter-search jobs can be retried")
        payload = dict(previous.payload)
        payload["resume_of_job_id"] = previous.id
        return self.create_job(job_type=previous.job_type, payload=payload)

    def get_job(self, job_id: str) -> Job:
        return self.repository.get_job(job_id)

    def list_job_logs(self, job_id: str) -> Sequence[Mapping[str, Any]]:
        self.repository.get_job(job_id)
        return self.repository.list_job_logs(job_id)

    def list_audit_events(self, *, limit: int = 100) -> Sequence[AuditEvent]:
        return self.repository.list_events(limit=limit)

    def record_handoff(
        self,
        *,
        session_id: str,
        subject_id: str,
        status: str,
        stop_reason_code: str,
        stop_reason_text: str,
        completed_actions: Sequence[str],
        not_started_actions: Sequence[str],
        user_action_required: bool,
        required_user_action: str | None,
        next_recommended_action: str,
        safe_to_continue: bool,
        agent_run_id: str | None = None,
        approval_subject_id: str | None = None,
        actor_type: str = "external_agent",
    ) -> ResearchHandoff:
        self.repository.get_session(session_id)
        handoff = ResearchHandoff(
            id=new_id("handoff"),
            session_id=session_id,
            agent_run_id=agent_run_id,
            subject_id=subject_id,
            status=status,  # type: ignore[arg-type]
            stop_reason_code=stop_reason_code,
            stop_reason_text=stop_reason_text,
            completed_actions=tuple(completed_actions),
            not_started_actions=tuple(not_started_actions),
            user_action_required=user_action_required,
            required_user_action=required_user_action,
            next_recommended_action=next_recommended_action,
            approval_subject_id=approval_subject_id,
            safe_to_continue=safe_to_continue,
            created_at=utc_now(),
        )
        created = self.repository.create_research_handoff(handoff)
        self._audit(
            event_type="research_handoff.recorded",
            aggregate_type="research_handoff",
            aggregate_id=created.id,
            payload={
                "session_id": created.session_id,
                "agent_run_id": created.agent_run_id,
                "subject_id": created.subject_id,
                "status": created.status,
                "stop_reason_code": created.stop_reason_code,
                "user_action_required": created.user_action_required,
                "approval_subject_id": created.approval_subject_id,
                "safe_to_continue": created.safe_to_continue,
            },
            actor_type=actor_type,
        )
        return created

    def get_latest_session_handoff(self, session_id: str) -> ResearchHandoff:
        self.repository.get_session(session_id)
        return self.repository.get_latest_session_handoff(session_id)

    def get_latest_agent_run_handoff(self, agent_run_id: str) -> ResearchHandoff:
        self.repository.get_agent_run(agent_run_id)
        return self.repository.get_latest_agent_run_handoff(agent_run_id)

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
        proposal_id: str | None = None,
        candidate_version_id: str | None = None,
        search_strategy: str = "grid",
        random_seed: int = 0,
    ) -> ExperimentPlan:
        if proposal_id is not None:
            proposal = self.repository.get_proposal(proposal_id)
            if proposal.status != "approved":
                raise ApprovalRequiredError(
                    "experiment plan requires an approved improvement proposal"
                )
            if proposal.baseline_version_id != baseline_version_id:
                raise ConflictError("plan baseline does not match its proposal")
            if candidate_version_id != proposal.candidate_version_id:
                raise ConflictError("plan candidate does not match its approved proposal")
        if candidate_version_id is not None:
            candidate = self.repository.get_strategy_version(candidate_version_id)
            if candidate.status != "candidate" or not candidate.immutable:
                raise ConflictError("experiment plan candidate must be immutable")
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
            proposal_id=proposal_id,
            candidate_version_id=candidate_version_id,
            search_strategy=search_strategy,  # type: ignore[arg-type]
            random_seed=random_seed,
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
        if current.status != "draft":
            raise ConflictError("only a draft experiment plan can be approved")
        approved = current.approve(actor="user")
        session_id = self.repository.get_session_id_for_strategy_version(
            current.baseline_version_id
        )
        budget = self._ensure_budget(session_id)
        if budget.require_user_approval_for_new_hypothesis and not confirmed_by_user:
            raise ApprovalRequiredError("new hypothesis requires explicit user approval")
        try:
            self.repository.reserve_hypothesis(session_id)
        except ConflictError as exc:
            self._audit(
                event_type="research_budget.blocked",
                aggregate_type="research_session",
                aggregate_id=session_id,
                payload={"experiment_plan_id": plan_id, "reason": str(exc)},
                actor_type="system",
            )
            self.record_handoff(
                session_id=session_id,
                subject_id=plan_id,
                status="budget_exhausted",
                stop_reason_code="hypothesis_budget_exhausted",
                stop_reason_text=str(exc),
                completed_actions=("校验 ExperimentPlan", "检查会话 hypothesis 预算"),
                not_started_actions=("批准 ExperimentPlan", "创建 parameter-search Job"),
                user_action_required=True,
                required_user_action="审阅会话预算与已研究假设。",
                next_recommended_action="停止新增假设或由用户明确建立新的研究会话。",
                approval_subject_id=plan_id,
                safe_to_continue=False,
                actor_type="system",
            )
            raise
        saved = self.repository.approve_experiment_plan(
            approved,
            approval_id=new_id("approval"),
            created_at=utc_now(),
        )
        self._audit(
            event_type="experiment_plan.approved",
            aggregate_type="experiment_plan",
            aggregate_id=saved.id,
            payload={
                "approved_by": "user",
                "status": saved.status,
                "research_budget_session_id": session_id,
            },
        )
        return saved

    def create_agent_run(
        self,
        *,
        session_id: str,
        agent_name: str,
        mode: str | None = None,
        agent_provider: AgentProviderKind = AgentProviderKind.EXTERNAL_LOCAL_AGENT,
        execution_target: ExecutionTargetKind = ExecutionTargetKind.LOCAL_RUNTIME,
        plan_summary: str | None = None,
    ) -> AgentRun:
        session = self.repository.get_session(session_id)
        resolved_mode = mode or str(
            session.mode_config.get(
                "agent_run_mode",
                self.research_mode_definition(session.research_mode)["agent_run_mode"],
            )
        )
        if resolved_mode not in {"supervised", "guided", "bounded_autonomous"}:
            raise ValueError("invalid AgentRun mode")
        agent_run = AgentRun(
            id=new_id("agent_run"),
            session_id=session_id,
            agent_name=agent_name,
            agent_provider=agent_provider,
            execution_target=execution_target,
            mode=resolved_mode,  # type: ignore[arg-type]
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
                "research_mode": session.research_mode,
                "mode_revision": session.mode_revision,
            },
            actor_type="external_agent",
        )
        return created

    def update_agent_run_status(self, *, agent_run_id: str, status: str) -> AgentRun:
        allowed = {
            "queued",
            "running",
            "waiting_approval",
            "paused",
            "completed",
            "failed",
            "cancelled",
        }
        if status not in allowed:
            raise ValueError("invalid agent run status")
        updated = self.repository.update_agent_run_status(agent_run_id, status=status)
        self._audit(
            event_type="agent_run.status_changed",
            aggregate_type="agent_run",
            aggregate_id=agent_run_id,
            payload={"status": updated.status},
            actor_type="external_agent",
        )
        return updated

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
        candidate_version_id: str | None = None,
        parameter_signature: str | None = None,
        split: str = "train_validation",
        cost_model: Mapping[str, Any] | None = None,
        seed: int = 0,
    ) -> Trial:
        trial = Trial(
            id=new_id("trial"),
            experiment_plan_id=experiment_plan_id,
            parameters=dict(parameters),
            data_version=data_version,
            status=status,  # type: ignore[arg-type]
            metrics=dict(metrics or {}),
            log_artifact_key=log_artifact_key,
            candidate_version_id=candidate_version_id,
            parameter_signature=parameter_signature,
            split=split,
            cost_model=dict(cost_model or {}),
            seed=seed,
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

    def update_trial(
        self,
        *,
        trial_id: str,
        status: str,
        metrics: Mapping[str, float],
        log_artifact_key: str | None,
        error: str | None = None,
        elapsed_seconds: float | None = None,
        peak_rss_mb: float | None = None,
        result_artifact_key: str | None = None,
        metrics_artifact_key: str | None = None,
    ) -> Trial:
        if status not in {"running", "succeeded", "failed", "cancelled"}:
            raise ValueError("invalid trial status")
        updated = self.repository.update_trial(
            trial_id,
            status=status,
            metrics=metrics,
            log_artifact_key=log_artifact_key,
            error=error,
            elapsed_seconds=elapsed_seconds,
            peak_rss_mb=peak_rss_mb,
            result_artifact_key=result_artifact_key,
            metrics_artifact_key=metrics_artifact_key,
        )
        self._audit(
            event_type="trial.status_changed",
            aggregate_type="experiment_plan",
            aggregate_id=updated.experiment_plan_id,
            payload={"trial_id": updated.id, "status": updated.status},
            actor_type="system",
        )
        return updated
