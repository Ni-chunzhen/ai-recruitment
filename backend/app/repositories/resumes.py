from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.candidate import Candidate, JobApplication
from app.models.job import Job
from app.models.resume import (
    VERSION_KIND_CONFIRMED,
    VERSION_KIND_FILE,
    AiResult,
    ApplicationStatusLog,
    Resume,
    ResumeVersion,
    ScreeningDecision,
)


class ResumeNotFoundError(Exception):
    pass


async def get_resume_by_id(session: AsyncSession, resume_id: UUID) -> Resume | None:
    result = await session.execute(
        select(Resume)
        .where(Resume.id == resume_id)
        .options(selectinload(Resume.versions), selectinload(Resume.candidate))
    )
    return result.scalar_one_or_none()


async def get_resume_version_by_id(
    session: AsyncSession,
    version_id: UUID,
    *,
    with_resume: bool = True,
) -> ResumeVersion | None:
    stmt = select(ResumeVersion).where(ResumeVersion.id == version_id)
    if with_resume:
        stmt = stmt.options(
            selectinload(ResumeVersion.resume).selectinload(Resume.candidate)
        )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def add_resume(session: AsyncSession, resume: Resume) -> Resume:
    session.add(resume)
    await session.flush()
    return resume


async def add_resume_version(
    session: AsyncSession, version: ResumeVersion
) -> ResumeVersion:
    session.add(version)
    await session.flush()
    return version


async def count_file_versions(session: AsyncSession, resume_id: UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ResumeVersion)
        .where(
            ResumeVersion.resume_id == resume_id,
            ResumeVersion.kind == VERSION_KIND_FILE,
        )
    )
    return int(result.scalar_one())


async def count_confirmed_versions(session: AsyncSession, resume_id: UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ResumeVersion)
        .where(
            ResumeVersion.resume_id == resume_id,
            ResumeVersion.kind == VERSION_KIND_CONFIRMED,
        )
    )
    return int(result.scalar_one())


