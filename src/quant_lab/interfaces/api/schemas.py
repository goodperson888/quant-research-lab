from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CreateSessionRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)


class SessionResponse(StrictModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str


class CreateMessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=50_000)


class MessageResponse(StrictModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: str


class CreateIntakeRequest(StrictModel):
    source_type: Literal["natural_language", "pine", "file"]
    source_name: str | None = Field(default=None, max_length=240)
    raw_content: str = Field(min_length=1, max_length=500_000)


class StrategyDraftResponse(StrictModel):
    id: str
    session_id: str
    source_type: str
    source_name: str | None
    raw_content: str
    structured_content: dict[str, Any]
    status: str
    baseline_version_id: str | None
    created_at: str


class FreezeBaselineRequest(StrictModel):
    confirmed_by_user: bool


class StrategyVersionResponse(StrictModel):
    id: str
    strategy_id: str
    version: int
    status: str
    content_snapshot: dict[str, Any]
    source_snapshot: str
    created_at: str
    immutable: bool


class CreateJobRequest(StrictModel):
    job_type: Literal["backtest", "data_quality", "report"]
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResponse(StrictModel):
    id: str
    job_type: str
    status: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    error: str | None


class AuditEventResponse(StrictModel):
    id: int | None
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    payload: dict[str, Any]
    created_at: str


class SessionDetailResponse(StrictModel):
    session: SessionResponse
    messages: list[MessageResponse]
    drafts: list[StrategyDraftResponse]
