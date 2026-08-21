"""Comprehensive analyze via sensitive path stays mock; public carriers body-free."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.ai_task import (
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_APPLICATION,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
)
from app.services.ai_providers.base import ProviderOutcome
from tests.workers.test_sensitive_ai_queue import _patch_worker_db_session

SECRET_SUMMARY = "TOP_SECRET_COMPREHENSIVE_SUMMARY_24680"
FORBIDDEN_BODIES = (
    SECRET_SUMMARY,
    "这是转写正文",
    "JD全文机密",
    "简历正文机密",
)


def _comprehensive_snapshot(*, application_id):
    return {
        "schema_version": "1.0",
        "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        "application_id": str(application_id),
        "workflow_key": "interview_comprehensive_analyze",
        "workflow_version": "1.0",
        "input_snapshot_hash": "hash-comp-1",
        "round_refs": [
            {
                "round_id": str(uuid4()),
                "sequence_no": 1,
                "analysis_version_id": str(uuid4()),
                "analysis_version_no": 1,
                "overall_score": 4.0,
                "dimensions": [
                    {
                        "dimension_key": "collab",
                        "dimension_name": "协作",
                        "weight": 100.0,
                        "score": 4,
                        "insufficient_information": False,
                    }
                ],
                "evidence_refs": [
                    {
                        "dimension_key": "collab",
                        "segment_no": 1,
                        "transcript_segment_id": str(uuid4()),
                    }
                ],
            }
        ],
        "coverage_report": {
            "eligible_round_count": 1,
            "total_round_count": 1,
            "included_rounds": [],
            "gaps": [],
            "coverage_insufficient": False,
            "single_round_only": True,
            "missing_round_count": 0,
        },
    }


def _comprehensive_result() -> dict:
    return {
        "overall_summary": SECRET_SUMMARY,
        "overall_score": "4.00",
        "dimension_notes": [
            {"dimension_key": "collab", "score": 4, "note": "结构化维度汇总"},
        ],
    }


def test_sensitive_path_comprehensive_no_plaintext_in_public_payload(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    application_id = uuid4()
    snapshot = _comprehensive_snapshot(application_id=application_id)
    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        status="pending",
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application_id,
        input_snapshot=snapshot,
        attempt_count=0,
        retry_cycle_no=0,
        cycle_attempt_count=0,
        created_by=uuid4(),
        raw_purged_at=None,
        raw_request=None,
        raw_response=None,
        result_payload=None,
        error_code=None,
        error_message=None,
        error_category=None,
        started_at=None,
        finished_at=None,
        updated_at=None,
        provider_run_id=None,
        request_id=None,
    )

    persist_calls: list = []

    outcome = ProviderOutcome(
        ok=True,
        result=_comprehensive_result(),
        raw_request={
            "provider": "mock",
            "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        },
        raw_response={"outputs": _comprehensive_result()},
        http_status=200,
    )

    async def fake_persist(
        session, *, task_id, payload, actor=None, request_context=None
    ):
        persist_calls.append({"task_id": task_id, "payload": payload})
        return SimpleNamespace(id=uuid4(), version_no=1)

    async def via_handle(task_id):
        session = SimpleNamespace()
        prepared = await worker._prepare_stage8_provider_input(session, task)
        blob = json.dumps(prepared, ensure_ascii=False)
        assert "segments" not in prepared
        assert "jd_text" not in blob
        assert "resume_text" not in blob
        assert "quote" not in blob
        meta = await worker._after_task_success(session, task=task, outcome=outcome)
        attempt = SimpleNamespace(
            sensitive_request_encrypted=None,
            sensitive_response_encrypted=None,
            raw_response=None,
        )
        worker._write_stage8_raw(
            task=task,
            attempt=attempt,
            provider_input=prepared,
            outcome=outcome,
            extra=worker._stage8_success_extra(task, outcome, meta),
        )
        return {
            "status": AI_TASK_STATUS_SUCCEEDED,
            "prepared": prepared,
            "attempt": attempt,
        }

    monkeypatch.setattr(
        "app.services.comprehensive_analyses.persist_comprehensive_analysis_result",
        fake_persist,
    )
    monkeypatch.setattr(
        worker, "_actor_for_ai_task", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(worker, "_process_ai_task_async", via_handle)
    _patch_worker_db_session(
        monkeypatch, worker, get_task=AsyncMock(return_value=task)
    )

    result = worker.process_sensitive_ai_task.run(str(task.id))
    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(persist_calls) == 1
    assert persist_calls[0]["payload"]["overall_summary"] == SECRET_SUMMARY
    public_blob = json.dumps(
        [task.raw_request, task.raw_response, task.result_payload],
        ensure_ascii=False,
        default=str,
    )
    for body in FORBIDDEN_BODIES:
        assert body not in public_blob
    assert task.raw_request.get("provider") == "mock"
    assert SECRET_SUMMARY not in public_blob


def test_stage8_types_include_comprehensive() -> None:
    from app.models.ai_task import TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE
    from app.workers.ai_tasks import STAGE8_TASK_TYPES

    assert TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE in STAGE8_TASK_TYPES
