"""Strict Pydantic contracts for stage 8 interview question and analysis outputs."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

EvidenceSource = Literal["JOB_REQUIREMENT", "RESUME_EXPERIENCE", "GENERAL"]

_AI_CONFIG = ConfigDict(
    extra="forbid",
    str_strip_whitespace=True,
    hide_input_in_errors=True,
)


def _non_empty(value: str) -> str:
    if not value:
        raise ValueError("must not be blank")
    return value


NonEmptyStr = Annotated[str, AfterValidator(_non_empty)]


class InterviewAIModel(BaseModel):
    model_config = _AI_CONFIG


class InterviewDimensionSnapshot(InterviewAIModel):
    dimension_key: NonEmptyStr
    display_order: int = Field(ge=1, le=50)
    name: NonEmptyStr
    weight: Decimal
    description: str = ""
    anchors: list[str] = Field(default_factory=list)

    @field_validator("weight")
    @classmethod
    def _finite_weight(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("weight must be a finite decimal")
        return value

    @field_validator("anchors")
    @classmethod
    def _trim_anchors(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value]


class InterviewQuestionItemResult(InterviewAIModel):
    dimension_key: NonEmptyStr
    question: NonEmptyStr = Field(min_length=1, max_length=2000)
    purpose: NonEmptyStr = Field(min_length=1, max_length=2000)
    evidence_source: EvidenceSource
    resume_evidence: str | None = Field(default=None, max_length=2000)
    follow_up_prompts: list[NonEmptyStr] = Field(default_factory=list, max_length=10)
    risk_flags: list[NonEmptyStr] = Field(default_factory=list, max_length=10)
    display_order: int = Field(ge=1, le=30)

    @field_validator("resume_evidence", mode="before")
    @classmethod
    def _blank_resume_evidence(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("follow_up_prompts")
    @classmethod
    def _follow_up_item_length(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 1000:
                raise ValueError("follow_up_prompts items must be 1-1000 characters")
        return value

    @field_validator("risk_flags")
    @classmethod
    def _risk_flag_item_length(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 500:
                raise ValueError("risk_flags items must be 1-500 characters")
        return value


class InterviewQuestionGenerateResult(InterviewAIModel):
    questions: list[InterviewQuestionItemResult] = Field(min_length=1, max_length=30)


class InterviewEvidenceRef(InterviewAIModel):
    segment_id: UUID
    segment_no: int = Field(gt=0)
    quote: NonEmptyStr = Field(min_length=1, max_length=2000)


class InterviewDimensionAnalysisResult(InterviewAIModel):
    dimension_key: NonEmptyStr
    score: int | None = None
    evidence: list[InterviewEvidenceRef] = Field(default_factory=list, max_length=5)
    analysis: NonEmptyStr = Field(min_length=1, max_length=10000)
    strengths: list[NonEmptyStr] = Field(default_factory=list, max_length=20)
    risks: list[NonEmptyStr] = Field(default_factory=list, max_length=20)
    insufficient_information: str | None = Field(default=None, max_length=5000)
    suggested_follow_ups: list[NonEmptyStr] = Field(default_factory=list, max_length=20)

    @field_validator("score", mode="before")
    @classmethod
    def _score_int(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("score must be an integer")
        if value < 1 or value > 5:
            raise ValueError("score must be between 1 and 5")
        return value

    @field_validator("insufficient_information", mode="before")
    @classmethod
    def _blank_insufficient(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("strengths", "risks", "suggested_follow_ups")
    @classmethod
    def _item_length(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 1000:
                raise ValueError("list items must be 1-1000 characters")
        return value


class InterviewRoundAnalyzeResult(InterviewAIModel):
    dimensions: list[InterviewDimensionAnalysisResult] = Field(
        min_length=1, max_length=50
    )
    overall_summary: NonEmptyStr = Field(min_length=1, max_length=20000)
    model_reported_overall_score: Decimal | None = None

    @field_validator("model_reported_overall_score")
    @classmethod
    def _finite_optional_score(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("model_reported_overall_score must be finite")
        return value


class InterviewEvidenceSegment(InterviewAIModel):
    """In-memory transcript segment for evidence checks. Never persist `.text`."""

    id: UUID
    transcript_version_id: UUID
    segment_no: int = Field(gt=0)
    is_included_in_analysis: bool
    text: str
