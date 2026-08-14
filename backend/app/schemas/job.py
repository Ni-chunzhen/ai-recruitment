from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

JobStatus = Literal["draft", "open", "paused", "closed"]
UpgradeType = Literal["initial", "major", "minor"]


class StructuredJd(BaseModel):
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class ScoreDimension(BaseModel):
    name: str = ""
    weight: float = Field(default=0, ge=0, le=100)
    description: str = ""
    anchors: list[str] = Field(default_factory=list)
    custom: bool | None = None


class JobVersionOut(BaseModel):
    id: UUID
    version_label: str | None
    major: int
    minor: int
    status: str
    upgrade_type: str | None
    change_summary: str | None
    raw_jd_text: str
    structured_jd: StructuredJd
    score_dimensions: list[ScoreDimension]
    job_snapshot: dict[str, Any] | None = None
    base_version_id: UUID | None
    published_at: datetime | None
    published_by: UUID | None
    created_at: datetime
    updated_at: datetime
    is_current: bool = False
    bound_candidates: int = 0


class JobVersionListItem(BaseModel):
    id: UUID
    version_label: str | None
    major: int
    minor: int
    status: str
    status_label: str
    upgrade_type: str | None
    upgrade_type_label: str | None
    change_summary: str | None
    published_at: datetime | None
    published_by: UUID | None
    created_at: datetime
    updated_at: datetime
    is_current: bool = False
    is_draft: bool = False
    bound_candidates: int = 0


class JobVersionListResponse(BaseModel):
    items: list[JobVersionListItem]
    total: int


class VersionDiffChange(BaseModel):
    field: str
    label: str
    before: Any
    after: Any


class VersionDiffResponse(BaseModel):
    from_version: JobVersionListItem
    to_version: JobVersionListItem
    changes: list[VersionDiffChange]
    has_changes: bool


class JobListItem(BaseModel):
    id: UUID
    code: str
    status: JobStatus
    status_label: str
    name: str
    department: str
    level: str | None
    headcount: int | None
    location: str
    owner_user_id: UUID | None
    owner_name: str
    urgency: str | None
    current_version_label: str | None = None
    updated_at: datetime
    created_at: datetime


class JobListResponse(BaseModel):
    items: list[JobListItem]
    total: int
    page: int
    page_size: int


class JobDetail(BaseModel):
    id: UUID
    code: str
    status: JobStatus
    status_label: str
    name: str
    department: str
    level: str | None
    headcount: int | None
    location: str
    owner_user_id: UUID | None
    owner_name: str
    urgency: str | None
    source_job_id: UUID | None
    current_version_id: UUID | None
    draft_version_id: UUID | None
    current_version: JobVersionOut | None = None
    draft_version: JobVersionOut | None = None
    closed_at: datetime | None
    close_reason: str | None
    pause_reason: str | None
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


class CreateJobRequest(BaseModel):
    name: str = ""
    department: str = ""
    level: str | None = None
    headcount: int | None = Field(default=None, ge=1)
    location: str = ""
    owner_user_id: UUID | None = None
    owner_name: str = ""
    urgency: str | None = None
    raw_jd_text: str = ""
    structured_jd: StructuredJd | None = None
    score_dimensions: list[ScoreDimension] | None = None


class SaveDraftRequest(BaseModel):
    name: str | None = None
    department: str | None = None
    level: str | None = None
    headcount: int | None = Field(default=None, ge=1)
    location: str | None = None
    owner_user_id: UUID | None = None
    owner_name: str | None = None
    urgency: str | None = None
    raw_jd_text: str | None = None
    structured_jd: StructuredJd | None = None
    score_dimensions: list[ScoreDimension] | None = None
    change_summary: str | None = None


class PublishJobRequest(BaseModel):
    reason: str | None = None
    force_major: bool = False
    upgrade_type: UpgradeType | None = None
    change_summary: str | None = None


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ValidationErrorItem(BaseModel):
    field: str
    message: str


class ValidationErrorBody(BaseModel):
    code: Literal["validation_error"] = "validation_error"
    errors: list[ValidationErrorItem]


class JobFilterParams(BaseModel):
    code: str | None = None
    name: str | None = None
    department: str | None = None
    owner: str | None = None
    status: JobStatus | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None


def structured_jd_to_dict(value: StructuredJd | dict | None) -> dict[str, Any]:
    if value is None:
        return {
            "responsibilities": [],
            "requirements": [],
            "must_have": [],
            "nice_to_have": [],
            "skills": [],
        }
    if isinstance(value, StructuredJd):
        return value.model_dump()
    return {
        "responsibilities": list(value.get("responsibilities") or []),
        "requirements": list(value.get("requirements") or []),
        "must_have": list(value.get("must_have") or []),
        "nice_to_have": list(value.get("nice_to_have") or []),
        "skills": list(value.get("skills") or []),
    }


def score_dimensions_to_list(
    value: list[ScoreDimension] | list[dict] | None,
) -> list[dict[str, Any]]:
    if not value:
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, ScoreDimension):
            result.append(item.model_dump(exclude_none=True))
        else:
            result.append(dict(item))
    return result
