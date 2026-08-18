from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, JobApplication
from app.models.interview import InterviewRound, InterviewRoundInterviewer, InterviewSchedule
from app.models.interview_ai import InterviewQuestionSet, InterviewRoundAnalysis, InterviewRoundAnalysisVersion
from app.models.interview_transcript import InterviewTranscript
from app.models.invitation import InterviewInvitationMessage
from app.models.job import Job, JobVersion

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


@dataclass(frozen=True, slots=True)
class CandidateCenterDetailRow:
    application: JobApplication
    candidate_name: str
    candidate_phone: str | None
    candidate_email: str | None
    job_name: str
    job_code: str
    job_version_label: str | None


@dataclass(frozen=True, slots=True)
class OtherApplicationRow:
    application_id: UUID
    job_id: UUID
    job_name: str
    job_code: str
    status: str
    pipeline_status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RoundStatusRow:
    round_id: UUID
    current_schedule_id: UUID | None
    current_schedule_status: str | None
    has_meeting_password: bool
    invitation_confirmed_at: datetime | None
    invitation_message_statuses: tuple[str, ...]
    transcript_completion_mode: str | None
    transcript_confirmed_version_id: UUID | None
    transcript_draft_version_id: UUID | None
    transcript_original_version_id: UUID | None
    question_set_status: str | None
    analysis_current_version_id: UUID | None
    analysis_transcript_version_id: UUID | None
    analysis_overall_score: Decimal | None


async def get_candidate_center_application_row(
    session: AsyncSession, application_id: UUID
) -> CandidateCenterDetailRow | None:
    row = (
        await session.execute(
            select(
                JobApplication,
                Candidate.name,
                Candidate.phone,
                Candidate.email,
                Job.name,
                Job.code,
                JobVersion.version_label,
                JobVersion.major,
                JobVersion.minor,
            )
            .select_from(JobApplication)
            .join(Candidate, JobApplication.candidate_id == Candidate.id)
            .join(Job, JobApplication.job_id == Job.id)
            .join(JobVersion, JobApplication.job_version_id == JobVersion.id)
            .where(JobApplication.id == application_id)
        )
    ).first()
    if row is None:
        return None
    (
        application,
        candidate_name,
        candidate_phone,
        candidate_email,
        job_name,
        job_code,
        version_label,
        major,
        minor,
    ) = row
    return CandidateCenterDetailRow(
        application=application,
        candidate_name=candidate_name,
        candidate_phone=candidate_phone,
        candidate_email=candidate_email,
        job_name=job_name,
        job_code=job_code,
        job_version_label=version_label or f"V{major}.{minor}",
    )


async def list_other_applications_for_candidate(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    exclude_application_id: UUID,
) -> list[OtherApplicationRow]:
    rows = await session.execute(
        select(
            JobApplication.id,
            JobApplication.job_id,
            Job.name,
            Job.code,
            JobApplication.status,
            JobApplication.pipeline_status,
            JobApplication.created_at,
        )
        .select_from(JobApplication)
        .join(Job, JobApplication.job_id == Job.id)
        .where(
            JobApplication.candidate_id == candidate_id,
            JobApplication.id != exclude_application_id,
        )
        .order_by(JobApplication.created_at.desc())
    )
    return [
        OtherApplicationRow(
            application_id=application_id,
            job_id=job_id,
            job_name=job_name,
            job_code=job_code,
            status=status,
            pipeline_status=pipeline_status,
            created_at=created_at,
        )
        for (
            application_id,
            job_id,
            job_name,
            job_code,
            status,
            pipeline_status,
            created_at,
        ) in rows.all()
    ]


async def list_job_version_labels(
    session: AsyncSession, *, version_ids: list[UUID]
) -> dict[UUID, str]:
    if not version_ids:
        return {}
    rows = await session.execute(
        select(
            JobVersion.id,
            JobVersion.version_label,
            JobVersion.major,
            JobVersion.minor,
        ).where(JobVersion.id.in_(version_ids))
    )
    labels: dict[UUID, str] = {}
    for version_id, label, major, minor in rows.all():
        labels[version_id] = label or f"V{major}.{minor}"
    return labels


