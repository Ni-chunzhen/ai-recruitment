from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models.ai_task import (
    AI_TASK_STATUS_CANCELLED,
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_OUTPUT_INVALID,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    SENSITIVE_AI_TASK_TYPES,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    AITask,
    AITaskAttempt,
)
from app.repositories.ai_tasks import (
    add_ai_task_attempt,
    get_ai_task_by_id,
    list_tasks_for_raw_purge,
)
from app.repositories.interviews import InterviewNotFoundError
from app.services.ai_providers.base import (
    ProviderOutcome,
    retry_countdown_seconds,
    should_auto_retry,
)
from app.services.ai_providers.dify import extract_dify_run_ids, run_dify
from app.services.ai_providers.mock import run_mock
from app.services.audit import RequestContext, record_audit
from app.services.crypto import encrypt_secret
from app.services.interview_ai_validation import AIOutputValidationError
from app.services.interviews import InterviewValidationError
from app.services.score_validation import ScoreOutputInvalidError
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

STAGE8_TASK_TYPES = frozenset(
    {
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    }
)
_STAGE8_OUTPUT_INVALID_EXCEPTIONS = (
    ScoreOutputInvalidError,
    AIOutputValidationError,
    InterviewValidationError,
    InterviewNotFoundError,
)


async def _run_provider(
    *,
    task_type: str,
    input_snapshot: dict,
) -> ProviderOutcome:
    settings = get_settings()
    provider = (settings.AI_PROVIDER or "mock").strip().lower()
    if provider == "dify":
        return await run_dify(task_type=task_type, input_snapshot=input_snapshot)
    return await run_mock(
        task_type=task_type,
        input_snapshot=input_snapshot,
        sleep_seconds=0.0,
    )


def _question_memory_input(dto: Any) -> dict[str, Any]:
    return {
        "job_title": dto.job_title,
        "jd_text": dto.jd_text,
        "resume_text": dto.resume_text,
        "dimensions": dto.dimensions,
        "round_id": str(dto.round_id),
        "job_version_id": str(dto.job_version_id),
        "resume_version_id": str(dto.resume_version_id),
        "workflow_key": dto.workflow_key,
        "workflow_version": dto.workflow_version,
        "input_snapshot_hash": dto.input_snapshot_hash,
    }


def _analysis_memory_input(dto: Any) -> dict[str, Any]:
    return {
        "round_id": str(dto.round_id),
        "job_version_id": str(dto.job_version_id),
        "transcript_id": str(dto.transcript_id),
        "transcript_version_id": str(dto.transcript_version_id),
        "dimensions": [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in dto.dimensions
        ],
        "segments": [
            {
                "id": str(seg.id),
                "segment_id": str(seg.id),
                "segment_no": seg.segment_no,
                "speaker_role": seg.speaker_role,
                "speaker_name": seg.speaker_name,
                "start_time_ms": seg.start_time_ms,
                "end_time_ms": seg.end_time_ms,
                "text": seg.text,
            }
            for seg in dto.segments
        ],
    }


