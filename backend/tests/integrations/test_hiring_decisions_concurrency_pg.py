"""Real PostgreSQL concurrency for hiring decisions (014).

Requires TEST_DATABASE_URL pointing at recruit_test (never business `recruit`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.db.session import create_database_engine, create_session_factory
from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    AITask,
)
from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    Candidate,
    JobApplication,
)
from app.models.interview import (
    INTERVIEW_FORMAT_ONLINE,
    INTERVIEW_STATUS_COMPLETED,
    InterviewRound,
)
from app.models.interview_ai import (
    InterviewRoundAnalysis,
    InterviewRoundAnalysisVersion,
)
from app.models.interview_transcript import (
    TranscriptSourceMethod,
    TranscriptVersionStatus,
    TranscriptVersionType,
    InterviewTranscript,
    InterviewTranscriptVersion,
)
from app.models.job import JOB_STATUS_OPEN, VERSION_STATUS_PUBLISHED, Job, JobVersion
from app.models.resume import (
    HIRING_REASON_MEETS_ROLE_BAR,
    HIRING_RECOMMEND_HIRE,
    PIPELINE_INTERVIEWING,
    HiringDecision,
)
from app.services.audit import RequestContext
from app.services.hiring_decisions import (
    HiringConflictError,
    HiringDecisionRequestData,
    HiringStateError,
    create_hiring_decision,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
BUSINESS_DB_NAMES = frozenset({"recruit", "postgres", "template0", "template1"})


def _assert_safe_url(url: str) -> None:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    db_name = (parsed.path or "").lstrip("/")
    assert db_name not in BUSINESS_DB_NAMES, f"refusing business DB: {db_name}"
    assert db_name == "recruit_test" or db_name.endswith("_test")


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set",
)


@pytest.fixture
async def session_factory():
    _assert_safe_url(TEST_DATABASE_URL)
    engine = create_database_engine(TEST_DATABASE_URL)
    factory = create_session_factory(engine)
    async with factory() as session:
        table = await session.scalar(text("SELECT to_regclass('public.hiring_decisions')"))
        if table is None:
            await engine.dispose()
            pytest.skip("hiring_decisions missing; upgrade recruit_test to 014")
    yield factory
    await engine.dispose()


async def _seed_hiring_graph(session) -> dict[str, Any]:
    now = datetime.now(UTC)
    suffix = uuid4().hex[:10]
    user = User(
        username=f"hd{suffix}",
        username_normalized=f"hd{suffix}",
        display_name="HD Tester",
        password_hash="x",
        is_active=True,
        must_change_password=False,
        token_version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()

    job = Job(
        code=f"HD{suffix[:8].upper()}",
        status=JOB_STATUS_OPEN,
        name="Concurrency Job",
        department="QA",
        location="Remote",
        owner_user_id=user.id,
        owner_name="HD Tester",
        created_by=user.id,
        updated_by=user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.flush()

    job_version = JobVersion(
        job_id=job.id,
        version_label="V1.0",
        major=1,
        minor=0,
        status=VERSION_STATUS_PUBLISHED,
        raw_jd_text="jd",
        structured_jd={},
        score_dimensions=[],
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(job_version)
    await session.flush()
    job.current_version_id = job_version.id

    candidate = Candidate(
        name="并发候选人",
        phone=None,
        email=None,
        created_at=now,
        updated_at=now,
    )
    session.add(candidate)
    await session.flush()

    application = JobApplication(
        candidate_id=candidate.id,
        job_id=job.id,
        job_version_id=job_version.id,
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_INTERVIEWING,
        lock_version=1,
        interview_started=True,
        created_at=now,
        updated_at=now,
    )
    session.add(application)
    await session.flush()

    round_ = InterviewRound(
        application_id=application.id,
        job_version_id=job_version.id,
        name="R1",
        sequence_no=1,
        status=INTERVIEW_STATUS_COMPLETED,
        format=INTERVIEW_FORMAT_ONLINE,
        owner_id=user.id,
        version=1,
        created_by=user.id,
        updated_by=user.id,
        created_at=now,
        updated_at=now,
        finished_at=now,
    )
    session.add(round_)
    await session.flush()

    transcript = InterviewTranscript(
        interview_round_id=round_.id,
        version=1,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(transcript)
    await session.flush()

    tv = InterviewTranscriptVersion(
        transcript_id=transcript.id,
        version_type=TranscriptVersionType.CONFIRMED.value,
        version_no=1,
        version_label="C1",
        status=TranscriptVersionStatus.IMMUTABLE.value,
        raw_text_encrypted="cipher",
        source_method=TranscriptSourceMethod.PASTE.value,
        source_sha256="a" * 64,
        created_by=user.id,
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(tv)
    await session.flush()
    transcript.current_confirmed_version_id = tv.id
    transcript.original_version_id = tv.id

    task = AITask(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        status=AI_TASK_STATUS_SUCCEEDED,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_.id,
        created_by=user.id,
        input_snapshot={},
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()

    analysis = InterviewRoundAnalysis(
        interview_round_id=round_.id,
        created_at=now,
        updated_at=now,
    )
    session.add(analysis)
    await session.flush()

    version = InterviewRoundAnalysisVersion(
        analysis_id=analysis.id,
        version_no=1,
        version_label="A1",
        transcript_version_id=tv.id,
        job_version_id=job_version.id,
        ai_task_id=task.id,
        dimensions_snapshot=[],
        overall_score=Decimal("4.00"),
        overall_summary_encrypted="summary",
        created_by=user.id,
        created_at=now,
    )
    session.add(version)
    await session.flush()
    analysis.current_version_id = version.id

    await session.commit()
    return {
        "user_id": user.id,
        "application_id": application.id,
        "analysis_version_id": version.id,
        "lock_version": 1,
    }


@pytest.mark.asyncio
async def test_concurrent_same_lock_version_only_one_hiring_succeeds(
    session_factory,
) -> None:
    async with session_factory() as setup:
        seeded = await _seed_hiring_graph(setup)

    async def one_attempt(*, key: str | None) -> str:
        async with session_factory() as session:
            actor = (
                await session.execute(select(User).where(User.id == seeded["user_id"]))
            ).scalar_one()
            try:
                await create_hiring_decision(
                    session,
                    application_id=seeded["application_id"],
                    payload=HiringDecisionRequestData(
                        decision=HIRING_RECOMMEND_HIRE,
                        reason_code=HIRING_REASON_MEETS_ROLE_BAR,
                        analysis_version_id=seeded["analysis_version_id"],
                        lock_version=seeded["lock_version"],
                        idempotency_key=key,
                    ),
                    actor=actor,
                    request_context=RequestContext(request_id=f"c-{key or 'nokey'}"),
                )
                return "ok"
            except (HiringConflictError, HiringStateError):
                return "rejected"

    results = await asyncio.gather(
        one_attempt(key=None),
        one_attempt(key=None),
    )
    assert sorted(results) == ["ok", "rejected"]

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(HiringDecision)
            .where(HiringDecision.application_id == seeded["application_id"])
        )
        app = (
            await session.execute(
                select(JobApplication).where(
                    JobApplication.id == seeded["application_id"]
                )
            )
        ).scalar_one()
        assert count == 1
        assert app.lock_version == 2
        assert app.pipeline_status == "pending_offer"


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_returns_same_decision(
    session_factory,
) -> None:
    async with session_factory() as setup:
        seeded = await _seed_hiring_graph(setup)
    idem = f"idem-{uuid4().hex}"

    async def one_attempt() -> str:
        async with session_factory() as session:
            actor = (
                await session.execute(select(User).where(User.id == seeded["user_id"]))
            ).scalar_one()
            result = await create_hiring_decision(
                session,
                application_id=seeded["application_id"],
                payload=HiringDecisionRequestData(
                    decision=HIRING_RECOMMEND_HIRE,
                    reason_code=HIRING_REASON_MEETS_ROLE_BAR,
                    analysis_version_id=seeded["analysis_version_id"],
                    lock_version=seeded["lock_version"],
                    idempotency_key=idem,
                ),
                actor=actor,
                request_context=RequestContext(request_id="idem-race"),
            )
            return str(result.id)

    ids = await asyncio.gather(one_attempt(), one_attempt())
    assert ids[0] == ids[1]

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(HiringDecision)
            .where(
                HiringDecision.application_id == seeded["application_id"],
                HiringDecision.idempotency_key == idem,
            )
        )
        assert count == 1
