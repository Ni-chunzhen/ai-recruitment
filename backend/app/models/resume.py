import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# 简历处理状态（挂在 resume_versions）
RESUME_STATUS_PENDING_PARSE = "pending_parse"
RESUME_STATUS_PARSING = "parsing"
RESUME_STATUS_PENDING_REVIEW = "pending_review"
RESUME_STATUS_CONFIRMED = "confirmed"
RESUME_STATUS_PARSE_FAILED = "parse_failed"
RESUME_STATUS_VOID = "void"

RESUME_STATUSES = frozenset(
    {
        RESUME_STATUS_PENDING_PARSE,
        RESUME_STATUS_PARSING,
        RESUME_STATUS_PENDING_REVIEW,
        RESUME_STATUS_CONFIRMED,
        RESUME_STATUS_PARSE_FAILED,
        RESUME_STATUS_VOID,
    }
)

VERSION_KIND_FILE = "file"
VERSION_KIND_CONFIRMED = "confirmed"
VERSION_KINDS = frozenset({VERSION_KIND_FILE, VERSION_KIND_CONFIRMED})

# 应聘流程状态（挂在 job_applications.pipeline_status）
PIPELINE_PENDING_PARSE = "pending_parse"
PIPELINE_PENDING_HR_SCREEN = "pending_hr_screen"
PIPELINE_INTERVIEWING = "interviewing"
PIPELINE_REJECTED = "rejected"
PIPELINE_TALENT_POOL = "talent_pool"
PIPELINE_STATUSES = frozenset(
    {
        PIPELINE_PENDING_PARSE,
        PIPELINE_PENDING_HR_SCREEN,
        PIPELINE_INTERVIEWING,
        PIPELINE_REJECTED,
        PIPELINE_TALENT_POOL,
    }
)

SCREENING_ENTER_INTERVIEW = "enter_interview"
SCREENING_HOLD = "hold"
SCREENING_REJECT = "reject"
SCREENING_TALENT_POOL = "talent_pool"
SCREENING_DECISIONS = frozenset(
    {
        SCREENING_ENTER_INTERVIEW,
        SCREENING_HOLD,
        SCREENING_REJECT,
        SCREENING_TALENT_POOL,
    }
)

SCREENING_REASON_MUST_HAVE = "must_have_mismatch"
SCREENING_REASON_CORE_EXPERIENCE = "core_experience_insufficient"
SCREENING_REASON_SKILL_MISMATCH = "skill_mismatch"
SCREENING_REASON_PROJECT_EXPERIENCE = "project_experience_insufficient"
SCREENING_REASON_LOCATION_OR_START = "location_or_start_mismatch"
SCREENING_REASON_SALARY = "salary_mismatch"
SCREENING_REASON_INFO_INSUFFICIENT = "information_insufficient"
SCREENING_REASON_OTHER = "other"
SCREENING_REASON_CODES = frozenset(
    {
        SCREENING_REASON_MUST_HAVE,
        SCREENING_REASON_CORE_EXPERIENCE,
        SCREENING_REASON_SKILL_MISMATCH,
        SCREENING_REASON_PROJECT_EXPERIENCE,
        SCREENING_REASON_LOCATION_OR_START,
        SCREENING_REASON_SALARY,
        SCREENING_REASON_INFO_INSUFFICIENT,
        SCREENING_REASON_OTHER,
    }
)
SCREENING_REASON_REQUIRED_DECISIONS = frozenset(
    {SCREENING_REJECT, SCREENING_TALENT_POOL}
)
SCREENING_REASON_CATALOG: tuple[tuple[str, str], ...] = (
    (SCREENING_REASON_MUST_HAVE, "必备条件不符合"),
    (SCREENING_REASON_CORE_EXPERIENCE, "核心经历不足"),
    (SCREENING_REASON_SKILL_MISMATCH, "技能与岗位不匹配"),
    (SCREENING_REASON_PROJECT_EXPERIENCE, "项目经验不足"),
    (SCREENING_REASON_LOCATION_OR_START, "工作地点或到岗条件不匹配"),
    (SCREENING_REASON_SALARY, "薪资期望不匹配"),
    (SCREENING_REASON_INFO_INSUFFICIENT, "信息不足且无法继续核实"),
    (SCREENING_REASON_OTHER, "其他"),
)


def list_screening_reason_catalog() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for code, label in SCREENING_REASON_CATALOG:
        items.append(
            {
                "code": code,
                "label": label,
                "allowed_decisions": [SCREENING_REJECT, SCREENING_TALENT_POOL],
                "requires_description": code == SCREENING_REASON_OTHER,
            }
        )
    return items

FIELD_SOURCE_AI = "ai"
FIELD_SOURCE_MANUAL = "manual"
FIELD_SOURCE_FILE = "file"


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_file_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    current_confirmed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    is_void: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    candidate: Mapped["Candidate"] = relationship(back_populates="resumes")  # noqa: F821
    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
        foreign_keys="ResumeVersion.resume_id",
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RESUME_STATUS_PENDING_PARSE
    )
    source_file_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_structured: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    draft_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confirmed_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    standardized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="SET NULL"),
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    resume: Mapped[Resume] = relationship(
        back_populates="versions",
        foreign_keys=[resume_id],
    )


class AiResult(Base):
    """Normalized AI result versions (e.g. resume score M1/M2)."""

    __tablename__ = "ai_results"
    __table_args__ = (
        Index(
            "uq_ai_results_current",
            "application_id",
            "result_type",
            unique=True,
            postgresql_where=text("is_current = true AND application_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    result_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1.0"
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    calculated_total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_difference: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ScreeningDecision(Base):
    __tablename__ = "screening_decisions"
    __table_args__ = (
        Index(
            "uq_screening_decisions_idempotency",
            "application_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
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
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_pipeline_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_pipeline_status: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ai_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ApplicationStatusLog(Base):
    __tablename__ = "application_status_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