def _comprehensive_memory_input(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Provider input from Task-2 frozen snapshot only — never decrypt transcript/JD."""
    snap = dict(snapshot or {})
    return {
        "application_id": snap.get("application_id"),
        "schema_version": snap.get("schema_version"),
        "workflow_key": snap.get("workflow_key"),
        "workflow_version": snap.get("workflow_version"),
        "input_snapshot_hash": snap.get("input_snapshot_hash"),
        "round_refs": snap.get("round_refs") or [],
        "coverage_report": snap.get("coverage_report") or {},
    }


async def _prepare_stage8_provider_input(
    session: AsyncSession, task: AITask
) -> dict[str, Any]:
    if task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        from app.services.interview_questions import load_question_provider_input

        dto = await load_question_provider_input(session, task_id=task.id)
        return _question_memory_input(dto)
    if task.task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        from app.services.interview_analyses import load_analysis_provider_input

        dto = await load_analysis_provider_input(session, task_id=task.id)
        return _analysis_memory_input(dto)
    if task.task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE:
        from app.services.comprehensive_analyses import assert_no_forbidden_snapshot_keys

        prepared = _comprehensive_memory_input(dict(task.input_snapshot or {}))
        assert_no_forbidden_snapshot_keys(prepared)
        return prepared
    return dict(task.input_snapshot or {})


def _content_sha256(payload: object) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stage8_public_payload(
    *,
    task: AITask,
    outcome: ProviderOutcome | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snap = dict(task.input_snapshot or {})
    provider = None
    http_status = None
    if outcome is not None:
        if isinstance(outcome.raw_request, dict):
            provider = outcome.raw_request.get("provider")
        http_status = outcome.http_status
    payload: dict[str, Any] = {
        "provider": provider or "mock",
        "workflow_version": snap.get("workflow_version"),
        "http_status": http_status,
        "input_snapshot_hash": snap.get("input_snapshot_hash"),
    }
    if extra:
        payload.update(extra)
    return {key: value for key, value in payload.items() if value is not None}


def _encrypt_json_blob(payload: object | None) -> str | None:
    if payload is None:
        return None
    return encrypt_secret(json.dumps(payload, ensure_ascii=False, default=str))


def _is_question_live_http(
    task: AITask, outcome: ProviderOutcome | None
) -> bool:
    if task.task_type != TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        return False
    raw = outcome.raw_request if outcome is not None else None
    return isinstance(raw, dict) and raw.get("provider") == "dify"


def _question_live_audit_provider_input(
    provider_input: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(provider_input, dict):
        return provider_input
    body_keys = {"job_title", "jd_text", "resume_text", "dimensions"}
    redacted: dict[str, Any] = {
        "input_field_names": sorted(provider_input.keys()),
    }
    for key, value in provider_input.items():
        if key in body_keys:
            redacted[f"{key}_sha256"] = _content_sha256(value)
        else:
            redacted[key] = value
    return redacted


def _question_live_audit_outcome(
    outcome: ProviderOutcome,
) -> ProviderOutcome:
    result = outcome.result
    redacted_result: dict[str, Any] | None = None
    if isinstance(result, dict):
        questions = result.get("questions")
        count = len(questions) if isinstance(questions, list) else 0
        redacted_result = {"question_count": count}
    raw_response = outcome.raw_response
    if isinstance(raw_response, dict) and (
        "outputs" in raw_response or "data" in raw_response
    ):
        run_id, req_id = extract_dify_run_ids(raw_response)
        raw_response = {
            key: value
            for key, value in {
                "provider_run_id": run_id or outcome.provider_run_id,
                "request_id": req_id or outcome.request_id,
            }.items()
            if value is not None
        }
    return ProviderOutcome(
        ok=outcome.ok,
        result=redacted_result,
        raw_request=outcome.raw_request,
        raw_response=raw_response,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        error_category=outcome.error_category,
        http_status=outcome.http_status,
        provider_run_id=outcome.provider_run_id,
        request_id=outcome.request_id,
        extra=outcome.extra,
    )


def _write_stage8_raw(
    *,
    task: AITask,
    attempt: AITaskAttempt,
    provider_input: dict[str, Any] | None,
    outcome: ProviderOutcome | None,
    extra: dict[str, Any] | None = None,
) -> None:
    if _is_question_live_http(task, outcome):
        provider_input = _question_live_audit_provider_input(provider_input)
        if outcome is not None:
            outcome = _question_live_audit_outcome(outcome)
    if provider_input is not None:
        attempt.sensitive_request_encrypted = _encrypt_json_blob(
            {
                "provider_input": provider_input,
                "raw_request": outcome.raw_request if outcome is not None else None,
            }
        )
    if outcome is not None:
        attempt.sensitive_response_encrypted = _encrypt_json_blob(
            {
                "result": outcome.result,
                "raw_response": outcome.raw_response,
                "error_code": outcome.error_code,
                "error_message": outcome.error_message,
            }
        )
    public = _stage8_public_payload(task=task, outcome=outcome, extra=extra)
    if task.raw_purged_at is None:
        task.raw_request = public
        task.raw_response = public
    attempt.raw_response = public
    task.result_payload = public


def _stage8_success_extra(
    task: AITask,
    outcome: ProviderOutcome,
    persist_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    extra = dict(persist_meta or {})
    result = outcome.result or {}
    extra["content_sha256"] = _content_sha256(result)
    extra["validation"] = "ok"
    if task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        extra["question_count"] = len(result.get("questions") or [])
    if task.task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        extra["dimension_count"] = len(result.get("dimensions") or [])
    if task.task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE:
        extra["dimension_note_count"] = len(result.get("dimension_notes") or [])
        snap = dict(task.input_snapshot or {})
        coverage = snap.get("coverage_report") if isinstance(snap.get("coverage_report"), dict) else {}
        if coverage.get("eligible_round_count") is not None:
            extra["eligible_round_count"] = coverage.get("eligible_round_count")
    return extra


async def _actor_for_ai_task(session: AsyncSession, task: AITask):
    user_id = getattr(task, "created_by", None)
    if user_id is None:
        return None
    from app.repositories.users import get_user_by_id

    return await get_user_by_id(session, user_id)


def _worker_request_context(task: AITask) -> RequestContext:
    return RequestContext(request_id=f"ai-task:{task.id}")


def _enqueue_retry_for_task(task: AITask, *, countdown: int) -> None:
    if task.task_type in SENSITIVE_AI_TASK_TYPES:
        process_sensitive_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
    else:
        process_ai_task.apply_async(args=[str(task.id)], countdown=countdown)


async def _process_ai_task_async(task_id: UUID) -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await _handle_process(session, task_id)
    finally:
        await engine.dispose()


async def _maybe_reroute_sensitive_from_default(task_id: UUID) -> dict | None:
    """If task is stage-8 interview AI, reroute once to sensitive entry (pre-claim)."""
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            task = await get_ai_task_by_id(session, task_id, with_attempts=False)
            if task is None or task.task_type not in SENSITIVE_AI_TASK_TYPES:
                return None
            try:
                process_sensitive_ai_task.apply_async(
                    args=[str(task_id)], countdown=0
                )
            except Exception as exc:
                await record_audit(
                    session,
                    action="ai_task.sensitive_reroute_failed",
                    result="failure",
                    resource_type="ai_task",
                    request_context=_worker_request_context(task),
                    actor_user_id=None,
                    resource_id=str(task.id),
                    changes={
                        "ai_task_id": str(task.id),
                        "task_type": task.task_type,
                        "error_type": type(exc).__name__,
                    },
                )
                await session.commit()
                logger.warning(
                    "sensitive reroute failed for ai task %s type=%s error_type=%s",
                    task.id,
                    task.task_type,
                    type(exc).__name__,
                )
                return {
                    "status": "reroute_failed",
                    "reason": "interview_ai_requires_sensitive_queue",
                    "task_id": str(task_id),
                    "error_type": type(exc).__name__,
                }
            return {
                "status": "rerouted",
                "reason": "interview_ai_requires_sensitive_queue",
                "task_id": str(task_id),
            }
    finally:
        await engine.dispose()


async def _process_default_ai_task_async(task_id: UUID) -> dict:
    rerouted = await _maybe_reroute_sensitive_from_default(task_id)
    if rerouted is not None:
        return rerouted
    return await _process_ai_task_async(task_id)


async def _process_sensitive_ai_task_async(task_id: UUID) -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            task = await get_ai_task_by_id(session, task_id, with_attempts=False)
            if task is None:
                logger.warning("ai task %s not found (sensitive)", task_id)
                return {"status": "missing"}
            if task.task_type not in SENSITIVE_AI_TASK_TYPES:
                logger.info(
                    "sensitive entry rejected task %s type=%s",
                    task_id,
                    task.task_type,
                )
                return {
                    "status": "rejected",
                    "reason": "unsupported_task_type",
                    "task_type": task.task_type,
                }
    finally:
        await engine.dispose()
    return await _process_ai_task_async(task_id)


def _scrubbed_persist_error_message(exc: BaseException) -> str:
    return f"orm_persistence:{type(exc).__name__}"


async def _reassert_running_ownership_for_terminal(
    session: AsyncSession,
    *,
    task_id: UUID,
    attempt_id: UUID,
) -> AITask | None:
    """SELECT … FOR UPDATE; return task only if still running with this attempt."""
    task = (
        await session.execute(
            select(AITask).where(AITask.id == task_id).with_for_update()
        )
    ).scalar_one_or_none()
    if task is None or task.status != AI_TASK_STATUS_RUNNING:
        observed = task.status if task is not None else "missing"
        logger.info(
            "ai task %s skip terminal write: observed_status=%s error_type=stale_owner",
            task_id,
            observed,
        )
        return None
    attempt = (
        await session.execute(
            select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
        )
    ).scalar_one_or_none()
    if attempt is None or attempt.status != AI_TASK_STATUS_RUNNING:
        logger.info(
            "ai task %s skip terminal write: observed_status=%s error_type=stale_attempt",
            task_id,
            task.status,
        )
        return None
    return task


async def _skipped_stale_owner_result(
    session: AsyncSession,
    *,
    task_id: UUID,
    attempt_no: int,
) -> dict:
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    return {
        "status": "skipped_stale_owner",
        "observed_status": task.status if task is not None else "missing",
        "attempt_no": attempt_no,
    }


async def _after_task_success(
    session: AsyncSession,
    *,
    task: AITask,
    outcome: ProviderOutcome,
) -> dict[str, Any] | None:
    if task.task_type == TASK_TYPE_RESUME_PARSE and outcome.result is not None:
        from app.services.resumes import apply_resume_parse_success

        await apply_resume_parse_success(
            session, task=task, result_payload=outcome.result
        )
        return None
    if task.task_type == TASK_TYPE_RESUME_SCORE and outcome.result is not None:
        from app.services.resumes import persist_resume_score_result

        await persist_resume_score_result(
            session,
            task=task,
            raw_output=outcome.raw_response
            if isinstance(outcome.raw_response, dict)
            else None,
            normalized=outcome.result,
        )
        return None
    if (
        task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
        and outcome.result is not None
    ):
        from app.services.interview_questions import persist_question_generation_result

        version = await persist_question_generation_result(
            session,
            task_id=task.id,
            payload=outcome.result,
            actor=await _actor_for_ai_task(session, task),
            request_context=_worker_request_context(task),
        )
        version_id = getattr(version, "id", None)
        return {"version_id": str(version_id)} if version_id is not None else {}
    if (
        task.task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
        and outcome.result is not None
    ):
        from app.services.interview_analyses import persist_analysis_generation_result

        version = await persist_analysis_generation_result(
            session,
            task_id=task.id,
            payload=outcome.result,
            actor=await _actor_for_ai_task(session, task),
            request_context=_worker_request_context(task),
        )
        version_id = getattr(version, "id", None)
        return {"version_id": str(version_id)} if version_id is not None else {}
    if (
        task.task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE
        and outcome.result is not None
    ):
        from app.services.comprehensive_analyses import (
            persist_comprehensive_analysis_result,
        )

        version = await persist_comprehensive_analysis_result(
            session,
            task_id=task.id,
            payload=outcome.result,
            actor=await _actor_for_ai_task(session, task),
            request_context=_worker_request_context(task),
        )
        version_id = getattr(version, "id", None)
        return {"version_id": str(version_id)} if version_id is not None else {}
    return None


async def _after_task_failure(session: AsyncSession, *, task: AITask) -> None:
    if task.task_type == TASK_TYPE_RESUME_PARSE:
        from app.services.resumes import apply_resume_parse_failure

        await apply_resume_parse_failure(session, task=task)


def _resolve_run_ids(outcome: ProviderOutcome) -> tuple[str | None, str | None]:
    run_id = outcome.provider_run_id
    req_id = outcome.request_id
    if run_id is None or req_id is None:
        parsed_run, parsed_req = extract_dify_run_ids(
            outcome.raw_response if isinstance(outcome.raw_response, dict) else None
        )
        run_id = run_id or parsed_run
        req_id = req_id or parsed_req
    return run_id, req_id


def _output_invalid_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip():
        return code
    return "output_validation_failed"


async def _handle_process(session: AsyncSession, task_id: UUID) -> dict:
    now = datetime.now(UTC)
    claimed = await session.execute(
        update(AITask)
        .where(AITask.id == task_id, AITask.status == AI_TASK_STATUS_PENDING)
        .values(status=AI_TASK_STATUS_RUNNING, updated_at=now)
    )
    if claimed.rowcount != 1:
        task = await get_ai_task_by_id(session, task_id, with_attempts=False)
        if task is None:
            logger.warning("ai task %s not found", task_id)
            return {"status": "missing"}
        logger.info("ai task %s skip: status=%s", task_id, task.status)
        return {"status": task.status, "skipped": True}
    await session.commit()

    # Lock task row and allocate global + cycle attempt numbers.
    locked = (
        await session.execute(
            select(AITask).where(AITask.id == task_id).with_for_update()
        )
    ).scalar_one()

    max_no = (
        await session.execute(
            select(func.coalesce(func.max(AITaskAttempt.attempt_no), 0)).where(
                AITaskAttempt.task_id == task_id
            )
        )
    ).scalar_one()
    attempt_no = int(max_no) + 1
    cycle_attempt_no = int(locked.cycle_attempt_count) + 1
    locked.attempt_count = attempt_no
    locked.cycle_attempt_count = cycle_attempt_no
    locked.started_at = locked.started_at or now
    locked.updated_at = now
    locked.error_code = None
    locked.error_message = None
    locked.error_category = None

    attempt = AITaskAttempt(
        task_id=locked.id,
        attempt_no=attempt_no,
        retry_cycle_no=locked.retry_cycle_no,
        cycle_attempt_no=cycle_attempt_no,
        status=AI_TASK_STATUS_RUNNING,
        started_at=now,
    )
    await add_ai_task_attempt(session, attempt)
    await session.commit()

    attempt_id = attempt.id
    task_type = locked.task_type
    input_snapshot = dict(locked.input_snapshot or {})
    retry_cycle_no = locked.retry_cycle_no
    provider_input = input_snapshot
    load_error: BaseException | None = None

    if task_type in STAGE8_TASK_TYPES:
        try:
            provider_input = await _prepare_stage8_provider_input(session, locked)
        except _STAGE8_OUTPUT_INVALID_EXCEPTIONS as exc:
            load_error = exc

    if load_error is None:
        outcome = await _run_provider(
            task_type=task_type,
            input_snapshot=provider_input,
        )
    else:
        outcome = ProviderOutcome(
            ok=False,
            error_code=_output_invalid_code(load_error),
            error_message=str(load_error) or "frozen input failed validation",
            error_category="non_retryable",
        )

    finished = datetime.now(UTC)
    duration_ms = int((finished - now).total_seconds() * 1000)
    provider_run_id, request_id = _resolve_run_ids(outcome)

    # Reload by primary key only — never search stale task.attempts.
    task = (
        await session.execute(
            select(AITask).where(AITask.id == task_id).with_for_update()
        )
    ).scalar_one()
    attempt = (
        await session.execute(
            select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
        )
    ).scalar_one()

    if task_type in STAGE8_TASK_TYPES:
        extra = None
        if not outcome.ok and (
            load_error is not None
            or outcome.error_code == "output_validation_failed"
        ):
            extra = {
                "validation_error_code": outcome.error_code
                or "output_validation_failed"
            }
        _write_stage8_raw(
            task=task,
            attempt=attempt,
            provider_input=provider_input if load_error is None else None,
            outcome=outcome,
            extra=extra,
        )
    elif task.raw_purged_at is None:
        task.raw_request = outcome.raw_request
        task.raw_response = outcome.raw_response

    attempt.finished_at = finished
    attempt.duration_ms = duration_ms
    attempt.http_status = outcome.http_status
    attempt.provider_run_id = provider_run_id
    attempt.request_id = request_id
    if task_type not in STAGE8_TASK_TYPES:
        if isinstance(outcome.raw_response, dict):
            attempt.raw_response = outcome.raw_response
        elif outcome.raw_response is not None:
            attempt.raw_response = {"body": outcome.raw_response}

    if task.status == AI_TASK_STATUS_CANCELLED:
        attempt.status = AI_TASK_STATUS_FAILED
        attempt.error_message = "late response after cancel; not set as current result"
        await session.commit()
        return {
            "status": AI_TASK_STATUS_CANCELLED,
            "late_response": True,
            "attempt_no": attempt_no,
        }

    if outcome.ok and outcome.result is not None:
        try:
            persist_meta = await _after_task_success(
                session, task=task, outcome=outcome
            )
        except _STAGE8_OUTPUT_INVALID_EXCEPTIONS as exc:
            owned = await _reassert_running_ownership_for_terminal(
                session, task_id=task_id, attempt_id=attempt_id
            )
            if owned is None:
                return await _skipped_stale_owner_result(
                    session, task_id=task_id, attempt_no=attempt_no
                )
            task = owned
            attempt = (
                await session.execute(
                    select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
                )
            ).scalar_one()
            task.status = AI_TASK_STATUS_OUTPUT_INVALID
            task.error_code = _output_invalid_code(exc)
            task.error_message = str(exc)
            task.error_category = "non_retryable"
            task.finished_at = finished
            task.updated_at = finished
            attempt.status = AI_TASK_STATUS_OUTPUT_INVALID
            attempt.error_category = "non_retryable"
            attempt.error_message = str(exc)
            if task_type in STAGE8_TASK_TYPES:
                _write_stage8_raw(
                    task=task,
                    attempt=attempt,
                    provider_input=provider_input,
                    outcome=outcome,
                    extra={"validation_error_code": task.error_code},
                )
            else:
                await _after_task_failure(session, task=task)
            await session.commit()
            return {"status": AI_TASK_STATUS_OUTPUT_INVALID, "attempt_no": attempt_no}
        except Exception as exc:
            owned = await _reassert_running_ownership_for_terminal(
                session, task_id=task_id, attempt_id=attempt_id
            )
            if owned is None:
                return await _skipped_stale_owner_result(
                    session, task_id=task_id, attempt_no=attempt_no
                )
            task = owned
            attempt = (
                await session.execute(
                    select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
                )
            ).scalar_one()
            scrubbed = _scrubbed_persist_error_message(exc)
            task.status = AI_TASK_STATUS_FAILED
            task.error_code = "persist_failed"
            task.error_message = scrubbed
            task.error_category = "non_retryable"
            task.finished_at = finished
            task.updated_at = finished
            attempt.status = AI_TASK_STATUS_FAILED
            attempt.error_category = "non_retryable"
            attempt.error_message = scrubbed
            if task_type in STAGE8_TASK_TYPES:
                _write_stage8_raw(
                    task=task,
                    attempt=attempt,
                    provider_input=provider_input,
                    outcome=outcome,
                    extra={"persist_error_type": type(exc).__name__},
                )
            await session.commit()
            return {"status": AI_TASK_STATUS_FAILED, "attempt_no": attempt_no}

        owned = await _reassert_running_ownership_for_terminal(
            session, task_id=task_id, attempt_id=attempt_id
        )
        if owned is None:
            return await _skipped_stale_owner_result(
                session, task_id=task_id, attempt_no=attempt_no
            )
        task = owned
        attempt = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
            )
        ).scalar_one()
        task.status = AI_TASK_STATUS_SUCCEEDED
        if task_type in STAGE8_TASK_TYPES:
            _write_stage8_raw(
                task=task,
                attempt=attempt,
                provider_input=provider_input,
                outcome=outcome,
                extra=_stage8_success_extra(task, outcome, persist_meta),
            )
        else:
            task.result_payload = outcome.result
        task.finished_at = finished
        task.updated_at = finished
        attempt.status = AI_TASK_STATUS_SUCCEEDED
        await session.commit()
        return {"status": AI_TASK_STATUS_SUCCEEDED, "attempt_no": attempt_no}

    # failure path — ownership check before any terminal mutation/commit
    if outcome.error_code == "output_validation_failed" or load_error is not None:
        owned = await _reassert_running_ownership_for_terminal(
            session, task_id=task_id, attempt_id=attempt_id
        )
        if owned is None:
            return await _skipped_stale_owner_result(
                session, task_id=task_id, attempt_no=attempt_no
            )
        task = owned
        attempt = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
            )
        ).scalar_one()
        task.error_code = outcome.error_code or "provider_error"
        task.error_message = outcome.error_message or "AI provider failed"
        task.error_category = outcome.error_category or "non_retryable"
        task.status = AI_TASK_STATUS_OUTPUT_INVALID
        task.finished_at = finished
        task.updated_at = finished
        attempt.status = AI_TASK_STATUS_OUTPUT_INVALID
        attempt.error_category = task.error_category
        attempt.error_message = task.error_message
        if task_type not in STAGE8_TASK_TYPES:
            await _after_task_failure(session, task=task)
        await session.commit()
        return {"status": AI_TASK_STATUS_OUTPUT_INVALID, "attempt_no": attempt_no}

    error_category = outcome.error_category or "non_retryable"
    if should_auto_retry(
        error_category=error_category,
        cycle_attempt_count=task.cycle_attempt_count,
    ):
        owned = await _reassert_running_ownership_for_terminal(
            session, task_id=task_id, attempt_id=attempt_id
        )
        if owned is None:
            return await _skipped_stale_owner_result(
                session, task_id=task_id, attempt_no=attempt_no
            )
        task = owned
        attempt = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
            )
        ).scalar_one()
        task.error_code = outcome.error_code or "provider_error"
        task.error_message = outcome.error_message or "AI provider failed"
        task.error_category = error_category
        attempt.status = AI_TASK_STATUS_FAILED
        attempt.error_category = error_category
        attempt.error_message = task.error_message
        countdown = retry_countdown_seconds(task.cycle_attempt_count) or 10
        task.status = AI_TASK_STATUS_PENDING
        task.finished_at = None
        task.updated_at = finished
        await session.commit()
        _enqueue_retry_for_task(task, countdown=countdown)
        return {
            "status": AI_TASK_STATUS_PENDING,
            "retry_countdown": countdown,
            "attempt_no": attempt_no,
            "retry_cycle_no": retry_cycle_no,
            "cycle_attempt_no": cycle_attempt_no,
        }

    owned = await _reassert_running_ownership_for_terminal(
        session, task_id=task_id, attempt_id=attempt_id
    )
    if owned is None:
        return await _skipped_stale_owner_result(
            session, task_id=task_id, attempt_no=attempt_no
        )
    task = owned
    attempt = (
        await session.execute(
            select(AITaskAttempt).where(AITaskAttempt.id == attempt_id)
        )
    ).scalar_one()
    task.error_code = outcome.error_code or "provider_error"
    task.error_message = outcome.error_message or "AI provider failed"
    task.error_category = error_category
    task.status = AI_TASK_STATUS_FAILED
    task.finished_at = finished
    task.updated_at = finished
    attempt.status = AI_TASK_STATUS_FAILED
    attempt.error_category = error_category
    attempt.error_message = task.error_message
    if task_type not in STAGE8_TASK_TYPES:
        await _after_task_failure(session, task=task)
    await session.commit()
    return {"status": AI_TASK_STATUS_FAILED, "attempt_no": attempt_no}


def _clear_attempt_raw(attempt: AITaskAttempt, now: datetime) -> bool:
    changed = False
    if attempt.raw_response is not None:
        attempt.raw_response = None
        changed = True
    if attempt.sensitive_request_encrypted is not None:
        attempt.sensitive_request_encrypted = None
        changed = True
    if attempt.sensitive_response_encrypted is not None:
        attempt.sensitive_response_encrypted = None
        changed = True
    if attempt.response_purged_at is None:
        attempt.response_purged_at = now
        changed = True
    return changed


async def _purge_raw_payloads(session: AsyncSession, *, cutoff: datetime) -> dict:
    tasks = await list_tasks_for_raw_purge(session, cutoff=cutoff)
    purged = 0
    attempt_purged = 0
    now = datetime.now(UTC)
    for task in tasks:
        task.raw_request = None
        task.raw_response = None
        task.raw_purged_at = now
        task.updated_at = now
        purged += 1
        attempts = (
            await session.execute(
                select(AITaskAttempt).where(AITaskAttempt.task_id == task.id)
            )
        ).scalars().all()
        for attempt in attempts:
            if _clear_attempt_raw(attempt, now):
                attempt_purged += 1
    aged_attempts = (
        await session.execute(
            select(AITaskAttempt).where(
                AITaskAttempt.created_at < cutoff,
                AITaskAttempt.response_purged_at.is_(None),
                or_(
                    AITaskAttempt.raw_response.is_not(None),
                    AITaskAttempt.sensitive_request_encrypted.is_not(None),
                    AITaskAttempt.sensitive_response_encrypted.is_not(None),
                ),
            )
        )
    ).scalars().all()
    for attempt in aged_attempts:
        if _clear_attempt_raw(attempt, now):
            attempt_purged += 1
    if purged or attempt_purged:
        await session.commit()
    return {
        "purged": purged,
        "attempt_purged": attempt_purged,
        "cutoff": cutoff.isoformat(),
    }


async def _purge_expired_raw_async() -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            cutoff = datetime.now(UTC) - timedelta(
                days=settings.AI_RAW_PAYLOAD_RETENTION_DAYS
            )
            return await _purge_raw_payloads(session, cutoff=cutoff)
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.ai_tasks.process_ai_task", bind=True)
def process_ai_task(self, task_id: str) -> dict:  # noqa: ARG001
    return asyncio.run(_process_default_ai_task_async(UUID(task_id)))


@celery_app.task(name="app.workers.ai_tasks.process_sensitive_ai_task", bind=True)
def process_sensitive_ai_task(self, task_id: str) -> dict:  # noqa: ARG001
    return asyncio.run(_process_sensitive_ai_task_async(UUID(task_id)))


@celery_app.task(name="app.workers.ai_tasks.purge_expired_ai_raw_payloads")
def purge_expired_ai_raw_payloads() -> dict:
    return asyncio.run(_purge_expired_raw_async())
