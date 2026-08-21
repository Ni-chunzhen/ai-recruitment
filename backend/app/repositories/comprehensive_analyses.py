"""Repository helpers for application comprehensive analysis aggregates."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comprehensive_analysis import (
    ApplicationComprehensiveAnalysis,
    ApplicationComprehensiveAnalysisVersion,
)


async def get_comprehensive_analysis_by_application(
    session: AsyncSession, *, application_id: UUID
) -> ApplicationComprehensiveAnalysis | None:
    return await session.scalar(
        select(ApplicationComprehensiveAnalysis).where(
            ApplicationComprehensiveAnalysis.application_id == application_id
        )
    )


async def get_comprehensive_analysis_for_update(
    session: AsyncSession, *, application_id: UUID
) -> ApplicationComprehensiveAnalysis | None:
    return await session.scalar(
        select(ApplicationComprehensiveAnalysis)
        .where(ApplicationComprehensiveAnalysis.application_id == application_id)
        .with_for_update()
    )


async def get_comprehensive_version_by_id(
    session: AsyncSession,
    *,
    application_id: UUID,
    version_id: UUID,
) -> ApplicationComprehensiveAnalysisVersion | None:
    return await session.scalar(
        select(ApplicationComprehensiveAnalysisVersion)
        .join(
            ApplicationComprehensiveAnalysis,
            ApplicationComprehensiveAnalysis.id
            == ApplicationComprehensiveAnalysisVersion.analysis_id,
        )
        .where(
            ApplicationComprehensiveAnalysisVersion.id == version_id,
            ApplicationComprehensiveAnalysis.application_id == application_id,
        )
    )


async def get_comprehensive_version_by_task_id(
    session: AsyncSession, *, ai_task_id: UUID
) -> ApplicationComprehensiveAnalysisVersion | None:
    return await session.scalar(
        select(ApplicationComprehensiveAnalysisVersion).where(
            ApplicationComprehensiveAnalysisVersion.ai_task_id == ai_task_id
        )
    )


async def list_comprehensive_version_rows(
    session: AsyncSession, *, application_id: UUID
) -> list[ApplicationComprehensiveAnalysisVersion]:
    result = await session.scalars(
        select(ApplicationComprehensiveAnalysisVersion)
        .join(
            ApplicationComprehensiveAnalysis,
            ApplicationComprehensiveAnalysis.id
            == ApplicationComprehensiveAnalysisVersion.analysis_id,
        )
        .where(ApplicationComprehensiveAnalysis.application_id == application_id)
        .order_by(
            ApplicationComprehensiveAnalysisVersion.version_no.asc(),
            ApplicationComprehensiveAnalysisVersion.created_at.asc(),
        )
    )
    return list(result.all())


async def next_comprehensive_version_no(
    session: AsyncSession, *, analysis_id: UUID
) -> int:
    current = await session.scalar(
        select(func.max(ApplicationComprehensiveAnalysisVersion.version_no)).where(
            ApplicationComprehensiveAnalysisVersion.analysis_id == analysis_id
        )
    )
    return int(current or 0) + 1


async def create_comprehensive_analysis(
    session: AsyncSession, row: ApplicationComprehensiveAnalysis
) -> ApplicationComprehensiveAnalysis:
    session.add(row)
    await session.flush()
    return row


async def create_comprehensive_version(
    session: AsyncSession, row: ApplicationComprehensiveAnalysisVersion
) -> ApplicationComprehensiveAnalysisVersion:
    session.add(row)
    await session.flush()
    return row
