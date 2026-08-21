"""HTTP contracts for comprehensive interview analysis APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_API_CONFIG = ConfigDict(
    extra="forbid",
    from_attributes=True,
    str_strip_whitespace=True,
    hide_input_in_errors=True,
)


class ComprehensiveAPIModel(BaseModel):
    model_config = _API_CONFIG


class ComprehensiveGenerateRequest(ComprehensiveAPIModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class ComprehensiveGenerateOut(ComprehensiveAPIModel):
    task_id: UUID
    task_type: str
    status: str
    application_id: UUID
    dispatch_status: Literal["queued", "pending_dispatch"]


class CoverageGapOut(ComprehensiveAPIModel):
    round_id: UUID
    sequence_no: int | None = None
    reason_code: str
    status: str | None = None


class CoverageIncludedRoundOut(ComprehensiveAPIModel):
    round_id: UUID
    sequence_no: int
    analysis_version_id: UUID
    overall_score: Decimal | float | None = None


class CoverageReportOut(ComprehensiveAPIModel):
    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    eligible_round_count: int
    total_round_count: int
    included_rounds: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    coverage_insufficient: bool
    single_round_only: bool
    missing_round_count: int


class ComprehensiveVersionSummaryOut(ComprehensiveAPIModel):
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    ai_task_id: UUID
    overall_score: Decimal | None = None
    coverage_report: dict[str, Any]
    created_by: UUID | None = None
    created_at: datetime
    is_current: bool
    is_stale: bool


class ComprehensiveSetOut(ComprehensiveAPIModel):
    analysis_id: UUID | None = None
    application_id: UUID
    current_version_id: UUID | None = None
    versions: list[ComprehensiveVersionSummaryOut] = Field(default_factory=list)


class ComprehensiveVersionDetailOut(ComprehensiveAPIModel):
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    ai_task_id: UUID
    overall_score: Decimal | None = None
    overall_summary: str
    round_refs: list[dict[str, Any]] = Field(default_factory=list)
    coverage_report: dict[str, Any]
    created_by: UUID | None = None
    created_at: datetime
    is_current: bool
    is_stale: bool
