from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuditLogItem(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    result: str
    ip_address: str | None
    request_id: str
    changes: dict | None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int


class AuditLogQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    action: str | None = Field(default=None, max_length=64)
    resource_type: str | None = Field(default=None, max_length=64)
    actor_user_id: UUID | None = None
    result: str | None = Field(default=None, max_length=32)
