"""Admin integration configuration schemas (extra=forbid; secrets write-only)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SecretFieldStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    enabled: bool
    status: str


class NonSecretFieldStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    configured: bool
    enabled: bool
    status: str


class MailBlockOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_provider: Literal["console"]
    queue_name: str
    smtp_enabled: Literal[False]
    note: str


class DifyUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    jd_parse_api_key: str | None = Field(default=None, max_length=512)
    score_dimension_api_key: str | None = Field(default=None, max_length=512)
    jd_parse_workflow_id: str | None = Field(default=None, max_length=128)
    score_dimension_workflow_id: str | None = Field(default=None, max_length=128)
    resume_parse_api_key: str | None = Field(default=None, max_length=512)
    resume_score_api_key: str | None = Field(default=None, max_length=512)
    resume_parse_workflow_id: str | None = Field(default=None, max_length=128)
    resume_score_workflow_id: str | None = Field(default=None, max_length=128)
    interview_question_generate_api_key: str | None = Field(
        default=None, max_length=512
    )
    interview_question_generate_workflow_id: str | None = Field(
        default=None, max_length=128
    )
    ai_provider: Literal["mock", "dify"] | None = None


class MinioUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = Field(default=None, max_length=255)
    access_key: str | None = Field(default=None, max_length=255)
    secret_key: str | None = Field(default=None, max_length=255)
    bucket: str | None = Field(default=None, max_length=128)
    secure: str | bool | None = None
    presign_seconds: str | int | None = None


class ConnectivityTestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    error_code: str | None
    latency_ms: int


class IntegrationsSummaryOut(BaseModel):
    """Summary is intentionally flexible for nested field maps; validated loosely."""

    model_config = ConfigDict(extra="allow")

    dify: dict[str, Any]
    minio: dict[str, Any]
    mail: MailBlockOut
    restart_required: bool
    message_key: str
