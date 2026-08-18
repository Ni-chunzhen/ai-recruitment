"""Repository helpers for interview question sets, versions and items."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.interview_ai import (
    InterviewQuestionItem,
    InterviewQuestionSet,
    InterviewQuestionVersion,
)


async def get_question_set_by_round(
    session: AsyncSession, round_id: UUID
) -> InterviewQuestionSet | None:
    return await session.scalar(
        select(InterviewQuestionSet)
        .options(
            selectinload(InterviewQuestionSet.versions).selectinload(
                InterviewQuestionVersion.items
            )
        )
        .where(InterviewQuestionSet.interview_round_id == round_id)
    )


async def get_question_set_for_update(
    session: AsyncSession, round_id: UUID
) -> InterviewQuestionSet | None:
    return await session.scalar(
        select(InterviewQuestionSet)
        .options(
            selectinload(InterviewQuestionSet.versions).selectinload(
                InterviewQuestionVersion.items
            )
        )
        .where(InterviewQuestionSet.interview_round_id == round_id)
        .with_for_update()
    )


async def get_question_version_by_id(
    session: AsyncSession,
    *,
    round_id: UUID,
    version_id: UUID,
) -> InterviewQuestionVersion | None:
    return await session.scalar(
        select(InterviewQuestionVersion)
        .join(
            InterviewQuestionSet,
            InterviewQuestionSet.id == InterviewQuestionVersion.question_set_id,
        )
        .options(selectinload(InterviewQuestionVersion.items))
        .where(
            InterviewQuestionVersion.id == version_id,
            InterviewQuestionSet.interview_round_id == round_id,
        )
    )


async def get_question_version_by_task_id(
    session: AsyncSession,
    ai_task_id: UUID,
    *,
    round_id: UUID | None = None,
) -> InterviewQuestionVersion | None:
    stmt = (
        select(InterviewQuestionVersion)
        .join(
            InterviewQuestionSet,
            InterviewQuestionSet.id == InterviewQuestionVersion.question_set_id,
        )
        .options(selectinload(InterviewQuestionVersion.items))
        .where(InterviewQuestionVersion.ai_task_id == ai_task_id)
    )
    if round_id is not None:
        stmt = stmt.where(InterviewQuestionSet.interview_round_id == round_id)
    return await session.scalar(stmt)


async def list_question_versions(
    session: AsyncSession, round_id: UUID
) -> list[InterviewQuestionVersion]:
    result = await session.scalars(
        select(InterviewQuestionVersion)
        .join(
            InterviewQuestionSet,
            InterviewQuestionSet.id == InterviewQuestionVersion.question_set_id,
        )
        .options(selectinload(InterviewQuestionVersion.items))
        .where(InterviewQuestionSet.interview_round_id == round_id)
        .order_by(
            InterviewQuestionVersion.version_no.asc(),
            InterviewQuestionVersion.created_at.asc(),
        )
    )
    return list(result.all())


async def create_question_set(
    session: AsyncSession, question_set: InterviewQuestionSet
) -> InterviewQuestionSet:
    session.add(question_set)
    await session.flush()
    return question_set


async def create_question_version(
    session: AsyncSession, version: InterviewQuestionVersion
) -> InterviewQuestionVersion:
    session.add(version)
    await session.flush()
    return version


async def create_question_items(
    session: AsyncSession, items: list[InterviewQuestionItem]
) -> list[InterviewQuestionItem]:
    for item in items:
        session.add(item)
    await session.flush()
    return items


async def next_question_version_no(
    session: AsyncSession, question_set_id: UUID
) -> int:
    current = await session.scalar(
        select(func.max(InterviewQuestionVersion.version_no)).where(
            InterviewQuestionVersion.question_set_id == question_set_id
        )
    )
    return int(current or 0) + 1
