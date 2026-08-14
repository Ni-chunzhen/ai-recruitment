from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ApplicationStatus = Literal[
    "in_progress",
    "rejected",
    "transferred",
    "terminated",
    "hired",
]
CloseAction = Literal["reject", "transfer", "terminate"]
InterviewTaskState = Literal[
    "none",
    "active",
    "pending_cancel",
    "cancelled",
    "pending_rebuild",
    "rebuilt",
]


class CreateCandidateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    interview_started: bool = False


class ResolveCloseRequest(BaseModel):
    action: CloseAction
    reason: str = Field(min_length=1, max_length=2000)
    target_job_id: UUID | None = None


class MigrateVersionRequest(BaseModel):
    to_version_id: UUID
    reason: str | None = Field(default=None, max_length=2000)


class TimelineEventOut(BaseModel):
    type: str
    at: str
    actor_id: str | None = None
    from_version_id: str | None = None
    to_version_id: str | None = None
    reason: str | None = None


class JobApplicationOut(BaseModel):
    id: UUID
    candidate_id: UUID
    candidate_name: str
    candidate_phone: str | None = None
    candidate_email: str | None = None
    job_id: UUID
    job_version_id: UUID
    status: ApplicationStatus
    pipeline_status: str = "pending_hr_screen"
    resume_version_id: UUID | None = None
    lock_version: int = 1
    interview_started: bool
    interview_task_state: InterviewTaskState
    close_action: str | None = None
    close_reason: str | None = None
    transferred_to_job_id: UUID | None = None
    previous_version_id: UUID | None = None
    migration_reason: str | None = None
    migrated_at: datetime | None = None
    migrated_by: UUID | None = None
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobApplicationListResponse(BaseModel):
    items: list[JobApplicationOut]
    total: int


class ClosePreviewItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    candidate_name: str
    status: ApplicationStatus
    interview_started: bool
    job_version_id: UUID


class ClosePreviewResponse(BaseModel):
    can_close: bool
    in_flight_count: int
    items: list[ClosePreviewItem]
