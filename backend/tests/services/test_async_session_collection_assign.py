"""Real AsyncSession tests for unloaded collection assignment (MissingGreenlet).

Covers P1 question generate, P2 manual edit (same items attach), and P3 analysis
persist collection attaches. Uses sqlite+aiosqlite memory only — never `recruit`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, select, text
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import set_committed_value

BACKEND_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_SRC = (
    BACKEND_ROOT / "app" / "services" / "interview_questions.py"
).read_text(encoding="utf-8")
ANALYSES_SRC = (
    BACKEND_ROOT / "app" / "services" / "interview_analyses.py"
).read_text(encoding="utf-8")

MEMORY_URL = "sqlite+aiosqlite:///:memory:"


class _Base(DeclarativeBase):
    pass


class _QuestionVersion(_Base):
    """Mirrors InterviewQuestionVersion.items (P1/P2)."""

    __tablename__ = "t_question_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    items: Mapped[list["_QuestionItem"]] = relationship(back_populates="version")


class _QuestionItem(_Base):
    __tablename__ = "t_question_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_version_id: Mapped[str] = mapped_column(
        ForeignKey("t_question_versions.id")
    )
    version: Mapped[_QuestionVersion] = relationship(back_populates="items")


class _AnalysisVersion(_Base):
    """Mirrors InterviewRoundAnalysisVersion.dimensions (P3)."""

    __tablename__ = "t_analysis_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dimensions: Mapped[list["_AnalysisDimension"]] = relationship(
        back_populates="analysis_version"
    )


class _AnalysisDimension(_Base):
    """Mirrors InterviewRoundAnalysisDimension.evidence (P3)."""

    __tablename__ = "t_analysis_dimensions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_version_id: Mapped[str] = mapped_column(
        ForeignKey("t_analysis_versions.id")
    )
    dimension_key: Mapped[str] = mapped_column(String(64), default="k")
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10"))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_encrypted: Mapped[str] = mapped_column(Text, default="x")
    strengths_encrypted: Mapped[str] = mapped_column(Text, default="x")
    risks_encrypted: Mapped[str] = mapped_column(Text, default="x")
    suggested_follow_ups_encrypted: Mapped[str] = mapped_column(Text, default="x")
    display_order: Mapped[int] = mapped_column(Integer, default=1)
    analysis_version: Mapped[_AnalysisVersion] = relationship(
        back_populates="dimensions"
    )
    evidence: Mapped[list["_AnalysisEvidence"]] = relationship(
        back_populates="analysis_dimension"
    )


class _AnalysisEvidence(_Base):
    __tablename__ = "t_analysis_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_dimension_id: Mapped[str] = mapped_column(
        ForeignKey("t_analysis_dimensions.id")
    )
    segment_no: Mapped[int] = mapped_column(Integer, default=1)
    quote_encrypted: Mapped[str] = mapped_column(Text, default="q")
    analysis_dimension: Mapped[_AnalysisDimension] = relationship(
        back_populates="evidence"
    )


def _assert_not_business_db(url: str) -> None:
    assert "recruit" not in url.split("?")[0].rstrip("/").split("/")[-1]
    assert url.startswith("sqlite+aiosqlite://")


@pytest.fixture
async def async_session() -> AsyncSession:
    _assert_not_business_db(MEMORY_URL)
    engine = create_async_engine(MEMORY_URL)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_question_version_items(
    session: AsyncSession,
) -> tuple[_QuestionVersion, list[_QuestionItem]]:
    version = _QuestionVersion(id=str(uuid4()))
    session.add(version)
    await session.flush()
    assert version.id is not None
    item = _QuestionItem(id=str(uuid4()), question_version_id=version.id)
    session.add(item)
    await session.flush()
    # Prove fixture/table graph is healthy before touching the collection.
    loaded = (
        await session.execute(
            select(_QuestionItem).where(_QuestionItem.question_version_id == version.id)
        )
    ).scalar_one()
    assert loaded.id == item.id
    return version, [item]


async def _seed_analysis_collections(
    session: AsyncSession,
) -> tuple[_AnalysisVersion, list[_AnalysisDimension], list[_AnalysisEvidence]]:
    version = _AnalysisVersion(id=str(uuid4()))
    session.add(version)
    await session.flush()
    dim = _AnalysisDimension(
        id=str(uuid4()),
        analysis_version_id=version.id,
        score=3,
    )
    session.add(dim)
    await session.flush()
    ev = _AnalysisEvidence(
        id=str(uuid4()),
        analysis_dimension_id=dim.id,
    )
    session.add(ev)
    await session.flush()
    assert (
        await session.execute(
            select(_AnalysisEvidence).where(
                _AnalysisEvidence.analysis_dimension_id == dim.id
            )
        )
    ).scalar_one().id == ev.id
    return version, [dim], [ev]


@pytest.mark.asyncio
async def test_unloaded_collection_assign_raises_missing_greenlet(
    async_session: AsyncSession,
) -> None:
    version, items = await _seed_question_version_items(async_session)
    with pytest.raises(MissingGreenlet):
        version.items = items


@pytest.mark.asyncio
async def test_p1_question_generate_bare_items_assign_is_missing_greenlet_not_fixture(
    async_session: AsyncSession,
) -> None:
    """P1 path: version.items = items after FK-only item insert."""
    version, items = await _seed_question_version_items(async_session)
    with pytest.raises(MissingGreenlet) as exc_info:
        version.items = items
    assert exc_info.type is MissingGreenlet
    # Same objects: set_committed_value works → failure was bare assign, not schema.
    set_committed_value(version, "items", items)
    assert len(version.items) == 1
    assert version.items[0].id == items[0].id


@pytest.mark.asyncio
async def test_p2_manual_edit_bare_items_assign_is_missing_greenlet_not_fixture(
    async_session: AsyncSession,
) -> None:
    """P2 path: create_manual_question_version uses the same items attach."""
    version, items = await _seed_question_version_items(async_session)
    with pytest.raises(MissingGreenlet) as exc_info:
        version.items = items
    assert exc_info.type is MissingGreenlet
    set_committed_value(version, "items", items)
    assert len(version.items) == 1


@pytest.mark.asyncio
async def test_p3_analysis_bare_collection_assign_is_missing_greenlet_not_fixture(
    async_session: AsyncSession,
) -> None:
    """P3 path: dim.evidence = … then analysis_version.dimensions = dim_rows."""
    version, dims, evidence = await _seed_analysis_collections(async_session)
    dim = dims[0]
    with pytest.raises(MissingGreenlet) as exc_info:
        dim.evidence = evidence
    assert exc_info.type is MissingGreenlet
    set_committed_value(dim, "evidence", evidence)
    assert len(dim.evidence) == 1

    with pytest.raises(MissingGreenlet) as exc_info2:
        version.dimensions = dims
    assert exc_info2.type is MissingGreenlet
    set_committed_value(version, "dimensions", dims)
    assert len(version.dimensions) == 1
    assert len(version.dimensions[0].evidence) == 1


@pytest.mark.asyncio
async def test_persist_question_generation_result_async_session_survives_items_attach(
    async_session: AsyncSession,
) -> None:
    """Desired P1 behavior: attach items without MissingGreenlet (production must match)."""
    version, items = await _seed_question_version_items(async_session)
    # Production currently does bare assign — exercise the fixed API the service must use.
    set_committed_value(version, "items", items)
    assert len(version.items) == len(items)
    p1 = _slice_between(
        QUESTIONS_SRC,
        "async def persist_question_generation_result",
        "async def create_manual_question_version",
    )
    assert 'set_committed_value(version, "items"' in p1
    assert not re.search(r"(?m)^\s*version\.items\s*=", p1)


@pytest.mark.asyncio
async def test_create_manual_question_version_uses_set_committed_value_for_items() -> None:
    p2 = _slice_between(
        QUESTIONS_SRC,
        "async def create_manual_question_version",
        "async def confirm_question_set",
    )
    assert 'set_committed_value(version, "items"' in p2
    assert not re.search(r"(?m)^\s*version\.items\s*=", p2)


@pytest.mark.asyncio
async def test_persist_analysis_generation_result_async_session_survives_collection_attach(
    async_session: AsyncSession,
) -> None:
    version, dims, evidence = await _seed_analysis_collections(async_session)
    dim = dims[0]
    set_committed_value(dim, "evidence", evidence)
    set_committed_value(version, "dimensions", dims)
    assert len(version.dimensions) == 1
    assert len(dim.evidence) == 1
    p3 = _slice_between(
        ANALYSES_SRC,
        "async def persist_analysis_generation_result",
        "async def list_analysis_versions",
    )
    assert 'set_committed_value(dim_row, "evidence"' in p3
    assert 'set_committed_value(analysis_version, "dimensions"' in p3
    assert not re.search(r"(?m)^\s*dim_row\.evidence\s*=", p3)
    assert not re.search(r"(?m)^\s*analysis_version\.dimensions\s*=", p3)


def test_p1_p2_p3_source_has_no_sync_collection_assign() -> None:
    assert not re.search(r"(?m)^\s*version\.items\s*=", QUESTIONS_SRC)
    assert QUESTIONS_SRC.count('set_committed_value(version, "items"') >= 2
    assert not re.search(r"(?m)^\s*dim_row\.evidence\s*=", ANALYSES_SRC)
    assert not re.search(r"(?m)^\s*analysis_version\.dimensions\s*=", ANALYSES_SRC)
    assert 'set_committed_value(dim_row, "evidence"' in ANALYSES_SRC
    assert 'set_committed_value(analysis_version, "dimensions"' in ANALYSES_SRC


def _slice_between(
    src: str, start: str, end: str, *, allow_eof: bool = False
) -> str:
    i = src.index(start)
    try:
        j = src.index(end, i + len(start))
    except ValueError:
        if allow_eof:
            return src[i:]
        raise
    return src[i:j]
