from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    IN_FLIGHT_STATUSES,
    Candidate,
    JobApplication,
)


class CandidateNotFoundError(Exception):
    pass


async def create_candidate(
    session: AsyncSession,
    *,
    name: str,
    phone: str | None = None,
    email: str | None = None,
) -> Candidate:
    candidate = Candidate(name=name, phone=phone, email=email)
    session.add(candidate)
    await session.flush()
    return candidate


async def get_candidate_by_id(
    session: AsyncSession, candidate_id: UUID
) -> Candidate | None:
    return await session.scalar(select(Candidate).where(Candidate.id == candidate_id))


async def create_application(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    job_id: UUID,
    job_version_id: UUID,
    interview_started: bool = False,
    interview_task_state: str = "none",
    status: str = APPLICATION_STATUS_IN_PROGRESS,
    pipeline_status: str = "pending_hr_screen",
    resume_version_id: UUID | None = None,
    lock_version: int = 1,
) -> JobApplication:
    application = JobApplication(
        candidate_id=candidate_id,
        job_id=job_id,
        job_version_id=job_version_id,
        status=status,
        pipeline_status=pipeline_status,
        resume_version_id=resume_version_id,
        lock_version=lock_version,
        interview_started=interview_started,
        interview_task_state=interview_task_state,
        timeline_events=[],
    )
    session.add(application)
    await session.flush()
    return application


async def get_application_by_id(
    session: AsyncSession,
    *,
    application_id: UUID,
    job_id: UUID | None = None,
) -> JobApplication | None:
    query = (
        select(JobApplication)
        .options(selectinload(JobApplication.candidate))
        .where(JobApplication.id == application_id)
    )
    if job_id is not None:
        query = query.where(JobApplication.job_id == job_id)
    return await session.scalar(query)


async def list_applications_for_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    in_flight_only: bool = False,
) -> list[JobApplication]:
    query = (
        select(JobApplication)
        .options(selectinload(JobApplication.candidate))
        .where(JobApplication.job_id == job_id)
        .order_by(JobApplication.created_at.desc())
    )
    if in_flight_only:
        query = query.where(JobApplication.status.in_(tuple(IN_FLIGHT_STATUSES)))
    result = await session.scalars(query)
    return list(result.all())


async def count_in_flight_applications(
    session: AsyncSession,
    *,
    job_id: UUID,
) -> int:
    total = await session.scalar(
        select(func.count())
        .select_from(JobApplication)
        .where(
            JobApplication.job_id == job_id,
            JobApplication.status == APPLICATION_STATUS_IN_PROGRESS,
        )
    )
    return int(total or 0)


async def count_applications_by_version_ids(
    session: AsyncSession,
    *,
    version_ids: list[UUID],
) -> dict[UUID, int]:
    if not version_ids:
        return {}
    rows = await session.execute(
        select(JobApplication.job_version_id, func.count())
        .where(JobApplication.job_version_id.in_(version_ids))
        .group_by(JobApplication.job_version_id)
    )
    return {version_id: int(count) for version_id, count in rows.all()}
