"""Worker tests for stage 8 interview AI tasks (TDD).

Covers sensitive JSONB isolation, Fernet attempt columns, persist callbacks,
OUTPUT_INVALID (no business version), retry snapshot reuse, and purge.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.ai_task import (
    AI_TASK_STATUS_OUTPUT_INVALID,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    AITaskAttempt,
)
from app.schemas.interview_ai import InterviewDimensionSnapshot
from app.services.ai_providers.base import ProviderOutcome
from app.services.crypto import CIPHER_PREFIX, decrypt_secret
from app.services.interview_ai_validation import AIOutputValidationError
from app.services.interview_analyses import (
    AnalysisProviderInput,
    AnalysisProviderSegment,
)
from app.services.interview_questions import QuestionProviderInput

SECRET_RESUME = "TOP_SECRET_RESUME_BODY"
SECRET_JD = "TOP_SECRET_JD_BODY"
SECRET_TRANSCRIPT = "TOP_SECRET_TRANSCRIPT_BODY"
SECRET_QUESTION = "SECRET_QUESTION_TEXT_PLEASE_DESCRIBE"
SECRET_SUMMARY = "SECRET_OVERALL_SUMMARY_TEXT"
SECRET_QUOTE = "SECRET_EVIDENCE_QUOTE_TEXT"

FORBIDDEN_JSONB_KEYS = {
    "question",
    "purpose",
    "resume_evidence",
    "follow_up_prompts",
    "risk_flags",
    "overall_summary",
    "analysis",
    "strengths",
    "risks",
    "insufficient_information",
    "suggested_follow_ups",
    "quote",
    "resume_text",
    "jd_text",
    "standardized_text",
    "speaker_name",
    "sensitive_request",
    "sensitive_response",
}

SECRET_MARKERS = (
    SECRET_RESUME,
    SECRET_JD,
    SECRET_TRANSCRIPT,
    SECRET_QUESTION,
    SECRET_SUMMARY,
    SECRET_QUOTE,
    CIPHER_PREFIX,
)


class _ExecResult:
    def __init__(self, *, rowcount: int = 0, scalar=None, rows=None):
        self.rowcount = rowcount
        self._scalar = scalar
        self._rows = list(rows) if rows is not None else (
            [scalar] if scalar is not None else []
        )

    def scalar_one(self):
        if self._scalar is None:
            raise LookupError("no row")
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeWorkerSession:
    """Minimal session double for `_handle_process` / purge unit tests."""

    def __init__(self, task, *, extra_attempts=None):
        self.task = task
        self.attempts: list[AITaskAttempt] = []
        self.extra_attempts = list(extra_attempts or [])
        self.commits = 0
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if isinstance(obj, AITaskAttempt):
            self.attempts.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, stmt):
        compiled = str(stmt).lower()
        name = type(stmt).__name__
        if name == "Update":
            if self.task.status == AI_TASK_STATUS_PENDING:
                self.task.status = "running"
                self.task.updated_at = datetime.now(UTC)
                return _ExecResult(rowcount=1)
            return _ExecResult(rowcount=0)
        descriptions = list(getattr(stmt, "column_descriptions", None) or [])
        col_names = " ".join(
            str(item.get("name") or "") for item in descriptions
        ).lower()
        entities = [item.get("entity") for item in descriptions]
        if (
            "max(" in compiled
            or "coalesce" in compiled
            or "coalesce" in col_names
            or "max" in col_names
        ):
            max_no = max((item.attempt_no for item in self.attempts), default=0)
            return _ExecResult(scalar=max_no)
        if "response_purged" in compiled and self.extra_attempts:
            return _ExecResult(rows=self.extra_attempts, scalar=None)
        if AITaskAttempt in entities or "ai_task_attempts" in compiled:
            last = self.attempts[-1] if self.attempts else None
            return _ExecResult(scalar=last, rows=list(self.attempts))
        return _ExecResult(scalar=self.task, rows=[self.task])


def _frozen_question_snapshot(*, round_id: UUID, extra: dict | None = None) -> dict:
    payload = {
        "schema_version": "1.0",
        "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        "round_id": str(round_id),
        "job_version_id": str(uuid4()),
        "resume_version_id": str(uuid4()),
        "workflow_key": "interview_question_generate",
        "workflow_version": "1.0",
        "input_snapshot_hash": "frozen-question-hash",
        "dimensions": [
            {
                "dimension_key": "D001",
                "display_order": 1,
                "name": "协作",
                "weight": "40.00",
            }
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def _frozen_analysis_snapshot(*, round_id: UUID, segment_id: UUID) -> dict:
    return {
        "schema_version": "1.0",
        "task_type": TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        "round_id": str(round_id),
        "job_version_id": str(uuid4()),
        "transcript_id": str(uuid4()),
        "transcript_version_id": str(uuid4()),
        "workflow_key": "interview_round_analyze",
        "workflow_version": "1.0",
        "input_snapshot_hash": "frozen-analysis-hash",
        "dimensions": [
            {
                "dimension_key": "D001",
                "display_order": 1,
                "name": "协作",
                "weight": "40.00",
            }
        ],
        "segments": [
            {
                "segment_id": str(segment_id),
                "segment_no": 1,
                "plaintext_sha256": "abc",
            }
        ],
    }


def _make_task(*, task_type: str, snapshot: dict, round_id: UUID) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        task_type=task_type,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_id,
        input_snapshot=snapshot,
        retry_cycle_no=0,
        cycle_attempt_count=0,
        attempt_count=0,
        started_at=None,
        finished_at=None,
        updated_at=now,
        created_at=now,
        created_by=uuid4(),
        error_code=None,
        error_message=None,
        error_category=None,
        raw_purged_at=None,
        raw_request=None,
        raw_response=None,
        result_payload=None,
    )


def _question_loader(task) -> QuestionProviderInput:
    snap = task.input_snapshot
    return QuestionProviderInput(
        task_id=task.id,
        round_id=UUID(str(snap["round_id"])),
        job_version_id=UUID(str(snap["job_version_id"])),
        resume_version_id=UUID(str(snap["resume_version_id"])),
        job_title="后端工程师",
        jd_text=SECRET_JD,
        resume_text=SECRET_RESUME,
        dimensions=list(snap["dimensions"]),
        workflow_key=str(snap["workflow_key"]),
        workflow_version=str(snap["workflow_version"]),
        input_snapshot_hash=str(snap["input_snapshot_hash"]),
    )


def _analysis_loader(task, segment_id: UUID) -> AnalysisProviderInput:
    snap = task.input_snapshot
    dim = InterviewDimensionSnapshot.model_validate(snap["dimensions"][0])
    return AnalysisProviderInput(
        round_id=UUID(str(snap["round_id"])),
        job_version_id=UUID(str(snap["job_version_id"])),
        transcript_id=UUID(str(snap["transcript_id"])),
        transcript_version_id=UUID(str(snap["transcript_version_id"])),
        dimensions=(dim,),
        segments=(
            AnalysisProviderSegment(
                id=segment_id,
                segment_no=1,
                speaker_role="CANDIDATE",
                speaker_name="面试官甲",
                start_time_ms=0,
                end_time_ms=1000,
                text=SECRET_TRANSCRIPT,
            ),
        ),
    )


def _question_result() -> dict:
    return {
        "questions": [
            {
                "dimension_key": "D001",
                "question": SECRET_QUESTION,
                "purpose": "考察协作",
                "evidence_source": "JOB_REQUIREMENT",
                "resume_evidence": None,
                "follow_up_prompts": ["对方立场是什么？"],
                "risk_flags": ["可能回避责任"],
                "display_order": 1,
            }
        ]
    }


def _analysis_result(segment_id: UUID) -> dict:
    return {
        "dimensions": [
            {
                "dimension_key": "D001",
                "score": 4,
                "evidence": [
                    {
                        "segment_id": str(segment_id),
                        "segment_no": 1,
                        "quote": SECRET_QUOTE,
                    }
                ],
                "analysis": "候选人能描述冲突处理路径。",
                "strengths": ["目标对齐"],
                "risks": ["细节偏少"],
                "insufficient_information": None,
                "suggested_follow_ups": ["请补充具体结果"],
            }
        ],
        "overall_summary": SECRET_SUMMARY,
        "model_reported_overall_score": "4.00",
    }


def _json_blob(*values) -> str:
    return json.dumps(values, ensure_ascii=False, default=str)


def assert_jsonb_has_no_sensitive(task, attempt) -> None:
    blob = _json_blob(
        task.input_snapshot,
        task.raw_request,
        task.raw_response,
        task.result_payload,
        attempt.raw_response,
        task.error_message,
        attempt.error_message,
    )
    for marker in SECRET_MARKERS:
        assert marker not in blob
    for payload in (
        task.input_snapshot,
        task.raw_request,
        task.raw_response,
        task.result_payload,
        attempt.raw_response,
    ):
        _assert_no_forbidden_keys(payload)


def _assert_no_forbidden_keys(payload, path: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_JSONB_KEYS, (
                f"forbidden JSONB key {key} at {path}"
            )
            _assert_no_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _assert_no_forbidden_keys(value, f"{path}[{index}]")


def _decrypt_json(cipher: str | None) -> dict:
    assert cipher is not None
    assert cipher.startswith(CIPHER_PREFIX)
    plain = decrypt_secret(cipher)
    assert plain is not None
    parsed = json.loads(plain)
    assert isinstance(parsed, dict)
    return parsed


async def _bind_stage8_mocks(
    monkeypatch,
    *,
    task,
    outcome: ProviderOutcome,
    persist=None,
    load_error: Exception | None = None,
    segment_id: UUID | None = None,
    user_missing: bool = False,
):
    from app.workers import ai_tasks as worker

    captured: dict = {"provider_inputs": []}

    async def fake_add(session, attempt):
        session.add(attempt)
        await session.flush()
        return attempt

    async def fake_provider(*, task_type, input_snapshot):
        captured["provider_inputs"].append(dict(input_snapshot))
        return outcome

    async def fake_load_question(session, *, task_id):
        if load_error is not None:
            raise load_error
        return _question_loader(task)

    async def fake_load_analysis(session, *, task_id):
        if load_error is not None:
            raise load_error
        assert segment_id is not None
        return _analysis_loader(task, segment_id)

    persist_calls: list[dict] = []
    actor_user = None
    if getattr(task, "created_by", None) is not None and not user_missing:
        actor_user = SimpleNamespace(
            id=task.created_by,
            display_name="SHOULD_NOT_LEAK_DISPLAY",
            email="should-not-leak@example.com",
        )

    async def fake_persist(
        session, *, task_id, payload, actor=None, request_context=None
    ):
        persist_calls.append(
            {
                "task_id": task_id,
                "payload": payload,
                "actor": actor,
                "request_context": request_context,
            }
        )
        if persist is not None:
            return await persist(
                session,
                task_id=task_id,
                payload=payload,
                actor=actor,
                request_context=request_context,
            )
        return SimpleNamespace(id=uuid4(), version_no=1)

    async def fake_get_user(_session, user_id):
        if actor_user is not None and user_id == actor_user.id:
            return actor_user
        return None

    monkeypatch.setattr(worker, "add_ai_task_attempt", fake_add)
    monkeypatch.setattr(worker, "_run_provider", fake_provider)
    monkeypatch.setattr(
        "app.services.interview_questions.load_question_provider_input",
        fake_load_question,
    )
    monkeypatch.setattr(
        "app.services.interview_analyses.load_analysis_provider_input",
        fake_load_analysis,
    )
    monkeypatch.setattr(
        "app.services.interview_questions.persist_question_generation_result",
        fake_persist,
    )
    monkeypatch.setattr(
        "app.services.interview_analyses.persist_analysis_generation_result",
        fake_persist,
    )
    monkeypatch.setattr("app.repositories.users.get_user_by_id", fake_get_user)
    captured["persist_calls"] = persist_calls
    captured["actor"] = actor_user
    return captured


def _assert_worker_persist_audit_args(
    call: dict,
    *,
    task,
    actor_id,
) -> None:
    ctx = call["request_context"]
    assert ctx is not None
    assert ctx.request_id == f"ai-task:{task.id}"
    ctx_blob = json.dumps(
        {"request_id": ctx.request_id, "ip_address": getattr(ctx, "ip_address", None)},
        ensure_ascii=False,
        default=str,
    )
    for marker in SECRET_MARKERS:
        assert marker not in ctx_blob
    assert "SHOULD_NOT_LEAK_DISPLAY" not in ctx_blob
    assert "should-not-leak@example.com" not in ctx_blob
    actor = call["actor"]
    if actor_id is None:
        assert actor is None
    else:
        assert actor is not None
        assert actor.id == actor_id


def test_worker_does_not_mutate_recruitment_business_state() -> None:
    from app.workers import ai_tasks as worker

    source = inspect.getsource(worker)
    assert "INTERVIEW_STATUS_" not in source
    assert "APPLICATION_STATUS_" not in source
    assert "round.status" not in source
    assert "application.status" not in source


@pytest.mark.asyncio
async def test_question_success_persists_and_keeps_bodies_out_of_jsonb(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    snapshot = _frozen_question_snapshot(round_id=round_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={
            "provider": "mock",
            "task_type": task.task_type,
            "input": "ignore",
        },
        raw_response={"outputs": _question_result()},
        http_status=200,
    )
    captured = await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(captured["persist_calls"]) == 1
    assert captured["persist_calls"][0]["task_id"] == task.id
    assert captured["persist_calls"][0]["payload"] == outcome.result
    _assert_worker_persist_audit_args(
        captured["persist_calls"][0],
        task=task,
        actor_id=task.created_by,
    )
    assert captured["provider_inputs"]
    provider_input = captured["provider_inputs"][0]
    assert provider_input["resume_text"] == SECRET_RESUME
    assert provider_input["jd_text"] == SECRET_JD
    assert task.input_snapshot == snapshot
    assert SECRET_RESUME not in json.dumps(task.input_snapshot)
    attempt = session.attempts[0]
    assert_jsonb_has_no_sensitive(task, attempt)
    request_plain = _decrypt_json(attempt.sensitive_request_encrypted)
    response_plain = _decrypt_json(attempt.sensitive_response_encrypted)
    assert SECRET_RESUME in json.dumps(request_plain, ensure_ascii=False)
    assert SECRET_QUESTION in json.dumps(response_plain, ensure_ascii=False)


@pytest.mark.asyncio
async def test_analysis_success_persists_and_keeps_bodies_out_of_jsonb(
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
    captured = await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=outcome, segment_id=segment_id
    )

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(captured["persist_calls"]) == 1
    _assert_worker_persist_audit_args(
        captured["persist_calls"][0],
        task=task,
        actor_id=task.created_by,
    )
    provider_input = captured["provider_inputs"][0]
    assert provider_input["segments"][0]["text"] == SECRET_TRANSCRIPT
    assert task.input_snapshot["segments"][0].get("text") is None
    attempt = session.attempts[0]
    assert_jsonb_has_no_sensitive(task, attempt)
    request_plain = _decrypt_json(attempt.sensitive_request_encrypted)
    response_plain = _decrypt_json(attempt.sensitive_response_encrypted)
    assert SECRET_TRANSCRIPT in json.dumps(request_plain, ensure_ascii=False)
    assert SECRET_SUMMARY in json.dumps(response_plain, ensure_ascii=False)


@pytest.mark.asyncio
async def test_succeeded_redelivery_is_idempotent_and_skips_second_persist(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    snapshot = _frozen_question_snapshot(round_id=round_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={"provider": "mock"},
        raw_response={"outputs": _question_result()},
        http_status=200,
    )
    captured = await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)

    first = await worker._handle_process(session, task.id)
    second = await worker._handle_process(session, task.id)

    assert first["status"] == AI_TASK_STATUS_SUCCEEDED
    assert second.get("skipped") is True
    assert len(captured["persist_calls"]) == 1
    _assert_worker_persist_audit_args(
        captured["persist_calls"][0],
        task=task,
        actor_id=task.created_by,
    )
    assert len(session.attempts) == 1


@pytest.mark.asyncio
async def test_question_success_uses_none_actor_when_user_missing(
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
    outcome = ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={"provider": "mock"},
        raw_response={"outputs": _question_result()},
        http_status=200,
    )
    captured = await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=outcome, user_missing=True
    )

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(captured["persist_calls"]) == 1
    _assert_worker_persist_audit_args(
        captured["persist_calls"][0],
        task=task,
        actor_id=None,
    )
    assert captured["persist_calls"][0]["actor"] is None


@pytest.mark.asyncio
async def test_question_success_uses_none_actor_when_created_by_is_null(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    task.created_by = None
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={"provider": "mock"},
        raw_response={"outputs": _question_result()},
        http_status=200,
    )
    captured = await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_SUCCEEDED
    assert len(captured["persist_calls"]) == 1
    _assert_worker_persist_audit_args(
        captured["persist_calls"][0],
        task=task,
        actor_id=None,
    )


@pytest.mark.asyncio
async def test_persist_replay_does_not_create_second_version(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    version_id = uuid4()
    calls = {"n": 0}

    async def persist_existing(session, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(id=version_id, version_no=1)

    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=_frozen_question_snapshot(round_id=uuid4()),
    )
    monkeypatch.setattr(
        "app.services.interview_questions.persist_question_generation_result",
        persist_existing,
    )
    outcome = ProviderOutcome(ok=True, result=_question_result())
    await worker._after_task_success(None, task=task, outcome=outcome)
    await worker._after_task_success(None, task=task, outcome=outcome)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_output_invalid_from_persist_writes_encrypted_and_skips_version(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    snapshot = _frozen_question_snapshot(round_id=round_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    versions: list[object] = []

    async def persist_invalid(session, **kwargs):
        raise AIOutputValidationError(
            "AI output failed snapshot validation",
            code="output_validation_failed",
        )

    outcome = ProviderOutcome(
        ok=True,
        result=_question_result(),
        raw_request={"provider": "mock"},
        raw_response={"outputs": _question_result()},
        http_status=200,
    )
    captured = await _bind_stage8_mocks(
        monkeypatch, task=task, outcome=outcome, persist=persist_invalid
    )

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_OUTPUT_INVALID
    assert task.status == AI_TASK_STATUS_OUTPUT_INVALID
    assert versions == []
    assert len(captured["persist_calls"]) == 1
    attempt = session.attempts[0]
    assert attempt.status == AI_TASK_STATUS_OUTPUT_INVALID
    assert_jsonb_has_no_sensitive(task, attempt)
    assert SECRET_QUESTION in json.dumps(
        _decrypt_json(attempt.sensitive_response_encrypted), ensure_ascii=False
    )
    assert SECRET_RESUME in json.dumps(
        _decrypt_json(attempt.sensitive_request_encrypted), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_loader_hash_failure_is_output_invalid_without_provider_or_version(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    snapshot = _frozen_question_snapshot(round_id=round_id)
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    outcome = ProviderOutcome(ok=True, result=_question_result())
    captured = await _bind_stage8_mocks(
        monkeypatch,
        task=task,
        outcome=outcome,
        load_error=AIOutputValidationError(
            "frozen input hash mismatch",
            code="output_validation_failed",
        ),
    )

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_OUTPUT_INVALID
    assert captured["provider_inputs"] == []
    assert captured["persist_calls"] == []
    attempt = session.attempts[0]
    assert_jsonb_has_no_sensitive(task, attempt)
    assert task.input_snapshot == snapshot


@pytest.mark.asyncio
async def test_provider_retryable_failure_reuses_original_snapshot(
    monkeypatch,
) -> None:
    from app.workers import ai_tasks as worker

    round_id = uuid4()
    snapshot = _frozen_question_snapshot(round_id=round_id)
    frozen_copy = json.loads(json.dumps(snapshot))
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=snapshot,
        round_id=round_id,
    )
    session = FakeWorkerSession(task)
    enqueued: list[tuple] = []

    def fake_apply_async(*, args, countdown):
        enqueued.append((args[0], countdown))

    outcome = ProviderOutcome(
        ok=False,
        raw_request={"provider": "mock"},
        raw_response={"error": "flaky"},
        error_code="provider_5xx",
        error_message="flaky",
        error_category=ERROR_CATEGORY_RETRYABLE,
        http_status=502,
    )
    captured = await _bind_stage8_mocks(monkeypatch, task=task, outcome=outcome)
    monkeypatch.setattr(worker.process_ai_task, "apply_async", fake_apply_async)

    result = await worker._handle_process(session, task.id)

    assert result["status"] == AI_TASK_STATUS_PENDING
    assert enqueued
    assert captured["persist_calls"] == []
    assert task.input_snapshot == frozen_copy
    attempt = session.attempts[0]
    assert_jsonb_has_no_sensitive(task, attempt)
    assert SECRET_RESUME in json.dumps(
        _decrypt_json(attempt.sensitive_request_encrypted), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_purge_clears_stage8_encrypted_columns(monkeypatch) -> None:
    from app.workers import ai_tasks as worker

    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    round_id = uuid4()
    task = _make_task(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        snapshot=_frozen_question_snapshot(round_id=round_id),
        round_id=round_id,
    )
    task.status = AI_TASK_STATUS_SUCCEEDED
    task.created_at = old
    task.raw_request = {"provider": "mock"}
    task.raw_response = {"http_status": 200}
    attempt = AITaskAttempt(
        task_id=task.id,
        attempt_no=1,
        retry_cycle_no=0,
        cycle_attempt_no=1,
        status=AI_TASK_STATUS_SUCCEEDED,
        started_at=old,
        finished_at=old,
        created_at=old,
        raw_response={"provider": "mock"},
        sensitive_request_encrypted="enc:v1:request",
        sensitive_response_encrypted="enc:v1:response",
    )
    session = FakeWorkerSession(task)
    session.attempts = [attempt]
    session.task = task

    async def fake_list(session_arg, *, cutoff, limit=200):
        return [task]

    monkeypatch.setattr(worker, "list_tasks_for_raw_purge", fake_list)
    purged = await worker._purge_raw_payloads(
        session, cutoff=now - timedelta(days=1)
    )

    assert purged["purged"] >= 1
    assert task.raw_request is None
    assert task.raw_response is None
    assert task.raw_purged_at is not None
    assert attempt.raw_response is None
    assert attempt.sensitive_request_encrypted is None
    assert attempt.sensitive_response_encrypted is None
    assert attempt.response_purged_at is not None


@pytest.mark.asyncio
async def test_mock_question_generate_follows_snapshot_dimension_keys() -> None:
    from app.services.ai_providers.mock import run_mock

    out = await run_mock(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot={
            "dimensions": [
                {"dimension_key": "D002"},
                {"dimension_key": "D003"},
            ]
        },
    )
    assert out.ok is True
    keys = [item["dimension_key"] for item in out.result["questions"]]
    assert keys == ["D002", "D003"]
    assert "input" not in (out.raw_request or {})


@pytest.mark.asyncio
async def test_mock_analysis_uses_real_segment_ids() -> None:
    from app.services.ai_providers.mock import run_mock

    segment_id = str(uuid4())
    out = await run_mock(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        input_snapshot={
            "dimensions": [{"dimension_key": "D007"}],
            "segments": [
                {"id": segment_id, "segment_no": 3, "text": "对齐目标后推进。"}
            ],
        },
    )
    assert out.ok is True
    evidence = out.result["dimensions"][0]["evidence"][0]
    assert evidence["segment_id"] == segment_id
    assert evidence["segment_no"] == 3
    assert out.result["dimensions"][0]["dimension_key"] == "D007"


def test_dify_interview_inputs_do_not_dump_full_snapshot() -> None:
    from app.models.ai_task import TASK_TYPE_JD_PARSE
    from app.services.ai_providers.dify import build_dify_inputs

    question_inputs = build_dify_inputs(
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        {
            "job_title": "后端",
            "jd_text": SECRET_JD,
            "resume_text": SECRET_RESUME,
            "dimensions": [{"dimension_key": "D001"}],
            "plaintext": "should-not-leak-as-top-level",
        },
    )
    assert question_inputs["jd_text"] == SECRET_JD
    assert question_inputs["resume_text"] == SECRET_RESUME
    assert "plaintext" not in question_inputs
    assert "dimensions_json" in question_inputs

    analysis_inputs = build_dify_inputs(
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        {
            "dimensions": [{"dimension_key": "D001"}],
            "segments": [
                {
                    "id": str(uuid4()),
                    "segment_no": 1,
                    "text": SECRET_TRANSCRIPT,
                    "speaker_name": "面试官甲",
                }
            ],
            "extra_secret": "nope",
        },
    )
    assert "extra_secret" not in analysis_inputs
    assert SECRET_TRANSCRIPT in analysis_inputs["segments_json"]
    assert "speaker_name" not in analysis_inputs["segments_json"]

    jd_inputs = build_dify_inputs(
        TASK_TYPE_JD_PARSE, {"raw_jd_text": "岗位职责", "job_title": "x"}
    )
    assert "hr_manual_override" in jd_inputs


@pytest.mark.asyncio
async def test_dify_unconfigured_interview_falls_back_to_mock(monkeypatch) -> None:
    from app.services.ai_providers import dify

    posted = {"n": 0}

    async def fail_post(**kwargs):
        posted["n"] += 1
        raise AssertionError("must not call real Dify for interview tasks")

    monkeypatch.setattr(dify, "_post_workflow", fail_post)
    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot={"dimensions": [{"dimension_key": "D001"}]},
    )
    assert out.ok is True
    assert posted["n"] == 0
    assert out.result is not None
    assert out.result["questions"]
