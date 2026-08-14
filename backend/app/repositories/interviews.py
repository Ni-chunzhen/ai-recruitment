from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Permission, Role, User
from app.models.candidate import JobApplication
from app.models.interview import (
    SCHEDULE_STATUS_ACTIVE,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
    InterviewSchedule,
)


class InterviewNotFoundError(Exception):
    pass


async def get_round_by_id(
    session: AsyncSession, round_id: UUID
) -> InterviewRound | None:
    return await session.scalar(
        select(InterviewRound)
        .options(
            selectinload(InterviewRound.interviewers),
            selectinload(InterviewRound.schedules),
        )
        .where(InterviewRound.id == round_id)
    )


async def get_round_for_update(
    session: AsyncSession, round_id: UUID
) -> InterviewRound | None:
    return await session.scalar(
        select(InterviewRound)
        .options(
            selectinload(InterviewRound.interviewers),
            selectinload(InterviewRound.schedules),
        )
        .where(InterviewRound.id == round_id)
        .with_for_update()
    )


async def list_rounds_for_application(
    session: AsyncSession, application_id: UUID
) -> list[InterviewRound]:
    result = await session.scalars(
        select(InterviewRound)
        .options(
            selectinload(InterviewRound.interviewers),
            selectinload(InterviewRound.schedules),
        )
        .where(InterviewRound.application_id == application_id)
        .order_by(InterviewRound.sequence_no.asc())
    )
    return list(result.all())


async def list_rounds_for_application_for_update(
    session: AsyncSession, application_id: UUID
) -> list[InterviewRound]:
    result = await session.scalars(
        select(InterviewRound)
        .options(
            selectinload(InterviewRound.interviewers),
            selectinload(InterviewRound.schedules),
        )
        .where(InterviewRound.application_id == application_id)
        .order_by(InterviewRound.sequence_no.asc())
        .with_for_update()
    )
    return list(result.all())


async def next_sequence_no(session: AsyncSession, application_id: UUID) -> int:
    current = await session.scalar(
        select(func.max(InterviewRound.sequence_no)).where(
            InterviewRound.application_id == application_id
        )
    )
    return int(current or 0) + 1


async def add_round(session: AsyncSession, round_: InterviewRound) -> InterviewRound:
    session.add(round_)
    await session.flush()
    return round_


async def add_schedule(
    session: AsyncSession, schedule: InterviewSchedule
) -> InterviewSchedule:
    session.add(schedule)
    await session.flush()
    return schedule


async def get_active_schedule_for_update(
    session: AsyncSession, round_id: UUID
) -> InterviewSchedule | None:
    return await session.scalar(
        select(InterviewSchedule)
        .where(
            InterviewSchedule.interview_round_id == round_id,
            InterviewSchedule.status == SCHEDULE_STATUS_ACTIVE,
        )
        .with_for_update()
    )


async def actor_assigned_to_round(
    session: AsyncSession, *, round_id: UUID, user_id: UUID
) -> bool:
    row = await session.scalar(
        select(InterviewRoundInterviewer.id).where(
            InterviewRoundInterviewer.interview_round_id == round_id,
            InterviewRoundInterviewer.interviewer_id == user_id,
        )
    )
    return row is not None


async def actor_assigned_to_application(
    session: AsyncSession, *, application_id: UUID, user_id: UUID
) -> bool:
    row = await session.scalar(
        select(InterviewRoundInterviewer.id)
        .join(
            InterviewRound,
            InterviewRound.id == InterviewRoundInterviewer.interview_round_id,
        )
        .where(
            InterviewRound.application_id == application_id,
            InterviewRoundInterviewer.interviewer_id == user_id,
        )
        .limit(1)
    )
    return row is not None


def _active_overlap_query(
    *,
    start_at: datetime,
    end_at: datetime,
    exclude_round_id: UUID | None,
) -> Select:
    query = (
        select(InterviewSchedule, InterviewRound, JobApplication)
        .join(
            InterviewRound,
            InterviewRound.id == InterviewSchedule.interview_round_id,
        )
        .join(JobApplication, JobApplication.id == InterviewRound.application_id)
        .where(
            InterviewSchedule.status == SCHEDULE_STATUS_ACTIVE,
            InterviewSchedule.start_at_utc < end_at,
            InterviewSchedule.end_at_utc > start_at,
        )
    )
    if exclude_round_id is not None:
        query = query.where(InterviewSchedule.interview_round_id != exclude_round_id)
    return query


async def find_candidate_conflicts(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    start_at: datetime,
    end_at: datetime,
    exclude_round_id: UUID | None = None,
) -> list[tuple[InterviewSchedule, InterviewRound]]:
    query = _active_overlap_query(
        start_at=start_at, end_at=end_at, exclude_round_id=exclude_round_id
    ).where(JobApplication.candidate_id == candidate_id)
    rows = (await session.execute(query)).all()
    return [(row[0], row[1]) for row in rows]


async def find_interviewer_conflicts(
    session: AsyncSession,
    *,
    interviewer_ids: list[UUID],
    start_at: datetime,
    end_at: datetime,
    exclude_round_id: UUID | None = None,
) -> list[tuple[InterviewSchedule, InterviewRound, UUID]]:
    if not interviewer_ids:
        return []
    query = (
        _active_overlap_query(
            start_at=start_at, end_at=end_at, exclude_round_id=exclude_round_id
        )
        .join(
            InterviewRoundInterviewer,
            InterviewRoundInterviewer.interview_round_id == InterviewRound.id,
        )
        .where(InterviewRoundInterviewer.interviewer_id.in_(interviewer_ids))
        .add_columns(InterviewRoundInterviewer.interviewer_id)
    )
    rows = (await session.execute(query)).all()
    return [(row[0], row[1], row[3]) for row in rows]


async def find_idempotency(
    session: AsyncSession,
    *,
    actor_id: UUID,
    action: str,
    scope_id: UUID,
    idempotency_key: str,
) -> InterviewIdempotencyKey | None:
    return await session.scalar(
        select(InterviewIdempotencyKey).where(
            InterviewIdempotencyKey.actor_id == actor_id,
            InterviewIdempotencyKey.action == action,
            InterviewIdempotencyKey.scope_id == scope_id,
            InterviewIdempotencyKey.idempotency_key == idempotency_key,
        )
    )


async def add_idempotency(
    session: AsyncSession, record: InterviewIdempotencyKey
) -> InterviewIdempotencyKey:
    session.add(record)
    await session.flush()
    return record


async def list_users_with_permission(
    session: AsyncSession, permission_code: str
) -> list[User]:
    result = await session.scalars(
        select(User)
        .join(User.roles)
        .join(Role.permissions)
        .where(Permission.code == permission_code, User.is_active.is_(True))
        .options(selectinload(User.roles))
        .distinct()
        .order_by(User.display_name.asc())
    )
    return list(result.all())


async def get_users_by_ids(session: AsyncSession, user_ids: list[UUID]) -> list[User]:
    if not user_ids:
        return []
    result = await session.scalars(select(User).where(User.id.in_(user_ids)))
    return list(result.all())
