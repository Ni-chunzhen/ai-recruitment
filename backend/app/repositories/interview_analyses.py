"""Repository helpers for single-round interview analysis aggregates."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.interview_ai import (
    InterviewRoundAnalysis,
    InterviewRoundAnalysisDimension,
    InterviewRoundAnalysisEvidence,
    InterviewRoundAnalysisVersion,
)


async def get_analysis_by_round(
    session: AsyncSession, *, round_id: UUID
) -> InterviewRoundAnalysis | None:
    return await session.scalar(
        select(InterviewRoundAnalysis)
        .options(
            selectinload(InterviewRoundAnalysis.versions)
            .selectinload(InterviewRoundAnalysisVersion.dimensions)
            .selectinload(InterviewRoundAnalysisDimension.evidence)
            .selectinload(InterviewRoundAnalysisEvidence.transcript_segment)
        )
        .where(InterviewRoundAnalysis.interview_round_id == round_id)
    )


async def get_analysis_for_update(
    session: AsyncSession, *, round_id: UUID
) -> InterviewRoundAnalysis | None:
    return await session.scalar(
        select(InterviewRoundAnalysis)
        .options(
            selectinload(InterviewRoundAnalysis.versions)
            .selectinload(InterviewRoundAnalysisVersion.dimensions)
            .selectinload(InterviewRoundAnalysisDimension.evidence)
        )
        .where(InterviewRoundAnalysis.interview_round_id == round_id)
        .with_for_update()
    )


async def get_analysis_version_by_id(
    session: AsyncSession,
    *,
    round_id: UUID,
    version_id: UUID,
) -> InterviewRoundAnalysisVersion | None:
    return await session.scalar(
        select(InterviewRoundAnalysisVersion)
        .join(
            InterviewRoundAnalysis,
            InterviewRoundAnalysis.id == InterviewRoundAnalysisVersion.analysis_id,
        )
        .options(
            selectinload(InterviewRoundAnalysisVersion.dimensions)
            .selectinload(InterviewRoundAnalysisDimension.evidence)
            .selectinload(InterviewRoundAnalysisEvidence.transcript_segment)
        )
        .where(
            InterviewRoundAnalysisVersion.id == version_id,
            InterviewRoundAnalysis.interview_round_id == round_id,
        )
    )


async def get_analysis_version_by_task_id(
    session: AsyncSession,
    *,
    ai_task_id: UUID,
    round_id: UUID | None = None,
) -> InterviewRoundAnalysisVersion | None:
    stmt = (
        select(InterviewRoundAnalysisVersion)
        .join(
            InterviewRoundAnalysis,
            InterviewRoundAnalysis.id == InterviewRoundAnalysisVersion.analysis_id,
        )
        .options(
            selectinload(InterviewRoundAnalysisVersion.dimensions)
            .selectinload(InterviewRoundAnalysisDimension.evidence)
        )
        .where(InterviewRoundAnalysisVersion.ai_task_id == ai_task_id)
    )
    if round_id is not None:
        stmt = stmt.where(InterviewRoundAnalysis.interview_round_id == round_id)
    return await session.scalar(stmt)


async def list_analysis_versions(
    session: AsyncSession, *, round_id: UUID
) -> list[InterviewRoundAnalysisVersion]:
    result = await session.scalars(
        select(InterviewRoundAnalysisVersion)
        .join(
            InterviewRoundAnalysis,
            InterviewRoundAnalysis.id == InterviewRoundAnalysisVersion.analysis_id,
        )
        .options(
            selectinload(InterviewRoundAnalysisVersion.dimensions)
            .selectinload(InterviewRoundAnalysisDimension.evidence)
        )
        .where(InterviewRoundAnalysis.interview_round_id == round_id)
        .order_by(
            InterviewRoundAnalysisVersion.version_no.asc(),
            InterviewRoundAnalysisVersion.created_at.asc(),
        )
    )
    return list(result.all())


async def create_analysis(
    session: AsyncSession, analysis: InterviewRoundAnalysis
) -> InterviewRoundAnalysis:
    session.add(analysis)
    await session.flush()
    return analysis


async def create_analysis_version(
    session: AsyncSession, version: InterviewRoundAnalysisVersion
) -> InterviewRoundAnalysisVersion:
    session.add(version)
    await session.flush()
    return version


async def create_analysis_dimensions(
    session: AsyncSession, rows: list[InterviewRoundAnalysisDimension]
) -> list[InterviewRoundAnalysisDimension]:
    for row in rows:
        session.add(row)
    await session.flush()
    return rows


async def create_analysis_evidence(
    session: AsyncSession, rows: list[InterviewRoundAnalysisEvidence]
) -> list[InterviewRoundAnalysisEvidence]:
    for row in rows:
        session.add(row)
    await session.flush()
    return rows


async def next_analysis_version_no(
    session: AsyncSession, *, analysis_id: UUID
) -> int:
    current = await session.scalar(
        select(func.max(InterviewRoundAnalysisVersion.version_no)).where(
            InterviewRoundAnalysisVersion.analysis_id == analysis_id
        )
    )
    return int(current or 0) + 1
