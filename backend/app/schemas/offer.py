"""Schemas for Offer console delivery API (Task 4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OfferCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, max_length=128)


class OfferUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=500)
    body_html: str
    body_text: str
    lock_version: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class OfferReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_version: int
    idempotency_key: str | None = Field(default=None, max_length=128)


class OfferSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_version_id: UUID
    lock_version: int
    idempotency_key: str = Field(min_length=1, max_length=128)


class OfferRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_version: int
    idempotency_key: str = Field(min_length=1, max_length=128)


class OfferVoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_version: int
    void_reason_code: str = Field(min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)


class OfferSummaryOut(BaseModel):
    id: UUID
    application_id: UUID
    status: str
    hiring_decision_id: UUID
    recipient_email_masked: str | None
    recipient_name: str
    lock_version: int
    version_no: int | None
    content_hash: str | None
    frozen: bool | None
    created_at: datetime
    updated_at: datetime


class OfferDetailOut(BaseModel):
    id: UUID
    application_id: UUID
    status: str
    hiring_decision_id: UUID
    recipient_email_masked: str | None
    recipient_name: str
    lock_version: int
    version_id: UUID | None
    version_no: int | None
    content_hash: str | None
    frozen: bool | None
    subject: str
    body_html: str
    body_text: str
    template_code: str | None
    template_version: str | None
    created_at: datetime
    updated_at: datetime


class OfferListResponse(BaseModel):
    items: list[OfferSummaryOut]


class OfferSendOut(BaseModel):
    offer_id: UUID
    attempt_id: UUID
    status: str
    attempt_status: str
    attempt_no: int
    lock_version: int
    version_id: UUID
    provider: str


class OfferAttemptOut(BaseModel):
    id: UUID
    offer_id: UUID
    offer_version_id: UUID
    provider: str
    status: str
    attempt_no: int
    error_code: str | None
    error_message_safe: str | None
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime


class OfferAttemptListResponse(BaseModel):
    items: list[OfferAttemptOut]


class MarkStaleOfferAttemptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime


class MarkStaleOfferAttemptOut(BaseModel):
    offer_id: UUID
    attempt_id: UUID
    offer_status: str
    attempt_status: str
    error_code: str
    updated_at: datetime
    finished_at: datetime
