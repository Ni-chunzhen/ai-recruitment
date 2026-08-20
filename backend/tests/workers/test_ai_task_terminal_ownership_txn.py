"""Real AsyncSession ownership race: late worker must not overwrite admin failed."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.db.base import Base
from app.models.ai_task import (
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_JOB,
    TASK_TYPE_JD_PARSE,
    AITask,
    AITaskAttempt,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


MEMORY_URL = "sqlite+aiosqlite:///:memory:"


def _assert_safe_url(url: str) -> None:
    assert url.startswith("sqlite+aiosqlite://")
    assert "/recruit" not in url


@pytest.mark.asyncio
async def test_late_worker_does_not_overwrite_admin_failed_real_txn() -> None:
    from app.workers import ai_tasks as worker

    _assert_safe_url(MEMORY_URL)
    engine = create_async_engine(MEMORY_URL)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=[AITask.__table__, AITaskAttempt.__table__]
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    task_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)

    async with factory() as s1:
        s1.add(
            AITask(
                id=task_id,
                task_type=TASK_TYPE_JD_PARSE,
                status=AI_TASK_STATUS_RUNNING,
                business_type=BUSINESS_TYPE_JOB,
                business_id=uuid4(),
                input_snapshot={},
                updated_at=now,
            )
        )
        await s1.flush()
        s1.add(
            AITaskAttempt(
                id=attempt_id,
                task_id=task_id,
                attempt_no=1,
                status=AI_TASK_STATUS_RUNNING,
                started_at=now,
            )
        )
        await s1.commit()

    async with factory() as admin:
        task = (
            await admin.execute(select(AITask).where(AITask.id == task_id).with_for_update())
        ).scalar_one()
        attempt = (
            await admin.execute(
                select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
            )
        ).scalar_one()
        task.status = AI_TASK_STATUS_FAILED
        task.error_code = "stale_running_recovered"
        task.error_category = "non_retryable"
        task.error_message = "stale running recovered"
        task.finished_at = datetime.now(UTC)
        attempt.status = AI_TASK_STATUS_FAILED
        await admin.commit()

    async with factory() as late_worker:
        owned = await worker._reassert_running_ownership_for_terminal(
            late_worker, task_id=task_id, attempt_id=attempt_id
        )
        assert owned is None
        # Simulate a buggy late write that ignores the helper — must not be what
        # production does; assert helper gate and final DB stay admin-failed.
        row = (
            await late_worker.execute(select(AITask).where(AITask.id == task_id))
        ).scalar_one()
        assert row.status == AI_TASK_STATUS_FAILED
        assert row.error_code == "stale_running_recovered"
        assert row.status != AI_TASK_STATUS_SUCCEEDED

    await engine.dispose()
