"""Application-level comprehensive interview analysis ORM (stage 9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION = "1.0"
COMPREHENSIVE_WORKFLOW_KEY = "interview_comprehensive_analyze"
COMPREHENSIVE_WORKFLOW_VERSION = "1.0"

COMPREHENSIVE_GAP_CANCELLED = "cancelled"
COMPREHENSIVE_GAP_ENDED_ABNORMALLY = "ended_abnormally"
COMPREHENSIVE_GAP_NOT_COMPLETED = "not_completed"
COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT = "without_transcript"
COMPREHENSIVE_GAP_TRANSCRIPT_UNCONFIRMED = "transcript_unconfirmed"
COMPREHENSIVE_GAP_ANALYSIS_NONE = "analysis_none"
COMPREHENSIVE_GAP_ANALYSIS_STALE = "analysis_stale"
COMPREHENSIVE_GAP_EXCLUDED_OTHER = "excluded_other"

COMPREHENSIVE_GAP_CODES = frozenset(
    {
        COMPREHENSIVE_GAP_CANCELLED,
        COMPREHENSIVE_GAP_ENDED_ABNORMALLY,
        COMPREHENSIVE_GAP_NOT_COMPLETED,
        COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT,
        COMPREHENSIVE_GAP_TRANSCRIPT_UNCONFIRMED,
        COMPREHENSIVE_GAP_ANALYSIS_NONE,
        COMPREHENSIVE_GAP_ANALYSIS_STALE,
        COMPREHENSIVE_GAP_EXCLUDED_OTHER,
    }
)


class ApplicationComprehensiveAnalysis(Base):
    __tablename__ = "application_comprehensive_analyses"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            name="uq_comprehensive_analyses_application_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "application_comprehensive_analysis_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_comprehensive_analyses_current_version",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    versions: Mapped[list["ApplicationComprehensiveAnalysisVersion"]] = (
        relationship(
            back_populates="analysis",
            foreign_keys=(
                "ApplicationComprehensiveAnalysisVersion.analysis_id"
            ),
            cascade="all, delete-orphan",
        )
    )
    current_version: Mapped[
        "ApplicationComprehensiveAnalysisVersion | None"
    ] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )


class ApplicationComprehensiveAnalysisVersion(Base):
    __tablename__ = "application_comprehensive_analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "version_no",
            name="uq_comprehensive_versions_analysis_no",
        ),
        UniqueConstraint(
            "analysis_id",
            "version_label",
            name="uq_comprehensive_versions_analysis_label",
        ),
        UniqueConstraint(
            "ai_task_id",
            name="uq_comprehensive_versions_ai_task",
        ),
        CheckConstraint(
            "version_no > 0",
            name="ck_comprehensive_versions_no_positive",
        ),
        CheckConstraint(
            "overall_score IS NULL OR ("
            "overall_score >= 1 AND overall_score <= 5"
            ")",
            name="ck_comprehensive_versions_overall_score",
        ),
        Index("ix_comprehensive_versions_analysis_id", "analysis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "application_comprehensive_analyses.id", ondelete="CASCADE"
        ),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    ai_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    round_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    coverage_report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    overall_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    overall_summary_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    analysis: Mapped[ApplicationComprehensiveAnalysis] = relationship(
        back_populates="versions",
        foreign_keys=[analysis_id],
    )
