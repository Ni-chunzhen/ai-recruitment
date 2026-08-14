from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ResumeStatus = Literal[
    "pending_parse",
    "parsing",
    "pending_review",
    "confirmed",
    "parse_failed",
    "void",
]
PipelineStatus = Literal[
    "pending_parse",
    "pending_hr_screen",
    "interviewing",
    "rejected",
    "talent_pool",
]
ScreeningDecisionType = Literal[
    "enter_interview",
    "hold",
    "reject",
    "talent_pool",
]


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start: str = ""
    end: str = ""
    missing: bool = False


class ExperienceItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    description: str = ""
    missing: bool = False
    source: Literal["ai", "manual", "file"] = "ai"


class ProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    description: str = ""
    missing: bool = False
    source: Literal["ai", "manual", "file"] = "ai"


class ResumeStructuredContent(BaseModel):
    name: str = ""
    name_pending: bool = False
    phone: str = ""
    email: str = ""
    years_of_experience: float | None = None
    education: list[EducationItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    standardized_text: str = ""
    field_sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("skills", mode="before")
    @classmethod
    def _skills(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("skills must be a list")
        return [str(item) for item in value]


class ResumeParseResult(BaseModel):
    """AI RESUME_PARSE validated payload."""

    name: str = ""
    phone: str = ""
    email: str = ""
    years_of_experience: float | None = None
    education: list[EducationItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    standardized_text: str = ""

    @field_validator("standardized_text")
    @classmethod
    def _text_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("standardized_text must be non-empty")
        return value.strip()


class DimensionScoreItem(BaseModel):
    name: str
    description: str = ""
    weight: float
    score: float
    weighted_score: float | None = None
    evidence: str = ""
    gap: str = ""
    risk: str = ""

    @field_validator("score")
    @classmethod
    def _score_range(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("score must be between 0 and 100")
        return value


class ResumeScoreResult(BaseModel):
    dimensions: list[DimensionScoreItem]
    total_score: float | None = None
    recommendation: str = ""
    score_band: str = ""
    must_have_check: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    summary: str = ""
    information_insufficient: bool = False


class DuplicateCandidateHint(BaseModel):
    id: UUID
    name: str
    phone: str | None = None
    email: str | None = None
    match_on: list[str] = Field(default_factory=list)


class ResumeVersionOut(BaseModel):
    id: UUID
    resume_id: UUID
    candidate_id: UUID
    kind: str
    version_label: str
    status: ResumeStatus
    original_filename: str | None = None
    content_type: str | None = None
    file_size: int | None = None
    extracted_text: str | None = None
    ai_structured: dict[str, Any] | None = None
    draft_content: dict[str, Any] | None = None
    confirmed_content: dict[str, Any] | None = None
    standardized_text: str | None = None
    parse_task_id: UUID | None = None
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    preview_url: str | None = None
    duplicate_hints: list[DuplicateCandidateHint] = Field(default_factory=list)


class ResumeUploadItemOut(BaseModel):
    resume_id: UUID
    resume_version_id: UUID
    candidate_id: UUID
    application_id: UUID | None = None
    parse_task_id: UUID | None = None
    status: ResumeStatus
    original_filename: str
    duplicate_hints: list[DuplicateCandidateHint] = Field(default_factory=list)


class ResumeUploadResponse(BaseModel):
    items: list[ResumeUploadItemOut]


class ResumeListItem(BaseModel):
    resume_id: UUID
    resume_version_id: UUID
    candidate_id: UUID
    candidate_name: str
    phone: str | None = None
    email: str | None = None
    status: ResumeStatus
    has_application: bool
    job_names: list[str] = Field(default_factory=list)
    original_filename: str | None = None
    updated_at: datetime
    awaiting_match: bool = False


class ResumeListResponse(BaseModel):
    items: list[ResumeListItem]
    total: int


class SaveDraftRequest(BaseModel):
    content: ResumeStructuredContent


class ConfirmResumeRequest(BaseModel):
    content: ResumeStructuredContent
    link_candidate_id: UUID | None = None


class CreateApplicationRequest(BaseModel):
    candidate_id: UUID
    job_id: UUID
    resume_version_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class ApplicationOut(BaseModel):
    id: UUID
    candidate_id: UUID
    candidate_name: str
    job_id: UUID
    job_name: str | None = None
    job_version_id: UUID
    job_version_label: str | None = None
    resume_version_id: UUID | None = None
    pipeline_status: PipelineStatus
    status: str
    lock_version: int
    created_at: datetime
    updated_at: datetime


class CreateScoreTaskRequest(BaseModel):
    resume_version_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class ScoreTaskCreated(BaseModel):
    task_id: UUID
    application_id: UUID
    status: str


ScreeningReasonCodeType = Literal[
    "must_have_mismatch",
    "core_experience_insufficient",
    "skill_mismatch",
    "project_experience_insufficient",
    "location_or_start_mismatch",
    "salary_mismatch",
    "information_insufficient",
    "other",
]


class ScoreReportOut(BaseModel):
    application_id: UUID
    result_id: UUID
    version_label: str
    schema_version: str = "1.0"
    candidate_id: UUID
    candidate_name: str
    job_id: UUID
    job_name: str
    job_version_id: UUID
    job_version_label: str
    resume_version_id: UUID
    resume_version_label: str
    total_score: float
    calculated_total_score: float
    model_total_score: float | None = None
    score_difference: float | None = None
    validation_warnings: list[str] = Field(default_factory=list)
    recommendation: str
    score_band: str
    summary: str
    information_insufficient: bool
    dimensions: list[DimensionScoreItem]
    risks: list[str] = Field(default_factory=list)
    must_have_check: list[str] = Field(default_factory=list)
    is_current: bool = True
    is_stale: bool = False
    requested_by: str | None = None
    created_at: datetime
    ai_disclaimer: str = "AI辅助建议，最终筛选结果由招聘人员确认。"
    lock_version: int = 1


class ScoreHistoryResponse(BaseModel):
    items: list[ScoreReportOut]


class ScreeningReasonCodeItem(BaseModel):
    code: str
    label: str
    allowed_decisions: list[str]
    requires_description: bool


class ScreeningReasonCodeListResponse(BaseModel):
    items: list[ScreeningReasonCodeItem]


class ScreeningDecisionRequest(BaseModel):
    decision: ScreeningDecisionType
    reason_code: ScreeningReasonCodeType | None = None
    reason: str | None = Field(default=None, max_length=2000)
    lock_version: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class ScreeningDecisionOut(BaseModel):
    id: UUID
    application_id: UUID
    decision: ScreeningDecisionType
    reason_code: str | None = None
    reason: str | None
    from_pipeline_status: PipelineStatus
    to_pipeline_status: PipelineStatus
    lock_version: int
    created_at: datetime
