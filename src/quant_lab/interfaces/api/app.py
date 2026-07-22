from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quant_lab import __version__
from quant_lab.application.services import ResearchApplicationService
from quant_lab.application.pipeline import (
    PipelineApplicationService,
    PipelineProfileCatalog,
    profile_as_dict,
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
from quant_lab.infrastructure.project_readers import DataSummaryReader, ProjectStatusReader
from quant_lab.infrastructure.sqlite_product_repository import SQLiteProductRepository
from quant_lab.paths import app_database_path, project_root

from .schemas import (
    AuditEventResponse,
    ComponentCandidateResponse,
    ComponentEvidenceResponse,
    CreateComponentCandidateRequest,
    CreateRegimeValidationRequest,
    CreateStrategyOutcomeRequest,
    CreateIntakeRequest,
    CreateExperimentPlanRequest,
    CreateJobRequest,
    CreateMessageRequest,
    CreateSessionRequest,
    FreezeBaselineRequest,
    ExperimentPlanResponse,
    EvaluateGateRequest,
    GateEvaluationResponse,
    JobResponse,
    MessageResponse,
    PipelineProfileResponse,
    RegimeValidationResponse,
    SessionDetailResponse,
    SessionResponse,
    StrategyDraftResponse,
    StrategyOutcomeResponse,
    StrategyVersionResponse,
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
    service = ResearchApplicationService(repository)
    pipeline_service = PipelineApplicationService(
        repository, PipelineProfileCatalog(resolved_root)
    )
    provider = UnconfiguredLLMProvider()
    project_reader = ProjectStatusReader(resolved_root)
    data_reader = DataSummaryReader(resolved_root)

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
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.state.repository = repository
    application.state.research_service = service
    application.state.pipeline_service = pipeline_service
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

    @application.get("/api/project/status", tags=["system"])
    def project_status() -> dict[str, Any]:
        provider_status = asdict(provider.status())
        return project_reader.read(provider_status=provider_status)

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
    def list_experiment_plans() -> Any:
        return service.list_experiment_plans()

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

    @application.get("/api/jobs/{job_id}/logs", tags=["jobs"])
    def list_job_logs(job_id: str) -> Any:
        return service.list_job_logs(job_id)

    @application.get("/api/data/summary", tags=["data"])
    def data_summary() -> dict[str, Any]:
        return data_reader.read()

    @application.get("/api/settings/status", tags=["system"])
    def settings_status() -> dict[str, Any]:
        return {
            "ai_provider": asdict(provider.status()),
            "api_bind_default": "127.0.0.1",
            "live_trading_enabled": False,
            "credentials_api_available": False,
        }

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
            "controls": {
                "pause_cancel_reserved": True,
                "approval_required_for": [
                    "freeze_baseline",
                    "accept_strategy_change",
                    "run_parameter_search",
                ],
            },
        }

    @application.get(
        "/api/audit/events",
        response_model=list[AuditEventResponse],
        tags=["agent-control-plane"],
    )
    def audit_events(limit: int = Query(default=100, ge=1, le=500)) -> Any:
        return service.list_audit_events(limit=limit)

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
    def strategy_outcomes() -> Any:
        return pipeline_service.list_strategy_outcomes()

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
    def component_candidates() -> Any:
        return pipeline_service.list_component_candidates()

    @application.get(
        "/api/component-evidence",
        response_model=list[ComponentEvidenceResponse],
        tags=["research-components"],
    )
    def component_evidence() -> Any:
        return pipeline_service.list_component_evidence()

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

    @application.get(
        "/api/regime-validations",
        response_model=list[RegimeValidationResponse],
        tags=["research-regimes"],
    )
    def regime_validations() -> Any:
        return pipeline_service.list_regime_validations()

    @application.post(
        "/api/regime-validations",
        response_model=RegimeValidationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["research-regimes"],
    )
    def create_regime_validation(body: CreateRegimeValidationRequest) -> Any:
        return pipeline_service.create_regime_validation(**body.model_dump())

    return application


app = create_app()
