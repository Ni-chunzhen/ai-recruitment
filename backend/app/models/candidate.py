import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.resume import PIPELINE_PENDING_PARSE

APPLICATION_STATUS_IN_PROGRESS = "in_progress"
APPLICATION_STATUS_REJECTED = "rejected"
APPLICATION_STATUS_TRANSFERRED = "transferred"
APPLICATION_STATUS_TERMINATED = "terminated"
APPLICATION_STATUS_HIRED = "hired"

APPLICATION_STATUSES = frozenset(
    {
        APPLICATION_STATUS_IN_PROGRESS,
        APPLICATION_STATUS_REJECTED,
        APPLICATION_STATUS_TRANSFERRED,
        APPLICATION_STATUS_TERMINATED,
        APPLICATION_STATUS_HIRED,
    }
)

IN_FLIGHT_STATUSES = frozenset({APPLICATION_STATUS_IN_PROGRESS})

CLOSE_ACTION_REJECT = "reject"
CLOSE_ACTION_TRANSFER = "transfer"
CLOSE_ACTION_TERMINATE = "terminate"
CLOSE_ACTIONS = frozenset(
    {CLOSE_ACTION_REJECT, CLOSE_ACTION_TRANSFER, CLOSE_ACTION_TERMINATE}
)

INTERVIEW_TASK_NONE = "none"
INTERVIEW_TASK_ACTIVE = "active"
INTERVIEW_TASK_PENDING_CANCEL = "pending_cancel"
INTERVIEW_TASK_CANCELLED = "cancelled"
INTERVIEW_TASK_PENDING_REBUILD = "pending_rebuild"
INTERVIEW_TASK_REBUILT = "rebuilt"

INTERVIEW_TASK_STATES = frozenset(
    {
        INTERVIEW_TASK_NONE,
        INTERVIEW_TASK_ACTIVE,
        INTERVIEW_TASK_PENDING_CANCEL,
        INTERVIEW_TASK_CANCELLED,
        INTERVIEW_TASK_PENDING_REBUILD,
        INTERVIEW_TASK_REBUILT,
    }
)

TIMELINE_EVENT_VERSION_MIGRATED = "version_migrated"


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    applications: Mapped[list["JobApplication"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    resumes: Mapped[list["Resume"]] = relationship(  # noqa: F821
        back_populates="candidate",
        cascade="all, delete-orphan",
    )


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=APPLICATION_STATUS_IN_PROGRESS
    )
    pipeline_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PIPELINE_PENDING_PARSE
    )
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    interview_started: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    interview_task_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=INTERVIEW_TASK_NONE
    )
    close_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transferred_to_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    migration_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    migrated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    migrated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    timeline_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    candidate: Mapped[Candidate] = relationship(back_populates="applications")
