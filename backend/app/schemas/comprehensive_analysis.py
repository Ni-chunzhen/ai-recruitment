"""Pydantic / dataclass contracts for comprehensive interview analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CoverageGap:
    round_id: UUID
    sequence_no: int | None
    reason_code: str
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": str(self.round_id),
            "sequence_no": self.sequence_no,
            "reason_code": self.reason_code,
            "status": self.status,
        }


@dataclass
class CoverageReport:
    eligible_round_count: int
    total_round_count: int
    included_rounds: list[dict[str, Any]]
    gaps: list[CoverageGap]
    coverage_insufficient: bool
    single_round_only: bool
    missing_round_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible_round_count": self.eligible_round_count,
            "total_round_count": self.total_round_count,
            "included_rounds": list(self.included_rounds),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "coverage_insufficient": self.coverage_insufficient,
            "single_round_only": self.single_round_only,
            "missing_round_count": self.missing_round_count,
        }


@dataclass
class ComprehensiveVersionSummary:
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    ai_task_id: UUID
    overall_score: Decimal | None
    coverage_report: dict[str, Any]
    created_by: UUID | None
    created_at: datetime
    is_current: bool
    is_stale: bool


@dataclass
class ComprehensiveSetSummary:
    analysis_id: UUID | None
    application_id: UUID
    current_version_id: UUID | None
    versions: list[ComprehensiveVersionSummary] = field(default_factory=list)


@dataclass
class ComprehensiveVersionDetail:
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    ai_task_id: UUID
    overall_score: Decimal | None
    overall_summary: str
    round_refs: list[dict[str, Any]]
    coverage_report: dict[str, Any]
    created_by: UUID | None
    created_at: datetime
    is_current: bool
    is_stale: bool
