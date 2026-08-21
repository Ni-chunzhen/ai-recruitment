"""Real PostgreSQL concurrency for comprehensive analysis generate.

Requires TEST_DATABASE_URL → recruit_test (never business `recruit`).
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
    BUSINESS_TYPE_APPLICATION,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
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
    TranscriptCompletionMode,
    TranscriptSourceMethod,
    TranscriptVersionStatus,
    TranscriptVersionType,
    InterviewTranscript,
    InterviewTranscriptVersion,
)
from app.models.job import JOB_STATUS_OPEN, VERSION_STATUS_PUBLISHED, Job, JobVersion
from app.models.resume import PIPELINE_INTERVIEWING
from app.services.audit import RequestContext
from app.services.comprehensive_analyses import (
    request_comprehensive_analysis_generation,
)
from app.services.interviews import InterviewConflictError

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
        table = await session.scalar(
            text("SELECT to_regclass('public.application_comprehensive_analyses')")
        )
        if table is None:
            await engine.dispose()
            pytest.skip(
                "application_comprehensive_analyses missing; upgrade recruit_test to 015"
            )
    yield factory
    await engine.dispose()


async def _seed_comprehensive_ready_graph(session) -> dict[str, Any]:
    now = datetime.now(UTC)
    suffix = uuid4().hex[:10]
    user = User(
        username=f"ca{suffix}",
        username_normalized=f"ca{suffix}",
        display_name="CA Tester",
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
        code=f"CA{suffix[:8].upper()}",
        status=JOB_STATUS_OPEN,
        name="Comprehensive Concurrency Job",
        department="QA",
        location="Remote",
        owner_user_id=user.id,
        owner_name="CA Tester",
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
        name="综合并发候选人",
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
        transcript_completion_mode=TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value,
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
        source_sha256="b" * 64,
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
    }


@pytest.mark.asyncio
async def test_concurrent_different_idempotency_keys_create_one_inflight_task(
    session_factory, monkeypatch
) -> None:
    """Force a wide race window: first create blocks before commit visibility.

    Without application FOR UPDATE, the second session can pass inflight checks
    while the first task is still uncommitted and create a duplicate.
    With FOR UPDATE, the second session blocks on the application row until the
    first commits, then reuses the single inflight task.
    """
    async with session_factory() as setup:
        seeded = await _seed_comprehensive_ready_graph(setup)

    from app.services import comprehensive_analyses as svc

    real_add = svc.add_ai_task
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    add_calls = 0

    async def gated_add(session, task):
        nonlocal add_calls
        add_calls += 1
        if add_calls == 1:
            first_entered.set()
            await release_first.wait()
        return await real_add(session, task)

    monkeypatch.setattr(svc, "add_ai_task", gated_add)

    async def one_attempt(*, key: str) -> tuple[str, str | None]:
        async with session_factory() as session:
            actor = (
                await session.execute(select(User).where(User.id == seeded["user_id"]))
            ).scalar_one()
            actor.permission_codes = ["recruitment.manage"]
            try:
                task = await request_comprehensive_analysis_generation(
                    session,
                    application_id=seeded["application_id"],
                    idempotency_key=key,
                    actor=actor,
                    request_context=RequestContext(request_id=f"ca-{key}"),
                )
                await session.commit()
                return ("ok", str(task.id))
            except InterviewConflictError:
                await session.rollback()
                return ("conflict", None)

    key_a = f"idem-a-{uuid4().hex}"
    key_b = f"idem-b-{uuid4().hex}"
    task_a = asyncio.create_task(one_attempt(key=key_a))
    await asyncio.wait_for(first_entered.wait(), timeout=10)
    task_b = asyncio.create_task(one_attempt(key=key_b))
    # Give B time to either block on row lock (GREEN) or race past inflight (RED).
    await asyncio.sleep(0.3)
    release_first.set()
    results = await asyncio.gather(task_a, task_b)

    outcomes = sorted(item[0] for item in results)
    ok_ids = {item[1] for item in results if item[0] == "ok" and item[1]}

    assert outcomes in (
        ["conflict", "ok"],
        ["ok", "ok"],
    ), f"unexpected outcomes: {results}"
    if outcomes == ["ok", "ok"]:
        assert len(ok_ids) == 1, f"reuse must share one task id, got {ok_ids}"
    else:
        assert len(ok_ids) == 1

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AITask)
            .where(
                AITask.business_type == BUSINESS_TYPE_APPLICATION,
                AITask.business_id == seeded["application_id"],
                AITask.task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
            )
        )
        assert count == 1
