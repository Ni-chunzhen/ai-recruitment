from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskType = Literal[
    "JD_PARSE",
    "SCORE_DIMENSION_RECOMMEND",
    "RESUME_PARSE",
    "RESUME_SCORE",
    "INTERVIEW_QUESTION_GENERATE",
    "INTERVIEW_ROUND_ANALYZE",
    "INTERVIEW_COMPREHENSIVE_ANALYZE",
]
AITaskStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "output_invalid",
    "cancelled",
]
ErrorCategory = Literal["retryable", "non_retryable"]


class ScoreDimensionRecommendItem(BaseModel):
    name: str
    weight: float
    description: str = ""
    anchors: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must be non-empty")
        return cleaned

    @field_validator("weight")
    @classmethod
    def _weight_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("weight must be > 0")
        return value

    @field_validator("anchors", mode="before")
    @classmethod
    def _coerce_anchors(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("anchors must be a list of strings")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("anchor items must be strings")
            result.append(item)
        return result


class ScoreDimensionRecommendResult(BaseModel):
    dimensions: list[ScoreDimensionRecommendItem]

    @field_validator("dimensions")
    @classmethod
    def _dimensions_non_empty(
        cls, value: list[ScoreDimensionRecommendItem]
    ) -> list[ScoreDimensionRecommendItem]:
        if not value:
            raise ValueError("dimensions must not be empty")
        return value


class JdParseResult(BaseModel):
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    # Dify JD_PARSE 串联「能力维度生成」后附带；仅 JD 结构化时可为 None
    dimensions: list[ScoreDimensionRecommendItem] | None = None

    @field_validator(
        "responsibilities",
        "requirements",
        "must_have",
        "nice_to_have",
        "skills",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("must be a list of strings")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("list items must be strings")
            result.append(item)
        return result

    @field_validator("dimensions")
    @classmethod
    def _dimensions_if_present(
        cls, value: list[ScoreDimensionRecommendItem] | None
    ) -> list[ScoreDimensionRecommendItem] | None:
        if value is not None and len(value) == 0:
            raise ValueError("dimensions must not be empty when provided")
        return value


class CancelAITaskRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class MarkStaleFailedAITaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime


class MarkStaleFailedAITaskOut(BaseModel):
    id: UUID
    status: str
    error_code: str | None
    updated_at: datetime
    finished_at: datetime | None


class CreateAITaskRequest(BaseModel):
    task_type: TaskType
    business_type: str = "job"
    business_id: UUID
    input: dict[str, Any] | None = None


class AITaskAttemptOut(BaseModel):
    id: UUID
    attempt_no: int
    retry_cycle_no: int = 0
    cycle_attempt_no: int = 1
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    http_status: int | None
    error_category: str | None
    error_message: str | None
    created_at: datetime
    # provider_run_id / request_id / raw_response are admin/tech-only;
    # not exposed on the recruiter-facing task API.


class AITaskSummaryOut(BaseModel):
    """Ordinary recruiter-facing task payload. No snapshots, raw bodies, or tech IDs."""

    id: UUID
    task_type: TaskType
    status: AITaskStatus
    business_type: str
    business_id: UUID
    version_id: UUID | None = None
    created_by: UUID | None = None
    error_code: str | None
    error_message: str | None
    error_category: ErrorCategory | None = None
    attempt_count: int
    retry_cycle_no: int = 0
    cycle_attempt_count: int = 0
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attempts: list[AITaskAttemptOut] = Field(default_factory=list)


class AITaskOut(AITaskSummaryOut):
    """Internal/admin-capable shape. Ordinary APIs must use AITaskSummaryOut."""

    input_snapshot: dict[str, Any]
    result_payload: dict[str, Any] | None
    raw_purged_at: datetime | None = None


class AITaskListResponse(BaseModel):
    items: list[AITaskSummaryOut]
    total: int


class AITaskAttemptAdminOut(BaseModel):
    id: UUID
    attempt_no: int
    retry_cycle_no: int = 0
    cycle_attempt_no: int = 1
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    http_status: int | None
    error_category: str | None
    error_message: str | None
    provider_run_id: str | None = None
    request_id: str | None = None


class AITaskAdminListItemOut(BaseModel):
    id: UUID
    task_type: TaskType
    business_type: str
    business_id: UUID
    status: AITaskStatus
    attempt_count: int
    retry_cycle_no: int = 0
    cycle_attempt_count: int = 0
    error_category: ErrorCategory | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class AITaskAdminListResponse(BaseModel):
    items: list[AITaskAdminListItemOut]
    total: int
    page: int
    page_size: int


class AITaskAdminDetailOut(AITaskAdminListItemOut):
    attempts: list[AITaskAttemptAdminOut] = Field(default_factory=list)
