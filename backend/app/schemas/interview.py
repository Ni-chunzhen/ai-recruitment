from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.interview import REASON_OTHER


class InterviewerAssignmentIn(BaseModel):
    interviewer_id: UUID
    is_primary: bool = False


class InterviewScheduleCreate(BaseModel):
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: str = Field(min_length=1, max_length=64)
    format: str
    meeting_mode: str | None = None
    meeting_provider: str | None = Field(default=None, max_length=64)
    meeting_url: str | None = Field(default=None, max_length=512)
    meeting_no: str | None = Field(default=None, max_length=64)
    meeting_password: str | None = Field(default=None, max_length=128)
    clear_meeting_password: bool = False
    location: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=128)
    contact_phone: str | None = Field(default=None, max_length=32)
    version: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("format")
    @classmethod
    def _format_ok(cls, value: str) -> str:
        if value not in {"ONLINE", "OFFLINE"}:
            raise ValueError("invalid format")
        return value


class InterviewRoundCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    sequence_no: int | None = Field(default=None, ge=1)
    format: str
    owner_id: UUID
    interviewers: list[InterviewerAssignmentIn] = Field(min_length=1)
    schedule: InterviewScheduleCreate | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("format")
    @classmethod
    def _format_ok(cls, value: str) -> str:
        if value not in {"ONLINE", "OFFLINE"}:
            raise ValueError("invalid format")
        return value

    @model_validator(mode="after")
    def _require_primary(self) -> "InterviewRoundCreate":
        if not any(item.is_primary for item in self.interviewers):
            if len(self.interviewers) == 1:
                self.interviewers[0].is_primary = True
            else:
                raise ValueError("at least one primary interviewer is required")
        ids = [item.interviewer_id for item in self.interviewers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate interviewer")
        return self


class InterviewRoundUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    format: str | None = None
    owner_id: UUID | None = None
    interviewers: list[InterviewerAssignmentIn] | None = Field(
        default=None, min_length=1
    )
    version: int

    @field_validator("format")
    @classmethod
    def _format_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in {"ONLINE", "OFFLINE"}:
            raise ValueError("invalid format")
        return value

    @model_validator(mode="after")
    def _require_primary(self) -> "InterviewRoundUpdate":
        if self.interviewers is None:
            return self
        if not any(item.is_primary for item in self.interviewers):
            if len(self.interviewers) == 1:
                self.interviewers[0].is_primary = True
            else:
                raise ValueError("at least one primary interviewer is required")
        ids = [item.interviewer_id for item in self.interviewers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate interviewer")
        return self


class InterviewRescheduleRequest(InterviewScheduleCreate):
    reschedule_reason: str = Field(min_length=1, max_length=2000)
    version: int
    override_interviewer_conflict: bool = False
    override_reason: str | None = Field(default=None, max_length=2000)


class InterviewCancelRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    version: int
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _other_needs_description(self) -> "InterviewCancelRequest":
        if self.reason_code == REASON_OTHER and not (self.description or "").strip():
            raise ValueError("description is required when reason_code is OTHER")
        return self


class InterviewAbnormalEndRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    version: int
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _other_needs_description(self) -> "InterviewAbnormalEndRequest":
        if self.reason_code == REASON_OTHER and not (self.description or "").strip():
            raise ValueError("description is required when reason_code is OTHER")
        return self


class InterviewRoundActionRequest(BaseModel):
    version: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class InterviewRoundReorderRequest(BaseModel):
    round_ids: list[UUID] = Field(min_length=1)


class InterviewConflictCheckRequest(BaseModel):
    application_id: UUID
    interviewer_ids: list[UUID] = Field(min_length=1)
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: str = Field(min_length=1, max_length=64)
    exclude_round_id: UUID | None = None
    override_interviewer_conflict: bool = False
    override_reason: str | None = Field(default=None, max_length=2000)


class InterviewerOut(BaseModel):
    interviewer_id: UUID
    display_name: str
    is_primary: bool


class InterviewScheduleSummaryOut(BaseModel):
    id: UUID
    schedule_version: int
    status: str
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: str
    format: str
    meeting_mode: str | None = None
    meeting_provider: str | None = None
    meeting_url: str | None = None
    meeting_no: str | None = None
    has_meeting_password: bool = False
    location: str | None = None
    contact_name: str | None = None
    contact_phone_masked: str | None = None
    reschedule_reason: str | None = None
    created_at: datetime


class InterviewRoundListItemOut(BaseModel):
    id: UUID
    application_id: UUID
    name: str
    sequence_no: int
    status: str
    format: str
    owner_id: UUID
    owner_name: str
    version: int
    allowed_actions: list[str]


class InterviewRoundOut(BaseModel):
    id: UUID
    application_id: UUID
    job_version_id: UUID
    name: str
    sequence_no: int
    status: str
    format: str
    owner_id: UUID
    owner_name: str
    interviewers: list[InterviewerOut]
    current_schedule: InterviewScheduleSummaryOut | None = None
    schedule_history: list[InterviewScheduleSummaryOut] = Field(default_factory=list)
    version: int
    allowed_actions: list[str] = Field(default_factory=list)
    cancellation_reason_code: str | None = None
    cancellation_description: str | None = None
    abnormal_reason_code: str | None = None
    abnormal_description: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancelled_at: datetime | None = None
    invitation_confirmed_at: datetime | None = None
    invitation_confirmed_by: UUID | None = None
    invitation_confirmed_by_name: str | None = None
    invitation_confirmed_schedule_version: int | None = None
    invitation_confirmation_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class InterviewRoundActionOut(InterviewRoundOut):
    pass


class InterviewTimelineOut(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str
    job_id: UUID
    job_name: str | None = None
    job_version_id: UUID
    job_version_label: str | None = None
    pipeline_status: str
    application_status: str
    completed_round_count: int
    total_round_count: int
    rounds: list[InterviewRoundOut]


class InterviewConflictItemOut(BaseModel):
    interviewer_id: UUID | None = None
    interviewer_name: str | None = None
    round_id: UUID
    round_name: str
    start_at_utc: datetime
    end_at_utc: datetime


class InterviewConflictOut(BaseModel):
    has_candidate_conflict: bool
    has_interviewer_conflict: bool
    candidate_conflicts: list[InterviewConflictItemOut] = Field(default_factory=list)
    interviewer_conflicts: list[InterviewConflictItemOut] = Field(default_factory=list)


class InterviewReasonCodeItem(BaseModel):
    code: str
    label: str
    category: str
    requires_description: bool


class InterviewReasonCodeListResponse(BaseModel):
    items: list[InterviewReasonCodeItem]


class InterviewStaffItemOut(BaseModel):
    id: UUID
    display_name: str
    username: str


class InterviewStaffListResponse(BaseModel):
    items: list[InterviewStaffItemOut]
