from __future__ import annotations

from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import yaml

from quant_lab import __version__
from quant_lab.application.services import ResearchApplicationService, utc_now
from quant_lab.agents.local_codex import resolve_codex_binary
from quant_lab.agents.provider_dispatcher import WebProviderDispatcher
from quant_lab.application.guided_research import GuidedResearchService
from quant_lab.application.component_attribution import DefaultComponentEvidenceAggregator
from quant_lab.application.batch_trials import summarize_stable_ranges
from quant_lab.application.backtest_engines import BacktestEngineRegistry
from quant_lab.application.equity_series import (
    RunBundleEquityReader,
    TrialEquityReader,
)
from quant_lab.application.pipeline import (
    PipelineApplicationService,
    PipelineProfileCatalog,
    profile_as_dict,
)
from quant_lab.application.research_authorization import (
    ResearchAuthorizationPolicyReader,
    ResearchAuthorizationService,
)
from quant_lab.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    ExperimentPlanValidationError,
    GatePolicyError,
    InvalidJobError,
    NotFoundError,
    ProviderNotConfiguredError,
)
from quant_lab.domain.models import (
    AgentProviderKind,
    AssistantEntryMode,
    AuditEvent,
    ExecutionTargetKind,
)
from quant_lab.domain.models import Constraint, Objective, ParameterSpace
from quant_lab.infrastructure.llm import InMemoryOpenAICompatibleProvider
from quant_lab.infrastructure.execution_models import ExecutionModelCatalog
from quant_lab.infrastructure.backtest_engines import (
    FreqtradeBacktestEngineAdapter,
    NativeBacktestEngineAdapter,
)
from quant_lab.infrastructure.project_readers import (
    AgentManifestReader,
    DataSummaryReader,
    ProjectStatusReader,
)
from quant_lab.infrastructure.policy_readers import (
    ResearchBudgetPolicyReader,
    VersioningPolicyReader,
    WorkerResourcePolicyReader,
)
from quant_lab.application.storage import StorageReporter
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.infrastructure.artifact_store import LocalArtifactStore
from quant_lab.infrastructure.builtin_strategy_plugins import (
    build_builtin_strategy_plugins,
)
from quant_lab.infrastructure.generic_strategy_dsl_runner import (
    DEFAULT_DATA_MANIFEST,
)
from quant_lab.paths import app_database_path, project_root
from quant_lab.registry import (
    get_factor,
    list_factors,
    register_factor,
    set_factor_status,
)

from .schemas import (
    AuditEventResponse,
    AgentRunResponse,
    ComponentArchiveRequest,
    ComponentCandidateResponse,
    ComponentEvidenceResponse,
    FactorStatusRequest,
    PromoteFactorCandidateRequest,
    ComponentTriageResponse,
    ComponentHypothesisResponse,
    ComponentAggregationRequest,
    CreateComponentCandidateRequest,
    CreateRegimeValidationRequest,
    CreateRegimeValidationJobRequest,
    CreateResearchAuthorizationRequest,
    CreateResearchDiagnosticJobRequest,
    CreateCorrectnessDiagnosticJobRequest,
    CreateEngineReconciliationRequest,
    CreateStrategyOutcomeRequest,
    CreateIntakeRequest,
    CreateAgentFormalizationRequest,
    CreateExperimentPlanRequest,
    CreateJobRequest,
    CreateMessageRequest,
    CreateSessionRequest,
    ConfigureProviderRequest,
    CreateImprovementDirectionRequest,
    FreezeBaselineRequest,
    ExperimentPlanResponse,
    EvaluateGateRequest,
    FormalizeStrategyRequest,
    GateEvaluationResponse,
    JobResponse,
    JobActionRequest,
    MaterializeComponentHypothesisRequest,
    MessageResponse,
    PipelineProfileResponse,
    RegimeValidationResponse,
    ResearchHandoffResponse,
    ResearchAuthorizationResponse,
    ResearchAuthorizationStageResponse,
    StartResearchAuthorizationRequest,
    ResearchModeDefinitionResponse,
    ImprovementDirectionResponse,
    LaunchImprovementBatchRequest,
    ProposalTransitionRequest,
    ProposalBudgetRequest,
    SessionAgentOccupancyResponse,
    SessionDetailResponse,
    SessionResponse,
    ProviderStatusResponse,
    UpdateAssistantEntryModeRequest,
    UpdateResearchModeRequest,
    StrategyDraftResponse,
    StrategyOutcomeResponse,
    StrategyVersionResponse,
    TrialResponse,
)


def local_origins() -> list[str]:
    configured_port = os.environ.get("QUANT_LAB_WEB_PORT", "3100")
    try:
        port = int(configured_port)
    except ValueError:
        port = 3100
    if not 1 <= port <= 65535:
        port = 3100
    ports = sorted({3000, 3100, port})
    return [
        origin
        for current_port in ports
        for origin in (
            f"http://localhost:{current_port}",
            f"http://127.0.0.1:{current_port}",
        )
    ]


def _select_batch_evaluator(
    evaluator_ids: tuple[str, ...],
    *,
    parameter_names: set[str],
    strategy_spec_id: str,
    baseline_snapshot: dict[str, Any] | Any,
) -> str:
    if strategy_spec_id == "generic_strategy_dsl_v1":
        dsl = baseline_snapshot.get("strategy_dsl", {})
        supported = set(dsl.get("parameters", {}))
        unknown = sorted(parameter_names - supported)
        if unknown:
            raise ConflictError(
                "改进方向包含通用策略未声明的参数：" + "、".join(unknown)
            )
        return "generic_strategy_dsl_v1"
    if len(evaluator_ids) == 1:
        return evaluator_ids[0]
    parameter_routes = {
        frozenset({"structure_exit_variant"}): (
            "ema_mtf_scalp_exit_component_v1"
        ),
        frozenset({"exit_rule_variant"}): (
            "boll_rsi_slope_exit_component_v1"
        ),
        frozenset({"max_stop_distance_fraction"}): (
            "boll_rsi_slope_stop_distance_component_v1"
        ),
        frozenset({"minimum_reward_r"}): (
            "boll_rsi_slope_minimum_reward_component_v1"
        ),
    }
    evaluator_id = parameter_routes.get(frozenset(parameter_names))
    if evaluator_id is None or evaluator_id not in evaluator_ids:
        raise ConflictError(
            "当前改进方向无法唯一匹配已审阅的参数执行器；"
            "请保持一次只验证一个受支持的参数假设"
        )
    return evaluator_id


def _batch_data_version(
    *,
    resolved_root: Path,
    repository: SQLiteProductRepository,
    baseline: Any,
    spec: Any,
) -> str:
    artifacts = LocalArtifactStore(resolved_root)
    if spec.strategy_spec_id == "generic_strategy_dsl_v1":
        manifest_key = DEFAULT_DATA_MANIFEST
    else:
        config_key = (
            spec.config_resolver(
                baseline,
                repository.get_session_id_for_strategy_version(baseline.id),
            )
            if spec.config_resolver is not None
            else spec.config_artifact_key
        )
        if not config_key:
            raise ConflictError("策略执行器没有可用的数据配置")
        loaded = yaml.safe_load(artifacts.get(str(config_key)))
        if not isinstance(loaded, dict):
            raise ConflictError("策略执行配置格式无效")
        manifest_key = loaded.get("data_manifest_key")
        if not isinstance(manifest_key, str):
            raise ConflictError("策略执行配置没有声明数据清单")
    manifest = json.loads(artifacts.get(str(manifest_key)))
    data_version = manifest.get("data_version")
    if not isinstance(data_version, str) or not data_version:
        raise ConflictError("数据清单没有有效 data_version")
    return data_version


def _baseline_for_component_hypothesis(
    repository: SQLiteProductRepository,
    *,
    subject_id: str,
) -> Any:
    subject = repository.get_strategy_version(subject_id)
    baselines = [
        item
        for item in repository.list_strategy_versions(subject.strategy_id)
        if item.status == "baseline" and item.immutable
    ]
    if len(baselines) != 1:
        raise ConflictError(
            "组件假设必须能唯一追溯到同一策略的冻结 Baseline"
        )
    return baselines[0]


def _proposal_research_contract(
    repository: SQLiteProductRepository,
    *,
    baseline: Any,
) -> dict[str, Any] | None:
    for proposal in repository.list_proposals(baseline.strategy_id):
        if (
            proposal.proposal_type == "improvement_direction"
            and proposal.baseline_version_id == baseline.id
            and {"train", "validation", "locked_test"}.issubset(
                proposal.data_splits
            )
            and proposal.cost_model
            and proposal.objectives
            and proposal.constraints
        ):
            return {
                "data_splits": dict(proposal.data_splits),
                "cost_model": dict(proposal.cost_model),
                "objectives": tuple(proposal.objectives),
                "constraints": tuple(proposal.constraints),
                "contract_source": f"proposal:{proposal.id}",
            }
    return None


