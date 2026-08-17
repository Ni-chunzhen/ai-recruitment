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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

TASK_TYPE_JD_PARSE = "JD_PARSE"
TASK_TYPE_SCORE_DIMENSION_RECOMMEND = "SCORE_DIMENSION_RECOMMEND"
TASK_TYPE_RESUME_PARSE = "RESUME_PARSE"
TASK_TYPE_RESUME_SCORE = "RESUME_SCORE"
TASK_TYPE_INTERVIEW_QUESTION_GENERATE = "INTERVIEW_QUESTION_GENERATE"
TASK_TYPE_INTERVIEW_ROUND_ANALYZE = "INTERVIEW_ROUND_ANALYZE"
TASK_TYPES = frozenset(
    {
        TASK_TYPE_JD_PARSE,
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        TASK_TYPE_RESUME_PARSE,
        TASK_TYPE_RESUME_SCORE,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    }
)

AI_TASK_STATUS_PENDING = "pending"
AI_TASK_STATUS_RUNNING = "running"
AI_TASK_STATUS_SUCCEEDED = "succeeded"
AI_TASK_STATUS_FAILED = "failed"
AI_TASK_STATUS_OUTPUT_INVALID = "output_invalid"
AI_TASK_STATUS_CANCELLED = "cancelled"
AI_TASK_STATUSES = frozenset(
    {
        AI_TASK_STATUS_PENDING,
        AI_TASK_STATUS_RUNNING,
        AI_TASK_STATUS_SUCCEEDED,
        AI_TASK_STATUS_FAILED,
        AI_TASK_STATUS_OUTPUT_INVALID,
        AI_TASK_STATUS_CANCELLED,
    }
)
SCORE_SNAPSHOT_SCHEMA_VERSION = "1.0"
SCORE_RESULT_SCHEMA_VERSION = "1.0"
SCORE_WORKFLOW_KEY = "resume_multidimensional_score"

ERROR_CATEGORY_RETRYABLE = "retryable"
ERROR_CATEGORY_NON_RETRYABLE = "non_retryable"

BUSINESS_TYPE_JOB = "job"
BUSINESS_TYPE_RESUME_VERSION = "resume_version"
BUSINESS_TYPE_APPLICATION = "application"
BUSINESS_TYPE_INTERVIEW_ROUND = "interview_round"
BUSINESS_TYPES = frozenset(
    {
        BUSINESS_TYPE_JOB,
        BUSINESS_TYPE_RESUME_VERSION,
        BUSINESS_TYPE_APPLICATION,
        BUSINESS_TYPE_INTERVIEW_ROUND,
    }
)

# 1 次初始执行 + 最多 2 次自动重试
AI_TASK_MAX_ATTEMPTS = 3
# attempt_no 失败后的等待秒数：第 1 次失败 → 10s，第 2 次失败 → 30s
AI_TASK_RETRY_COUNTDOWNS = {1: 10, 2: 30}


class AITask(Base):
    __tablename__ = "ai_tasks"
    __table_args__ = (
        Index(
            "uq_ai_tasks_idempotency",
            "created_by",
            "business_id",
            "task_type",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint(
            "task_type IN ("
            "'JD_PARSE', "
            "'SCORE_DIMENSION_RECOMMEND', "
            "'RESUME_PARSE', "
            "'RESUME_SCORE', "
            "'INTERVIEW_QUESTION_GENERATE', "
            "'INTERVIEW_ROUND_ANALYZE'"
            ")",
            name="ck_ai_tasks_task_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AI_TASK_STATUS_PENDING
    )
    business_type: Mapped[str] = mapped_column(String(64), nullable=False)
    business_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_cycle_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycle_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    raw_request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    attempts: Mapped[list["AITaskAttempt"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="AITaskAttempt.attempt_no",
    )


class AITaskAttempt(Base):
    __tablename__ = "ai_task_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "attempt_no", name="uq_ai_task_attempts_task_attempt"
        ),
        Index(
            "ix_ai_task_attempts_provider_run_id",
            "provider_run_id",
            postgresql_where=text("provider_run_id IS NOT NULL"),
        ),
        Index(
            "ix_ai_task_attempts_request_id",
            "request_id",
            postgresql_where=text("request_id IS NOT NULL"),
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
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_cycle_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycle_attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sensitive_request_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    sensitive_response_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    task: Mapped[AITask] = relationship(back_populates="attempts")
