"""Admin mark-stale-failed service-layer tests (Task 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.ai_task import (
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
)
from app.repositories.ai_tasks import AITaskNotFoundError
from app.services.ai_tasks import AITaskStateError
from app.services.audit import RequestContext


def test_mark_stale_failed_schema_only_expected_updated_at() -> None:
    from app.schemas.ai_task import MarkStaleFailedAITaskIn

    assert set(MarkStaleFailedAITaskIn.model_fields) == {"expected_updated_at"}
    ts = datetime.now(UTC)
    ok = MarkStaleFailedAITaskIn(expected_updated_at=ts)
    assert ok.expected_updated_at == ts
    with pytest.raises(ValidationError):
        MarkStaleFailedAITaskIn.model_validate(
            {"expected_updated_at": ts.isoformat(), "reason": "should-reject"}
        )


def test_mark_stale_failed_out_fields_minimal() -> None:
    from app.schemas.ai_task import MarkStaleFailedAITaskOut

    assert set(MarkStaleFailedAITaskOut.model_fields) == {
        "id",
        "status",
        "error_code",
        "updated_at",
        "finished_at",
    }
    assert "task_type" not in MarkStaleFailedAITaskOut.model_fields
    assert "attempts" not in MarkStaleFailedAITaskOut.model_fields


def _actor():
    return SimpleNamespace(id=uuid4())


def _ctx() -> RequestContext:
    return RequestContext(request_id="test-req", ip_address="127.0.0.1")


def _running_task(*, age: timedelta, task_type: str = "RESUME_SCORE"):
    now = datetime.now(UTC)
    updated = now - age
    task = MagicMock()
    task.id = uuid4()
    task.status = AI_TASK_STATUS_RUNNING
    task.task_type = task_type
    task.updated_at = updated
    task.finished_at = None
    task.error_code = None
    task.error_message = None
    task.error_category = None
    attempt = MagicMock()
    attempt.id = uuid4()
    attempt.status = AI_TASK_STATUS_RUNNING
    attempt.error_message = None
    attempt.error_category = None
    task.attempts = [attempt]
    return task, updated


@pytest.mark.asyncio
async def test_mark_stale_failed_rejects_age_under_5_minutes(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=4))
    session = AsyncMock()
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "enqueue_ai_task", MagicMock())
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", MagicMock())

    with pytest.raises(AITaskStateError):
        await svc.mark_stale_failed_ai_task(
            session,
            task_id=task.id,
            expected_updated_at=updated,
            actor=_actor(),
            request_context=_ctx(),
        )
    assert task.status == AI_TASK_STATUS_RUNNING
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_stale_failed_rejects_expected_updated_at_mismatch(
    monkeypatch,
) -> None:
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=10))
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.rowcount = 0
    session.execute = AsyncMock(return_value=execute_result)
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "record_audit", AsyncMock())

    with pytest.raises(AITaskStateError):
        await svc.mark_stale_failed_ai_task(
            session,
            task_id=task.id,
            expected_updated_at=updated - timedelta(seconds=1),
            actor=_actor(),
            request_context=_ctx(),
        )
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_stale_failed_rejects_non_running(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    for status in (
        AI_TASK_STATUS_PENDING,
        AI_TASK_STATUS_FAILED,
        AI_TASK_STATUS_SUCCEEDED,
    ):
        task, updated = _running_task(age=timedelta(minutes=10))
        task.status = status
        session = AsyncMock()
        execute_result = MagicMock()
        execute_result.rowcount = 0
        session.execute = AsyncMock(return_value=execute_result)
        monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
        with pytest.raises(AITaskStateError):
            await svc.mark_stale_failed_ai_task(
                session,
                task_id=task.id,
                expected_updated_at=updated,
                actor=_actor(),
                request_context=_ctx(),
            )


@pytest.mark.asyncio
async def test_mark_stale_failed_not_found(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    session = AsyncMock()
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=None))
    with pytest.raises(AITaskNotFoundError):
        await svc.mark_stale_failed_ai_task(
            session,
            task_id=uuid4(),
            expected_updated_at=datetime.now(UTC) - timedelta(minutes=10),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_mark_stale_failed_success_updates_task_and_running_attempt(
    monkeypatch,
) -> None:
    from app.schemas.ai_task import MarkStaleFailedAITaskOut
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=10))
    attempt = task.attempts[0]
    session = AsyncMock()
    update_task = MagicMock()
    update_task.rowcount = 1
    update_attempt = MagicMock()
    update_attempt.rowcount = 1
    session.execute = AsyncMock(side_effect=[update_task, update_attempt])

    async def fake_get(_session, task_id, **_k):
        if task.status == AI_TASK_STATUS_RUNNING and update_task.rowcount == 1:
            # After successful update path, service reloads — simulate recovered row.
            recovered = MagicMock()
            recovered.id = task.id
            recovered.status = AI_TASK_STATUS_FAILED
            recovered.error_code = "stale_running_recovered"
            recovered.error_category = "non_retryable"
            recovered.error_message = "stale running recovered"
            recovered.updated_at = datetime.now(UTC)
            recovered.finished_at = recovered.updated_at
            recovered.task_type = task.task_type
            recovered.attempts = [attempt]
            return recovered
        return task

    gets = {"n": 0}

    async def get_by_id(_session, task_id, **_k):
        gets["n"] += 1
        if gets["n"] == 1:
            return task
        recovered = MagicMock()
        recovered.id = task.id
        recovered.status = AI_TASK_STATUS_FAILED
        recovered.error_code = "stale_running_recovered"
        recovered.error_category = "non_retryable"
        recovered.error_message = "stale running recovered"
        recovered.updated_at = datetime.now(UTC)
        recovered.finished_at = recovered.updated_at
        recovered.task_type = task.task_type
        return recovered

    monkeypatch.setattr(svc, "get_ai_task_by_id", get_by_id)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "enqueue_ai_task", MagicMock())
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", AsyncMock())

    out = await svc.mark_stale_failed_ai_task(
        session,
        task_id=task.id,
        expected_updated_at=updated,
        actor=_actor(),
        request_context=_ctx(),
    )
    assert isinstance(out, MarkStaleFailedAITaskOut)
    assert out.status == AI_TASK_STATUS_FAILED
    assert out.error_code == "stale_running_recovered"
    assert out.finished_at is not None
    assert session.execute.await_count >= 2
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_mark_stale_failed_conditional_update_race(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=10))
    session = AsyncMock()
    first = MagicMock()
    first.rowcount = 1
    second = MagicMock()
    second.rowcount = 0
    attempt_upd = MagicMock()
    attempt_upd.rowcount = 1
    session.execute = AsyncMock(side_effect=[first, attempt_upd, second])

    call = {"n": 0}

    async def get_by_id(_session, task_id, **_k):
        call["n"] += 1
        if call["n"] <= 2:
            if call["n"] == 2:
                recovered = MagicMock()
                recovered.id = task.id
                recovered.status = AI_TASK_STATUS_FAILED
                recovered.error_code = "stale_running_recovered"
                recovered.updated_at = datetime.now(UTC)
                recovered.finished_at = recovered.updated_at
                recovered.task_type = task.task_type
                return recovered
            return task
        return task

    monkeypatch.setattr(svc, "get_ai_task_by_id", get_by_id)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "enqueue_ai_task", MagicMock())
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", MagicMock())

    out = await svc.mark_stale_failed_ai_task(
        session,
        task_id=task.id,
        expected_updated_at=updated,
        actor=_actor(),
        request_context=_ctx(),
    )
    assert out.status == AI_TASK_STATUS_FAILED

    with pytest.raises(AITaskStateError):
        await svc.mark_stale_failed_ai_task(
            session,
            task_id=task.id,
            expected_updated_at=updated,
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_mark_stale_failed_audits_without_reason(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=10))
    session = AsyncMock()
    upd = MagicMock()
    upd.rowcount = 1
    session.execute = AsyncMock(return_value=upd)
    audits: list[dict] = []

    async def fake_audit(*_a, **kwargs):
        audits.append(kwargs)

    async def get_by_id(_session, task_id, **_k):
        if audits:
            recovered = MagicMock()
            recovered.id = task.id
            recovered.status = AI_TASK_STATUS_FAILED
            recovered.error_code = "stale_running_recovered"
            recovered.updated_at = datetime.now(UTC)
            recovered.finished_at = recovered.updated_at
            recovered.task_type = task.task_type
            return recovered
        return task

    monkeypatch.setattr(svc, "get_ai_task_by_id", get_by_id)
    monkeypatch.setattr(svc, "record_audit", fake_audit)
    monkeypatch.setattr(svc, "enqueue_ai_task", MagicMock())
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", MagicMock())

    await svc.mark_stale_failed_ai_task(
        session,
        task_id=task.id,
        expected_updated_at=updated,
        actor=_actor(),
        request_context=_ctx(),
    )
    assert len(audits) == 1
    entry = audits[0]
    assert entry["action"] == "ai_task.stale_running_recovered"
    assert entry["result"] == "success"
    assert entry["resource_type"] == "ai_task"
    assert entry["resource_id"] == str(task.id)
    allowed = {
        "ai_task_id",
        "task_type",
        "previous_status",
        "new_status",
        "expected_updated_at",
        "error_code",
    }
    assert set(entry["changes"]) <= allowed
    assert entry["changes"]["error_code"] == "stale_running_recovered"
    assert "reason" not in entry["changes"]


@pytest.mark.asyncio
async def test_mark_stale_failed_never_enqueues(monkeypatch) -> None:
    from app.services import ai_tasks as svc

    task, updated = _running_task(age=timedelta(minutes=10))
    session = AsyncMock()
    upd = MagicMock()
    upd.rowcount = 1
    session.execute = AsyncMock(return_value=upd)
    enqueue = MagicMock()
    enqueue_sens = MagicMock()
    apply_async = MagicMock()

    async def get_by_id(_session, task_id, **_k):
        recovered = MagicMock()
        recovered.id = task.id
        recovered.status = AI_TASK_STATUS_FAILED
        recovered.error_code = "stale_running_recovered"
        recovered.updated_at = datetime.now(UTC)
        recovered.finished_at = recovered.updated_at
        recovered.task_type = task.task_type
        if session.execute.await_count == 0:
            return task
        return recovered if session.commit.await_count else task

    call = {"n": 0}

    async def get_by_id2(_session, task_id, **_k):
        call["n"] += 1
        if call["n"] == 1:
            return task
        recovered = MagicMock()
        recovered.id = task.id
        recovered.status = AI_TASK_STATUS_FAILED
        recovered.error_code = "stale_running_recovered"
        recovered.updated_at = datetime.now(UTC)
        recovered.finished_at = recovered.updated_at
        recovered.task_type = task.task_type
        return recovered

    monkeypatch.setattr(svc, "get_ai_task_by_id", get_by_id2)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "enqueue_ai_task", enqueue)
    monkeypatch.setattr(svc, "enqueue_sensitive_question_task", enqueue_sens)
    monkeypatch.setattr(
        "app.workers.ai_tasks.process_ai_task.apply_async", apply_async, raising=False
    )
    monkeypatch.setattr(
        "app.workers.ai_tasks.process_sensitive_ai_task.apply_async",
        apply_async,
        raising=False,
    )

    await svc.mark_stale_failed_ai_task(
        session,
        task_id=task.id,
        expected_updated_at=updated,
        actor=_actor(),
        request_context=_ctx(),
    )
    enqueue.assert_not_called()
    enqueue_sens.assert_not_called()
    apply_async.assert_not_called()
