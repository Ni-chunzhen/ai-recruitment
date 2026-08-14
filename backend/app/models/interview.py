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

INTERVIEW_STATUS_DRAFT = "DRAFT"
INTERVIEW_STATUS_SCHEDULED = "SCHEDULED"
INTERVIEW_STATUS_CONFIRMED = "CONFIRMED"
INTERVIEW_STATUS_IN_PROGRESS = "IN_PROGRESS"
INTERVIEW_STATUS_PENDING_TRANSCRIPT = "PENDING_TRANSCRIPT"
INTERVIEW_STATUS_COMPLETED = "COMPLETED"
INTERVIEW_STATUS_CANCELLED = "CANCELLED"
INTERVIEW_STATUS_ENDED_ABNORMALLY = "ENDED_ABNORMALLY"

INTERVIEW_STATUSES = frozenset(
    {
        INTERVIEW_STATUS_DRAFT,
        INTERVIEW_STATUS_SCHEDULED,
        INTERVIEW_STATUS_CONFIRMED,
        INTERVIEW_STATUS_IN_PROGRESS,
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
        INTERVIEW_STATUS_COMPLETED,
        INTERVIEW_STATUS_CANCELLED,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
    }
)

EDITABLE_STATUSES = frozenset(
    {
        INTERVIEW_STATUS_DRAFT,
        INTERVIEW_STATUS_SCHEDULED,
        INTERVIEW_STATUS_CONFIRMED,
    }
)

TERMINAL_ROUND_STATUSES = frozenset(
    {
        INTERVIEW_STATUS_COMPLETED,
        INTERVIEW_STATUS_CANCELLED,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
    }
)

IMMUTABLE_SEQUENCE_STATUSES = frozenset(
    {
        INTERVIEW_STATUS_COMPLETED,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
        INTERVIEW_STATUS_IN_PROGRESS,
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    }
)

INTERVIEW_FORMAT_ONLINE = "ONLINE"
INTERVIEW_FORMAT_OFFLINE = "OFFLINE"
INTERVIEW_FORMATS = frozenset({INTERVIEW_FORMAT_ONLINE, INTERVIEW_FORMAT_OFFLINE})

MEETING_MODE_MANUAL = "MANUAL"
MEETING_MODE_ADAPTER = "ADAPTER"
MEETING_MODES = frozenset({MEETING_MODE_MANUAL, MEETING_MODE_ADAPTER})

SCHEDULE_STATUS_ACTIVE = "ACTIVE"
SCHEDULE_STATUS_SUPERSEDED = "SUPERSEDED"
SCHEDULE_STATUS_CANCELLED = "CANCELLED"
SCHEDULE_STATUSES = frozenset(
    {
        SCHEDULE_STATUS_ACTIVE,
        SCHEDULE_STATUS_SUPERSEDED,
        SCHEDULE_STATUS_CANCELLED,
    }
)

CANCEL_REASON_CANDIDATE_RESCHEDULE = "CANDIDATE_RESCHEDULE"
CANCEL_REASON_CANDIDATE_WITHDRAWAL = "CANDIDATE_WITHDRAWAL"
CANCEL_REASON_INTERVIEWER_CONFLICT = "INTERVIEWER_CONFLICT"
CANCEL_REASON_RECRUITMENT_PLAN_CHANGED = "RECRUITMENT_PLAN_CHANGED"
CANCEL_REASON_OTHER = "OTHER"

CANCEL_REASON_CATALOG: tuple[tuple[str, str], ...] = (
    (CANCEL_REASON_CANDIDATE_RESCHEDULE, "候选人改期"),
    (CANCEL_REASON_CANDIDATE_WITHDRAWAL, "候选人退出"),
    (CANCEL_REASON_INTERVIEWER_CONFLICT, "面试官时间冲突"),
    (CANCEL_REASON_RECRUITMENT_PLAN_CHANGED, "招聘计划变更"),
    (CANCEL_REASON_OTHER, "其他"),
)

