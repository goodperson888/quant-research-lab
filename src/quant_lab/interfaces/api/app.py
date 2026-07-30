from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quant_lab import __version__
from quant_lab.application.services import ResearchApplicationService
from quant_lab.application.guided_research import GuidedResearchService
from quant_lab.application.component_attribution import DefaultComponentEvidenceAggregator
from quant_lab.application.batch_trials import summarize_stable_ranges
from quant_lab.application.backtest_engines import BacktestEngineRegistry
from quant_lab.application.equity_series import RunBundleEquityReader
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
from quant_lab.domain.models import AgentProviderKind, ExecutionTargetKind
from quant_lab.domain.models import Constraint, Objective, ParameterSpace
from quant_lab.infrastructure.llm import UnconfiguredLLMProvider
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
from quant_lab.paths import app_database_path, project_root

from .schemas import (
    AuditEventResponse,
    ComponentArchiveRequest,
    ComponentCandidateResponse,
    ComponentEvidenceResponse,
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
    CreateExperimentPlanRequest,
    CreateJobRequest,
    CreateMessageRequest,
    CreateSessionRequest,
    CreateImprovementDirectionRequest,
    FreezeBaselineRequest,
    ExperimentPlanResponse,
    EvaluateGateRequest,
    FormalizeStrategyRequest,
    GateEvaluationResponse,
    JobResponse,
    JobActionRequest,
    MessageResponse,
    PipelineProfileResponse,
    RegimeValidationResponse,
    ResearchHandoffResponse,
    ResearchAuthorizationResponse,
    ResearchAuthorizationStageResponse,
    ResearchModeDefinitionResponse,
    ImprovementDirectionResponse,
    ProposalTransitionRequest,
    ProposalBudgetRequest,
    SessionDetailResponse,
    SessionResponse,
    UpdateResearchModeRequest,
    StrategyDraftResponse,
    StrategyOutcomeResponse,
    StrategyVersionResponse,
    TrialResponse,
)


LOCAL_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


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
    provider = UnconfiguredLLMProvider()
    project_reader = ProjectStatusReader(resolved_root)
    agent_manifest_reader = AgentManifestReader(resolved_root)
    data_reader = DataSummaryReader(resolved_root)
    storage_reporter = StorageReporter(resolved_root)
    artifact_store = LocalArtifactStore(resolved_root)
    equity_reader = RunBundleEquityReader(repository, artifact_store)
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
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    application.state.repository = repository
    application.state.research_service = service
    application.state.pipeline_service = pipeline_service
    application.state.research_authorization_service = authorization_service
    application.state.guided_research_service = guided_service
    application.state.llm_provider = provider

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
            f"http://127.0.0.1:{os.environ.get('QUANT_LAB_WEB_PORT', '3000')}/studio"
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
        return service.create_research_session(title=body.title)

    @application.get(
        "/api/research/sessions/{session_id}/mode",
        response_model=SessionResponse,
        tags=["research"],
    )
    def get_session_mode(session_id: str) -> Any:
        return service.get_research_session(session_id)

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
        "/api/strategy-drafts",
        response_model=list[StrategyDraftResponse],
        tags=["strategies"],
    )
    def list_drafts(session_id: str | None = Query(default=None)) -> Any:
        return service.list_strategy_drafts(session_id)

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

    @application.get("/api/versioning/policy", tags=["system"])
    def versioning_policy() -> dict[str, Any]:
        try:
            return {"available": True, **asdict(versioning_reader.read())}
        except ValueError as exc:
            return {"available": False, "reason": str(exc)}

    @application.get("/api/agent/status", tags=["agent-control-plane"])
    def agent_status() -> dict[str, Any]:
        return {
            "architecture": "agent_first_hybrid",
            "default_run_mode": "guided",
            "active_configuration": {
                "agent_provider": AgentProviderKind.EXTERNAL_LOCAL_AGENT,
                "execution_target": ExecutionTargetKind.LOCAL_RUNTIME,
                "data_location": "local_project",
                "privacy_note": "Research inputs and product state remain local in phase 0.",
            },
            "capability_matrix": {
                AgentProviderKind.EXTERNAL_LOCAL_AGENT: "supported",
                AgentProviderKind.EMBEDDED_CLOUD_PROVIDER: "planned_unsupported",
                AgentProviderKind.BYOK_PROVIDER: "planned_unsupported",
                AgentProviderKind.LOCAL_MODEL_PROVIDER: "planned_unsupported",
                ExecutionTargetKind.LOCAL_RUNTIME: "supported",
                ExecutionTargetKind.HOSTED_SANDBOX: "planned_unsupported",
            },
            "external_agent": {
                "supported": True,
                "preferred_for_phase_0": True,
                "connection_status": "awaiting_heartbeat",
                "note": "Codex/CLI/API actions share the append-only audit timeline.",
            },
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

    @application.get("/api/run-bundles/{bundle_id}/chart-series", tags=["reports"])
    def run_bundle_chart_series(
        bundle_id: str,
        max_points: int = Query(default=800, ge=50, le=2_000),
    ) -> dict[str, Any]:
        return equity_reader.read(bundle_id=bundle_id, max_points=max_points)

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


app = create_app()