async def list_round_status_rows(
    session: AsyncSession, *, round_ids: list[UUID]
) -> list[RoundStatusRow]:
    if not round_ids:
        return []

    round_rows = (
        await session.execute(
            select(
                InterviewRound.id,
                InterviewRound.current_schedule_id,
                InterviewRound.invitation_confirmed_at,
                InterviewRound.transcript_completion_mode,
                InterviewSchedule.status,
                InterviewSchedule.meeting_password_encrypted.isnot(None),
            )
            .select_from(InterviewRound)
            .outerjoin(
                InterviewSchedule,
                InterviewSchedule.id == InterviewRound.current_schedule_id,
            )
            .where(InterviewRound.id.in_(round_ids))
        )
    ).all()
    round_by_id = {row[0]: row for row in round_rows}

    message_rows = (
        await session.execute(
            select(
                InterviewInvitationMessage.interview_round_id,
                InterviewInvitationMessage.status,
            ).where(InterviewInvitationMessage.interview_round_id.in_(round_ids))
        )
    ).all()
    messages_by_round: dict[UUID, list[str]] = {}
    for round_id, status in message_rows:
        messages_by_round.setdefault(round_id, []).append(status)

    transcript_rows = (
        await session.execute(
            select(
                InterviewTranscript.interview_round_id,
                InterviewTranscript.current_confirmed_version_id,
                InterviewTranscript.current_draft_version_id,
                InterviewTranscript.original_version_id,
            ).where(InterviewTranscript.interview_round_id.in_(round_ids))
        )
    ).all()
    transcript_by_round = {row[0]: row for row in transcript_rows}

    question_rows = (
        await session.execute(
            select(
                InterviewQuestionSet.interview_round_id,
                InterviewQuestionSet.status,
            ).where(InterviewQuestionSet.interview_round_id.in_(round_ids))
        )
    ).all()
    question_by_round = {row[0]: row[1] for row in question_rows}

    analysis_rows = (
        await session.execute(
            select(
                InterviewRoundAnalysis.interview_round_id,
                InterviewRoundAnalysis.current_version_id,
                InterviewRoundAnalysisVersion.transcript_version_id,
                InterviewRoundAnalysisVersion.overall_score,
            )
            .select_from(InterviewRoundAnalysis)
            .outerjoin(
                InterviewRoundAnalysisVersion,
                InterviewRoundAnalysisVersion.id
                == InterviewRoundAnalysis.current_version_id,
            )
            .where(InterviewRoundAnalysis.interview_round_id.in_(round_ids))
        )
    ).all()
    analysis_by_round = {row[0]: row for row in analysis_rows}

    results: list[RoundStatusRow] = []
    for round_id in round_ids:
        row = round_by_id.get(round_id)
        if row is None:
            continue
        transcript = transcript_by_round.get(round_id)
        analysis = analysis_by_round.get(round_id)
        results.append(
            RoundStatusRow(
                round_id=round_id,
                current_schedule_id=row[1],
                current_schedule_status=row[4],
                has_meeting_password=bool(row[5]),
                invitation_confirmed_at=row[2],
                invitation_message_statuses=tuple(messages_by_round.get(round_id, ())),
                transcript_completion_mode=row[3],
                transcript_confirmed_version_id=transcript[1] if transcript else None,
                transcript_draft_version_id=transcript[2] if transcript else None,
                transcript_original_version_id=transcript[3] if transcript else None,
                question_set_status=question_by_round.get(round_id),
                analysis_current_version_id=analysis[1] if analysis else None,
                analysis_transcript_version_id=analysis[2] if analysis else None,
                analysis_overall_score=analysis[3] if analysis else None,
            )
        )
    return results
