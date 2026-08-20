"""Task 5A: ANALYZE via sensitive path stays mock; public carriers stay body-free."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.ai_task import (
    AI_TASK_STATUS_SUCCEEDED,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
)
from app.services.ai_providers.base import ProviderOutcome

# Reuse FakeWorkerSession / stage-8 helpers from the worker suite.
from tests.workers.test_interview_ai_worker import (
    SECRET_SUMMARY,
    SECRET_TRANSCRIPT,
    FakeWorkerSession,
    _analysis_result,
    _bind_stage8_mocks,
    _decrypt_json,
    _frozen_analysis_snapshot,
    _make_task,
    assert_jsonb_has_no_sensitive,
)
from tests.workers.test_sensitive_ai_queue import _patch_worker_db_session

STAGE8_ANALYZE_PUBLIC_KEYS = frozenset(
    {
        "provider",
        "workflow_version",
        "http_status",
        "input_snapshot_hash",
        "content_sha256",
        "validation",
        "dimension_count",
        "version_id",
    }
)


def test_sensitive_path_analyze_mock_e2e_no_plaintext_in_public_payload(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    segment_id = uuid4()
    snapshot = _frozen_analysis_snapshot(round_id=round_id, segment_id=segment_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(
        ok=True,
        result=_analysis_result(segment_id),
        raw_request={"provider": "mock", "task_type": task.task_type},
        raw_response={"outputs": _analysis_result(segment_id)},
        http_status=200,
    )
    captured = asyncio.run(
        _bind_stage8_mocks(
            monkeypatch, task=task, outcome=outcome, segment_id=segment_id
        )
    )

    async def via_handle(task_id):
        return await worker._handle_process(session, task_id)

    monkeypatch.setattr(worker, "_process_ai_task_async", via_handle)
    _patch_worker_db_session(
        monkeypatch, worker, get_task=AsyncMock(return_value=task)
    )

    result = worker.process_sensitive_ai_task.run(str(task.id))

    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(captured["persist_calls"]) == 1
    assert captured["provider_inputs"][0]["segments"][0]["text"] == SECRET_TRANSCRIPT
    attempt = session.attempts[0]
    assert_jsonb_has_no_sensitive(task, attempt)
    request_plain = _decrypt_json(attempt.sensitive_request_encrypted)
    assert SECRET_TRANSCRIPT in json.dumps(request_plain, ensure_ascii=False)
    public_blob = json.dumps(
        [task.raw_request, task.raw_response, task.result_payload, attempt.raw_response],
        ensure_ascii=False,
        default=str,
    )
    assert SECRET_TRANSCRIPT not in public_blob
    assert SECRET_SUMMARY not in public_blob


def test_sensitive_path_analyze_redacts_stage8_public_fields(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    segment_id = uuid4()
    snapshot = _frozen_analysis_snapshot(round_id=round_id, segment_id=segment_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(
        ok=True,
        result=_analysis_result(segment_id),
        raw_request={"provider": "mock", "task_type": task.task_type},
        raw_response={"outputs": _analysis_result(segment_id)},
        http_status=200,
    )
    asyncio.run(
        _bind_stage8_mocks(
            monkeypatch, task=task, outcome=outcome, segment_id=segment_id
        )
    )

    async def via_handle(task_id):
        return await worker._handle_process(session, task_id)

    monkeypatch.setattr(worker, "_process_ai_task_async", via_handle)
    _patch_worker_db_session(
        monkeypatch, worker, get_task=AsyncMock(return_value=task)
    )

    result = worker.process_sensitive_ai_task.run(str(task.id))
    assert result["status"] == AI_TASK_STATUS_SUCCEEDED

    for public in (
        task.raw_request,
        task.result_payload,
        session.attempts[0].raw_response,
    ):
        assert isinstance(public, dict)
        assert set(public.keys()) <= STAGE8_ANALYZE_PUBLIC_KEYS
        assert public.get("provider") == "mock"
        assert public.get("dimension_count") == 1
        assert "overall_summary" not in public
        assert "quote" not in public
        assert "segments" not in public
        assert SECRET_SUMMARY not in json.dumps(public, ensure_ascii=False)
