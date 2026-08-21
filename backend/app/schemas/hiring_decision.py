"""Schemas for post-interview HiringDecision API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.resume import PipelineStatus

HiringDecisionType = Literal["recommend_hire", "reject", "hold"]


class HiringDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: HiringDecisionType
    reason_code: str = Field(min_length=1, max_length=64)
    analysis_version_id: UUID
    lock_version: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class HiringDecisionOut(BaseModel):
    id: UUID
    application_id: UUID
    decision: HiringDecisionType
    reason_code: str
    round_id: UUID
    analysis_version_id: UUID
    overall_score: float | None
    analysis_version_no: int | None
    from_pipeline_status: PipelineStatus
    to_pipeline_status: PipelineStatus
    lock_version: int
    created_at: datetime
    decided_by: UUID | None


class HiringDecisionListResponse(BaseModel):
    items: list[HiringDecisionOut]


class HiringReasonCodeItem(BaseModel):
    code: str
    label: str
    allowed_decisions: list[str]


class HiringReasonCodeListResponse(BaseModel):
    items: list[HiringReasonCodeItem]
