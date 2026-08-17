from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.invitation import (
    CHANNEL_TYPES,
    INVITATION_EVENTS,
)


class GenerateInvitationsRequest(BaseModel):
    event_type: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("event_type")
    @classmethod
    def _event_type_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in INVITATION_EVENTS:
            raise ValueError("invalid event_type")
        return value


class UpdateInvitationMessageRequest(BaseModel):
    version: int
    subject: str = Field(min_length=1)
    body_html: str = Field(min_length=1)
    body_text: str | None = None


class CopyAuditRequest(BaseModel):
    copy_type: Literal["SUBJECT", "HTML_BODY", "FULL_TEXT"]


class RecordSentRequest(BaseModel):
    sent_at: datetime
    message_version_id: UUID
    channel_type: str
    channel_note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("channel_type")
    @classmethod
    def _channel_type_ok(cls, value: str) -> str:
        if value not in CHANNEL_TYPES:
            raise ValueError("invalid channel_type")
        return value


class ConfirmInvitationRequest(BaseModel):
    schedule_version: int
    version: int
    send_summary: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=128)


class InvitationMessageSummaryOut(BaseModel):
    id: UUID
    interview_round_id: UUID
    schedule_id: UUID
    schedule_version: int
    event_type: str
    audience_type: str
    recipient_user_id: UUID | None = None
    recipient_key: str
    recipient_name: str
    recipient_email_masked: str | None = None
    status: str
    current_version_id: UUID | None = None
    current_version_no: int | None = None
    template_code: str | None = None
    version: int
    missing_fields: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InvitationMessageDetailOut(InvitationMessageSummaryOut):
    subject: str
    body_html: str
    body_text: str | None = None
    template_code: str
    template_version: str
    content_hash: str
    current_version_no: int


class InvitationStatusCountsOut(BaseModel):
    generated: int = 0
    ready: int = 0
    recorded_sent: int = 0
    voided: int = 0


class InvitationListResponse(BaseModel):
    items: list[InvitationMessageSummaryOut]
    counts: InvitationStatusCountsOut


class GenerateInvitationsResponse(BaseModel):
    items: list[InvitationMessageSummaryOut]


class RecordSentResponse(BaseModel):
    id: UUID
    message_id: UUID
    message_version_id: UUID
    sent_at: datetime
    channel_type: str
    channel_note: str | None = None
    recipient_email_masked: str | None = None
    status: str


class ConfirmInvitationResponse(BaseModel):
    round_id: UUID
    status: str
    schedule_version: int
    version: int
    confirmed_at: datetime | None = None
    confirmed_by_name: str | None = None
    confirmation_summary: str | None = None
