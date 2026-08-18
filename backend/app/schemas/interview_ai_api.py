"""HTTP contracts for stage 8 interview question/analysis APIs.

Separated from Dify output schemas in ``app.schemas.interview_ai``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_API_CONFIG = ConfigDict(
    extra="forbid",
    from_attributes=True,
    str_strip_whitespace=True,
    hide_input_in_errors=True,
)


class InterviewAIAPIModel(BaseModel):
    model_config = _API_CONFIG


class InterviewAIGenerateRequest(InterviewAIAPIModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class InterviewQuestionItemWrite(InterviewAIAPIModel):
    dimension_key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=2000)
    evidence_source: Literal["JOB_REQUIREMENT", "RESUME_EXPERIENCE", "GENERAL"]
    resume_evidence: str | None = Field(default=None, max_length=2000)
    follow_up_prompts: list[str] = Field(default_factory=list, max_length=10)
    risk_flags: list[str] = Field(default_factory=list, max_length=10)
    display_order: int = Field(ge=1, le=30)


class InterviewQuestionEditRequest(InterviewAIAPIModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_current_version_id: UUID
    questions: list[InterviewQuestionItemWrite] = Field(min_length=1, max_length=30)


class InterviewQuestionConfirmRequest(InterviewAIAPIModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_current_version_id: UUID


class InterviewAIGenerateOut(InterviewAIAPIModel):
    task_id: UUID
    task_type: str
    status: str
    round_id: UUID
    dispatch_status: Literal["queued", "pending_dispatch"]


class InterviewQuestionVersionSummaryOut(InterviewAIAPIModel):
    id: UUID
    question_set_id: UUID
    round_id: UUID
    version_no: int
    version_label: str
    source_type: str
    job_version_id: UUID
    resume_version_id: UUID
    question_count: int
    is_current: bool
    created_at: datetime
    created_by: UUID | None = None
    ai_task_id: UUID | None = None


class InterviewQuestionSetOut(InterviewAIAPIModel):
    id: UUID | None = None
    round_id: UUID
    status: str | None = None
    current_version_id: UUID | None = None
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    versions: list[InterviewQuestionVersionSummaryOut] = Field(default_factory=list)


class InterviewQuestionItemOut(InterviewAIAPIModel):
    id: UUID
    dimension_key: str
    question: str
    purpose: str
    evidence_source: str
    resume_evidence: str | None = None
    follow_up_prompts: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    display_order: int


class InterviewQuestionVersionDetailOut(InterviewAIAPIModel):
    id: UUID
    question_set_id: UUID
    round_id: UUID
    version_no: int
    version_label: str
    source_type: str
    job_version_id: UUID
    resume_version_id: UUID
    question_count: int
    is_current: bool
    created_at: datetime
    created_by: UUID | None = None
    ai_task_id: UUID | None = None
    items: list[InterviewQuestionItemOut] = Field(default_factory=list)


class InterviewAnalysisVersionSummaryOut(InterviewAIAPIModel):
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    transcript_version_id: UUID
    transcript_version_label: str | None = None
    job_version_id: UUID
    ai_task_id: UUID
    overall_score: Decimal | None = None
    dimension_count: int
    evidence_count: int
    created_by: UUID | None = None
    created_at: datetime
    is_current: bool
    is_stale: bool


class InterviewAnalysisSetOut(InterviewAIAPIModel):
    analysis_id: UUID | None = None
    round_id: UUID
    current_version_id: UUID | None = None
    versions: list[InterviewAnalysisVersionSummaryOut] = Field(default_factory=list)


class InterviewAnalysisEvidenceOut(InterviewAIAPIModel):
    id: UUID
    transcript_segment_id: UUID
    segment_no: int
    quote: str


class InterviewAnalysisDimensionOut(InterviewAIAPIModel):
    id: UUID
    dimension_key: str
    dimension_name: str
    weight: Decimal
    score: int | None = None
    analysis: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    insufficient_information: str | None = None
    suggested_follow_ups: list[str] = Field(default_factory=list)
    display_order: int
    evidence: list[InterviewAnalysisEvidenceOut] = Field(default_factory=list)


class InterviewAnalysisVersionDetailOut(InterviewAIAPIModel):
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    transcript_version_id: UUID
    transcript_version_label: str | None = None
    job_version_id: UUID
    ai_task_id: UUID
    overall_score: Decimal | None = None
    overall_summary: str
    dimension_count: int
    evidence_count: int
    created_by: UUID | None = None
    created_at: datetime
    is_current: bool
    is_stale: bool
    dimensions: list[InterviewAnalysisDimensionOut] = Field(default_factory=list)
