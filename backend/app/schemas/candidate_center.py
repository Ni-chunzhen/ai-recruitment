from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.candidate import APPLICATION_STATUSES
from app.models.resume import PIPELINE_STATUSES

_QUERY_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    hide_input_in_errors=True,
)


class CandidateCenterListQuery(BaseModel):
    model_config = _QUERY_CONFIG

    assigned: bool = True
    status: str | None = None
    pipeline_status: str | None = None
    job_id: UUID | None = None
    keyword: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort: Literal["updated_at_desc", "created_at_desc"] = "updated_at_desc"

    @field_validator("status")
    @classmethod
    def _status_whitelist(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value not in APPLICATION_STATUSES:
            raise ValueError("invalid status")
        return value

    @field_validator("pipeline_status")
    @classmethod
    def _pipeline_whitelist(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value not in PIPELINE_STATUSES:
            raise ValueError("invalid pipeline_status")
        return value


class ScoreDimensionSummary(BaseModel):
    name: str
    weight: float
    score: float


class ScoreSummaryOut(BaseModel):
    result_id: UUID
    version_label: str
    total_score: float
    calculated_total_score: float
    score_band: str
    recommendation: str
    summary: str
    information_insufficient: bool
    is_stale: bool
    is_current: bool
    dimensions: list[ScoreDimensionSummary] = Field(default_factory=list)


class ResumeSummaryOut(BaseModel):
    resume_id: UUID
    resume_version_id: UUID
    version_label: str
    kind: str
    status: str
    original_filename: str | None = None
    confirmed_at: datetime | None = None


class CandidateCenterListItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    name: str
    phone: str | None = None
    email: str | None = None
    job_id: UUID
    job_name: str
    job_code: str
    job_version_id: UUID
    job_version_label: str | None = None
    status: str
    pipeline_status: str
    round_id: UUID | None = None
    round_name: str | None = None
    sequence_no: int | None = None
    round_status: str | None = None
    schedule_status: str
    invitation_status: str
    transcript_status: str
    question_status: str
    analysis_status: str
    analysis_overall_score: Decimal | None = None


class CandidateCenterListResponse(BaseModel):
    items: list[CandidateCenterListItem]
    total: int
    page: int
    page_size: int


class CandidateCenterRoundOut(BaseModel):
    application_id: UUID
    round_id: UUID
    name: str
    sequence_no: int
    status: str
    schedule_status: str
    has_meeting_password: bool = False
    invitation_status: str
    transcript_status: str
    question_status: str
    analysis_status: str
    analysis_overall_score: Decimal | None = None


class OtherApplicationSummary(BaseModel):
    application_id: UUID
    job_id: UUID
    job_name: str
    job_code: str
    status: str
    pipeline_status: str
    created_at: datetime


class CandidateCenterDetailOut(BaseModel):
    application_id: UUID
    candidate_id: UUID
    name: str
    phone: str | None = None
    email: str | None = None
    job_id: UUID
    job_name: str
    job_code: str
    job_version_id: UUID
    job_version_label: str | None = None
    status: str
    pipeline_status: str
    close_action: str | None = None
    interview_started: bool
    lock_version: int
    resume_summary: ResumeSummaryOut | None = None
    score_summary: ScoreSummaryOut | None = None
    rounds: list[CandidateCenterRoundOut] = Field(default_factory=list)
    other_applications: list[OtherApplicationSummary] = Field(default_factory=list)
