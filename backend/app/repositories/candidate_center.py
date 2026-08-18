from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, JobApplication
from app.models.interview import InterviewRound, InterviewRoundInterviewer
from app.models.job import Job

SORT_UPDATED_AT_DESC = "updated_at_desc"
SORT_CREATED_AT_DESC = "created_at_desc"


@dataclass(frozen=True, slots=True)
class CandidateCenterApplicationRow:
    application: JobApplication
    candidate_name: str
    candidate_phone: str | None
    candidate_email: str | None
    job_name: str
    job_code: str
    display_round_id: UUID | None
    display_round_name: str | None
    display_round_sequence_no: int | None
    display_round_status: str | None


def assigned_interview_exists():
    return exists(
        select(1)
        .select_from(InterviewRound)
        .where(
            InterviewRound.application_id == JobApplication.id,
            exists(
                select(1)
                .select_from(InterviewRoundInterviewer)
                .where(
                    InterviewRoundInterviewer.interview_round_id == InterviewRound.id
                )
            ),
        )
    )


def display_round_id_subquery(*, assigned: bool):
    stmt = (
        select(InterviewRound.id)
        .where(InterviewRound.application_id == JobApplication.id)
        .order_by(InterviewRound.sequence_no.desc())
        .limit(1)
    )
    if assigned:
        stmt = stmt.where(
            exists(
                select(1)
                .select_from(InterviewRoundInterviewer)
                .where(
                    InterviewRoundInterviewer.interview_round_id == InterviewRound.id
                )
            )
        )
    return stmt.correlate(JobApplication).scalar_subquery()


def display_round_lateral(*, assigned: bool):
    stmt = (
        select(
            InterviewRound.id.label("display_round_id"),
            InterviewRound.name.label("display_round_name"),
            InterviewRound.sequence_no.label("display_round_sequence_no"),
            InterviewRound.status.label("display_round_status"),
        )
        .where(InterviewRound.application_id == JobApplication.id)
        .order_by(InterviewRound.sequence_no.desc())
        .limit(1)
    )
    if assigned:
        stmt = stmt.where(
            exists(
                select(1)
                .select_from(InterviewRoundInterviewer)
                .where(
                    InterviewRoundInterviewer.interview_round_id == InterviewRound.id
                )
            )
        )
    return stmt.correlate(JobApplication).lateral("display_round")


def _apply_list_filters(
    query: Select,
    *,
    assigned: bool,
    status: str | None,
    pipeline_status: str | None,
    job_id: UUID | None,
    keyword: str | None,
) -> Select:
    if assigned:
        query = query.where(assigned_interview_exists())
    if status is not None:
        query = query.where(JobApplication.status == status)
    if pipeline_status is not None:
        query = query.where(JobApplication.pipeline_status == pipeline_status)
    if job_id is not None:
        query = query.where(JobApplication.job_id == job_id)
    if keyword:
        pattern = f"%{keyword}%"
        query = query.where(
            or_(
                Candidate.name.ilike(pattern),
                Candidate.phone.ilike(pattern),
                Candidate.email.ilike(pattern),
                Job.code.ilike(pattern),
                Job.name.ilike(pattern),
            )
        )
    return query


def _list_from_joins(*, assigned: bool) -> Select:
    display_round = display_round_lateral(assigned=assigned)
    return (
        select(
            JobApplication,
            Candidate.name,
            Candidate.phone,
            Candidate.email,
            Job.name,
            Job.code,
            display_round.c.display_round_id,
            display_round.c.display_round_name,
            display_round.c.display_round_sequence_no,
            display_round.c.display_round_status,
        )
        .select_from(JobApplication)
        .join(Candidate, JobApplication.candidate_id == Candidate.id)
        .join(Job, JobApplication.job_id == Job.id)
        .outerjoin(display_round, true())
    )


def build_list_candidate_center_application_rows_select(
    *,
    assigned: bool,
    status: str | None,
    pipeline_status: str | None,
    job_id: UUID | None,
    keyword: str | None,
    sort: str,
    page: int,
    page_size: int,
) -> Select:
    query = _list_from_joins(assigned=assigned)
    query = _apply_list_filters(
        query,
        assigned=assigned,
        status=status,
        pipeline_status=pipeline_status,
        job_id=job_id,
        keyword=keyword,
    )
    if sort == SORT_CREATED_AT_DESC:
        query = query.order_by(
            JobApplication.created_at.desc(),
            JobApplication.id.desc(),
        )
    else:
        query = query.order_by(
            JobApplication.updated_at.desc(),
            JobApplication.id.desc(),
        )
    return query.offset((page - 1) * page_size).limit(page_size)


def build_count_candidate_center_applications_select(
    *,
    assigned: bool,
    status: str | None,
    pipeline_status: str | None,
    job_id: UUID | None,
    keyword: str | None,
) -> Select:
    query = (
        select(func.count())
        .select_from(JobApplication)
        .join(Candidate, JobApplication.candidate_id == Candidate.id)
        .join(Job, JobApplication.job_id == Job.id)
    )
    return _apply_list_filters(
        query,
        assigned=assigned,
        status=status,
        pipeline_status=pipeline_status,
        job_id=job_id,
        keyword=keyword,
    )


def _row_from_result(row) -> CandidateCenterApplicationRow:
    (
        application,
        candidate_name,
        candidate_phone,
        candidate_email,
        job_name,
        job_code,
        display_round_id,
        display_round_name,
        display_round_sequence_no,
        display_round_status,
    ) = row
    return CandidateCenterApplicationRow(
        application=application,
        candidate_name=candidate_name,
        candidate_phone=candidate_phone,
        candidate_email=candidate_email,
        job_name=job_name,
        job_code=job_code,
        display_round_id=display_round_id,
        display_round_name=display_round_name,
        display_round_sequence_no=display_round_sequence_no,
        display_round_status=display_round_status,
    )


async def list_candidate_center_application_rows(
    session: AsyncSession,
    *,
    assigned: bool,
    status: str | None,
    pipeline_status: str | None,
    job_id: UUID | None,
    keyword: str | None,
    sort: str,
    page: int,
    page_size: int,
) -> list[CandidateCenterApplicationRow]:
    query = build_list_candidate_center_application_rows_select(
        assigned=assigned,
        status=status,
        pipeline_status=pipeline_status,
        job_id=job_id,
        keyword=keyword,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    result = await session.execute(query)
    return [_row_from_result(row) for row in result.all()]


async def count_candidate_center_applications(
    session: AsyncSession,
    *,
    assigned: bool,
    status: str | None,
    pipeline_status: str | None,
    job_id: UUID | None,
    keyword: str | None,
) -> int:
    query = build_count_candidate_center_applications_select(
        assigned=assigned,
        status=status,
        pipeline_status=pipeline_status,
        job_id=job_id,
        keyword=keyword,
    )
    total = await session.scalar(query)
    return int(total or 0)
