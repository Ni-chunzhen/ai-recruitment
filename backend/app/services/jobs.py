from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.job import (
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
    JobVersion,
    empty_structured_jd,
)
from app.repositories.candidates import count_applications_by_version_ids
from app.repositories.jobs import (
    JobNotFoundError,
    allocate_job_code,
    create_draft_version_from_base,
    create_job_with_draft,
    delete_job,
    get_job_by_id,
    get_version_by_id,
    list_jobs,
)
from app.schemas.job import (
    CreateJobRequest,
    JobDetail,
    JobListItem,
    JobListResponse,
    JobVersionListItem,
    JobVersionListResponse,
    JobVersionOut,
    PublishJobRequest,
    SaveDraftRequest,
    ScoreDimension,
    StructuredJd,
    VersionDiffChange,
    VersionDiffResponse,
    score_dimensions_to_list,
    structured_jd_to_dict,
)
from app.services.audit import RequestContext, record_audit
from app.services.candidates import assert_job_can_close


class JobValidationError(Exception):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("validation_error")


class JobStateError(Exception):
    pass


def format_version_label(major: int, minor: int) -> str:
    return f"V{major}.{minor}"


def _non_empty(value: str | None) -> bool:
    return bool(value and value.strip())


def _jd_list(structured_jd: dict | None, key: str) -> list:
    if not structured_jd:
        return []
    value = structured_jd.get(key) or []
    return [item for item in value if _non_empty(str(item))]


