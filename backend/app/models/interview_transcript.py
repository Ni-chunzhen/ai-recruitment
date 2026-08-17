"""Interview transcript ORM models for external note import and proofreading."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TranscriptVersionType(str, Enum):
    ORIGINAL = "ORIGINAL"
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class TranscriptVersionStatus(str, Enum):
    EDITING = "EDITING"
    IMMUTABLE = "IMMUTABLE"


class TranscriptSourceMethod(str, Enum):
    PASTE = "PASTE"
    TXT = "TXT"
    MD = "MD"


class TranscriptSpeakerRole(str, Enum):
    CANDIDATE = "CANDIDATE"
    INTERVIEWER = "INTERVIEWER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class TranscriptSegmentSource(str, Enum):
    ORIGINAL = "ORIGINAL"
    CORRECTED = "CORRECTED"
    MANUAL_ADDITION = "MANUAL_ADDITION"


class TranscriptCompletionMode(str, Enum):
    CONFIRMED_TRANSCRIPT = "CONFIRMED_TRANSCRIPT"
    WITHOUT_TRANSCRIPT = "WITHOUT_TRANSCRIPT"


TRANSCRIPT_REASON_EXTERNAL_TOOL_UNAVAILABLE = "EXTERNAL_TOOL_UNAVAILABLE"
TRANSCRIPT_REASON_RECORDING_NOT_PERMITTED = "RECORDING_NOT_PERMITTED"
TRANSCRIPT_REASON_FILE_LOST = "TRANSCRIPT_FILE_LOST"
TRANSCRIPT_REASON_CONTENT_UNUSABLE = "CONTENT_UNUSABLE"
TRANSCRIPT_REASON_OTHER = "OTHER"

TRANSCRIPT_REASON_CATALOG: tuple[tuple[str, str], ...] = (
    (TRANSCRIPT_REASON_EXTERNAL_TOOL_UNAVAILABLE, "外部听记工具不可用"),
    (TRANSCRIPT_REASON_RECORDING_NOT_PERMITTED, "未获准录音或转写"),
    (TRANSCRIPT_REASON_FILE_LOST, "转写文件丢失"),
    (TRANSCRIPT_REASON_CONTENT_UNUSABLE, "内容无法使用"),
    (TRANSCRIPT_REASON_OTHER, "其他"),
)


def list_transcript_reason_catalog() -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "label": label,
            "requires_description": code == TRANSCRIPT_REASON_OTHER,
        }
        for code, label in TRANSCRIPT_REASON_CATALOG
    ]


class InterviewTranscript(Base):
    __tablename__ = "interview_transcripts"
    __table_args__ = (
        UniqueConstraint(
            "interview_round_id", name="uq_interview_transcripts_round_id"
        ),
        Index("ix_interview_transcripts_round_id", "interview_round_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_transcript_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_transcripts_original_version",
        ),
        nullable=True,
    )
    current_draft_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_transcript_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_transcripts_current_draft_version",
        ),
        nullable=True,
    )
    current_confirmed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_transcript_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_transcripts_current_confirmed_version",
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    versions: Mapped[list["InterviewTranscriptVersion"]] = relationship(
        back_populates="transcript",
        foreign_keys="InterviewTranscriptVersion.transcript_id",
        cascade="all, delete-orphan",
    )


class InterviewTranscriptVersion(Base):
    __tablename__ = "interview_transcript_versions"
    __table_args__ = (
        UniqueConstraint(
            "transcript_id",
            "version_label",
            name="uq_transcript_version_label",
        ),
        CheckConstraint(
            "version_type IN ('ORIGINAL', 'DRAFT', 'CONFIRMED')",
            name="ck_transcript_version_type",
        ),
        CheckConstraint(
            "status IN ('EDITING', 'IMMUTABLE')",
            name="ck_transcript_version_status",
        ),
        CheckConstraint(
            "source_method IN ('PASTE', 'TXT', 'MD')",
            name="ck_transcript_source_method",
        ),
        CheckConstraint(
            "("
            "version_type IN ('ORIGINAL', 'CONFIRMED') AND status = 'IMMUTABLE'"
            ") OR ("
            "version_type = 'DRAFT'"
            ")",
            name="ck_transcript_version_type_status",
        ),
        CheckConstraint("version_no > 0", name="ck_transcript_version_no_positive"),
        Index("ix_transcript_versions_transcript_id", "transcript_id"),
        Index("ix_transcript_versions_version_type", "version_type"),
        Index("ix_transcript_versions_status", "status"),
        Index("ix_transcript_versions_version_label", "version_label"),
        Index(
            "uq_transcript_one_original",
            "transcript_id",
            unique=True,
            postgresql_where=text("version_type = 'ORIGINAL'"),
        ),
        Index(
            "uq_transcript_one_editing_draft",
            "transcript_id",
            unique=True,
            postgresql_where=text("version_type = 'DRAFT' AND status = 'EDITING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_transcripts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_type: Mapped[str] = mapped_column(String(16), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    source_method: Mapped[str] = mapped_column(String(16), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_encoding: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    based_on_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_transcript_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    transcript: Mapped[InterviewTranscript] = relationship(
        back_populates="versions",
        foreign_keys=[transcript_id],
    )
    segments: Mapped[list["InterviewTranscriptSegment"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="InterviewTranscriptSegment.segment_no",
    )


class InterviewTranscriptSegment(Base):
    __tablename__ = "interview_transcript_segments"
    __table_args__ = (
        UniqueConstraint(
            "transcript_version_id",
            "segment_no",
            name="uq_transcript_segment_no",
        ),
        CheckConstraint(
            "segment_no > 0", name="ck_transcript_segment_no_positive"
        ),
        CheckConstraint(
            "("
            "start_time_ms IS NULL AND end_time_ms IS NULL"
            ") OR ("
            "start_time_ms IS NOT NULL AND end_time_ms IS NOT NULL "
            "AND start_time_ms >= 0 AND start_time_ms < end_time_ms"
            ")",
            name="ck_transcript_segment_time_range",
        ),
        CheckConstraint(
            "speaker_role IN ('CANDIDATE', 'INTERVIEWER', 'OTHER', 'UNKNOWN')",
            name="ck_transcript_segment_speaker_role",
        ),
        CheckConstraint(
            "source_type IN ('ORIGINAL', 'CORRECTED', 'MANUAL_ADDITION')",
            name="ck_transcript_segment_source_type",
        ),
        Index("ix_transcript_segments_version_id", "transcript_version_id"),
        Index("ix_transcript_segments_segment_no", "segment_no"),
        Index("ix_transcript_segments_speaker_role", "speaker_role"),
        Index("ix_transcript_segments_included", "is_included_in_analysis"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transcript_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_transcript_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    segment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_key: Mapped[str] = mapped_column(String(64), nullable=False)
    speaker_name: Mapped[str] = mapped_column(String(128), nullable=False)
    speaker_role: Mapped[str] = mapped_column(String(32), nullable=False)
    start_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_segment_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    is_included_in_analysis: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    is_unclear: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    version: Mapped[InterviewTranscriptVersion] = relationship(
        back_populates="segments",
        foreign_keys=[transcript_version_id],
    )