ABNORMAL_REASON_CANDIDATE_NO_SHOW = "CANDIDATE_NO_SHOW"
ABNORMAL_REASON_INTERVIEWER_NO_SHOW = "INTERVIEWER_NO_SHOW"
ABNORMAL_REASON_TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
ABNORMAL_REASON_OTHER = "OTHER"

ABNORMAL_REASON_CATALOG: tuple[tuple[str, str], ...] = (
    (ABNORMAL_REASON_CANDIDATE_NO_SHOW, "候选人未到场"),
    (ABNORMAL_REASON_INTERVIEWER_NO_SHOW, "面试官未到场"),
    (ABNORMAL_REASON_TECHNICAL_FAILURE, "技术故障"),
    (ABNORMAL_REASON_OTHER, "其他"),
)

REASON_OTHER = "OTHER"


def list_interview_reason_catalog() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for code, label in CANCEL_REASON_CATALOG:
        items.append(
            {
                "code": code,
                "label": label,
                "category": "cancel",
                "requires_description": code == REASON_OTHER,
            }
        )
    for code, label in ABNORMAL_REASON_CATALOG:
        items.append(
            {
                "code": code,
                "label": label,
                "category": "abnormal",
                "requires_description": code == REASON_OTHER,
            }
        )
    return items


class InterviewRound(Base):
    __tablename__ = "interview_rounds"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "sequence_no",
            name="uq_interview_rounds_application_sequence",
        ),
        CheckConstraint(
            "sequence_no > 0", name="ck_interview_rounds_sequence_positive"
        ),
        Index("ix_interview_rounds_application_id", "application_id"),
        Index("ix_interview_rounds_status", "status"),
        Index("ix_interview_rounds_owner_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=INTERVIEW_STATUS_DRAFT
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "interview_schedules.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_interview_rounds_current_schedule",
        ),
        nullable=True,
    )
    cancellation_reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    cancellation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    abnormal_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abnormal_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    interviewers: Mapped[list["InterviewRoundInterviewer"]] = relationship(
        back_populates="interview_round",
        cascade="all, delete-orphan",
        foreign_keys="InterviewRoundInterviewer.interview_round_id",
    )
    schedules: Mapped[list["InterviewSchedule"]] = relationship(
        back_populates="interview_round",
        cascade="all, delete-orphan",
        foreign_keys="InterviewSchedule.interview_round_id",
    )


class InterviewRoundInterviewer(Base):
    __tablename__ = "interview_round_interviewers"
    __table_args__ = (
        UniqueConstraint(
            "interview_round_id",
            "interviewer_id",
            name="uq_interview_round_interviewer",
        ),
        Index("ix_interview_round_interviewers_interviewer_id", "interviewer_id"),
        Index("ix_interview_round_interviewers_round_id", "interview_round_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    interviewer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    interview_round: Mapped[InterviewRound] = relationship(
        back_populates="interviewers",
        foreign_keys=[interview_round_id],
    )


class InterviewSchedule(Base):
    __tablename__ = "interview_schedules"
    __table_args__ = (
        UniqueConstraint(
            "interview_round_id",
            "schedule_version",
            name="uq_interview_schedules_round_version",
        ),
        Index(
            "uq_interview_schedules_one_active",
            "interview_round_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint(
            "end_at_utc > start_at_utc",
            name="ck_interview_schedules_end_after_start",
        ),
        Index("ix_interview_schedules_round_id", "interview_round_id"),
        Index("ix_interview_schedules_status", "status"),
        Index(
            "ix_interview_schedules_start_end",
            "start_at_utc",
            "end_at_utc",
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
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    start_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    meeting_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    meeting_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meeting_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meeting_password_encrypted: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reschedule_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    interview_round: Mapped[InterviewRound] = relationship(
        back_populates="schedules",
        foreign_keys=[interview_round_id],
    )


class InterviewIdempotencyKey(Base):
    __tablename__ = "interview_idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "action",
            "scope_id",
            "idempotency_key",
            name="uq_interview_idempotency_actor_action_scope_key",
        ),
        Index("ix_interview_idempotency_scope", "scope_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_round_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interview_rounds.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
