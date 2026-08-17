import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ai_task import (  # noqa: F401
    AI_TASK_MAX_ATTEMPTS,
    AI_TASK_RETRY_COUNTDOWNS,
    AI_TASK_STATUS_CANCELLED,
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_OUTPUT_INVALID,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    AI_TASK_STATUSES,
    BUSINESS_TYPE_APPLICATION,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    BUSINESS_TYPE_JOB,
    BUSINESS_TYPE_RESUME_VERSION,
    BUSINESS_TYPES,
    ERROR_CATEGORY_NON_RETRYABLE,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
    TASK_TYPES,
    AITask,
    AITaskAttempt,
)
from app.models.candidate import (  # noqa: F401
    APPLICATION_STATUS_HIRED,
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
    APPLICATION_STATUS_TERMINATED,
    APPLICATION_STATUS_TRANSFERRED,
    APPLICATION_STATUSES,
    CLOSE_ACTION_REJECT,
    CLOSE_ACTION_TERMINATE,
    CLOSE_ACTION_TRANSFER,
    CLOSE_ACTIONS,
    IN_FLIGHT_STATUSES,
    INTERVIEW_TASK_ACTIVE,
    INTERVIEW_TASK_CANCELLED,
    INTERVIEW_TASK_NONE,
    INTERVIEW_TASK_PENDING_CANCEL,
    INTERVIEW_TASK_PENDING_REBUILD,
    INTERVIEW_TASK_REBUILT,
    INTERVIEW_TASK_STATES,
    TIMELINE_EVENT_VERSION_MIGRATED,
    Candidate,
    JobApplication,
)
from app.models.interview import (  # noqa: F401
    INTERVIEW_FORMAT_OFFLINE,
    INTERVIEW_FORMAT_ONLINE,
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_ENDED_ABNORMALLY,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
    MEETING_MODE_MANUAL,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_CANCELLED,
    SCHEDULE_STATUS_SUPERSEDED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
    InterviewSchedule,
)
from app.models.interview_transcript import (  # noqa: F401
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
    TranscriptCompletionMode,
    TranscriptSegmentSource,
    TranscriptSourceMethod,
    TranscriptSpeakerRole,
    TranscriptVersionStatus,
    TranscriptVersionType,
    list_transcript_reason_catalog,
)
from app.models.interview_ai import (  # noqa: F401
    InterviewQuestionItem,
    InterviewQuestionSet,
    InterviewQuestionVersion,
    InterviewRoundAnalysis,
    InterviewRoundAnalysisDimension,
    InterviewRoundAnalysisEvidence,
    InterviewRoundAnalysisVersion,
)
from app.models.invitation import (  # noqa: F401
    CHANNEL_CORPORATE_EMAIL,
    CHANNEL_OTHER,
    CHANNEL_TYPES,
    CHANNEL_WORK_EMAIL,
    COPY_TYPE_FULL_TEXT,
    COPY_TYPE_HTML_BODY,
    COPY_TYPE_SUBJECT,
    COPY_TYPES,
    INVITATION_AUDIENCE_CANDIDATE,
    INVITATION_AUDIENCE_INTERVIEWER,
    INVITATION_AUDIENCES,
    INVITATION_EVENT_CANCELLATION,
    INVITATION_EVENT_INITIAL,
    INVITATION_EVENT_RESCHEDULE,
    INVITATION_EVENTS,
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
    INVITATION_STATUSES,
    TEMPLATE_CANDIDATE_CANCELLATION,
    TEMPLATE_CANDIDATE_INITIAL,
    TEMPLATE_CANDIDATE_RESCHEDULE,
    TEMPLATE_CODES,
    TEMPLATE_INTERVIEWER_CANCELLATION,
    TEMPLATE_INTERVIEWER_INITIAL,
    TEMPLATE_INTERVIEWER_RESCHEDULE,
    TEMPLATE_VERSION,
    InterviewInvitationMessage,
    InterviewInvitationSendRecord,
    InterviewInvitationVersion,
)
from app.models.job import (  # noqa: F401
    JOB_STATUS_CLOSED,
    JOB_STATUS_DRAFT,
    JOB_STATUS_LABELS,
    JOB_STATUS_OPEN,
    JOB_STATUS_PAUSED,
    UPGRADE_INITIAL,
    UPGRADE_MAJOR,
    UPGRADE_MINOR,
    VERSION_STATUS_DRAFT,
    VERSION_STATUS_PUBLISHED,
    VERSION_STATUS_SUPERSEDED,
    Job,
    JobCodeSequence,
    JobVersion,
    empty_structured_jd,
)
from app.models.resume import (  # noqa: F401
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_HR_SCREEN,
    PIPELINE_PENDING_PARSE,
    PIPELINE_REJECTED,
    PIPELINE_STATUSES,
    PIPELINE_TALENT_POOL,
    RESUME_STATUS_CONFIRMED,
    RESUME_STATUS_PARSE_FAILED,
    RESUME_STATUS_PARSING,
    RESUME_STATUS_PENDING_PARSE,
    RESUME_STATUS_PENDING_REVIEW,
    RESUME_STATUS_VOID,
    RESUME_STATUSES,
    SCREENING_DECISIONS,
    SCREENING_ENTER_INTERVIEW,
    SCREENING_HOLD,
    SCREENING_REASON_CODES,
    SCREENING_REASON_REQUIRED_DECISIONS,
    SCREENING_REJECT,
    SCREENING_TALENT_POOL,
    VERSION_KIND_CONFIRMED,
    VERSION_KIND_FILE,
    AiResult,
    ApplicationStatusLog,
    Resume,
    ResumeVersion,
    ScreeningDecision,
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Uuid(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("role_id", Uuid(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
    UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Uuid(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
    Column(
        "permission_id",
        Uuid(as_uuid=True),
        ForeignKey("permissions.id"),
        primary_key=True,
    ),
    UniqueConstraint(
        "role_id", "permission_id", name="uq_role_permissions_role_permission"
    ),
)

SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "refresh_token",
        "access_token",
        "authorization",
        "cookie",
        "temporary_password",
        "api_key",
        "dify_api_key",
        "minio_secret_key",
        "standardized_text",
        "extracted_text",
        "resume_text",
        "jd_content",
        "meeting_password",
        "meeting_password_encrypted",
        "contact_phone",
        "subject",
        "body_html",
        "body_text",
        "subject_encrypted",
        "body_html_encrypted",
        "body_text_encrypted",
        "recipient_email",
        "email",
        "question",
        "purpose",
        "resume_evidence",
        "follow_up_prompts",
        "risk_flags",
        "overall_summary",
        "analysis",
        "strengths",
        "risks",
        "insufficient_information",
        "suggested_follow_ups",
        "quote",
        "sensitive_request",
        "sensitive_response",
        "raw_request",
        "raw_response",
        "result_payload",
        "transcript_text",
        "segment_text",
        "question_encrypted",
        "purpose_encrypted",
        "resume_evidence_encrypted",
        "follow_up_prompts_encrypted",
        "risk_flags_encrypted",
        "overall_summary_encrypted",
        "analysis_encrypted",
        "strengths_encrypted",
        "risks_encrypted",
        "insufficient_information_encrypted",
        "suggested_follow_ups_encrypted",
        "quote_encrypted",
        "sensitive_request_encrypted",
        "sensitive_response_encrypted",
    }
)


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _sanitize_audit_value(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_AUDIT_KEYS:
                raise ValueError(f"sensitive key not allowed in audit changes: {key}")
            sanitized[key] = _sanitize_audit_value(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_audit_value(item) for item in value]
    return value


def sanitize_audit_changes(changes: dict | None) -> dict | None:
    if changes is None:
        return None

    sanitized = _sanitize_audit_value(changes)
    assert isinstance(sanitized, dict)
    return sanitized


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(64), unique=True)
    username_normalized: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles,
        back_populates="users",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="actor",
        foreign_keys="AuditLog.actor_user_id",
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    users: Mapped[list[User]] = relationship(
        secondary=user_roles,
        back_populates="roles",
    )
    permissions: Mapped[list["Permission"]] = relationship(
        secondary=role_permissions,
        back_populates="roles",
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    roles: Mapped[list[Role]] = relationship(
        secondary=role_permissions,
        back_populates="permissions",
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_occurred_at_id", "occurred_at", "id"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32))
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    actor: Mapped[User | None] = relationship(
        back_populates="audit_logs",
        foreign_keys=[actor_user_id],
    )
