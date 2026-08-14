"""PostgreSQL integration tests for 008 attempt audit.

Set TEST_DATABASE_URL to a DB already migrated to 008_stage6_attempt_audit.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.db.session import create_database_engine, create_session_factory
from app.models.ai_task import (
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_JOB,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_JD_PARSE,
    AITask,
    AITaskAttempt,
)
from app.services.ai_providers.base import ProviderOutcome

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set",
)


@pytest.fixture
async def session_factory():
    engine = create_database_engine(TEST_DATABASE_URL)
    factory = create_session_factory(engine)
    async with factory() as session:
        cols = {
            r[0]
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name='ai_tasks'
                        """
                    )
                )
            ).all()
        }
        if "retry_cycle_no" not in cols:
            await engine.dispose()
            pytest.skip("test DB not upgraded to 008 yet")
    yield factory
    await engine.dispose()


async def _noop_after(session, *, task, outcome=None):
    return None


async def _noop_after_fail(session, *, task):
    return None


async def _insert_pending_task(session, **overrides) -> AITask:
    now = datetime.now(UTC)
    vals = {
        "task_type": TASK_TYPE_JD_PARSE,
        "status": AI_TASK_STATUS_PENDING,
        "business_type": BUSINESS_TYPE_JOB,
        "business_id": uuid4(),
        "input_snapshot": {
            "raw_jd_text": "岗位职责\n- 写代码\n任职要求\n- 本科",
            "job_title": "工程师",
        },
        "attempt_count": 0,
        "retry_cycle_no": 0,
        "cycle_attempt_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    vals.update(overrides)
    task = AITask(**vals)
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest.mark.asyncio
async def test_worker_success_creates_exactly_one_attempt(
    session_factory, monkeypatch
) -> None:
    from app.workers import ai_tasks as worker

    async def fake_provider(**kwargs):
        return ProviderOutcome(
            ok=True,
            result={
                "responsibilities": ["写代码"],
                "requirements": ["本科"],
                "must_have": [],
                "nice_to_have": [],
                "skills": [],
            },
            raw_request={"provider": "mock"},
            raw_response={
                "workflow_run_id": "run-ok-1",
                "task_id": "req-ok-1",
                "data": {"id": "run-ok-1", "status": "succeeded", "outputs": {}},
            },
            http_status=200,
            provider_run_id="run-ok-1",
            request_id="req-ok-1",
        )

    monkeypatch.setattr(worker, "_run_provider", fake_provider)
    monkeypatch.setattr(worker, "_after_task_success", _noop_after)
    monkeypatch.setattr(worker, "_after_task_failure", _noop_after_fail)

    async with session_factory() as session:
        task = await _insert_pending_task(session)
        task_id = task.id
        result = await worker._handle_process(session, task_id)
        assert result["status"] == AI_TASK_STATUS_SUCCEEDED
        rows = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.task_id == task_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == AI_TASK_STATUS_SUCCEEDED
        assert rows[0].finished_at is not None
        assert rows[0].attempt_no == 1
        assert rows[0].cycle_attempt_no == 1
        assert rows[0].retry_cycle_no == 0
        assert rows[0].provider_run_id == "run-ok-1"
        assert rows[0].request_id == "req-ok-1"
        assert rows[0].raw_response is not None
        assert not any(r.status == AI_TASK_STATUS_RUNNING for r in rows)
        refreshed = (
            await session.execute(select(AITask).where(AITask.id == task_id))
        ).scalar_one()
        assert refreshed.attempt_count == 1
        assert refreshed.cycle_attempt_count == 1


@pytest.mark.asyncio
async def test_worker_failure_creates_exactly_one_attempt(
    session_factory, monkeypatch
) -> None:
    from app.workers import ai_tasks as worker

    async def fake_provider(**kwargs):
        return ProviderOutcome(
            ok=False,
            raw_request={"provider": "mock"},
            raw_response={"error": "boom"},
            error_code="provider_error",
            error_message="boom",
            error_category="non_retryable",
            http_status=500,
        )

    monkeypatch.setattr(worker, "_run_provider", fake_provider)
    monkeypatch.setattr(worker, "_after_task_failure", _noop_after_fail)

    async with session_factory() as session:
        task = await _insert_pending_task(session)
        result = await worker._handle_process(session, task.id)
        assert result["status"] == AI_TASK_STATUS_FAILED
        rows = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.task_id == task.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == AI_TASK_STATUS_FAILED
        assert not any(r.status == AI_TASK_STATUS_RUNNING for r in rows)


@pytest.mark.asyncio
async def test_auto_retry_global_attempt_nos_1_2_3(
    session_factory, monkeypatch
) -> None:
    from app.workers import ai_tasks as worker

    calls = {"n": 0}

    async def fake_provider(**kwargs):
        calls["n"] += 1
        return ProviderOutcome(
            ok=False,
            raw_request={"provider": "mock"},
            raw_response={"error": "flaky"},
            error_code="provider_5xx",
            error_message="flaky",
            error_category=ERROR_CATEGORY_RETRYABLE,
            http_status=502,
            provider_run_id=f"run-{calls['n']}",
            request_id=f"req-{calls['n']}",
        )

    enqueued: list[tuple] = []

    def fake_apply_async(*, args, countdown):
        enqueued.append((args[0], countdown))

    monkeypatch.setattr(worker, "_run_provider", fake_provider)
    monkeypatch.setattr(worker, "_after_task_failure", _noop_after_fail)
    monkeypatch.setattr(worker.process_ai_task, "apply_async", fake_apply_async)

    async with session_factory() as session:
        task = await _insert_pending_task(session)
        task_id = task.id
        for expected_no in (1, 2, 3):
            t = (
                await session.execute(select(AITask).where(AITask.id == task_id))
            ).scalar_one()
            if t.status != AI_TASK_STATUS_PENDING:
                t.status = AI_TASK_STATUS_PENDING
                await session.commit()
            result = await worker._handle_process(session, task_id)
            rows = (
                await session.execute(
                    select(AITaskAttempt)
                    .where(AITaskAttempt.task_id == task_id)
                    .order_by(AITaskAttempt.attempt_no)
                )
            ).scalars().all()
            assert len(rows) == expected_no
            assert [r.attempt_no for r in rows] == list(range(1, expected_no + 1))
            assert [r.cycle_attempt_no for r in rows] == list(
                range(1, expected_no + 1)
            )
            if expected_no < 3:
                assert result["status"] == AI_TASK_STATUS_PENDING
            else:
                assert result["status"] == AI_TASK_STATUS_FAILED


@pytest.mark.asyncio
async def test_manual_retry_increments_cycle_keeps_global_count(
    session_factory, monkeypatch
) -> None:
    from app.models import User
    from app.services import ai_tasks as svc
    from app.services.audit import RequestContext
    from app.services.ai_tasks import AITaskStateError

    monkeypatch.setattr(svc, "enqueue_ai_task", lambda *a, **k: None)

    async with session_factory() as session:
        now = datetime.now(UTC)
        uname = f"t{uuid4().hex[:10]}"
        user = User(
            username=uname,
            username_normalized=uname,
            display_name="tester",
            password_hash="x",
            is_active=True,
            must_change_password=False,
            token_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()

        task = AITask(
            task_type=TASK_TYPE_JD_PARSE,
            status=AI_TASK_STATUS_FAILED,
            business_type=BUSINESS_TYPE_JOB,
            business_id=uuid4(),
            created_by=user.id,
            input_snapshot={"raw_jd_text": "x", "keep": True},
            attempt_count=3,
            retry_cycle_no=0,
            cycle_attempt_count=3,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()
        for i in range(1, 4):
            session.add(
                AITaskAttempt(
                    task_id=task.id,
                    attempt_no=i,
                    retry_cycle_no=0,
                    cycle_attempt_no=i,
                    status=AI_TASK_STATUS_FAILED,
                    started_at=now,
                    finished_at=now,
                    created_at=now,
                )
            )
        await session.commit()
        snap = dict(task.input_snapshot)
        task_id = task.id
        prior_count = task.attempt_count
        ctx = RequestContext(request_id="test")

        out = await svc.retry_ai_task(
            session, task_id=task_id, actor=user, request_context=ctx
        )
        assert out.status == AI_TASK_STATUS_PENDING
        refreshed = (
            await session.execute(select(AITask).where(AITask.id == task_id))
        ).scalar_one()
        assert refreshed.attempt_count == prior_count
        assert refreshed.retry_cycle_no == 1
        assert refreshed.cycle_attempt_count == 0
        assert refreshed.input_snapshot == snap
        assert refreshed.id == task_id

        with pytest.raises(AITaskStateError):
            await svc.retry_ai_task(
                session, task_id=task_id, actor=user, request_context=ctx
            )


@pytest.mark.asyncio
async def test_manual_retry_then_worker_global_4_cycle_1(
    session_factory, monkeypatch
) -> None:
    from app.workers import ai_tasks as worker

    async def fake_provider(**kwargs):
        return ProviderOutcome(
            ok=True,
            result={
                "responsibilities": ["a"],
                "requirements": [],
                "must_have": [],
                "nice_to_have": [],
                "skills": [],
            },
            raw_request={"provider": "mock"},
            raw_response={
                "workflow_run_id": "run-4",
                "task_id": "req-4",
                "data": {"id": "run-4"},
            },
            http_status=200,
            provider_run_id="run-4",
            request_id="req-4",
        )

    monkeypatch.setattr(worker, "_run_provider", fake_provider)
    monkeypatch.setattr(worker, "_after_task_success", _noop_after)
    monkeypatch.setattr(worker, "_after_task_failure", _noop_after_fail)

    async with session_factory() as session:
        now = datetime.now(UTC)
        task = AITask(
            task_type=TASK_TYPE_JD_PARSE,
            status=AI_TASK_STATUS_PENDING,
            business_type=BUSINESS_TYPE_JOB,
            business_id=uuid4(),
            input_snapshot={"raw_jd_text": "岗位职责\n- a\n任职要求\n- b"},
            attempt_count=3,
            retry_cycle_no=1,
            cycle_attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()
        for i in range(1, 4):
            session.add(
                AITaskAttempt(
                    task_id=task.id,
                    attempt_no=i,
                    retry_cycle_no=0,
                    cycle_attempt_no=i,
                    status=AI_TASK_STATUS_FAILED,
                    started_at=now,
                    finished_at=now,
                    created_at=now,
                )
            )
        await session.commit()
        result = await worker._handle_process(session, task.id)
        assert result["status"] == AI_TASK_STATUS_SUCCEEDED
        rows = (
            await session.execute(
                select(AITaskAttempt)
                .where(AITaskAttempt.task_id == task.id)
                .order_by(AITaskAttempt.attempt_no)
            )
        ).scalars().all()
        assert len(rows) == 4
        last = rows[-1]
        assert last.attempt_no == 4
        assert last.cycle_attempt_no == 1
        assert last.retry_cycle_no == 1


@pytest.mark.asyncio
async def test_concurrent_manual_retry_only_one_succeeds(
    session_factory, monkeypatch
) -> None:
    import asyncio

    from app.models import User
    from app.services import ai_tasks as svc
    from app.services.ai_tasks import AITaskStateError
    from app.services.audit import RequestContext

    monkeypatch.setattr(svc, "enqueue_ai_task", lambda *a, **k: None)

    async with session_factory() as setup:
        now = datetime.now(UTC)
        uname = f"c{uuid4().hex[:10]}"
        user = User(
            username=uname,
            username_normalized=uname,
            display_name="tester",
            password_hash="x",
            is_active=True,
            must_change_password=False,
            token_version=1,
            created_at=now,
            updated_at=now,
        )
        setup.add(user)
        await setup.flush()
        task = AITask(
            task_type=TASK_TYPE_JD_PARSE,
            status=AI_TASK_STATUS_FAILED,
            business_type=BUSINESS_TYPE_JOB,
            business_id=uuid4(),
            created_by=user.id,
            input_snapshot={"raw_jd_text": "x"},
            attempt_count=1,
            retry_cycle_no=0,
            cycle_attempt_count=1,
            created_at=now,
            updated_at=now,
        )
        setup.add(task)
        await setup.commit()
        task_id = task.id
        user_id = user.id

    async def one_retry() -> str:
        async with session_factory() as session:
            actor = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            try:
                await svc.retry_ai_task(
                    session,
                    task_id=task_id,
                    actor=actor,
                    request_context=RequestContext(request_id="c"),
                )
                return "ok"
            except AITaskStateError:
                return "rejected"

    results = await asyncio.gather(one_retry(), one_retry())
    assert sorted(results) == ["ok", "rejected"]
    async with session_factory() as session:
        t = (
            await session.execute(select(AITask).where(AITask.id == task_id))
        ).scalar_one()
        assert t.status == AI_TASK_STATUS_PENDING
        assert t.retry_cycle_no == 1
        assert t.attempt_count == 1


@pytest.mark.asyncio
async def test_unique_task_attempt_constraint(session_factory) -> None:
    async with session_factory() as session:
        task = await _insert_pending_task(session, status=AI_TASK_STATUS_SUCCEEDED)
        now = datetime.now(UTC)
        session.add(
            AITaskAttempt(
                task_id=task.id,
                attempt_no=1,
                retry_cycle_no=0,
                cycle_attempt_no=1,
                status=AI_TASK_STATUS_SUCCEEDED,
                started_at=now,
                finished_at=now,
                created_at=now,
            )
        )
        await session.commit()
        session.add(
            AITaskAttempt(
                task_id=task.id,
                attempt_no=1,
                retry_cycle_no=0,
                cycle_attempt_no=1,
                status=AI_TASK_STATUS_RUNNING,
                started_at=now,
                created_at=now,
            )
        )
        with pytest.raises(Exception):  # noqa: BLE001 — IntegrityError
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_purge_clears_raw_keeps_ids(session_factory) -> None:
    async with session_factory() as session:
        now = datetime.now(UTC)
        old = datetime(2020, 1, 1, tzinfo=UTC)
        task = AITask(
            task_type=TASK_TYPE_JD_PARSE,
            status=AI_TASK_STATUS_SUCCEEDED,
            business_type=BUSINESS_TYPE_JOB,
            business_id=uuid4(),
            input_snapshot={},
            attempt_count=1,
            retry_cycle_no=0,
            cycle_attempt_count=1,
            raw_request={"x": 1},
            raw_response={"workflow_run_id": "keep-me"},
            created_at=old,
            updated_at=old,
        )
        session.add(task)
        await session.flush()
        attempt = AITaskAttempt(
            task_id=task.id,
            attempt_no=1,
            retry_cycle_no=0,
            cycle_attempt_no=1,
            status=AI_TASK_STATUS_SUCCEEDED,
            started_at=old,
            finished_at=old,
            created_at=old,
            provider_run_id="run-keep",
            request_id="req-keep",
            raw_response={"workflow_run_id": "run-keep", "task_id": "req-keep"},
        )
        session.add(attempt)
        await session.commit()
        attempt_id = attempt.id
        task_id = task.id

        cutoff = datetime.now(UTC) - timedelta(days=1)
        tasks = (
            await session.execute(
                select(AITask).where(
                    AITask.id == task_id,
                    AITask.raw_purged_at.is_(None),
                    AITask.created_at < cutoff,
                )
            )
        ).scalars().all()
        assert tasks
        now2 = datetime.now(UTC)
        for t in tasks:
            t.raw_request = None
            t.raw_response = None
            t.raw_purged_at = now2
            for a in (
                await session.execute(
                    select(AITaskAttempt).where(AITaskAttempt.task_id == t.id)
                )
            ).scalars().all():
                if a.raw_response is not None:
                    a.raw_response = None
                a.response_purged_at = now2
        await session.commit()
        a = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
            )
        ).scalar_one()
        assert a.raw_response is None
        assert a.response_purged_at is not None
        assert a.provider_run_id == "run-keep"
        assert a.request_id == "req-keep"
