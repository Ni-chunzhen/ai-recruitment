"""Stage 8 interview question generation and single-round analysis ORM."""

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

QUESTION_SET_STATUS_DRAFT = "DRAFT"
QUESTION_SET_STATUS_READY = "READY"
QUESTION_SET_STATUS_ARCHIVED = "ARCHIVED"
QUESTION_SET_STATUSES = frozenset(
    {
        QUESTION_SET_STATUS_DRAFT,
        QUESTION_SET_STATUS_READY,
        QUESTION_SET_STATUS_ARCHIVED,
    }
)

QUESTION_SOURCE_AI_GENERATED = "AI_GENERATED"
QUESTION_SOURCE_MANUAL_EDIT = "MANUAL_EDIT"
QUESTION_SOURCE_TYPES = frozenset(
    {QUESTION_SOURCE_AI_GENERATED, QUESTION_SOURCE_MANUAL_EDIT}
)

EVIDENCE_SOURCE_JOB_REQUIREMENT = "JOB_REQUIREMENT"
EVIDENCE_SOURCE_RESUME_EXPERIENCE = "RESUME_EXPERIENCE"
EVIDENCE_SOURCE_GENERAL = "GENERAL"
EVIDENCE_SOURCES = frozenset(
    {
        EVIDENCE_SOURCE_JOB_REQUIREMENT,
        EVIDENCE_SOURCE_RESUME_EXPERIENCE,
        EVIDENCE_SOURCE_GENERAL,
    }
)