def normalize_score_dimensions(dimensions: list | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in dimensions or []:
        if hasattr(item, "model_dump"):
            data = item.model_dump(exclude_none=True)
        else:
            data = dict(item)
        normalized.append(
            {
                "name": str(data.get("name") or "").strip(),
                "weight": float(data.get("weight") or 0),
                "description": str(data.get("description") or ""),
                "anchors": list(data.get("anchors") or []),
                **(
                    {"custom": bool(data["custom"])}
                    if "custom" in data and data["custom"] is not None
                    else {}
                ),
            }
        )
    return normalized


def score_dimensions_equal(left: list | None, right: list | None) -> bool:
    return normalize_score_dimensions(left) == normalize_score_dimensions(right)


def validate_publish_payload(
    *,
    name: str,
    department: str,
    owner_name: str,
    headcount: int | None,
    structured_jd: dict | None,
    score_dimensions: list | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not _non_empty(name):
        errors.append({"field": "name", "message": "岗位名称不能为空"})
    if not _non_empty(department):
        errors.append({"field": "department", "message": "所属部门不能为空"})
    if not _non_empty(owner_name):
        errors.append({"field": "owner_name", "message": "负责人不能为空"})
    if headcount is None or headcount < 1:
        errors.append({"field": "headcount", "message": "招聘人数必须大于等于 1"})
    if not _jd_list(structured_jd, "responsibilities"):
        errors.append(
            {
                "field": "structured_jd.responsibilities",
                "message": "岗位职责不能为空",
            }
        )
    if not _jd_list(structured_jd, "requirements"):
        errors.append(
            {
                "field": "structured_jd.requirements",
                "message": "任职要求不能为空",
            }
        )

    dims = normalize_score_dimensions(score_dimensions)
    if not dims:
        errors.append({"field": "score_dimensions", "message": "至少需要 1 个评分维度"})
    else:
        weight_sum = sum(item["weight"] for item in dims)
        if abs(weight_sum - 100.0) > 0.01:
            errors.append(
                {
                    "field": "score_dimensions",
                    "message": f"评分维度权重合计须为 100%，当前为 {weight_sum:g}%",
                }
            )
        for index, item in enumerate(dims):
            if not item["name"]:
                errors.append(
                    {
                        "field": f"score_dimensions[{index}].name",
                        "message": "评分维度名称不能为空",
                    }
                )
    return errors


def decide_version_bump(
    *,
    current_major: int,
    current_minor: int,
    current_score_dimensions: list | None,
    draft_score_dimensions: list | None,
    current_structured_jd: dict | None = None,
    draft_structured_jd: dict | None = None,
    current_raw_jd_text: str | None = None,
    draft_raw_jd_text: str | None = None,
    force_major: bool = False,
    requested_upgrade_type: str | None = None,
    is_initial: bool = False,
) -> tuple[int, int, str, str]:
    if is_initial:
        return 1, 0, UPGRADE_INITIAL, format_version_label(1, 0)

    dims_changed = not score_dimensions_equal(
        current_score_dimensions, draft_score_dimensions
    )
    jd_changed = structured_jd_to_dict(
        current_structured_jd
    ) != structured_jd_to_dict(draft_structured_jd)
    raw_changed = (current_raw_jd_text or "").strip() != (
        draft_raw_jd_text or ""
    ).strip()
    evaluation_changed = dims_changed or jd_changed or raw_changed

    use_major = (
        requested_upgrade_type == UPGRADE_MAJOR
        or force_major
        or evaluation_changed
    )
    if use_major:
        major = current_major + 1
        minor = 0
        upgrade_type = UPGRADE_MAJOR
    else:
        major = current_major
        minor = current_minor + 1
        upgrade_type = UPGRADE_MINOR

    return major, minor, upgrade_type, format_version_label(major, minor)


def _job_snapshot(job: Job) -> dict[str, Any]:
    return {
        "name": job.name,
        "department": job.department,
        "level": job.level,
        "headcount": job.headcount,
        "location": job.location,
        "owner_name": job.owner_name,
        "urgency": job.urgency,
    }


VERSION_STATUS_LABELS = {
    VERSION_STATUS_DRAFT: "未发布草稿",
    VERSION_STATUS_PUBLISHED: "当前生效",
    VERSION_STATUS_SUPERSEDED: "历史版本",
}

UPGRADE_TYPE_LABELS = {
    UPGRADE_INITIAL: "首次发布",
    UPGRADE_MAJOR: "主版本",
    UPGRADE_MINOR: "修订版本",
}

FIELD_LABELS = {
    "name": "岗位名称",
    "department": "所属部门",
    "level": "职级",
    "headcount": "招聘人数",
    "location": "工作地点",
    "owner_name": "负责人",
    "urgency": "紧急程度",
    "raw_jd_text": "原始 JD",
    "structured_jd.responsibilities": "岗位职责",
    "structured_jd.requirements": "任职要求",
    "structured_jd.must_have": "必备要求",
    "structured_jd.nice_to_have": "加分项",
    "structured_jd.skills": "技能关键词",
    "score_dimensions": "评分维度",
    "change_summary": "变更说明",
}


def _to_version_out(
    version: JobVersion | None,
    *,
    current_version_id: UUID | None = None,
    bound_candidates: int = 0,
) -> JobVersionOut | None:
    if version is None:
        return None
    structured = structured_jd_to_dict(version.structured_jd)
    dimensions = [
        ScoreDimension.model_validate(item)
        for item in (version.score_dimensions or [])
    ]
    return JobVersionOut(
        id=version.id,
        version_label=version.version_label,
        major=version.major,
        minor=version.minor,
        status=version.status,
        upgrade_type=version.upgrade_type,
        change_summary=version.change_summary,
        raw_jd_text=version.raw_jd_text or "",
        structured_jd=StructuredJd.model_validate(structured),
        score_dimensions=dimensions,
        job_snapshot=version.job_snapshot,
        base_version_id=version.base_version_id,
        published_at=version.published_at,
        published_by=version.published_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
        is_current=bool(
            current_version_id and version.id == current_version_id
        ),
        bound_candidates=bound_candidates,
    )


def _to_version_list_item(
    version: JobVersion,
    *,
    current_version_id: UUID | None,
    bound_candidates: int = 0,
) -> JobVersionListItem:
    is_current = bool(current_version_id and version.id == current_version_id)
    if version.status == VERSION_STATUS_DRAFT:
        status_label = VERSION_STATUS_LABELS[VERSION_STATUS_DRAFT]
    elif is_current:
        status_label = VERSION_STATUS_LABELS[VERSION_STATUS_PUBLISHED]
    else:
        status_label = VERSION_STATUS_LABELS[VERSION_STATUS_SUPERSEDED]
    return JobVersionListItem(
        id=version.id,
        version_label=version.version_label,
        major=version.major,
        minor=version.minor,
        status=version.status,
        status_label=status_label,
        upgrade_type=version.upgrade_type,
        upgrade_type_label=UPGRADE_TYPE_LABELS.get(version.upgrade_type or ""),
        change_summary=version.change_summary,
        published_at=version.published_at,
        published_by=version.published_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
        is_current=is_current,
        is_draft=version.status == VERSION_STATUS_DRAFT,
        bound_candidates=bound_candidates,
    )


def to_job_list_item(job: Job) -> JobListItem:
    current = get_version_by_id(job, job.current_version_id)
    return JobListItem(
        id=job.id,
        code=job.code,
        status=job.status,  # type: ignore[arg-type]
        status_label=JOB_STATUS_LABELS.get(job.status, job.status),
        name=job.name,
        department=job.department,
        level=job.level,
        headcount=job.headcount,
        location=job.location,
        owner_user_id=job.owner_user_id,
        owner_name=job.owner_name,
        urgency=job.urgency,
        current_version_label=current.version_label if current else None,
        updated_at=job.updated_at,
        created_at=job.created_at,
    )


async def _bound_counts_for_job(
    session: AsyncSession,
    job: Job,
) -> dict[UUID, int]:
    version_ids = [version.id for version in job.versions]
    return await count_applications_by_version_ids(session, version_ids=version_ids)


async def to_job_detail(session: AsyncSession, job: Job) -> JobDetail:
    counts = await _bound_counts_for_job(session, job)
    current = get_version_by_id(job, job.current_version_id)
    draft = get_version_by_id(job, job.draft_version_id)
    return JobDetail(
        id=job.id,
        code=job.code,
        status=job.status,  # type: ignore[arg-type]
        status_label=JOB_STATUS_LABELS.get(job.status, job.status),
        name=job.name,
        department=job.department,
        level=job.level,
        headcount=job.headcount,
        location=job.location,
        owner_user_id=job.owner_user_id,
        owner_name=job.owner_name,
        urgency=job.urgency,
        source_job_id=job.source_job_id,
        current_version_id=job.current_version_id,
        draft_version_id=job.draft_version_id,
        current_version=_to_version_out(
            current,
            current_version_id=job.current_version_id,
            bound_candidates=counts.get(current.id, 0) if current else 0,
        ),
        draft_version=_to_version_out(
            draft,
            current_version_id=job.current_version_id,
            bound_candidates=counts.get(draft.id, 0) if draft else 0,
        ),
        closed_at=job.closed_at,
        close_reason=job.close_reason,
        pause_reason=job.pause_reason,
        created_by=job.created_by,
        updated_by=job.updated_by,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _apply_basic_fields(job: Job, payload: SaveDraftRequest | CreateJobRequest) -> None:
    data = payload.model_dump(exclude_unset=True)
    for field in (
        "name",
        "department",
        "level",
        "headcount",
        "location",
        "owner_user_id",
        "owner_name",
        "urgency",
    ):
        if field in data:
            setattr(job, field, data[field])


def _apply_version_content(
    version: JobVersion,
    *,
    raw_jd_text: str | None = None,
    structured_jd: StructuredJd | dict | None = None,
    score_dimensions: list | None = None,
    change_summary: str | None = None,
    update_change_summary: bool = False,
) -> None:
    if raw_jd_text is not None:
        version.raw_jd_text = raw_jd_text
    if structured_jd is not None:
        version.structured_jd = structured_jd_to_dict(structured_jd)
    if score_dimensions is not None:
        version.score_dimensions = score_dimensions_to_list(score_dimensions)
    if update_change_summary:
        version.change_summary = change_summary
    version.updated_at = datetime.now(UTC)


async def create_job(
    session: AsyncSession,
    *,
    payload: CreateJobRequest,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    code = await allocate_job_code(session)
    job = await create_job_with_draft(
        session,
        code=code,
        actor_id=actor.id,
        name=payload.name,
        department=payload.department,
        level=payload.level,
        headcount=payload.headcount,
        location=payload.location,
        owner_user_id=payload.owner_user_id,
        owner_name=payload.owner_name,
        urgency=payload.urgency,
        raw_jd_text=payload.raw_jd_text,
        structured_jd=structured_jd_to_dict(payload.structured_jd),
        score_dimensions=score_dimensions_to_list(payload.score_dimensions),
    )
    await record_audit(
        session,
        action="job.create",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"code": job.code, "status": job.status},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def get_job_detail(session: AsyncSession, job_id: UUID) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    return await to_job_detail(session, job)


async def list_job_details(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    code: str | None = None,
    name: str | None = None,
    keyword: str | None = None,
    department: str | None = None,
    owner: str | None = None,
    status: str | None = None,
    updated_from: datetime | None = None,
    updated_to: datetime | None = None,
) -> JobListResponse:
    jobs, total = await list_jobs(
        session,
        page=page,
        page_size=page_size,
        code=code,
        name=name,
        keyword=keyword,
        department=department,
        owner=owner,
        status=status,
        updated_from=updated_from,
        updated_to=updated_to,
    )
    return JobListResponse(
        items=[to_job_list_item(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


async def save_job_draft(
    session: AsyncSession,
    *,
    job_id: UUID,
    payload: SaveDraftRequest,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status == JOB_STATUS_CLOSED:
        raise JobStateError("closed job cannot be edited")

    draft = get_version_by_id(job, job.draft_version_id)
    if draft is None:
        if job.status == JOB_STATUS_DRAFT:
            raise JobStateError("draft version missing")
        current = get_version_by_id(job, job.current_version_id)
        if current is None:
            raise JobStateError("current version missing")
        draft = await create_draft_version_from_base(
            session, job=job, base=current, actor_id=actor.id
        )
        await session.refresh(job, attribute_names=["versions"])
        draft = get_version_by_id(job, job.draft_version_id)
        assert draft is not None

    _apply_basic_fields(job, payload)
    data = payload.model_dump(exclude_unset=True)
    _apply_version_content(
        draft,
        raw_jd_text=data.get("raw_jd_text"),
        structured_jd=payload.structured_jd if "structured_jd" in data else None,
        score_dimensions=(
            payload.score_dimensions if "score_dimensions" in data else None
        ),
        change_summary=data.get("change_summary"),
        update_change_summary="change_summary" in data,
    )
    job.updated_by = actor.id
    job.updated_at = datetime.now(UTC)
    await session.flush()

    await record_audit(
        session,
        action="job.save_draft",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"draft_version_id": str(job.draft_version_id)},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def publish_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    payload: PublishJobRequest,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status == JOB_STATUS_CLOSED:
        raise JobStateError("closed job cannot be published")

    draft = get_version_by_id(job, job.draft_version_id)
    if draft is None or draft.status != VERSION_STATUS_DRAFT:
        raise JobStateError("no draft version to publish")

    errors = validate_publish_payload(
        name=job.name,
        department=job.department,
        owner_name=job.owner_name,
        headcount=job.headcount,
        structured_jd=draft.structured_jd,
        score_dimensions=draft.score_dimensions,
    )
    if errors:
        raise JobValidationError(errors)

    now = datetime.now(UTC)
    current = get_version_by_id(job, job.current_version_id)
    is_initial = job.status == JOB_STATUS_DRAFT or current is None

    if is_initial:
        major, minor, upgrade_type, label = decide_version_bump(
            current_major=0,
            current_minor=0,
            current_score_dimensions=None,
            draft_score_dimensions=draft.score_dimensions,
            current_structured_jd=None,
            draft_structured_jd=draft.structured_jd,
            current_raw_jd_text=None,
            draft_raw_jd_text=draft.raw_jd_text,
            force_major=payload.force_major,
            requested_upgrade_type=payload.upgrade_type,
            is_initial=True,
        )
        job.status = JOB_STATUS_OPEN
    else:
        assert current is not None
        major, minor, upgrade_type, label = decide_version_bump(
            current_major=current.major,
            current_minor=current.minor,
            current_score_dimensions=current.score_dimensions,
            draft_score_dimensions=draft.score_dimensions,
            current_structured_jd=current.structured_jd,
            draft_structured_jd=draft.structured_jd,
            current_raw_jd_text=current.raw_jd_text,
            draft_raw_jd_text=draft.raw_jd_text,
            force_major=payload.force_major,
            requested_upgrade_type=payload.upgrade_type,
            is_initial=False,
        )
        current.status = VERSION_STATUS_SUPERSEDED
        # Publishing a new version must not auto-resume a paused job.

    draft.major = major
    draft.minor = minor
    draft.version_label = label
    draft.upgrade_type = upgrade_type
    draft.status = VERSION_STATUS_PUBLISHED
    draft.published_at = now
    draft.published_by = actor.id
    draft.job_snapshot = _job_snapshot(job)
    if payload.change_summary is not None:
        draft.change_summary = payload.change_summary
    elif payload.reason is not None:
        draft.change_summary = payload.reason

    job.current_version_id = draft.id
    job.draft_version_id = None
    job.updated_by = actor.id
    job.updated_at = now
    await session.flush()

    await record_audit(
        session,
        action="job.publish",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={
            "version_label": label,
            "upgrade_type": upgrade_type,
            "status": job.status,
        },
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def pause_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    reason: str,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status != JOB_STATUS_OPEN:
        raise JobStateError("only open jobs can be paused")

    job.status = JOB_STATUS_PAUSED
    job.pause_reason = reason
    job.updated_by = actor.id
    job.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        action="job.pause",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"reason": reason},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def resume_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    reason: str,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status != JOB_STATUS_PAUSED:
        raise JobStateError("only paused jobs can be resumed")

    job.status = JOB_STATUS_OPEN
    job.pause_reason = None
    job.updated_by = actor.id
    job.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        action="job.resume",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"reason": reason},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def close_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    reason: str,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status not in {JOB_STATUS_OPEN, JOB_STATUS_PAUSED}:
        raise JobStateError("only open or paused jobs can be closed")
    await assert_job_can_close(session, job_id=job_id)

    now = datetime.now(UTC)
    job.status = JOB_STATUS_CLOSED
    job.close_reason = reason
    job.closed_at = now
    job.pause_reason = None
    job.updated_by = actor.id
    job.updated_at = now
    await session.flush()
    await record_audit(
        session,
        action="job.close",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"reason": reason},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def copy_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    source = await get_job_by_id(session, job_id)
    if source is None:
        raise JobNotFoundError("job not found")

    source_version = get_version_by_id(source, source.current_version_id)
    if source_version is None:
        source_version = get_version_by_id(source, source.draft_version_id)
    if source_version is None:
        raise JobStateError("source job has no version to copy")

    code = await allocate_job_code(session)
    job = await create_job_with_draft(
        session,
        code=code,
        actor_id=actor.id,
        name=source.name,
        department=source.department,
        level=source.level,
        headcount=source.headcount,
        location=source.location,
        owner_user_id=source.owner_user_id,
        owner_name=source.owner_name,
        urgency=source.urgency,
        source_job_id=source.id,
        raw_jd_text=source_version.raw_jd_text,
        structured_jd=deepcopy(source_version.structured_jd or empty_structured_jd()),
        score_dimensions=deepcopy(source_version.score_dimensions or []),
    )
    await record_audit(
        session,
        action="job.copy",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={"source_job_id": str(source.id), "code": job.code},
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)


async def remove_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> None:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status != JOB_STATUS_DRAFT:
        raise JobStateError("only draft jobs can be deleted")

    code = job.code
    await delete_job(session, job)
    await record_audit(
        session,
        action="job.delete",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job_id),
        changes={"code": code},
    )
    await session.commit()


def _sorted_versions(job: Job) -> list[JobVersion]:
    def sort_key(version: JobVersion) -> tuple:
        is_draft = 0 if version.status == VERSION_STATUS_DRAFT else 1
        published = version.published_at or version.updated_at or version.created_at
        return (
            is_draft,
            -(version.major or 0),
            -(version.minor or 0),
            -published.timestamp(),
        )

    return sorted(job.versions, key=sort_key)


def build_version_diff(
    left: JobVersion,
    right: JobVersion,
) -> list[VersionDiffChange]:
    changes: list[VersionDiffChange] = []

    left_snap = left.job_snapshot or {}
    right_snap = right.job_snapshot or {}
    for key in (
        "name",
        "department",
        "level",
        "headcount",
        "location",
        "owner_name",
        "urgency",
    ):
        before = left_snap.get(key)
        after = right_snap.get(key)
        if before != after:
            changes.append(
                VersionDiffChange(
                    field=key,
                    label=FIELD_LABELS.get(key, key),
                    before=before,
                    after=after,
                )
            )

    if (left.raw_jd_text or "").strip() != (right.raw_jd_text or "").strip():
        changes.append(
            VersionDiffChange(
                field="raw_jd_text",
                label=FIELD_LABELS["raw_jd_text"],
                before=left.raw_jd_text or "",
                after=right.raw_jd_text or "",
            )
        )

    left_jd = structured_jd_to_dict(left.structured_jd)
    right_jd = structured_jd_to_dict(right.structured_jd)
    for key in (
        "responsibilities",
        "requirements",
        "must_have",
        "nice_to_have",
        "skills",
    ):
        before = left_jd.get(key) or []
        after = right_jd.get(key) or []
        if before != after:
            field = f"structured_jd.{key}"
            changes.append(
                VersionDiffChange(
                    field=field,
                    label=FIELD_LABELS.get(field, field),
                    before=before,
                    after=after,
                )
            )

    left_dims = normalize_score_dimensions(left.score_dimensions)
    right_dims = normalize_score_dimensions(right.score_dimensions)
    if left_dims != right_dims:
        changes.append(
            VersionDiffChange(
                field="score_dimensions",
                label=FIELD_LABELS["score_dimensions"],
                before=left_dims,
                after=right_dims,
            )
        )

    if (left.change_summary or "") != (right.change_summary or ""):
        changes.append(
            VersionDiffChange(
                field="change_summary",
                label=FIELD_LABELS["change_summary"],
                before=left.change_summary,
                after=right.change_summary,
            )
        )

    return changes


async def list_job_versions(
    session: AsyncSession,
    *,
    job_id: UUID,
) -> JobVersionListResponse:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    counts = await _bound_counts_for_job(session, job)
    items = [
        _to_version_list_item(
            version,
            current_version_id=job.current_version_id,
            bound_candidates=counts.get(version.id, 0),
        )
        for version in _sorted_versions(job)
    ]
    return JobVersionListResponse(items=items, total=len(items))


async def get_job_version_detail(
    session: AsyncSession,
    *,
    job_id: UUID,
    version_id: UUID,
) -> JobVersionOut:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    version = get_version_by_id(job, version_id)
    if version is None:
        raise JobNotFoundError("version not found")
    counts = await count_applications_by_version_ids(
        session, version_ids=[version.id]
    )
    out = _to_version_out(
        version,
        current_version_id=job.current_version_id,
        bound_candidates=counts.get(version.id, 0),
    )
    assert out is not None
    return out


async def diff_job_versions(
    session: AsyncSession,
    *,
    job_id: UUID,
    from_version_id: UUID,
    to_version_id: UUID,
) -> VersionDiffResponse:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    left = get_version_by_id(job, from_version_id)
    right = get_version_by_id(job, to_version_id)
    if left is None or right is None:
        raise JobNotFoundError("version not found")
    counts = await count_applications_by_version_ids(
        session, version_ids=[left.id, right.id]
    )
    changes = build_version_diff(left, right)
    return VersionDiffResponse(
        from_version=_to_version_list_item(
            left,
            current_version_id=job.current_version_id,
            bound_candidates=counts.get(left.id, 0),
        ),
        to_version=_to_version_list_item(
            right,
            current_version_id=job.current_version_id,
            bound_candidates=counts.get(right.id, 0),
        ),
        changes=changes,
        has_changes=bool(changes),
    )


async def copy_version_to_draft(
    session: AsyncSession,
    *,
    job_id: UUID,
    version_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status == JOB_STATUS_CLOSED:
        raise JobStateError(
            "closed job is read-only; copy as a new job instead"
        )
    if job.status == JOB_STATUS_DRAFT:
        raise JobStateError("draft job already editable; edit directly")

    source = get_version_by_id(job, version_id)
    if source is None:
        raise JobNotFoundError("version not found")
    if source.status == VERSION_STATUS_DRAFT:
        raise JobStateError("cannot copy an unpublished draft version")

    draft = get_version_by_id(job, job.draft_version_id)
    if draft is None:
        draft = await create_draft_version_from_base(
            session, job=job, base=source, actor_id=actor.id
        )
        await session.refresh(job, attribute_names=["versions"])
        draft = get_version_by_id(job, job.draft_version_id)
    if draft is None:
        raise JobStateError("draft version missing")

    draft.raw_jd_text = source.raw_jd_text or ""
    draft.structured_jd = deepcopy(source.structured_jd or empty_structured_jd())
    draft.score_dimensions = deepcopy(source.score_dimensions or [])
    draft.base_version_id = source.id
    draft.change_summary = f"复制自 {source.version_label or '历史版本'}"
    draft.job_snapshot = deepcopy(source.job_snapshot) if source.job_snapshot else None
    draft.updated_at = datetime.now(UTC)

    if source.job_snapshot:
        snap = source.job_snapshot
        if "name" in snap and snap["name"] is not None:
            job.name = str(snap["name"])
        if "department" in snap and snap["department"] is not None:
            job.department = str(snap["department"])
        if "level" in snap:
            job.level = snap["level"]
        if "headcount" in snap:
            job.headcount = snap["headcount"]
        if "location" in snap and snap["location"] is not None:
            job.location = str(snap["location"])
        if "owner_name" in snap and snap["owner_name"] is not None:
            job.owner_name = str(snap["owner_name"])
        if "urgency" in snap:
            job.urgency = snap["urgency"]

    job.updated_by = actor.id
    job.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        action="job.version.copy_to_draft",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={
            "source_version_id": str(source.id),
            "source_version_label": source.version_label,
            "draft_version_id": str(draft.id),
        },
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return await to_job_detail(session, refreshed)
