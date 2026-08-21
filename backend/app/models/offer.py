"""Offer console delivery ORM models (stage 10 / Task 1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

OFFER_STATUS_DRAFT = "draft"
OFFER_STATUS_READY = "ready"
OFFER_STATUS_SENDING = "sending"
OFFER_STATUS_SENT = "sent"
OFFER_STATUS_FAILED = "failed"
OFFER_STATUS_VOIDED = "voided"
OFFER_STATUSES = frozenset(
    {
        OFFER_STATUS_DRAFT,
        OFFER_STATUS_READY,
        OFFER_STATUS_SENDING,
        OFFER_STATUS_SENT,
        OFFER_STATUS_FAILED,
        OFFER_STATUS_VOIDED,
    }
)

MAIL_PROVIDER_CONSOLE = "console"

OFFER_ATTEMPT_STATUS_PENDING = "pending"
OFFER_ATTEMPT_STATUS_RUNNING = "running"
OFFER_ATTEMPT_STATUS_SUCCEEDED = "succeeded"
OFFER_ATTEMPT_STATUS_FAILED = "failed"
OFFER_ATTEMPT_STATUS_DEAD = "dead"
OFFER_ATTEMPT_STATUSES = frozenset(
    {
        OFFER_ATTEMPT_STATUS_PENDING,
        OFFER_ATTEMPT_STATUS_RUNNING,
        OFFER_ATTEMPT_STATUS_SUCCEEDED,
        OFFER_ATTEMPT_STATUS_FAILED,
        OFFER_ATTEMPT_STATUS_DEAD,
    }
)

MAIL_RETRY_COUNTDOWNS_SECONDS = {1: 60, 2: 300, 3: 1800}
MAIL_MAX_AUTO_ATTEMPTS = 4
OFFER_TEMPLATE_CODE = "offer_console_v1"
OFFER_TEMPLATE_VERSION = "1"


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'draft', 'ready', 'sending', 'sent', 'failed', 'voided'"
            ")",
            name="ck_offers_status",
        ),
        Index("ix_offers_application_id", "application_id"),
        Index(
            "uq_offers_application_active",
            "application_id",
            unique=True,
            postgresql_where=text("status NOT IN ('voided')"),
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
    hiring_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hiring_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "offer_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_offers_current_version",
        ),
        nullable=True,
    )
    recipient_email_masked: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    recipient_name: Mapped[str] = mapped_column(String(128), nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
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
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    versions: Mapped[list["OfferVersion"]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
        foreign_keys="OfferVersion.offer_id",
    )
    send_attempts: Mapped[list["OfferSendAttempt"]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
        foreign_keys="OfferSendAttempt.offer_id",
    )


class OfferVersion(Base):
    __tablename__ = "offer_versions"
    __table_args__ = (
        UniqueConstraint(
            "offer_id",
            "version_no",
            name="uq_offer_versions_offer_version_no",
        ),
        CheckConstraint("version_no >= 1", name="ck_offer_versions_version_no"),
        Index("ix_offer_versions_offer_id", "offer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_html_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    offer: Mapped[Offer] = relationship(
        back_populates="versions",
        foreign_keys=[offer_id],
    )


class OfferSendAttempt(Base):
    __tablename__ = "offer_send_attempts"
    __table_args__ = (
        UniqueConstraint(
            "offer_id",
            "idempotency_key",
            name="uq_offer_send_attempts_idempotency",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_offer_send_attempts_attempt_no"),
        CheckConstraint(
            "status IN ("
            "'pending', 'running', 'succeeded', 'failed', 'dead'"
            ")",
            name="ck_offer_send_attempts_status",
        ),
        Index("ix_offer_send_attempts_offer_id", "offer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    offer_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("offer_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
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

    offer: Mapped[Offer] = relationship(
        back_populates="send_attempts",
        foreign_keys=[offer_id],
    )
