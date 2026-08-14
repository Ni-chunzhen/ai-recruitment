from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import (
    JOB_STATUS_DRAFT,
    VERSION_STATUS_DRAFT,
    Job,
    JobCodeSequence,
    JobVersion,
    empty_structured_jd,
)


class JobNotFoundError(Exception):
    pass


async def allocate_job_code(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(UTC)
    year_month = current.strftime("%Y%m")
    seq = await session.scalar(
        select(JobCodeSequence)
        .where(JobCodeSequence.year_month == year_month)
        .with_for_update()
    )
    if seq is None:
        seq = JobCodeSequence(year_month=year_month, last_value=0)
        session.add(seq)
        await session.flush()
    seq.last_value += 1
    await session.flush()
    return f"JOB-{year_month}-{seq.last_value:04d}"


async def get_job_by_id(session: AsyncSession, job_id: UUID) -> Job | None:
    return await session.scalar(
        select(Job)
        .options(selectinload(Job.versions))
        .where(Job.id == job_id)
    )


async def list_jobs(
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
) -> tuple[list[Job], int]:
    query = select(Job).options(selectinload(Job.versions))
    count_query = select(func.count()).select_from(Job)

    if keyword:
        pattern = f"%{keyword}%"
        clause = or_(
            Job.code.ilike(pattern),
            Job.name.ilike(pattern),
            Job.department.ilike(pattern),
        )
        query = query.where(clause)
        count_query = count_query.where(clause)
    if code:
        query = query.where(Job.code.ilike(f"%{code}%"))
        count_query = count_query.where(Job.code.ilike(f"%{code}%"))
    if name:
        query = query.where(Job.name.ilike(f"%{name}%"))
        count_query = count_query.where(Job.name.ilike(f"%{name}%"))
    if department:
        query = query.where(Job.department.ilike(f"%{department}%"))
        count_query = count_query.where(Job.department.ilike(f"%{department}%"))
    if owner:
        query = query.where(Job.owner_name.ilike(f"%{owner}%"))
        count_query = count_query.where(Job.owner_name.ilike(f"%{owner}%"))
    if status:
        query = query.where(Job.status == status)
        count_query = count_query.where(Job.status == status)
    if updated_from is not None:
        query = query.where(Job.updated_at >= updated_from)
        count_query = count_query.where(Job.updated_at >= updated_from)
    if updated_to is not None:
        query = query.where(Job.updated_at <= updated_to)
        count_query = count_query.where(Job.updated_at <= updated_to)

    total = await session.scalar(count_query) or 0
    result = await session.scalars(
        query.order_by(Job.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), int(total)


def get_version_by_id(job: Job, version_id: UUID | None) -> JobVersion | None:
    if version_id is None:
        return None
    for version in job.versions:
        if version.id == version_id:
            return version
    return None


async def create_job_with_draft(
    session: AsyncSession,
    *,
    code: str,
    actor_id: UUID,
    name: str = "",
    department: str = "",
    level: str | None = None,
    headcount: int | None = None,
    location: str = "",
    owner_user_id: UUID | None = None,
    owner_name: str = "",
    urgency: str | None = None,
    source_job_id: UUID | None = None,
    raw_jd_text: str = "",
    structured_jd: dict | None = None,
    score_dimensions: list | None = None,
) -> Job:
    job = Job(
        code=code,
        status=JOB_STATUS_DRAFT,
        name=name,
        department=department,
        level=level,
        headcount=headcount,
        location=location,
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        urgency=urgency,
        source_job_id=source_job_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(job)
    await session.flush()

    version = JobVersion(
        job_id=job.id,
        version_label="draft",
        major=0,
        minor=0,
        status=VERSION_STATUS_DRAFT,
        upgrade_type=None,
        raw_jd_text=raw_jd_text,
        structured_jd=deepcopy(structured_jd or empty_structured_jd()),
        score_dimensions=deepcopy(score_dimensions or []),
        created_by=actor_id,
    )
    session.add(version)
    await session.flush()
    job.draft_version_id = version.id
    await session.flush()
    await session.refresh(job, attribute_names=["versions"])
    return job


async def create_draft_version_from_base(
    session: AsyncSession,
    *,
    job: Job,
    base: JobVersion,
    actor_id: UUID,
) -> JobVersion:
    version = JobVersion(
        job_id=job.id,
        version_label="draft",
        major=0,
        minor=0,
        status=VERSION_STATUS_DRAFT,
        upgrade_type=None,
        raw_jd_text=base.raw_jd_text,
        structured_jd=deepcopy(base.structured_jd or empty_structured_jd()),
        score_dimensions=deepcopy(base.score_dimensions or []),
        job_snapshot=deepcopy(base.job_snapshot) if base.job_snapshot else None,
        base_version_id=base.id,
        created_by=actor_id,
    )
    session.add(version)
    await session.flush()
    job.draft_version_id = version.id
    await session.flush()
    return version


async def delete_job(session: AsyncSession, job: Job) -> None:
    await session.delete(job)
    await session.flush()
