"""Typed contracts for interview transcript preview, import and proofreading."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.interview_transcript import (
    TRANSCRIPT_REASON_OTHER,
    TranscriptSourceMethod,
    TranscriptSpeakerRole,
)


_SOURCE_METHODS = {item.value for item in TranscriptSourceMethod}
_SPEAKER_ROLES = {item.value for item in TranscriptSpeakerRole}


class TranscriptPreviewSegmentOut(BaseModel):
    segment_no: int
    speaker_key: str
    speaker_name: str
    speaker_role: str
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    text: str
    matched_rule: str


class TranscriptPreviewOut(BaseModel):
    encoding: str
    sha256: str
    char_count: int
    segment_count: int
    matched_rules: list[str]
    source_method: str
    filename: str | None = None
    size: int
    mime: str | None = None
    segments: list[TranscriptPreviewSegmentOut]


class TranscriptImportSegmentIn(BaseModel):
    speaker_key: str = Field(min_length=1, max_length=64)
    speaker_name: str = Field(min_length=1, max_length=128)
    speaker_role: str
    text: str = Field(min_length=0, max_length=50_000)
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    is_unclear: bool = False
    is_included_in_analysis: bool = True
    source_segment_refs: list[int] = Field(default_factory=list)

    @field_validator("speaker_role")
    @classmethod
    def _role_ok(cls, value: str) -> str:
        if value not in _SPEAKER_ROLES:
            raise ValueError("invalid speaker_role")
        return value

    @model_validator(mode="after")
    def _time_range_ok(self) -> "TranscriptImportSegmentIn":
        start = self.start_time_ms
        end = self.end_time_ms
        if start is None and end is None:
            return self
        if start is None or end is None:
            raise ValueError("start_time_ms and end_time_ms must both be set or both null")
        if start < 0 or start >= end:
            raise ValueError("invalid time range")
        return self


class TranscriptImportRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_method: str | None = None
    raw_text: str | None = None
    filename: str | None = Field(default=None, max_length=255)
    source_sha256: str = Field(min_length=64, max_length=64)
    segments: list[TranscriptImportSegmentIn] = Field(min_length=1)

    @field_validator("source_method")
    @classmethod
    def _method_ok(cls, value: str | None) -> str | None:
        if value is not None and value not in _SOURCE_METHODS:
            raise ValueError("invalid source_method")
        return value

    @model_validator(mode="after")
    def _require_content(self) -> "TranscriptImportRequest":
        if not (self.raw_text or "").strip() and self.filename is None:
            raise ValueError("raw_text or filename content is required")
        return self


class TranscriptSegmentOut(BaseModel):
    id: UUID
    segment_no: int
    speaker_key: str
    speaker_name: str
    speaker_role: str
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    text: str
    source_type: str
    source_segment_refs: list[int] = Field(default_factory=list)
    is_included_in_analysis: bool
    is_unclear: bool


class TranscriptSummaryOut(BaseModel):
    id: UUID
    interview_round_id: UUID
    original_version_id: UUID | None = None
    current_draft_version_id: UUID | None = None
    current_confirmed_version_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class TranscriptVersionSummaryOut(BaseModel):
    id: UUID
    transcript_id: UUID
    version_type: str
    version_no: int
    version_label: str
    status: str
    source_method: str
    source_filename: str | None = None
    source_size: int | None = None
    source_mime: str | None = None
    source_encoding: str | None = None
    based_on_version_id: UUID | None = None
    segment_count: int
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class TranscriptListOut(BaseModel):
    transcript: TranscriptSummaryOut | None = None
    versions: list[TranscriptVersionSummaryOut] = Field(default_factory=list)


class TranscriptVersionDetailOut(BaseModel):
    id: UUID
    transcript_id: UUID
    interview_round_id: UUID
    version_type: str
    version_no: int
    version_label: str
    status: str
    source_method: str
    source_filename: str | None = None
    source_size: int | None = None
    source_mime: str | None = None
    source_encoding: str | None = None
    source_sha256: str
    based_on_version_id: UUID | None = None
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    segments: list[TranscriptSegmentOut]
    raw_text: str


class DraftCreateRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class DraftSegmentIn(BaseModel):
    speaker_key: str = Field(min_length=1, max_length=64)
    speaker_name: str = Field(min_length=1, max_length=128)
    speaker_role: str
    text: str = Field(min_length=0, max_length=50_000)
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    is_unclear: bool = False
    is_included_in_analysis: bool = True
    source_segment_refs: list[int] = Field(default_factory=list)

    @field_validator("speaker_role")
    @classmethod
    def _role_ok(cls, value: str) -> str:
        if value not in _SPEAKER_ROLES:
            raise ValueError("invalid speaker_role")
        return value

    @model_validator(mode="after")
    def _time_range_ok(self) -> "DraftSegmentIn":
        start = self.start_time_ms
        end = self.end_time_ms
        if start is None and end is None:
            return self
        if start is None or end is None:
            raise ValueError("start_time_ms and end_time_ms must both be set or both null")
        if start < 0 or start >= end:
            raise ValueError("invalid time range")
        return self


class DraftSaveRequest(BaseModel):
    draft_version_id: UUID
    version: int
    idempotency_key: str = Field(min_length=1, max_length=128)
    segments: list[DraftSegmentIn] = Field(min_length=1)


class ChangeCountsOut(BaseModel):
    speaker_changes: int = 0
    text_corrections: int = 0
    merge_split_count: int = 0
    deleted_count: int = 0
    manual_addition_count: int = 0
    excluded_from_analysis_count: int = 0
    reorder_count: int = 0


class DraftSaveResponse(BaseModel):
    version: TranscriptVersionDetailOut
    change_counts: ChangeCountsOut


class ConfirmRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    version: int


class CompleteWithoutTranscriptRequest(BaseModel):
    reason_code: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    version: int
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _other_needs_description(self) -> "CompleteWithoutTranscriptRequest":
        if self.reason_code == TRANSCRIPT_REASON_OTHER and not (
            self.description or ""
        ).strip():
            raise ValueError("description is required when reason_code is OTHER")
        return self


class CompleteWithoutTranscriptOut(BaseModel):
    round_id: UUID
    status: str
    version: int
    transcript_completion_mode: str
    transcript_completion_reason_code: str
    transcript_completion_reason_description: str | None = None
    transcript_completed_by: UUID
    transcript_completed_at: datetime


class TranscriptReasonCodeItem(BaseModel):
    code: str
    label: str
    requires_description: bool


class TranscriptReasonCodeListResponse(BaseModel):
    items: list[TranscriptReasonCodeItem]