class InterviewQuestionSet(Base):
    __tablename__ = "interview_question_sets"
    __table_args__ = (
        UniqueConstraint(
            "interview_round_id", name="uq_interview_question_sets_round_id"
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'READY', 'ARCHIVED')",
            name="ck_interview_question_sets_status",
        ),
        CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)",
            name="ck_interview_question_sets_confirmed_pair",
        ),
        CheckConstraint(
            "status <> 'READY' OR ("
            "current_version_id IS NOT NULL "
            "AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL"
            ")",
            name="ck_interview_question_sets_ready_requires_confirm",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_question_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_question_sets_current_version",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
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

    round: Mapped["InterviewRound"] = relationship(
        foreign_keys=[interview_round_id],
    )
    versions: Mapped[list["InterviewQuestionVersion"]] = relationship(
        back_populates="question_set",
        foreign_keys="InterviewQuestionVersion.question_set_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped[InterviewQuestionVersion | None] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )
    confirmed_user: Mapped["User | None"] = relationship(
        foreign_keys=[confirmed_by],
    )
    creator: Mapped["User | None"] = relationship(
        foreign_keys=[created_by],
    )


class InterviewQuestionVersion(Base):
    __tablename__ = "interview_question_versions"
    __table_args__ = (
        UniqueConstraint(
            "question_set_id",
            "version_no",
            name="uq_question_versions_set_no",
        ),
        UniqueConstraint(
            "question_set_id",
            "version_label",
            name="uq_question_versions_set_label",
        ),
        UniqueConstraint("ai_task_id", name="uq_question_versions_ai_task"),
        CheckConstraint(
            "version_no > 0", name="ck_question_versions_no_positive"
        ),
        CheckConstraint(
            "source_type IN ('AI_GENERATED', 'MANUAL_EDIT')",
            name="ck_question_versions_source_type",
        ),
        CheckConstraint(
            "("
            "source_type = 'AI_GENERATED' AND ai_task_id IS NOT NULL"
            ") OR ("
            "source_type = 'MANUAL_EDIT' AND ai_task_id IS NULL"
            ")",
            name="ck_question_versions_source_ai_task",
        ),
        Index("ix_question_versions_set_id", "question_set_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    question_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_question_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    ai_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    job_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resume_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    question_set: Mapped[InterviewQuestionSet] = relationship(
        back_populates="versions",
        foreign_keys=[question_set_id],
    )
    job_version: Mapped["JobVersion"] = relationship(foreign_keys=[job_version_id])
    resume_version: Mapped["ResumeVersion"] = relationship(
        foreign_keys=[resume_version_id]
    )
    ai_task: Mapped["AITask | None"] = relationship(foreign_keys=[ai_task_id])
    items: Mapped[list["InterviewQuestionItem"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        foreign_keys="InterviewQuestionItem.question_version_id",
        order_by="InterviewQuestionItem.display_order",
    )


class InterviewQuestionItem(Base):
    __tablename__ = "interview_question_items"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id",
            "display_order",
            name="uq_question_items_version_order",
        ),
        CheckConstraint(
            "display_order > 0",
            name="ck_question_items_display_order_positive",
        ),
        CheckConstraint(
            "evidence_source IN ("
            "'JOB_REQUIREMENT', 'RESUME_EXPERIENCE', 'GENERAL'"
            ")",
            name="ck_question_items_evidence_source",
        ),
        Index("ix_question_items_version_id", "question_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_question_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension_key: Mapped[str] = mapped_column(String(128), nullable=False)
    question_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    purpose_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    resume_evidence_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    follow_up_prompts_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    risk_flags_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    version: Mapped[InterviewQuestionVersion] = relationship(
        back_populates="items",
        foreign_keys=[question_version_id],
    )


class InterviewRoundAnalysis(Base):
    __tablename__ = "interview_round_analyses"
    __table_args__ = (
        UniqueConstraint(
            "interview_round_id", name="uq_round_analyses_round_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_round_analysis_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_round_analyses_current_version",
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

    round: Mapped["InterviewRound"] = relationship(
        foreign_keys=[interview_round_id],
    )
    versions: Mapped[list["InterviewRoundAnalysisVersion"]] = relationship(
        back_populates="analysis",
        foreign_keys="InterviewRoundAnalysisVersion.analysis_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped[InterviewRoundAnalysisVersion | None] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )


class InterviewRoundAnalysisVersion(Base):
    __tablename__ = "interview_round_analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id",
            "version_no",
            name="uq_analysis_versions_analysis_no",
        ),
        UniqueConstraint(
            "analysis_id",
            "version_label",
            name="uq_analysis_versions_analysis_label",
        ),
        UniqueConstraint("ai_task_id", name="uq_analysis_versions_ai_task"),
        CheckConstraint(
            "version_no > 0", name="ck_analysis_versions_no_positive"
        ),
        CheckConstraint(
            "overall_score IS NULL OR ("
            "overall_score >= 1 AND overall_score <= 5"
            ")",
            name="ck_analysis_versions_overall_score",
        ),
        Index("ix_analysis_versions_analysis_id", "analysis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_round_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    transcript_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_transcript_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ai_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dimensions_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
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

    analysis: Mapped[InterviewRoundAnalysis] = relationship(
        back_populates="versions",
        foreign_keys=[analysis_id],
    )
    transcript_version: Mapped["InterviewTranscriptVersion"] = relationship(
        foreign_keys=[transcript_version_id]
    )
    job_version: Mapped["JobVersion"] = relationship(foreign_keys=[job_version_id])
    ai_task: Mapped["AITask"] = relationship(foreign_keys=[ai_task_id])
    dimensions: Mapped[list["InterviewRoundAnalysisDimension"]] = relationship(
        back_populates="analysis_version",
        cascade="all, delete-orphan",
        foreign_keys="InterviewRoundAnalysisDimension.analysis_version_id",
        order_by="InterviewRoundAnalysisDimension.display_order",
    )


class InterviewRoundAnalysisDimension(Base):
    __tablename__ = "interview_round_analysis_dimensions"
    __table_args__ = (
        UniqueConstraint(
            "analysis_version_id",
            "dimension_key",
            name="uq_analysis_dims_version_key",
        ),
        UniqueConstraint(
            "analysis_version_id",
            "display_order",
            name="uq_analysis_dims_version_order",
        ),
        CheckConstraint(
            "display_order > 0", name="ck_analysis_dims_order_positive"
        ),
        CheckConstraint(
            "weight > 0 AND weight <= 100",
            name="ck_analysis_dims_weight",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 1 AND score <= 5)",
            name="ck_analysis_dims_score",
        ),
        CheckConstraint(
            "("
            "score IS NOT NULL AND insufficient_information_encrypted IS NULL"
            ") OR ("
            "score IS NULL AND insufficient_information_encrypted IS NOT NULL"
            ")",
            name="ck_analysis_dims_score_info_mutex",
        ),
        Index("ix_analysis_dims_version_id", "analysis_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_round_analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    strengths_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    risks_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    insufficient_information_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    suggested_follow_ups_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    analysis_version: Mapped[InterviewRoundAnalysisVersion] = relationship(
        back_populates="dimensions",
        foreign_keys=[analysis_version_id],
    )
    evidence: Mapped[list["InterviewRoundAnalysisEvidence"]] = relationship(
        back_populates="analysis_dimension",
        cascade="all, delete-orphan",
        foreign_keys="InterviewRoundAnalysisEvidence.analysis_dimension_id",
    )


class InterviewRoundAnalysisEvidence(Base):
    __tablename__ = "interview_round_analysis_evidence"
    __table_args__ = (
        UniqueConstraint(
            "analysis_dimension_id",
            "transcript_segment_id",
            name="uq_analysis_evidence_dim_segment",
        ),
        CheckConstraint(
            "segment_no > 0",
            name="ck_analysis_evidence_segment_no_positive",
        ),
        Index("ix_analysis_evidence_dim_id", "analysis_dimension_id"),
        Index("ix_analysis_evidence_segment_id", "transcript_segment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_dimension_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_round_analysis_dimensions.id", ondelete="CASCADE"
        ),
        nullable=False,
    )
    transcript_segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_transcript_segments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    segment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    analysis_dimension: Mapped[InterviewRoundAnalysisDimension] = relationship(
        back_populates="evidence",
        foreign_keys=[analysis_dimension_id],
    )
    transcript_segment: Mapped["InterviewTranscriptSegment"] = relationship(
        foreign_keys=[transcript_segment_id]
    )
