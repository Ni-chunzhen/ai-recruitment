"""Interview invitation ORM models for manual email workflow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

INVITATION_EVENT_INITIAL = "INITIAL"
INVITATION_EVENT_RESCHEDULE = "RESCHEDULE"
INVITATION_EVENT_CANCELLATION = "CANCELLATION"
INVITATION_EVENTS = frozenset(
    {
        INVITATION_EVENT_INITIAL,
        INVITATION_EVENT_RESCHEDULE,
        INVITATION_EVENT_CANCELLATION,
    }
)

INVITATION_AUDIENCE_CANDIDATE = "CANDIDATE"
INVITATION_AUDIENCE_INTERVIEWER = "INTERVIEWER"
INVITATION_AUDIENCES = frozenset(
    {INVITATION_AUDIENCE_CANDIDATE, INVITATION_AUDIENCE_INTERVIEWER}
)

INVITATION_STATUS_DRAFT = "DRAFT"
INVITATION_STATUS_READY = "READY"
INVITATION_STATUS_RECORDED_SENT = "RECORDED_SENT"
INVITATION_STATUS_VOIDED = "VOIDED"
INVITATION_STATUSES = frozenset(
    {
        INVITATION_STATUS_DRAFT,
        INVITATION_STATUS_READY,
        INVITATION_STATUS_RECORDED_SENT,
        INVITATION_STATUS_VOIDED,
    }
)

CHANNEL_CORPORATE_EMAIL = "CORPORATE_EMAIL"
CHANNEL_WORK_EMAIL = "WORK_EMAIL"
CHANNEL_OTHER = "OTHER"
CHANNEL_TYPES = frozenset(
    {CHANNEL_CORPORATE_EMAIL, CHANNEL_WORK_EMAIL, CHANNEL_OTHER}
)

COPY_TYPE_SUBJECT = "SUBJECT"
COPY_TYPE_HTML_BODY = "HTML_BODY"
COPY_TYPE_FULL_TEXT = "FULL_TEXT"
COPY_TYPES = frozenset(
    {COPY_TYPE_SUBJECT, COPY_TYPE_HTML_BODY, COPY_TYPE_FULL_TEXT}
)

TEMPLATE_CANDIDATE_INITIAL = "candidate_initial"
TEMPLATE_INTERVIEWER_INITIAL = "interviewer_initial"
TEMPLATE_CANDIDATE_RESCHEDULE = "candidate_reschedule"
TEMPLATE_INTERVIEWER_RESCHEDULE = "interviewer_reschedule"
TEMPLATE_CANDIDATE_CANCELLATION = "candidate_cancellation"
TEMPLATE_INTERVIEWER_CANCELLATION = "interviewer_cancellation"
TEMPLATE_CODES = frozenset(
    {
        TEMPLATE_CANDIDATE_INITIAL,
        TEMPLATE_INTERVIEWER_INITIAL,
        TEMPLATE_CANDIDATE_RESCHEDULE,
        TEMPLATE_INTERVIEWER_RESCHEDULE,
        TEMPLATE_CANDIDATE_CANCELLATION,
        TEMPLATE_INTERVIEWER_CANCELLATION,
    }
)
TEMPLATE_VERSION = "1"


class InterviewInvitationMessage(Base):
    __tablename__ = "interview_invitation_messages"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id",
            "event_type",
            "audience_type",
            "recipient_key",
            name="uq_invitation_msg_schedule_event_audience_recipient",
        ),
        CheckConstraint(
            "("
            "audience_type = 'CANDIDATE' AND recipient_user_id IS NULL"
            ") OR ("
            "audience_type = 'INTERVIEWER' AND recipient_user_id IS NOT NULL"
            ")",
            name="ck_invitation_messages_audience_recipient",
        ),
        Index("ix_invitation_messages_round_id", "interview_round_id"),
        Index("ix_invitation_messages_schedule_id", "schedule_id"),
        Index("ix_invitation_messages_schedule_version", "schedule_version"),
        Index("ix_invitation_messages_event_type", "event_type"),
        Index("ix_invitation_messages_audience_type", "audience_type"),
        Index("ix_invitation_messages_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_schedules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    audience_type: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recipient_key: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_email_masked: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_invitation_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_invitation_messages_current_version",
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

    versions: Mapped[list["InterviewInvitationVersion"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        foreign_keys="InterviewInvitationVersion.message_id",
    )
    send_records: Mapped[list["InterviewInvitationSendRecord"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        foreign_keys="InterviewInvitationSendRecord.message_id",
    )


class InterviewInvitationVersion(Base):
    __tablename__ = "interview_invitation_versions"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "version_no",
            name="uq_invitation_version_message_version_no",
        ),
        Index("ix_invitation_versions_message_id", "message_id"),
        Index("ix_invitation_versions_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_invitation_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_html_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    message: Mapped[InterviewInvitationMessage] = relationship(
        back_populates="versions",
        foreign_keys=[message_id],
    )


class InterviewInvitationSendRecord(Base):
    __tablename__ = "interview_invitation_send_records"
    __table_args__ = (
        Index("ix_invitation_send_records_message_id", "message_id"),
        Index("ix_invitation_send_records_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_invitation_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_invitation_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_email_masked: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    idempotency_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_idempotency_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    message: Mapped[InterviewInvitationMessage] = relationship(
        back_populates="send_records",
        foreign_keys=[message_id],
    )
