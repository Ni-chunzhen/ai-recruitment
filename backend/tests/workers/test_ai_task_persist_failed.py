"""Worker persist_failed + terminal ownership (Task 2)."""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import MissingGreenlet

from app.models.ai_task import (
    AI_TASK_STATUS_CANCELLED,
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_OUTPUT_INVALID,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
)
from app.services.ai_providers.base import ProviderOutcome
from app.services.interview_ai_validation import AIOutputValidationError
from tests.workers.test_interview_ai_worker import (
    FakeWorkerSession,
    _bind_stage8_mocks,
    _frozen_question_snapshot,
    _make_task,
    _question_result,
)

SECRET_FRAGMENT = "SECRET_RESUME_BODY"
WORKER_SRC = (
    Path(__file__).resolve().parents[2] / "app" / "workers" / "ai_tasks.py"
).read_text(encoding="utf-8")


def _ok_outcome() -> ProviderOutcome:
    return ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={"provider": "mock"},
        raw_response={"outputs": _question_result()},
        http_status=200,
    )


@pytest.mark.asyncio
async def test_after_task_success_missing_greenlet_marks_failed_persist_failed(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)

    async def persist_boom(*_a, **_k):
        raise MissingGreenlet()

    enqueue = AsyncMock()
    monkeypatch.setattr(worker, "_enqueue_retry_for_task", enqueue)
    await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=_ok_outcome(), persist=persist_boom
    )

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_FAILED
    assert task.status == AI_TASK_STATUS_FAILED
    assert task.error_code == "persist_failed"
    assert task.error_category == "non_retryable"
    attempt = session.attempts[0]
    assert attempt.status == AI_TASK_STATUS_FAILED
    assert attempt.error_category == "non_retryable"
    assert "MissingGreenlet" in (task.error_message or "")
    assert SECRET_FRAGMENT not in (task.error_message or "")
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_persist_failed_does_not_use_output_invalid(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)

    async def persist_boom(*_a, **_k):
        raise RuntimeError("orm boom")

    await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=_ok_outcome(), persist=persist_boom
    )
    result = await worker._handle_process(session, task.id)
    assert result["status"] == AI_TASK_STATUS_FAILED
    assert result["status"] != AI_TASK_STATUS_OUTPUT_INVALID
    assert task.status == AI_TASK_STATUS_FAILED
    assert task.error_code == "persist_failed"
    assert task.error_code != "output_validation_failed"


@pytest.mark.asyncio
async def test_persist_failed_message_is_scrubbed(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)

    async def persist_boom(*_a, **_k):
        raise RuntimeError(f"leak {SECRET_FRAGMENT} in exc")

    await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=_ok_outcome(), persist=persist_boom
    )
    await worker._handle_process(session, task.id)
    attempt = session.attempts[0]
    public_blob = json.dumps(
        {
            "task_err": task.error_message,
            "attempt_err": attempt.error_message,
            "raw_request": task.raw_request,
            "raw_response": task.raw_response,
            "result_payload": task.result_payload,
            "attempt_raw": attempt.raw_response,
        },
        ensure_ascii=False,
        default=str,
    )
    assert SECRET_FRAGMENT not in public_blob
    assert "RuntimeError" in (task.error_message or "")
    if isinstance(task.result_payload, dict):
        assert task.result_payload.get("validation_error_code") is None
        assert task.result_payload.get("persist_error_type") == "RuntimeError"


@pytest.mark.asyncio
async def test_stage8_output_invalid_path_unchanged(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)

    async def persist_invalid(*_a, **_k):
        raise AIOutputValidationError(
            "AI output failed snapshot validation",
            code="output_validation_failed",
        )

    await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=_ok_outcome(), persist=persist_invalid
    )
    result = await worker._handle_process(session, task.id)
    assert result["status"] == AI_TASK_STATUS_OUTPUT_INVALID
    assert task.status == AI_TASK_STATUS_OUTPUT_INVALID
    assert task.error_code != "persist_failed"


@pytest.mark.asyncio
async def test_terminal_write_skips_when_task_no_longer_running(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)

    async def persist_then_admin_recover(session, **kwargs):
        task.status = AI_TASK_STATUS_FAILED
        task.error_code = "stale_running_recovered"
        task.error_category = "non_retryable"
        task.error_message = "stale running recovered"
        if session.attempts:
            session.attempts[-1].status = AI_TASK_STATUS_FAILED
        return SimpleNamespace(id=uuid4(), version_no=1)

    await _bind_stage8_mocks(
        monkeypatch,
        task=task,
        outcome=_ok_outcome(),
        persist=persist_then_admin_recover,
    )
    result = await worker._handle_process(session, task.id)
    assert result["status"] == "skipped_stale_owner"
    assert result.get("observed_status") == AI_TASK_STATUS_FAILED
    assert task.status == AI_TASK_STATUS_FAILED
    assert task.error_code == "stale_running_recovered"
    assert task.status != AI_TASK_STATUS_SUCCEEDED


@pytest.mark.asyncio
async def test_terminal_write_skips_succeeded_overwrite_after_cancelled(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = _ok_outcome()

    async def provider_marks_cancelled(**_k):
        task.status = AI_TASK_STATUS_CANCELLED
        return outcome

    await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)
    monkeypatch.setattr(worker, "_run_provider", provider_marks_cancelled)
    result = await worker._handle_process(session, task.id)
    assert result["status"] == AI_TASK_STATUS_CANCELLED
    assert result.get("late_response") is True
    assert task.status == AI_TASK_STATUS_CANCELLED
    assert task.status != AI_TASK_STATUS_SUCCEEDED


def test_reassert_running_ownership_helper_exists_and_used_by_all_terminal_paths() -> None:
    assert "async def _reassert_running_ownership_for_terminal" in WORKER_SRC
    tree = ast.parse(WORKER_SRC)
    handle = None
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_process":
            handle = node
            break
    assert handle is not None
    call_names: list[str] = []
    for node in ast.walk(handle):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                call_names.append(func.id)
            elif isinstance(func, ast.Attribute):
                call_names.append(func.attr)
    assert call_names.count("_reassert_running_ownership_for_terminal") >= 5
    # Five terminal families must appear as call sites in source order regions.
    assert WORKER_SRC.count("_reassert_running_ownership_for_terminal(") >= 6  # def+calls
    for marker in (
        "persist_failed",
        "AI_TASK_STATUS_SUCCEEDED",
        "AI_TASK_STATUS_OUTPUT_INVALID",
        "AI_TASK_STATUS_PENDING",
        "AI_TASK_STATUS_FAILED",
    ):
        assert marker in WORKER_SRC
