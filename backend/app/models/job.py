import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

JOB_STATUS_DRAFT = "draft"
JOB_STATUS_OPEN = "open"
JOB_STATUS_PAUSED = "paused"
JOB_STATUS_CLOSED = "closed"

JOB_STATUS_LABELS = {
    JOB_STATUS_DRAFT: "草稿",
    JOB_STATUS_OPEN: "招聘中",
    JOB_STATUS_PAUSED: "已暂停",
    JOB_STATUS_CLOSED: "已关闭",
}

VERSION_STATUS_DRAFT = "draft"
VERSION_STATUS_PUBLISHED = "published"
VERSION_STATUS_SUPERSEDED = "superseded"

UPGRADE_INITIAL = "initial"
UPGRADE_MAJOR = "major"
UPGRADE_MINOR = "minor"


def empty_structured_jd() -> dict:
    return {
        "responsibilities": [],
        "requirements": [],
        "must_have": [],
        "nice_to_have": [],
        "skills": [],
    }


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=JOB_STATUS_DRAFT)
    name: Mapped[str] = mapped_column(String(255), default="")
    department: Mapped[str] = mapped_column(String(128), default="")
    level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str] = mapped_column(String(128), default="")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_name: Mapped[str] = mapped_column(String(128), default="")
    urgency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Application-managed pointers (no DB FK to avoid circular constraint).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    draft_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
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
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    versions: Mapped[list["JobVersion"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        foreign_keys="JobVersion.job_id",
    )


class JobVersion(Base):
    __tablename__ = "job_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    major: Mapped[int] = mapped_column(Integer, default=0)
    minor: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=VERSION_STATUS_DRAFT)
    upgrade_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_jd_text: Mapped[str] = mapped_column(Text, default="")
    structured_jd: Mapped[dict] = mapped_column(
        JSONB, default=empty_structured_jd
    )
    score_dimensions: Mapped[list] = mapped_column(JSONB, default=list)
    job_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_by: Mapped[uuid.UUID | None] = mapped_column(
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
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    job: Mapped[Job] = relationship(
        back_populates="versions",
        foreign_keys=[job_id],
    )


class JobCodeSequence(Base):
    __tablename__ = "job_code_sequences"

    year_month: Mapped[str] = mapped_column(String(6), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)