def _format_time_splits(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConflictError("策略执行配置没有可信的时间切分")
    result: dict[str, str] = {}
    for name in ("train", "validation", "locked_test"):
        split = raw.get(name)
        if not isinstance(split, dict):
            raise ConflictError(f"策略执行配置缺少 {name} 时间切分")
        start = split.get("start_utc_inclusive")
        end = split.get("end_utc_exclusive")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ConflictError(f"策略执行配置的 {name} 时间范围无效")
        result[name] = f"{start}/{end}"
    return result


def _config_research_contract(
    *,
    resolved_root: Path,
    repository: SQLiteProductRepository,
    baseline: Any,
    spec: Any,
) -> dict[str, Any]:
    artifacts = LocalArtifactStore(resolved_root)
    config_key = (
        spec.config_resolver(
            baseline,
            repository.get_session_id_for_strategy_version(baseline.id),
        )
        if spec.config_resolver is not None
        else spec.config_artifact_key
    )
    if not config_key:
        raise ConflictError(
            "当前 Baseline 没有登记可复用的数据切分与成本配置；"
            "请先让本地研究助手补全方案"
        )
    loaded = yaml.safe_load(artifacts.get(str(config_key)))
    if not isinstance(loaded, dict):
        raise ConflictError("策略执行配置格式无效")
    cost_model = loaded.get("cost_model")
    if not isinstance(cost_model, dict) or not cost_model:
        raise ConflictError("策略执行配置没有可信的成本模型")
    return {
        "data_splits": _format_time_splits(loaded.get("time_splits")),
        "cost_model": dict(cost_model),
        "objectives": (
            Objective(metric="validation_net_return", direction="maximize"),
        ),
        "constraints": (
            Constraint(
                metric="incremental_net_return",
                operator="gte",
                value=0.0,
            ),
            Constraint(
                metric="validation_trade_count",
                operator="gte",
                value=3.0,
            ),
        ),
        "contract_source": f"strategy_config:{config_key}",
    }


def _component_parameter_space(
    *,
    strategy_spec_id: str,
    raw_space: Any,
) -> tuple[ParameterSpace, ...]:
    if not isinstance(raw_space, dict) or not raw_space:
        raise ConflictError("组件假设没有可执行的参数空间")
    aliases = {
        (
            "ema_mtf_pullback_scalp_20x_v0",
            "exit_rule_variant",
        ): "structure_exit_variant",
    }
    spaces: list[ParameterSpace] = []
    for raw_name, raw_values in raw_space.items():
        name = aliases.get((strategy_spec_id, str(raw_name)), str(raw_name))
        if not isinstance(raw_values, list) or not raw_values:
            raise ConflictError(f"组件参数 {name} 缺少有限候选值")
        spaces.append(
            ParameterSpace(
                name=name,
                kind="categorical",
                values=tuple(raw_values),
            )
        )
    return tuple(spaces)


LOCAL_ORIGINS = local_origins()


def connector_status(root: Path) -> dict[str, Any]:
    heartbeat_path = root / "runtime" / "agent-connector" / "heartbeat.json"
    heartbeat: dict[str, Any] = {}
    try:
        loaded = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            heartbeat = loaded
    except (OSError, json.JSONDecodeError):
        pass
    updated_at = heartbeat.get("updated_at_epoch")
    heartbeat_fresh = isinstance(updated_at, (int, float)) and (
        time.time() - float(updated_at) <= 15
    )
    codex_binary = resolve_codex_binary()
    connected = heartbeat_fresh and codex_binary is not None
    return {
        "supported": True,
        "available": connected,
        "connection_status": (
            "connected" if connected else "connector_not_running"
        ),
        "codex_cli_detected": codex_binary is not None,
        "connector_heartbeat_fresh": heartbeat_fresh,
        "state": heartbeat.get("state") if heartbeat_fresh else "offline",
        "active_agent_run_id": (
            heartbeat.get("agent_run_id") if heartbeat_fresh else None
        ),
        "note": (
            "网页任务会由独立 Local Connector 领取并交给 Codex CLI。"
            if connected
            else "请用 ./scripts/dev.sh 启动 API、网页、Worker 和 Local Connector。"
        ),
    }


def create_app(
    *, root: Path | None = None, database_path: Path | None = None
) -> FastAPI:
    resolved_root = (root or project_root()).resolve()
    resolved_database = (database_path or app_database_path()).resolve()
    try:
        resolved_database.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("product database must remain inside the project root") from exc

    repository = SQLiteProductRepository(resolved_database)
    budget_policy_path = resolved_root / "configs/research_budgets/default.yaml"
    budget_policy_data: dict[str, Any] | None = None
    budget_policy = None
    if budget_policy_path.is_file():
        budget_policy = ResearchBudgetPolicyReader(resolved_root).read()
        budget_policy_data = {
            "max_hypotheses": budget_policy.max_hypotheses,
            "max_trials_total": budget_policy.max_trials_total,
            "max_compute_minutes": budget_policy.max_compute_minutes,
            "max_locked_test_uses": budget_policy.max_locked_test_uses,
            "require_user_approval_for_new_hypothesis": (
                budget_policy.require_user_approval_for_new_hypothesis
            ),
        }
    service = ResearchApplicationService(repository, budget_policy=budget_policy_data)
    guided_service = GuidedResearchService(
        repository, max_directions=budget_policy.max_directions if budget_policy else 3
    )
    component_aggregator = DefaultComponentEvidenceAggregator(repository)
    pipeline_service = PipelineApplicationService(
        repository, PipelineProfileCatalog(resolved_root)
    )
    authorization_service = ResearchAuthorizationService(repository)
    authorization_policy_reader = ResearchAuthorizationPolicyReader(resolved_root)
    provider = InMemoryOpenAICompatibleProvider()
    project_reader = ProjectStatusReader(resolved_root)
    agent_manifest_reader = AgentManifestReader(resolved_root)
    data_reader = DataSummaryReader(resolved_root)
    storage_reporter = StorageReporter(resolved_root)
    artifact_store = LocalArtifactStore(resolved_root)
    equity_reader = RunBundleEquityReader(repository, artifact_store)
    trial_equity_reader = TrialEquityReader(repository, artifact_store)
    versioning_reader = VersioningPolicyReader(resolved_root)
    execution_model_catalog = ExecutionModelCatalog(resolved_root)
    engine_registry = BacktestEngineRegistry(
        (
            NativeBacktestEngineAdapter(lambda _job: {}),
            FreqtradeBacktestEngineAdapter(),
        )
    )

    application = FastAPI(
        title="Quant Research Lab API",
        version=__version__,
        description=(
            "Local-only research API. It exposes no live-trading, arbitrary-shell, "
            "credential, or automatic production-promotion endpoint."
        ),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    application.state.repository = repository
    application.state.research_service = service
    application.state.pipeline_service = pipeline_service
    application.state.research_authorization_service = authorization_service
    application.state.guided_research_service = guided_service
    application.state.llm_provider = provider
    provider_dispatcher = WebProviderDispatcher(service, provider)
    application.state.provider_dispatcher = provider_dispatcher

    @application.exception_handler(NotFoundError)
    async def handle_not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ConflictError)
    async def handle_conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(ApprovalRequiredError)
    async def handle_approval(
        _request: Request, exc: ApprovalRequiredError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(InvalidJobError)
    async def handle_invalid_job(_request: Request, exc: InvalidJobError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(ProviderNotConfiguredError)
    async def handle_provider(
        _request: Request, exc: ProviderNotConfiguredError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(ExperimentPlanValidationError)
    async def handle_invalid_plan(
        _request: Request, exc: ExperimentPlanValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(GatePolicyError)
    async def handle_gate_policy(
        _request: Request, exc: GatePolicyError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.get("/health", tags=["system"])
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "local_research_only",
            "live_trading_enabled": False,
        }

    @application.get("/", include_in_schema=False)
    def api_root() -> dict[str, Any]:
        web_studio = (
            f"http://127.0.0.1:{os.environ.get('QUANT_LAB_WEB_PORT', '3100')}/studio"
        )
        return {
            "service": "Quant Research Lab API",
            "status": "ok",
            "message": f"网页工作台不在 API 端口，请打开 {web_studio}",
            "web_studio": web_studio,
            "health": "/health",
            "openapi": "/docs",
            "live_trading_enabled": False,
        }

    @application.get("/api/project/status", tags=["system"])
    def project_status() -> dict[str, Any]:
        provider_status = asdict(provider.status())
        return project_reader.read(provider_status=provider_status)

    @application.get(
        "/api/research-modes",
        response_model=list[ResearchModeDefinitionResponse],
        tags=["research"],
    )
    def research_modes() -> Any:
        return service.list_research_mode_definitions()

    @application.get(
        "/api/research/sessions",
        response_model=list[SessionResponse],
        tags=["research"],
    )
    def list_sessions() -> Any:
        return service.list_research_sessions()

    @application.post(
        "/api/research/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research"],
    )
    def create_session(body: CreateSessionRequest) -> Any:
        return service.create_research_session(
            title=body.title,
            assistant_entry_mode=AssistantEntryMode(body.assistant_entry_mode),
        )

    @application.patch(
        "/api/research/sessions/{session_id}/assistant-entry",
        response_model=SessionResponse,
        tags=["agent-control-plane"],
    )
    def update_assistant_entry(
        session_id: str, body: UpdateAssistantEntryModeRequest
    ) -> Any:
        return service.update_assistant_entry_mode(
            session_id=session_id,
            assistant_entry_mode=AssistantEntryMode(body.assistant_entry_mode),
        )

    @application.get(
        "/api/research/sessions/{session_id}/mode",
        response_model=SessionResponse,
        tags=["research"],
    )
    def get_session_mode(session_id: str) -> Any:
        return service.get_research_session(session_id)

    @application.get(
        "/api/research/sessions/{session_id}/agent-occupancy",
        response_model=SessionAgentOccupancyResponse,
        tags=["agent-control-plane"],
    )
    def get_session_agent_occupancy(session_id: str) -> Any:
        return service.get_session_agent_occupancy(session_id)

    @application.put(
        "/api/research/sessions/{session_id}/mode",
        response_model=SessionResponse,
        tags=["research"],
    )
    def update_session_mode(
        session_id: str, body: UpdateResearchModeRequest
    ) -> Any:
        config = (
            body.mode_config.model_dump(exclude_none=True)
            if body.mode_config is not None
            else None
        )
        return service.update_research_mode(
            session_id=session_id,
            research_mode=body.mode,
            mode_config=config,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.get(
        "/api/research/sessions/{session_id}",
        response_model=SessionDetailResponse,
        tags=["research"],
    )
    def get_session(session_id: str) -> Any:
        return {
            "session": service.get_research_session(session_id),
            "messages": service.list_messages(session_id),
            "drafts": service.list_strategy_drafts(session_id),
        }

    @application.get(
        "/api/research/sessions/{session_id}/budget",
        tags=["research"],
    )
    def research_budget(session_id: str) -> dict[str, Any]:
        budget = service.get_research_budget(session_id)
        return {
            **asdict(budget),
            "remaining_hypotheses": budget.remaining_hypotheses,
            "remaining_trials": budget.remaining_trials,
            "remaining_compute_minutes": budget.remaining_compute_minutes,
            "remaining_locked_test_uses": budget.remaining_locked_test_uses,
        }

    @application.get("/api/research-budget/default", tags=["research"])
    def default_research_budget() -> dict[str, Any]:
        if not budget_policy_path.is_file():
            return {
                "available": False,
                "reason": "research budget policy not found",
            }
        return {
            "available": True,
            **asdict(ResearchBudgetPolicyReader(resolved_root).read()),
        }

    @application.post(
        "/api/research/sessions/{session_id}/messages",
        response_model=MessageResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research"],
    )
    def create_message(session_id: str, body: CreateMessageRequest) -> Any:
        return service.add_user_message(session_id=session_id, content=body.content)

    @application.post(
        "/api/research/sessions/{session_id}/intakes",
        response_model=StrategyDraftResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["strategies"],
    )
    def create_intake(session_id: str, body: CreateIntakeRequest) -> Any:
        return service.create_strategy_intake(
            session_id=session_id,
            source_type=body.source_type,
            source_name=body.source_name,
            raw_content=body.raw_content,
        )

    @application.get(
        "/api/research/sessions/{session_id}/agent-runs",
        response_model=list[AgentRunResponse],
        tags=["agent-control-plane"],
    )
    def session_agent_runs(session_id: str) -> Any:
        return service.list_session_agent_runs(session_id)

    @application.post(
        "/api/strategy-drafts/{draft_id}/agent-runs",
        response_model=AgentRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["agent-control-plane"],
    )
    def create_formalization_agent_run(
        draft_id: str, body: CreateAgentFormalizationRequest
    ) -> Any:
        draft = repository.get_draft(draft_id)
        session = repository.get_session(draft.session_id)
        requested_mode = AssistantEntryMode(body.entry_mode)
        if session.assistant_entry_mode != requested_mode:
            raise ConflictError(
                "当前研究会话的助手入口与本次任务不一致，请先切换入口。"
            )
        if requested_mode == AssistantEntryMode.WEB_LOCAL_CONNECTOR:
            local_status = connector_status(resolved_root)
            if not local_status["available"]:
                raise ConflictError(local_status["note"])
            run = service.create_agent_run(
                session_id=draft.session_id,
                agent_name="本地 Codex 助手",
                agent_provider=AgentProviderKind.EXTERNAL_LOCAL_AGENT,
                execution_target=ExecutionTargetKind.LOCAL_RUNTIME,
                plan_summary="读取策略草稿并生成待用户确认的结构化提案",
                subject_id=draft.id,
                task_type="strategy_formalization",
            )
            return run
        if not provider.status().configured:
            raise ProviderNotConfiguredError("请先在网页中配置模型和 API Key")
        run = service.create_agent_run(
            session_id=draft.session_id,
            agent_name=f"网页模型 · {provider.status().provider}",
            agent_provider=AgentProviderKind.BYOK_PROVIDER,
            execution_target=ExecutionTargetKind.LOCAL_RUNTIME,
            plan_summary="调用网页配置的模型生成待用户确认的结构化提案",
            subject_id=draft.id,
            task_type="strategy_formalization",
        )
        provider_dispatcher.submit(run)
        return run

    @application.get(
        "/api/strategy-drafts",
        response_model=list[StrategyDraftResponse],
        tags=["strategies"],
    )
    def list_drafts(session_id: str | None = Query(default=None)) -> Any:
        return service.list_strategy_drafts(session_id)

    @application.get(
        "/api/strategy-versions",
        response_model=list[StrategyVersionResponse],
        tags=["strategies"],
    )
    def list_strategy_versions(
        strategy_id: str | None = Query(default=None),
    ) -> Any:
        return service.list_strategy_versions(strategy_id)

    @application.get("/api/factors", tags=["research-components"])
    def factors() -> list[dict[str, Any]]:
        registry = resolved_root / "factor_library" / "registry.sqlite3"
        return [_factor_registry_item(row) for row in list_factors(registry)]

    @application.post(
        "/api/component-candidates/{candidate_id}/promote-factor-candidate",
        status_code=status.HTTP_201_CREATED,
        tags=["research-components"],
    )
    def promote_component_to_factor_candidate(
        candidate_id: str, body: PromoteFactorCandidateRequest
    ) -> dict[str, Any]:
        if not body.confirmed_by_user or body.subject_id != candidate_id:
            raise ApprovalRequiredError(
                "沉淀因子候选要求用户明确确认当前组件对象"
            )
        candidate = repository.get_component_candidate(candidate_id)
        if candidate.status != "component_candidate":
            raise ConflictError("只有组件候选可以沉淀为独立因子候选")
        if candidate.archived_at is not None:
            raise ConflictError("已归档组件不能沉淀因子候选，请先恢复")
        evidence = repository.get_component_evidence(candidate.evidence_id)
        logic_signature = candidate.logic_signature or evidence.logic_signature
        market_profile = (
            candidate.target_market_profile or evidence.target_market_profile
        )
        timeframe = candidate.timeframe or evidence.timeframe
        if not logic_signature or not market_profile or not timeframe:
            raise ConflictError("组件缺少逻辑签名、适用市场或周期，不能沉淀因子候选")
        if not evidence.source_trial_ids:
            raise ConflictError("组件缺少独立 Trial 证据，不能沉淀因子候选")
        evidence_markers = {
            str(evidence.evidence_level).lower(),
            str(evidence.out_of_sample_status).lower(),
            str(evidence.lineage.get("evidence_mode", "")).lower(),
        }
        if evidence_markers & {"fixture", "unavailable", "not_tested"}:
            raise ConflictError("fixture 或未执行证据不能沉淀因子候选")

        factor_id = f"factor_{logic_signature[:24]}"
        plan_cost_model: dict[str, Any] = {}
        if evidence.source_experiment_plan_id:
            try:
                plan_cost_model = dict(
                    repository.get_experiment_plan(
                        evidence.source_experiment_plan_id
                    ).cost_model
                )
            except NotFoundError:
                plan_cost_model = {}
        registry = resolved_root / "factor_library" / "registry.sqlite3"
        try:
            return _factor_registry_item(get_factor(registry, factor_id))
        except KeyError:
            pass
        register_factor(
            registry,
            factor_id=factor_id,
            name=(body.name or candidate.name).strip(),
            category=evidence.component_type,
            status="candidate",
            description=(
                body.description
                or "由策略组件候选受控沉淀；仍需按市场、周期和成本独立验证。"
            ),
            metadata={
                "source_component_candidate_id": candidate.id,
                "source_component_evidence_id": evidence.id,
                "source_strategy_version_id": evidence.source_strategy_version_id,
                "source_experiment_plan_id": evidence.source_experiment_plan_id,
                "source_trial_ids": list(evidence.source_trial_ids),
                "logic_signature": logic_signature,
                "market_profile": market_profile,
                "timeframe": timeframe,
                "cost_model": plan_cost_model,
                "evidence_level": evidence.evidence_level,
                "out_of_sample_status": evidence.out_of_sample_status,
                "regimes": list(evidence.regimes),
                "automatic_validation": False,
            },
        )
        repository.append_event(
            AuditEvent(
                id=None,
                event_type="factor_candidate.promoted_from_component",
                aggregate_type="factor",
                aggregate_id=factor_id,
                actor_type="user",
                payload={
                    "subject_id": candidate.id,
                    "component_evidence_id": evidence.id,
                    "market_profile": market_profile,
                    "timeframe": timeframe,
                    "automatic_validation": False,
                },
                created_at=utc_now(),
            )
        )
        return _factor_registry_item(get_factor(registry, factor_id))

    @application.post(
        "/api/factors/{factor_id}/archive",
        tags=["research-components"],
    )
    def archive_factor(
        factor_id: str, body: FactorStatusRequest
    ) -> dict[str, Any]:
        if not body.confirmed_by_user or body.subject_id != factor_id:
            raise ApprovalRequiredError("归档因子要求用户明确确认当前因子对象")
        registry = resolved_root / "factor_library" / "registry.sqlite3"
        try:
            row = set_factor_status(
                registry, factor_id=factor_id, status="retired"
            )
        except KeyError as exc:
            raise NotFoundError(str(exc)) from exc
        repository.append_event(
            AuditEvent(
                id=None,
                event_type="factor_candidate.archived",
                aggregate_type="factor",
                aggregate_id=factor_id,
                actor_type="user",
                payload={"subject_id": factor_id, "physical_deletion": False},
                created_at=utc_now(),
            )
        )
        return _factor_registry_item(row)

    @application.post(
        "/api/factors/{factor_id}/restore",
        tags=["research-components"],
    )
    def restore_factor(
        factor_id: str, body: FactorStatusRequest
    ) -> dict[str, Any]:
        if not body.confirmed_by_user or body.subject_id != factor_id:
            raise ApprovalRequiredError("恢复因子要求用户明确确认当前因子对象")
        registry = resolved_root / "factor_library" / "registry.sqlite3"
        try:
            current = get_factor(registry, factor_id)
            if current["status"] != "retired":
                return _factor_registry_item(current)
            row = set_factor_status(
                registry, factor_id=factor_id, status="candidate"
            )
        except KeyError as exc:
            raise NotFoundError(str(exc)) from exc
        repository.append_event(
            AuditEvent(
                id=None,
                event_type="factor_candidate.restored",
                aggregate_type="factor",
                aggregate_id=factor_id,
                actor_type="user",
                payload={"subject_id": factor_id, "restored_status": "candidate"},
                created_at=utc_now(),
            )
        )
        return _factor_registry_item(row)

    @application.post(
        "/api/strategy-drafts/{draft_id}/formalize",
        response_model=StrategyDraftResponse,
        tags=["strategies"],
    )
    def formalize_strategy(draft_id: str, body: FormalizeStrategyRequest) -> Any:
        if body.subject_id != draft_id:
            raise GatePolicyError("approval subject_id does not match the draft")
        return service.formalize_strategy(
            draft_id=draft_id,
            structured_content=body.structured_content,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.post(
        "/api/strategy-drafts/{draft_id}/freeze-baseline",
        response_model=StrategyVersionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["strategies"],
    )
    def freeze_baseline(draft_id: str, body: FreezeBaselineRequest) -> Any:
        if body.subject_id is not None and body.subject_id != draft_id:
            raise GatePolicyError("approval subject_id does not match the draft")
        draft = repository.get_draft(draft_id)
        if draft.status != "awaiting_confirmation" or not draft.structured_content:
            raise ConflictError(
                "baseline freeze requires confirmed structured strategy rules; "
                "formalize the draft before freezing"
            )
        return service.freeze_baseline(
            draft_id=draft_id, confirmed_by_user=body.confirmed_by_user
        )

    @application.get(
        "/api/jobs", response_model=list[JobResponse], tags=["jobs"]
    )
    def list_jobs() -> Any:
        return service.list_jobs()

    @application.get(
        "/api/experiment-plans",
        response_model=list[ExperimentPlanResponse],
        tags=["experiments"],
    )
    def list_experiment_plans(
        baseline_version_id: str | None = Query(default=None),
    ) -> Any:
        plans = list(service.list_experiment_plans())
        if baseline_version_id is not None:
            plans = [
                item
                for item in plans
                if item.baseline_version_id == baseline_version_id
            ]
        return plans

    @application.post(
        "/api/experiment-plans",
        response_model=ExperimentPlanResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["experiments"],
    )
    def create_experiment_plan(body: CreateExperimentPlanRequest) -> Any:
        return service.create_experiment_plan(
            baseline_version_id=body.baseline_version_id,
            hypothesis=body.hypothesis,
            parameter_space=tuple(
                ParameterSpace(**item.model_dump()) for item in body.parameter_space
            ),
            objectives=tuple(Objective(**item.model_dump()) for item in body.objectives),
            constraints=tuple(
                Constraint(**item.model_dump()) for item in body.constraints
            ),
            data_splits=body.data_splits,
            cost_model=body.cost_model,
            max_trials=body.max_trials,
            time_budget_seconds=body.time_budget_seconds,
            stopping_conditions=body.stopping_conditions,
            proposal_id=body.proposal_id,
            candidate_version_id=body.candidate_version_id,
            search_strategy=body.search_strategy,
            random_seed=body.random_seed,
        )

    @application.post(
        "/api/experiment-plans/{plan_id}/approve",
        response_model=ExperimentPlanResponse,
        tags=["experiments"],
    )
    def approve_experiment_plan(plan_id: str, body: FreezeBaselineRequest) -> Any:
        if body.subject_id is not None and body.subject_id != plan_id:
            raise GatePolicyError("approval subject_id does not match the experiment plan")
        return service.approve_experiment_plan(
            plan_id=plan_id, confirmed_by_user=body.confirmed_by_user
        )

    @application.post(
        "/api/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["jobs"],
    )
    def create_job(body: CreateJobRequest) -> Any:
        return service.create_job(job_type=body.job_type, payload=body.payload)

    @application.post(
        "/api/jobs/{job_id}/cancel",
        response_model=JobResponse,
        tags=["jobs"],
    )
    def cancel_job(job_id: str, body: JobActionRequest) -> Any:
        return service.cancel_job(
            job_id=job_id,
            subject_id=body.subject_id,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.post(
        "/api/jobs/{job_id}/retry",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["jobs"],
    )
    def retry_job(job_id: str, body: JobActionRequest) -> Any:
        return service.retry_job(
            job_id=job_id,
            subject_id=body.subject_id,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.get("/api/jobs/{job_id}/logs", tags=["jobs"])
    def list_job_logs(job_id: str) -> Any:
        return service.list_job_logs(job_id)

    @application.get("/api/data/summary", tags=["data"])
    def data_summary() -> dict[str, Any]:
        return data_reader.read()

    @application.get("/api/storage/report", tags=["data"])
    def storage_report() -> dict[str, Any]:
        return storage_reporter.read()

    @application.get("/api/backtest-engines", tags=["research-pipeline"])
    def backtest_engines() -> list[dict[str, Any]]:
        return [asdict(item) for item in engine_registry.capabilities()]

    def reconciliation_status(subject_id: str, market_profile: str) -> dict[str, Any]:
        viability = next(
            (
                item
                for item in pipeline_service.list_gate_results(subject_id=subject_id)
                if item.gate_name == "viability"
                and item.status == "passed"
                and item.market_profile == market_profile
            ),
            None,
        )
        rejected = any(
            item.strategy_version_id == subject_id
            and item.market_profile == market_profile
            and item.outcome_type == "rejected"
            for item in pipeline_service.list_strategy_outcomes()
        )
        if rejected:
            reason = "该策略已被拒绝，禁止重新执行第二引擎对账。"
        elif viability is None:
            reason = "同一策略与市场尚未通过可行性门槛。"
        else:
            reason = (
                "可行性门槛已通过，但真实 Freqtrade IStrategy 转换与外部执行器尚未接入。"
            )
        return {
            "subject_id": subject_id,
            "market_profile": market_profile,
            "viability_gate_result_id": viability.id if viability else None,
            "eligible": bool(viability and not rejected),
            "engine_id": "freqtrade_2026_6",
            "engine_boundary": "optional_external_process",
            "implementation_status": "not_connected",
            "job_created": False,
            "reason": reason,
            "locked_test_allowed": False,
            "live_trade_available": False,
        }

    @application.get(
        "/api/engine-reconciliation/status",
        tags=["research-pipeline"],
    )
    def get_engine_reconciliation_status(
        subject_id: str = Query(min_length=1),
        market_profile: str = Query(min_length=1),
    ) -> dict[str, Any]:
        return reconciliation_status(subject_id, market_profile)

    @application.post(
        "/api/engine-reconciliation/jobs",
        tags=["research-pipeline"],
    )
    def create_engine_reconciliation_job(
        body: CreateEngineReconciliationRequest,
    ) -> dict[str, Any]:
        if not body.confirmed_by_user:
            raise ApprovalRequiredError(
                "engine reconciliation requires explicit user confirmation"
            )
        current = reconciliation_status(body.subject_id, body.market_profile)
        if not current["eligible"]:
            raise GatePolicyError(str(current["reason"]))
        raise ConflictError(
            "Freqtrade external reconciliation executor is not connected; "
            "no Job was created and no strategy was run"
        )

    @application.get("/api/execution-models", tags=["research-pipeline"])
    def execution_models() -> list[dict[str, Any]]:
        """Expose reviewed research-only execution semantics; never submit orders."""
        return list(execution_model_catalog.list_summaries())

    @application.get("/api/settings/status", tags=["system"])
    def settings_status() -> dict[str, Any]:
        try:
            versioning = {"available": True, **asdict(versioning_reader.read())}
        except ValueError as exc:
            versioning = {"available": False, "reason": str(exc)}
        return {
            "ai_provider": asdict(provider.status()),
            "api_bind_default": "127.0.0.1",
            "live_trading_enabled": False,
            "credentials_api_available": False,
            "model_key_memory_api_available": True,
            "model_key_persistence_available": False,
            "versioning": versioning,
            "live_trade_safety_guard": {
                "status": "PASS",
                "expected_rejection_exit_code": 3,
                "message": (
                    "Live trade safety guard: PASS "
                    "(expected rejection, exit code 3)."
                ),
            },
        }

    @application.get(
        "/api/agent/provider",
        response_model=ProviderStatusResponse,
        tags=["agent-control-plane"],
    )
    def provider_configuration_status() -> Any:
        return provider.status()

    @application.post(
        "/api/agent/provider",
        response_model=ProviderStatusResponse,
        tags=["agent-control-plane"],
    )
    def configure_provider(body: ConfigureProviderRequest) -> Any:
        try:
            return provider.configure(
                provider_name=body.provider_name,
                base_url=body.base_url,
                model=body.model,
                api_key=body.api_key,
            )
        except ValueError as exc:
            raise InvalidJobError(str(exc)) from exc

    @application.delete(
        "/api/agent/provider",
        response_model=ProviderStatusResponse,
        tags=["agent-control-plane"],
    )
    def clear_provider() -> Any:
        provider.clear()
        return provider.status()

    @application.get("/api/versioning/policy", tags=["system"])
    def versioning_policy() -> dict[str, Any]:
        try:
            return {"available": True, **asdict(versioning_reader.read())}
        except ValueError as exc:
            return {"available": False, "reason": str(exc)}

    @application.get("/api/agent/status", tags=["agent-control-plane"])
    def agent_status() -> dict[str, Any]:
        local_connector = connector_status(resolved_root)
        return {
            "architecture": "agent_first_hybrid",
            "default_run_mode": "guided",
            "active_configuration": {
                "agent_provider": "selected_per_research_session",
                "execution_target": ExecutionTargetKind.LOCAL_RUNTIME,
                "data_location": "local_project",
                "privacy_note": (
                    "本地助手数据留在本机；网页模型仅把当前策略发给用户配置的模型服务。"
                ),
            },
            "capability_matrix": {
                AgentProviderKind.EXTERNAL_LOCAL_AGENT: "supported",
                AgentProviderKind.EMBEDDED_CLOUD_PROVIDER: "adapter_boundary",
                AgentProviderKind.BYOK_PROVIDER: "supported",
                AgentProviderKind.LOCAL_MODEL_PROVIDER: "planned_unsupported",
                ExecutionTargetKind.LOCAL_RUNTIME: "supported",
                ExecutionTargetKind.HOSTED_SANDBOX: "planned_unsupported",
            },
            "external_agent": {
                "supported": True,
                "preferred_for_phase_0": False,
                "connection_status": "direct_interaction",
                "note": (
                    "用户直接在 Codex 中研究；网页只显示同一项目中的会话、审批和结果。"
                ),
            },
            "local_connector": local_connector,
            "embedded_provider": asdict(provider.status()),
            "model_policy": agent_manifest_reader.summary(),
            "controls": {
                "pause_cancel_reserved": True,
                "approval_required_for": [
                    "freeze_baseline",
                    "accept_strategy_change",
                    "run_parameter_search",
                ],
            },
        }

    @application.get("/api/agent/manifest", tags=["agent-control-plane"])
    def agent_manifest() -> dict[str, Any]:
        """Expose the fixed model capability contract without provider secrets."""
        return agent_manifest_reader.read()

    @application.get(
        "/api/audit/events",
        response_model=list[AuditEventResponse],
        tags=["agent-control-plane"],
    )
    def audit_events(limit: int = Query(default=100, ge=1, le=500)) -> Any:
        return service.list_audit_events(limit=limit)

    @application.get(
        "/api/research/sessions/{session_id}/handoff",
        response_model=ResearchHandoffResponse,
        tags=["agent-control-plane"],
    )
    def latest_session_handoff(session_id: str) -> Any:
        return service.get_latest_session_handoff(session_id)

    @application.get(
        "/api/agent-runs/{agent_run_id}/handoff",
        response_model=ResearchHandoffResponse,
        tags=["agent-control-plane"],
    )
    def latest_agent_run_handoff(agent_run_id: str) -> Any:
        return service.get_latest_agent_run_handoff(agent_run_id)

    @application.get(
        "/api/research-authorizations/template",
        tags=["research-pipeline"],
    )
    def research_authorization_template() -> dict[str, Any]:
        return asdict(authorization_policy_reader.read())

    @application.get(
        "/api/research-authorizations",
        response_model=list[ResearchAuthorizationResponse],
        tags=["research-pipeline"],
    )
    def research_authorizations(
        session_id: str | None = Query(default=None),
        subject_id: str | None = Query(default=None),
    ) -> Any:
        return authorization_service.list(
            session_id=session_id, subject_id=subject_id
        )

    @application.post(
        "/api/research-authorizations",
        response_model=ResearchAuthorizationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def create_research_authorization(
        body: CreateResearchAuthorizationRequest,
    ) -> Any:
        values = body.model_dump()
        values["allowed_stages"] = tuple(values["allowed_stages"])
        values["stop_conditions"] = tuple(values["stop_conditions"])
        return authorization_service.create(**values)

    @application.get(
        "/api/research-authorizations/{authorization_id}",
        response_model=ResearchAuthorizationResponse,
        tags=["research-pipeline"],
    )
    def research_authorization(authorization_id: str) -> Any:
        return authorization_service.get(authorization_id)

    @application.get(
        "/api/research-authorizations/{authorization_id}/stages",
        response_model=list[ResearchAuthorizationStageResponse],
        tags=["research-pipeline"],
    )
    def research_authorization_stages(authorization_id: str) -> Any:
        return authorization_service.stages(authorization_id)

    @application.post(
        "/api/research-authorizations/{authorization_id}/start",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def start_research_authorization(
        authorization_id: str,
        body: StartResearchAuthorizationRequest,
    ) -> Any:
        if not body.confirmed_by_user:
            raise ApprovalRequiredError(
                "starting an authorized pipeline requires explicit confirmation"
            )
        authorization = authorization_service.get(authorization_id)
        if authorization.subject_id != body.subject_id:
            raise GatePolicyError(
                "pipeline start subject_id does not match the authorization"
            )
        authorization_service.assert_scope_covers(
            authorization.id,
            subject_id=authorization.subject_id,
            stages=("correctness", "smoke", "fast_screen", "viability"),
        )
        existing = [
            job
            for job in repository.list_jobs()
            if job.job_type == "pipeline_execution"
            and job.payload.get("authorization_id") == authorization.id
        ]
        if existing:
            raise ConflictError(
                "authorized pipeline already has a Job; inspect or explicitly retry it"
            )
        agent_run = service.create_agent_run(
            session_id=authorization.session_id,
            agent_name="authorized-pipeline-worker",
            plan_summary=(
                "correctness → smoke → fast_screen → viability；"
                "失败后仅运行授权内廉价诊断"
            ),
        )
        return service.create_job(
            job_type="pipeline_execution",
            payload={
                "authorization_id": authorization.id,
                "session_id": authorization.session_id,
                "subject_id": authorization.subject_id,
                "agent_run_id": agent_run.id,
                "pipeline_profile": "fast_screen",
                "locked_test_used": False,
            },
        )

    @application.get(
        "/api/pipeline-profiles",
        response_model=list[PipelineProfileResponse],
        tags=["research-pipeline"],
    )
    def pipeline_profiles() -> Any:
        return [profile_as_dict(profile) for profile in pipeline_service.list_profiles()]

    @application.post(
        "/api/gates/evaluate",
        response_model=GateEvaluationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def evaluate_gate(body: EvaluateGateRequest) -> Any:
        return pipeline_service.evaluate_gate(**body.model_dump())

    @application.get(
        "/api/gates/results",
        response_model=list[GateEvaluationResponse],
        tags=["research-pipeline"],
    )
    def gate_results(subject_id: str | None = Query(default=None)) -> Any:
        return pipeline_service.list_gate_results(subject_id=subject_id)

    @application.get(
        "/api/strategy-outcomes",
        response_model=list[StrategyOutcomeResponse],
        tags=["research-pipeline"],
    )
    def strategy_outcomes(
        strategy_version_id: str | None = Query(default=None),
    ) -> Any:
        outcomes = list(pipeline_service.list_strategy_outcomes())
        if strategy_version_id is not None:
            outcomes = [
                item
                for item in outcomes
                if item.strategy_version_id == strategy_version_id
            ]
        return outcomes

    @application.post(
        "/api/strategy-outcomes",
        response_model=StrategyOutcomeResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def create_strategy_outcome(body: CreateStrategyOutcomeRequest) -> Any:
        return pipeline_service.create_strategy_outcome(**body.model_dump())

    @application.get(
        "/api/component-candidates",
        response_model=list[ComponentCandidateResponse],
        tags=["research-components"],
    )
    def component_candidates(
        source_strategy_version_id: str | None = Query(default=None),
        include_archived: bool = Query(default=False),
    ) -> Any:
        candidates = list(pipeline_service.list_component_candidates())
        if not include_archived:
            candidates = [item for item in candidates if item.archived_at is None]
        if source_strategy_version_id is None:
            return candidates
        evidence_ids = {
            item.id
            for item in pipeline_service.list_component_evidence()
            if item.source_strategy_version_id == source_strategy_version_id
        }
        return [item for item in candidates if item.evidence_id in evidence_ids]

    @application.post(
        "/api/component-candidates/{candidate_id}/archive",
        response_model=ComponentCandidateResponse,
        tags=["research-components"],
    )
    def archive_component_candidate(
        candidate_id: str, body: ComponentArchiveRequest
    ) -> Any:
        return pipeline_service.archive_component_candidate(
            candidate_id=candidate_id,
            subject_id=body.subject_id,
            reason=body.reason,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.post(
        "/api/component-candidates/{candidate_id}/restore",
        response_model=ComponentCandidateResponse,
        tags=["research-components"],
    )
    def restore_component_candidate(
        candidate_id: str, body: ComponentArchiveRequest
    ) -> Any:
        return pipeline_service.restore_component_candidate(
            candidate_id=candidate_id,
            subject_id=body.subject_id,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.get(
        "/api/component-evidence",
        response_model=list[ComponentEvidenceResponse],
        tags=["research-components"],
    )
    def component_evidence(
        market_profile: str | None = Query(default=None),
        timeframe: str | None = Query(default=None),
        regime: str | None = Query(default=None),
        evidence_level: str | None = Query(default=None),
        source_strategy_version_id: str | None = Query(default=None),
        sort_by: str = Query(default="created_at", pattern="^(created_at|incremental_net_return)$"),
    ) -> Any:
        evidence = list(pipeline_service.list_component_evidence())
        if source_strategy_version_id is not None:
            evidence = [
                item
                for item in evidence
                if item.source_strategy_version_id == source_strategy_version_id
            ]
        if market_profile is not None:
            evidence = [item for item in evidence if item.target_market_profile == market_profile]
        if timeframe is not None:
            evidence = [item for item in evidence if item.timeframe == timeframe]
        if regime is not None:
            evidence = [item for item in evidence if regime in item.regimes]
        if evidence_level is not None:
            evidence = [item for item in evidence if item.evidence_level == evidence_level]
        if sort_by == "incremental_net_return":
            evidence.sort(
                key=lambda item: item.incremental_metrics.get(
                    "best_incremental_net_return",
                    item.incremental_metrics.get("incremental_net_return", float("-inf")),
                ),
                reverse=True,
            )
        return evidence

    @application.post(
        "/api/component-evidence/aggregate",
        status_code=status.HTTP_201_CREATED,
        tags=["research-components"],
    )
    def aggregate_component_evidence(body: ComponentAggregationRequest) -> Any:
        values = body.model_dump()
        plan_id = values.pop("experiment_plan_id")
        return component_aggregator.aggregate(
            experiment_plan_id=plan_id, attribution=values
        )

    @application.post(
        "/api/component-candidates",
        response_model=ComponentCandidateResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-components"],
    )
    def create_component_candidate(body: CreateComponentCandidateRequest) -> Any:
        values = body.model_dump()
        name = values.pop("name")
        candidate_status = values.pop("status")
        evidence = pipeline_service.create_component_evidence(**values)
        return pipeline_service.create_component_candidate(
            evidence_id=evidence.id, name=name, status=candidate_status
        )

    @application.post(
        "/api/component-triage",
        response_model=ComponentTriageResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-components"],
    )
    def component_triage(body: CreateComponentCandidateRequest) -> Any:
        evidence, candidate = pipeline_service.triage_component(**body.model_dump())
        return {
            "evidence": evidence,
            "candidate": candidate,
            "automatic_validation": False,
        }

    @application.get(
        "/api/component-hypotheses",
        response_model=list[ComponentHypothesisResponse],
        tags=["research-components"],
    )
    def component_hypotheses(
        subject_id: str | None = Query(default=None),
    ) -> Any:
        return repository.list_component_hypotheses(subject_id=subject_id)

    @application.post(
        "/api/component-hypotheses/{hypothesis_id}/materialize-proposal",
        response_model=ImprovementDirectionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-components", "guided-research"],
    )
    def materialize_component_hypothesis(
        hypothesis_id: str,
        body: MaterializeComponentHypothesisRequest,
    ) -> Any:
        if not body.confirmed_by_user or body.subject_id != hypothesis_id:
            raise ApprovalRequiredError(
                "形成改进方案要求用户明确选择当前组件假设；"
                "这一步只创建待审阅草稿，不批准批量试验"
            )
        hypothesis = repository.get_component_hypothesis(hypothesis_id)
        if hypothesis.status == "rejected":
            raise ConflictError("已拒绝的组件假设不能形成新的改进方案")
        baseline = _baseline_for_component_hypothesis(
            repository,
            subject_id=hypothesis.subject_id,
        )
        strategy_plugins = build_builtin_strategy_plugins(
            resolved_root, repository
        )
        try:
            spec = strategy_plugins.find_for_snapshot(
                baseline.content_snapshot
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        parameter_space = _component_parameter_space(
            strategy_spec_id=spec.strategy_spec_id,
            raw_space=hypothesis.parameter_space,
        )
        _select_batch_evaluator(
            spec.evaluator_ids,
            parameter_names={item.name for item in parameter_space},
            strategy_spec_id=spec.strategy_spec_id,
            baseline_snapshot=baseline.content_snapshot,
        )
        contract = _proposal_research_contract(
            repository,
            baseline=baseline,
        ) or _config_research_contract(
            resolved_root=resolved_root,
            repository=repository,
            baseline=baseline,
            spec=spec,
        )
        estimated_trials = min(
            hypothesis.suggested_trials,
            max(
                1,
                math.prod(len(item.values) for item in parameter_space),
            ),
        )
        return guided_service.create_direction(
            baseline_version_id=baseline.id,
            subject_id=baseline.id,
            hypothesis=hypothesis.hypothesis,
            rule_diff={
                "component_type": hypothesis.component_type,
                "change_scope": "只修改当前组件，其他规则保持 Baseline 不变",
                "parameter_changes": {
                    item.name: list(item.values)
                    for item in parameter_space
                },
                "expected_improvement": hypothesis.expected_improvement,
                "research_contract_source": contract["contract_source"],
                "contamination_status": hypothesis.contamination_status,
            },
            evidence_refs=hypothesis.evidence_refs,
            parameter_space=parameter_space,
            data_splits=contract["data_splits"],
            cost_model=contract["cost_model"],
            objectives=contract["objectives"],
            constraints=contract["constraints"],
            estimated_trials=estimated_trials,
            estimated_minutes=max(5, min(45, estimated_trials * 3)),
            failure_conditions=hypothesis.failure_conditions,
            stopping_conditions=(
                f"完成 {estimated_trials} 个已列明的确定性参数组合后停止",
                "任何执行器尝试访问最终保留测试时立即停止",
                "预算、时间或内存达到上限时停止并保留已完成证据",
            ),
            rollback_plan=(
                "放弃当前候选版本，保留冻结基准、全部参数试验、"
                "失败原因和诊断证据；不自动晋升策略或因子。"
            ),
            source=hypothesis.source,
            source_component_hypothesis_id=hypothesis.id,
        )

    @application.get(
        "/api/regime-validations",
        response_model=list[RegimeValidationResponse],
        tags=["research-regimes"],
    )
    def regime_validations(
        subject_id: str | None = Query(default=None),
    ) -> Any:
        validations = list(pipeline_service.list_regime_validations())
        if subject_id is not None:
            validations = [
                item for item in validations if item.subject_id == subject_id
            ]
        return validations

    @application.post(
        "/api/regime-validations",
        response_model=RegimeValidationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-regimes"],
    )
    def create_regime_validation(body: CreateRegimeValidationRequest) -> Any:
        return pipeline_service.create_regime_validation(**body.model_dump())

    @application.post(
        "/api/regime-validation-jobs",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-regimes"],
    )
    def create_regime_validation_job(body: CreateRegimeValidationJobRequest) -> Any:
        payload = body.model_dump(exclude_none=True)
        payload["intent"] = body.mode
        payload["ex_ante_observable"] = True
        payload["locked_test_used"] = False
        return service.create_job(job_type="regime_validation", payload=payload)

    @application.post(
        "/api/research-diagnostics/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def create_research_diagnostic_job(
        body: CreateResearchDiagnosticJobRequest,
    ) -> Any:
        payload = body.model_dump(exclude_none=True)
        payload.update(
            {
                "intent": "post_viability_failure_diagnostics",
                "locked_test_used": False,
                "rerun_strategy": False,
                "formal_regime_validation": False,
            }
        )
        return service.create_job(job_type="research_diagnostic", payload=payload)

    @application.get("/api/run-bundles", tags=["reports"])
    def run_bundles(subject_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
        jobs = {item.id: item for item in repository.list_jobs()}
        bundles: list[dict[str, Any]] = []
        for report in repository.list_reports():
            job = jobs.get(report.job_id)
            if job is None:
                continue
            report_subject = (
                job.payload.get("subject_id")
                or job.payload.get("strategy_version_id")
                or job.payload.get("candidate_version_id")
            )
            if subject_id is not None and report_subject != subject_id:
                continue
            bundles.append(
                {
                    "bundle_id": report.id,
                    "job_id": report.job_id,
                    "subject_id": report_subject,
                    "job_type": job.job_type,
                    "status": job.status,
                    "report_type": report.report_type,
                    "report_artifact_key": report.artifact_key,
                    "summary": report.summary,
                    "retention": {
                        "manifest": "authoritative",
                        "key_metrics": "authoritative",
                        "failure_reason": "authoritative",
                        "trades": "retain_when_required_for_attribution",
                        "signals": "archiveable_after_attribution",
                        "equity": "retain_for_baseline_or_key_candidate",
                        "charts_and_detailed_logs": "rebuildable_or_archiveable",
                    },
                    "created_at": report.created_at,
                }
            )
        return bundles

    @application.get("/api/run-bundles/{bundle_id}", tags=["reports"])
    def run_bundle(bundle_id: str) -> dict[str, Any]:
        jobs = {item.id: item for item in repository.list_jobs()}
        report = next(
            (
                item
                for item in repository.list_reports()
                if item.id == bundle_id
            ),
            None,
        )
        if report is None:
            raise NotFoundError(f"run bundle not found: {bundle_id}")
        job = jobs.get(report.job_id)
        if job is None:
            raise NotFoundError(
                f"run bundle job not found: {report.job_id}"
            )
        report_subject = (
            job.payload.get("subject_id")
            or job.payload.get("strategy_version_id")
            or job.payload.get("candidate_version_id")
        )
        return {
            "bundle_id": report.id,
            "job_id": report.job_id,
            "subject_id": report_subject,
            "job_type": job.job_type,
            "status": job.status,
            "report_type": report.report_type,
            "report_artifact_key": report.artifact_key,
            "summary": report.summary,
            "retention": {
                "manifest": "authoritative",
                "key_metrics": "authoritative",
                "failure_reason": "authoritative",
                "trades": "retain_when_required_for_attribution",
                "signals": "archiveable_after_attribution",
                "equity": "retain_for_baseline_or_key_candidate",
                "charts_and_detailed_logs": "rebuildable_or_archiveable",
            },
            "created_at": report.created_at,
        }

    @application.get("/api/run-bundles/{bundle_id}/chart-series", tags=["reports"])
    def run_bundle_chart_series(
        bundle_id: str,
        max_points: int = Query(default=800, ge=50, le=2_000),
        market_timeframe: str = Query(
            default="1h",
            pattern="^(5m|15m|1h|4h|1d)$",
        ),
    ) -> dict[str, Any]:
        return equity_reader.read(
            bundle_id=bundle_id,
            max_points=max_points,
            market_timeframe=market_timeframe,
        )

    @application.get(
        "/api/run-bundles/{bundle_id}/market-window",
        tags=["reports"],
    )
    def run_bundle_market_window(
        bundle_id: str,
        center_time: str = Query(min_length=10),
        market_timeframe: str = Query(
            default="1h",
            pattern="^(5m|15m|1h|4h|1d)$",
        ),
        bars: int = Query(default=360, ge=120, le=800),
    ) -> dict[str, Any]:
        return equity_reader.read_market_window(
            bundle_id=bundle_id,
            market_timeframe=market_timeframe,
            center_time=center_time,
            bars=bars,
        )

    @application.get("/api/research-diagnostics/latest", tags=["reports"])
    def latest_research_diagnostic(subject_id: str = Query(min_length=1)) -> dict[str, Any]:
        jobs = {item.id: item for item in repository.list_jobs()}
        report = next(
            (
                item
                for item in repository.list_reports()
                if item.report_type == "post_viability_failure_diagnostics"
                and jobs.get(item.job_id) is not None
                and jobs[item.job_id].payload.get("subject_id") == subject_id
            ),
            None,
        )
        if report is None:
            raise NotFoundError(
                f"research diagnostic report not found for subject: {subject_id}"
            )
        return json.loads(artifact_store.get(report.artifact_key))

    @application.post(
        "/api/correctness-diagnostics/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-pipeline"],
    )
    def create_correctness_diagnostic_job(
        body: CreateCorrectnessDiagnosticJobRequest,
    ) -> Any:
        payload = body.model_dump(exclude_none=True)
        payload.update(
            {
                "intent": "baseline_correctness",
                "locked_test_used": False,
                "live_trading": False,
            }
        )
        return service.create_job(job_type="correctness_diagnostic", payload=payload)

    @application.get("/api/worker/resource-policy", tags=["system"])
    def worker_resource_policy() -> dict[str, Any]:
        policy_path = resolved_root / "configs/workers/local.yaml"
        if not policy_path.is_file():
            return {"available": False, "reason": "worker resource policy not found"}
        return {
            "available": True,
            **asdict(WorkerResourcePolicyReader(resolved_root).read()),
        }

    @application.get(
        "/api/improvement-directions",
        response_model=list[ImprovementDirectionResponse],
        tags=["guided-research"],
    )
    def improvement_directions(
        baseline_version_id: str | None = Query(default=None),
    ) -> Any:
        return guided_service.list_directions(
            baseline_version_id=baseline_version_id
        )

    @application.post(
        "/api/improvement-directions",
        response_model=ImprovementDirectionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["guided-research"],
    )
    def create_improvement_direction(body: CreateImprovementDirectionRequest) -> Any:
        values = body.model_dump()
        values["parameter_space"] = tuple(
            ParameterSpace(**item) for item in values["parameter_space"]
        )
        values["objectives"] = tuple(Objective(**item) for item in values["objectives"])
        values["constraints"] = tuple(
            Constraint(**item) for item in values["constraints"]
        )
        return guided_service.create_direction(**values)

    @application.post(
        "/api/improvement-directions/{proposal_id}/submit",
        response_model=ImprovementDirectionResponse,
        tags=["guided-research"],
    )
    def submit_improvement_direction(proposal_id: str) -> Any:
        return guided_service.submit_for_approval(proposal_id)

    @application.post(
        "/api/improvement-directions/{proposal_id}/budget",
        response_model=ImprovementDirectionResponse,
        tags=["guided-research"],
    )
    def revise_improvement_direction_budget(
        proposal_id: str, body: ProposalBudgetRequest
    ) -> Any:
        return guided_service.revise_budget(
            proposal_id=proposal_id,
            subject_id=body.subject_id,
            estimated_trials=body.estimated_trials,
            estimated_minutes=body.estimated_minutes,
        )

    @application.post(
        "/api/improvement-directions/{proposal_id}/approve",
        tags=["guided-research"],
    )
    def approve_improvement_direction(
        proposal_id: str, body: JobActionRequest
    ) -> Any:
        proposal, candidate = guided_service.approve(
            proposal_id=proposal_id,
            subject_id=body.subject_id,
            confirmed_by_user=body.confirmed_by_user,
        )
        return {"proposal": proposal, "candidate": candidate}

    @application.post(
        "/api/improvement-directions/{proposal_id}/launch-batch",
        tags=["guided-research"],
    )
    def launch_improvement_batch(
        proposal_id: str, body: LaunchImprovementBatchRequest
    ) -> Any:
        if not body.confirmed_by_user or body.subject_id != proposal_id:
            raise ApprovalRequiredError(
                "批量试验要求用户明确批准当前改进方向和其中列明的预算"
            )
        proposal = repository.get_proposal(proposal_id)
        if proposal.status == "waiting_approval":
            proposal, candidate = guided_service.approve(
                proposal_id=proposal_id,
                subject_id=body.subject_id,
                confirmed_by_user=True,
            )
        elif proposal.status in {"approved", "executing"}:
            if not proposal.candidate_version_id:
                raise ConflictError("已批准方向缺少不可变候选快照")
            candidate = repository.get_strategy_version(
                proposal.candidate_version_id
            )
        else:
            raise ConflictError(
                f"当前改进方向状态不能启动批量试验：{proposal.status}"
            )

        if (
            not proposal.baseline_version_id
            or not proposal.estimated_trials
            or not proposal.estimated_minutes
        ):
            raise ConflictError("改进方向缺少 Baseline 或已批准预算")
        baseline = repository.get_strategy_version(
            proposal.baseline_version_id
        )
        strategy_plugins = build_builtin_strategy_plugins(
            resolved_root, repository
        )
        try:
            spec = strategy_plugins.find_for_snapshot(
                baseline.content_snapshot
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        evaluator_id = _select_batch_evaluator(
            spec.evaluator_ids,
            parameter_names={
                item.name for item in proposal.parameter_space
            },
            strategy_spec_id=spec.strategy_spec_id,
            baseline_snapshot=baseline.content_snapshot,
        )

        matching_plans = [
            item
            for item in repository.list_experiment_plans()
            if item.proposal_id == proposal.id
        ]
        if len(matching_plans) > 1:
            raise ConflictError("同一改进方向存在多个试验计划，请先审阅异常研究状态")
        if matching_plans:
            plan = matching_plans[0]
        else:
            plan = service.create_experiment_plan(
                baseline_version_id=baseline.id,
                proposal_id=proposal.id,
                candidate_version_id=candidate.id,
                hypothesis=proposal.hypothesis,
                parameter_space=proposal.parameter_space,
                objectives=proposal.objectives,
                constraints=proposal.constraints,
                data_splits=proposal.data_splits,
                cost_model=proposal.cost_model,
                max_trials=proposal.estimated_trials,
                time_budget_seconds=proposal.estimated_minutes * 60,
                stopping_conditions=proposal.stopping_conditions,
                search_strategy="grid",
                random_seed=20260801,
            )
        if plan.status == "draft":
            plan = service.approve_experiment_plan(
                plan_id=plan.id, confirmed_by_user=True
            )
        elif plan.status != "approved":
            raise ConflictError(f"试验计划状态不能执行：{plan.status}")

        existing_jobs = [
            item
            for item in repository.list_jobs()
            if item.job_type == "parameter_search"
            and item.payload.get("batch_mode") is True
            and item.payload.get("experiment_plan_id") == plan.id
        ]
        if existing_jobs:
            job = existing_jobs[0]
        else:
            data_version = _batch_data_version(
                resolved_root=resolved_root,
                repository=repository,
                baseline=baseline,
                spec=spec,
            )
            job = service.create_job(
                job_type="parameter_search",
                payload={
                    "experiment_plan_id": plan.id,
                    "batch_mode": True,
                    "locked_test_used": False,
                    "max_trials": int(plan.max_trials or 0),
                    "evaluator_id": evaluator_id,
                    "evidence_mode": "research",
                    "data_version": data_version,
                },
            )
        return {
            "proposal": repository.get_proposal(proposal.id),
            "candidate": candidate,
            "plan": plan,
            "job": job,
            "message": (
                f"已批准并排队 {plan.max_trials} 个有限参数方案；"
                "不会使用最终保留测试，也不会自动实盘。"
            ),
        }

    @application.post(
        "/api/improvement-directions/{proposal_id}/transition",
        response_model=ImprovementDirectionResponse,
        tags=["guided-research"],
    )
    def transition_improvement_direction(
        proposal_id: str, body: ProposalTransitionRequest
    ) -> Any:
        return guided_service.transition(
            proposal_id=proposal_id,
            target=body.target,
            subject_id=body.subject_id,
            confirmed_by_user=body.confirmed_by_user,
        )

    @application.get(
        "/api/experiment-plans/{plan_id}/trials",
        response_model=list[TrialResponse],
        tags=["experiments"],
    )
    def plan_trials(plan_id: str) -> Any:
        repository.get_experiment_plan(plan_id)
        return repository.list_trials(plan_id)

    @application.post(
        "/api/experiment-plans/{plan_id}/launch-candidate-validation",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["experiments"],
    )
    def launch_candidate_validation(
        plan_id: str, body: JobActionRequest
    ) -> Any:
        if not body.confirmed_by_user or body.subject_id != plan_id:
            raise ApprovalRequiredError(
                "候选验证要求用户明确批准当前试验计划"
            )
        plan = repository.get_experiment_plan(plan_id)
        recommendation = summarize_stable_ranges(
            repository.list_trials(plan_id), plan.constraints
        )["recommendation"]
        if recommendation.get("decision") != "candidate_validation":
            raise ConflictError(
                "当前批量结果尚未形成稳定推荐方案，不能进入候选验证"
            )
        existing = next(
            (
                item
                for item in repository.list_jobs()
                if item.job_type == "stress_test"
                and item.payload.get("intent")
                == "generic_strategy_dsl_candidate_validation"
                and item.payload.get("experiment_plan_id") == plan.id
            ),
            None,
        )
        if existing is not None:
            return existing
        return service.create_job(
            job_type="stress_test",
            payload={
                "intent": "generic_strategy_dsl_candidate_validation",
                "stress_level": "bounded_candidate_validation",
                "experiment_plan_id": plan.id,
                "recommended_trial_id": recommendation[
                    "recommended_trial_id"
                ],
                "candidate_version_id": plan.candidate_version_id,
                "subject_id": plan.id,
                "confirmed_by_user": True,
                "locked_test_used": False,
            },
        )

    @application.post(
        "/api/experiment-plans/{plan_id}/launch-locked-test",
        response_model=JobResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["experiments"],
    )
    def launch_locked_test(
        plan_id: str, body: JobActionRequest
    ) -> Any:
        if not body.confirmed_by_user or body.subject_id != plan_id:
            raise ApprovalRequiredError(
                "最终保留测试要求用户明确批准当前试验计划"
            )
        plan = repository.get_experiment_plan(plan_id)
        if plan.candidate_version_id is None:
            raise ConflictError("当前试验计划没有不可变候选版本")
        validation_job = next(
            (
                item
                for item in repository.list_jobs()
                if item.job_type == "stress_test"
                and item.payload.get("intent")
                == "generic_strategy_dsl_candidate_validation"
                and item.payload.get("experiment_plan_id") == plan.id
            ),
            None,
        )
        validation_report = (
            next(
                (
                    item
                    for item in repository.list_reports(
                        job_id=validation_job.id
                    )
                    if item.report_type == "generic_candidate_validation"
                ),
                None,
            )
            if validation_job is not None
            else None
        )
        validation_decision = (
            dict(validation_report.summary.get("decision") or {})
            if validation_report is not None
            else {}
        )
        if (
            validation_job is None
            or validation_job.status != "succeeded"
            or validation_decision.get("status")
            != "ready_for_locked_test_review"
        ):
            raise ConflictError(
                "候选尚未通过有界稳健性检查，不能运行最终保留测试"
            )
        existing = next(
            (
                item
                for item in repository.list_jobs()
                if item.job_type == "stress_test"
                and item.payload.get("intent")
                == "generic_strategy_dsl_locked_test"
                and item.payload.get("experiment_plan_id") == plan.id
            ),
            None,
        )
        if existing is not None:
            return existing
        recommendation = summarize_stable_ranges(
            repository.list_trials(plan.id), plan.constraints
        )["recommendation"]
        recommended_trial_id = recommendation.get("recommended_trial_id")
        if not isinstance(recommended_trial_id, str):
            raise ConflictError("当前计划没有可冻结的推荐参数方案")
        session_id = repository.get_session_id_for_strategy_version(
            plan.baseline_version_id
        )
        return service.create_job(
            job_type="stress_test",
            payload={
                "intent": "generic_strategy_dsl_locked_test",
                "stress_level": "locked_test",
                "experiment_plan_id": plan.id,
                "recommended_trial_id": recommended_trial_id,
                "candidate_version_id": plan.candidate_version_id,
                "subject_id": plan.id,
                "session_id": session_id,
                "confirmed_by_user": True,
                "locked_test_used": True,
                "candidate_validation_report_id": validation_report.id,
            },
        )

    @application.get(
        "/api/experiment-plans/{plan_id}/candidate-validation-summary",
        tags=["experiments"],
    )
    def candidate_validation_summary(plan_id: str) -> dict[str, Any]:
        repository.get_experiment_plan(plan_id)
        job = next(
            (
                item
                for item in repository.list_jobs()
                if item.job_type == "stress_test"
                and item.payload.get("intent")
                == "generic_strategy_dsl_candidate_validation"
                and item.payload.get("experiment_plan_id") == plan_id
            ),
            None,
        )
        if job is None:
            return {
                "available": False,
                "experiment_plan_id": plan_id,
                "job_status": "not_started",
                "decision_status": None,
                "weakest_evidence": [],
                "next_action": "先从稳定参数区间中批准一次有界候选验证。",
                "locked_test_used": False,
                "locked_test_automatically_started": False,
            }

        report = next(
            (
                item
                for item in repository.list_reports(job_id=job.id)
                if item.report_type == "generic_candidate_validation"
            ),
            None,
        )
        if report is None:
            if job.status in {"queued", "running"}:
                next_action = "等待后台程序完成已批准的候选验证。"
            elif job.status == "failed":
                next_action = "查看失败原因；保留现有证据，不自动重试或扩大搜索。"
            else:
                next_action = "候选验证没有形成报告，请检查后台任务记录。"
            return {
                "available": True,
                "experiment_plan_id": plan_id,
                "job_id": job.id,
                "job_status": job.status,
                "decision_status": None,
                "weakest_evidence": [],
                "next_action": next_action,
                "error": job.error,
                "locked_test_used": False,
                "locked_test_automatically_started": False,
            }

        summary = dict(report.summary)
        decision = dict(summary.get("decision") or {})
        locked_job = next(
            (
                item
                for item in repository.list_jobs()
                if item.job_type == "stress_test"
                and item.payload.get("intent")
                == "generic_strategy_dsl_locked_test"
                and item.payload.get("experiment_plan_id") == plan_id
            ),
            None,
        )
        locked_report = (
            next(
                (
                    item
                    for item in repository.list_reports(
                        job_id=locked_job.id
                    )
                    if item.report_type == "generic_locked_test"
                ),
                None,
            )
            if locked_job is not None
            else None
        )
        locked_summary = (
            dict(locked_report.summary)
            if locked_report is not None
            else {}
        )
        locked_decision = dict(locked_summary.get("decision") or {})
        locked_next_action = (
            "等待后台程序完成已批准的最终保留测试。"
            if locked_job is not None
            and locked_job.status in {"queued", "running"}
            else "查看最终保留测试失败原因；不得自动重试或针对该区间继续调参。"
            if locked_job is not None and locked_job.status == "failed"
            else None
        )
        weakest: list[str] = []
        if not decision.get("cost_sensitivity_passed"):
            weakest.append("成本提高后表现未能保持")
        if int(decision.get("positive_rolling_windows") or 0) < 2:
            weakest.append("滚动窗口中的正向区间不足")
        if float(decision.get("perturbation_pass_ratio") or 0.0) < 0.6:
            weakest.append("参数轻微扰动后的稳定性不足")
        if not decision.get("regime_evidence_sufficient"):
            weakest.append("行情拆分证据不足")
        return {
            "available": True,
            "experiment_plan_id": plan_id,
            "job_id": job.id,
            "job_status": job.status,
            "report_id": report.id,
            "report_artifact_key": report.artifact_key,
            "decision_status": decision.get("status"),
            "cost_sensitivity_passed": bool(
                decision.get("cost_sensitivity_passed")
            ),
            "positive_rolling_windows": int(
                decision.get("positive_rolling_windows") or 0
            ),
            "rolling_window_count": int(
                decision.get("rolling_window_count") or 0
            ),
            "perturbation_pass_ratio": float(
                decision.get("perturbation_pass_ratio") or 0.0
            ),
            "regime_evidence_sufficient": bool(
                decision.get("regime_evidence_sufficient")
            ),
            "weakest_evidence": weakest,
            "next_action": str(
                locked_next_action
                or locked_decision.get("next_action")
                or decision.get("next_action")
                or "审阅候选验证证据后决定停止或进入独立门禁。"
            ),
            "locked_test_job_id": locked_job.id if locked_job else None,
            "locked_test_status": (
                locked_job.status if locked_job else "not_started"
            ),
            "locked_test_decision": (
                locked_decision.get("status") or None
            ),
            "locked_test_metrics": dict(
                locked_summary.get("metrics") or {}
            ),
            "locked_test_report_id": (
                locked_report.id if locked_report else None
            ),
            "locked_test_used": bool(
                locked_summary.get(
                    "locked_test_used",
                    summary.get("locked_test_used", False),
                )
            ),
            "locked_test_automatically_started": bool(
                decision.get("locked_test_automatically_started", False)
            ),
        }

    @application.get(
        "/api/experiment-plans/{plan_id}/trial-equity",
        tags=["experiments"],
    )
    def plan_trial_equity(
        plan_id: str,
        trial_ids: list[str] | None = Query(default=None),
        split: str = Query(default="validation", pattern="^(train|validation)$"),
        max_points: int = Query(default=500, ge=50, le=2_000),
    ) -> dict[str, Any]:
        return trial_equity_reader.read(
            experiment_plan_id=plan_id,
            trial_ids=tuple(trial_ids or ()),
            split=split,
            max_points=max_points,
        )

    @application.get(
        "/api/experiment-plans/{plan_id}/batch-summary",
        tags=["experiments"],
    )
    def batch_summary(plan_id: str) -> Any:
        plan = repository.get_experiment_plan(plan_id)
        trials = repository.list_trials(plan_id)
        batch_job = next(
            (
                job
                for job in repository.list_jobs()
                if job.job_type == "parameter_search"
                and job.payload.get("batch_mode") is True
                and job.payload.get("experiment_plan_id") == plan_id
            ),
            None,
        )
        evidence_mode = (
            str(batch_job.payload.get("evidence_mode", "research"))
            if batch_job is not None
            else "unavailable"
        )
        job_report = (
            repository.list_reports(job_id=batch_job.id)[0]
            if batch_job is not None
            and repository.list_reports(job_id=batch_job.id)
            else None
        )
        runner_summary = (
            dict(job_report.summary)
            if job_report is not None
            else {}
        )
        status_counts = {
            state: sum(1 for trial in trials if trial.status == state)
            for state in ("queued", "running", "succeeded", "failed", "cancelled")
        }
        completed = (
            status_counts["succeeded"]
            + status_counts["failed"]
            + status_counts["cancelled"]
        )
        total = int(
            runner_summary.get("generated_combinations")
            or plan.max_trials
            or len(trials)
        )
        stop_reason = (
            runner_summary.get("stop_reason")
            or (batch_job.error if batch_job is not None else None)
        )
        return {
            "experiment_plan_id": plan_id,
            "baseline_version_id": plan.baseline_version_id,
            "candidate_version_id": plan.candidate_version_id,
            "search_strategy": plan.search_strategy,
            "cost_model": plan.cost_model,
            "baseline_metrics": None,
            "evidence_mode": evidence_mode,
            "research_conclusion_allowed": evidence_mode == "research",
            "job_id": batch_job.id if batch_job else None,
            "job_status": batch_job.status if batch_job else "not_queued",
            "total_trials": total,
            "completed_trials": completed,
            "succeeded_trials": status_counts["succeeded"],
            "failed_trials": status_counts["failed"],
            "cancelled_trials": status_counts["cancelled"],
            "running_trials": status_counts["running"],
            "queued_trials": status_counts["queued"],
            "remaining_trials": max(total - completed, 0),
            "concurrency": int(
                runner_summary.get("concurrency")
                or (batch_job.payload.get("max_concurrent_trials", 1) if batch_job else 1)
            ),
            "elapsed_seconds": float(runner_summary.get("elapsed_seconds") or 0.0),
            "peak_rss_mb": float(runner_summary.get("peak_rss_mb") or 0.0),
            "stop_reason": stop_reason,
            "continue_reason": (
                "仍有已批准且未完成的参数方案。"
                if batch_job is not None
                and batch_job.status in {"queued", "running"}
                and completed < total
                else None
            ),
            **summarize_stable_ranges(trials, plan.constraints),
        }

    return application


def _factor_registry_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    raw_metadata = item.pop("metadata_json", "{}")
    try:
        metadata = json.loads(raw_metadata)
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    item["metadata"] = metadata if isinstance(metadata, dict) else {}
    return item


app = create_app()