async def find_candidates_by_contact(
    session: AsyncSession,
    *,
    phone: str | None,
    email: str | None,
    exclude_id: UUID | None = None,
) -> list[Candidate]:
    clauses = []
    if phone:
        clauses.append(Candidate.phone == phone)
    if email:
        clauses.append(func.lower(Candidate.email) == email.lower())
    if not clauses:
        return []
    stmt = select(Candidate).where(or_(*clauses))
    if exclude_id is not None:
        stmt = stmt.where(Candidate.id != exclude_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _list_stmt(
    *,
    keyword: str | None,
    status: str | None,
    linked: bool | None,
) -> Select:
    stmt = (
        select(ResumeVersion, Resume, Candidate)
        .join(Resume, ResumeVersion.resume_id == Resume.id)
        .join(Candidate, Resume.candidate_id == Candidate.id)
        .where(Resume.is_void.is_(False))
        .where(
            or_(
                Resume.current_file_version_id == ResumeVersion.id,
                Resume.current_confirmed_version_id == ResumeVersion.id,
            )
        )
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Candidate.name.ilike(like),
                Candidate.phone.ilike(like),
                Candidate.email.ilike(like),
                ResumeVersion.original_filename.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(ResumeVersion.status == status)
    if linked is True:
        stmt = stmt.where(
            Resume.candidate_id.in_(select(JobApplication.candidate_id).distinct())
        )
    elif linked is False:
        stmt = stmt.where(
            ~Resume.candidate_id.in_(select(JobApplication.candidate_id).distinct())
        )
    return stmt.order_by(ResumeVersion.updated_at.desc())


async def list_resume_versions(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    status: str | None = None,
    linked: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[ResumeVersion, Resume, Candidate]], int]:
    base = _list_stmt(keyword=keyword, status=status, linked=linked)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    result = await session.execute(base.offset(offset).limit(limit))
    return list(result.all()), total


async def list_job_names_for_candidates(
    session: AsyncSession, candidate_ids: list[UUID]
) -> dict[UUID, list[str]]:
    if not candidate_ids:
        return {}
    result = await session.execute(
        select(JobApplication.candidate_id, Job.name)
        .join(Job, Job.id == JobApplication.job_id)
        .where(JobApplication.candidate_id.in_(candidate_ids))
    )
    mapping: dict[UUID, list[str]] = {cid: [] for cid in candidate_ids}
    for candidate_id, name in result.all():
        mapping.setdefault(candidate_id, []).append(name)
    return mapping


async def get_application_by_id(
    session: AsyncSession, application_id: UUID
) -> JobApplication | None:
    result = await session.execute(
        select(JobApplication)
        .where(JobApplication.id == application_id)
        .options(selectinload(JobApplication.candidate))
    )
    return result.scalar_one_or_none()


async def get_application_by_id_for_update(
    session: AsyncSession, application_id: UUID
) -> JobApplication | None:
    result = await session.execute(
        select(JobApplication)
        .where(JobApplication.id == application_id)
        .options(selectinload(JobApplication.candidate))
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def find_open_application(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    job_id: UUID,
) -> JobApplication | None:
    result = await session.execute(
        select(JobApplication).where(
            and_(
                JobApplication.candidate_id == candidate_id,
                JobApplication.job_id == job_id,
                JobApplication.status == "in_progress",
            )
        )
    )
    return result.scalar_one_or_none()


async def add_ai_result(session: AsyncSession, result: AiResult) -> AiResult:
    session.add(result)
    await session.flush()
    return result


async def mark_previous_results_not_current(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_type: str,
) -> None:
    items = await session.execute(
        select(AiResult).where(
            AiResult.application_id == application_id,
            AiResult.result_type == result_type,
            AiResult.is_current.is_(True),
        )
    )
    for item in items.scalars().all():
        item.is_current = False


async def count_ai_results_for_application(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_type: str,
) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(AiResult)
        .where(
            AiResult.application_id == application_id,
            AiResult.result_type == result_type,
        )
    )
    return int(result.scalar_one())


async def get_current_ai_result(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_type: str,
) -> AiResult | None:
    result = await session.execute(
        select(AiResult)
        .where(
            AiResult.application_id == application_id,
            AiResult.result_type == result_type,
            AiResult.is_current.is_(True),
        )
        .order_by(AiResult.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_ai_results(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_type: str,
) -> list[AiResult]:
    result = await session.execute(
        select(AiResult)
        .where(
            AiResult.application_id == application_id,
            AiResult.result_type == result_type,
        )
        .order_by(AiResult.created_at.desc())
    )
    return list(result.scalars().all())


async def get_ai_result_by_id(
    session: AsyncSession,
    result_id: UUID,
) -> AiResult | None:
    result = await session.execute(select(AiResult).where(AiResult.id == result_id))
    return result.scalar_one_or_none()


async def mark_current_results_stale(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_type: str,
) -> int:
    items = await session.execute(
        select(AiResult).where(
            AiResult.application_id == application_id,
            AiResult.result_type == result_type,
            AiResult.is_current.is_(True),
        )
    )
    count = 0
    for item in items.scalars().all():
        item.is_stale = True
        count += 1
    return count


async def find_screening_by_idempotency(
    session: AsyncSession,
    *,
    application_id: UUID,
    idempotency_key: str,
) -> ScreeningDecision | None:
    result = await session.execute(
        select(ScreeningDecision).where(
            ScreeningDecision.application_id == application_id,
            ScreeningDecision.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def add_screening_decision(
    session: AsyncSession, decision: ScreeningDecision
) -> ScreeningDecision:
    session.add(decision)
    await session.flush()
    return decision


async def add_status_log(
    session: AsyncSession, log: ApplicationStatusLog
) -> ApplicationStatusLog:
    session.add(log)
    await session.flush()
    return log


async def list_pending_review_versions(
    session: AsyncSession, *, limit: int = 50
) -> list[tuple[ResumeVersion, Resume, Candidate]]:
    result = await session.execute(
        select(ResumeVersion, Resume, Candidate)
        .join(Resume, ResumeVersion.resume_id == Resume.id)
        .join(Candidate, Resume.candidate_id == Candidate.id)
        .where(
            Resume.is_void.is_(False),
            ResumeVersion.status.in_(["pending_review", "parse_failed", "parsing"]),
            Resume.current_file_version_id == ResumeVersion.id,
        )
        .order_by(ResumeVersion.updated_at.desc())
        .limit(limit)
    )
    return list(result.all())
