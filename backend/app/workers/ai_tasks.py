from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
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
from app.services.ai_providers.base import (
    ProviderOutcome,
    retry_countdown_seconds,
    should_auto_retry,
)
from app.services.ai_providers.dify import extract_dify_run_ids, run_dify
from app.services.ai_providers.mock import run_mock
from app.services.score_validation import ScoreOutputInvalidError
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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


async def _process_ai_task_async(task_id: UUID) -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await _handle_process(session, task_id)
    finally:
        await engine.dispose()


async def _after_task_success(
    session: AsyncSession,
    *,
    task: AITask,
    outcome: ProviderOutcome,
) -> None:
    if task.task_type == TASK_TYPE_RESUME_PARSE and outcome.result is not None:
        from app.services.resumes import apply_resume_parse_success

        await apply_resume_parse_success(
            session, task=task, result_payload=outcome.result
        )
    elif task.task_type == TASK_TYPE_RESUME_SCORE and outcome.result is not None:
        from app.services.resumes import persist_resume_score_result

        await persist_resume_score_result(
            session,
            task=task,
            raw_output=outcome.raw_response
            if isinstance(outcome.raw_response, dict)
            else None,
            normalized=outcome.result,
        )


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

    outcome = await _run_provider(
        task_type=task_type,
        input_snapshot=input_snapshot,
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

    if task.raw_purged_at is None:
        task.raw_request = outcome.raw_request
        task.raw_response = outcome.raw_response

    attempt.finished_at = finished
    attempt.duration_ms = duration_ms
    attempt.http_status = outcome.http_status
    attempt.provider_run_id = provider_run_id
    attempt.request_id = request_id
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
            await _after_task_success(session, task=task, outcome=outcome)
        except ScoreOutputInvalidError as exc:
            task.status = AI_TASK_STATUS_OUTPUT_INVALID
            task.error_code = "output_validation_failed"
            task.error_message = str(exc)
            task.error_category = "non_retryable"
            task.finished_at = finished
            task.updated_at = finished
            attempt.status = AI_TASK_STATUS_OUTPUT_INVALID
            attempt.error_category = "non_retryable"
            attempt.error_message = str(exc)
            await _after_task_failure(session, task=task)
            await session.commit()
            return {"status": AI_TASK_STATUS_OUTPUT_INVALID, "attempt_no": attempt_no}

        task.status = AI_TASK_STATUS_SUCCEEDED
        task.result_payload = outcome.result
        task.finished_at = finished
        task.updated_at = finished
        attempt.status = AI_TASK_STATUS_SUCCEEDED
        await session.commit()
        return {"status": AI_TASK_STATUS_SUCCEEDED, "attempt_no": attempt_no}

    # failure path
    task.error_code = outcome.error_code or "provider_error"
    task.error_message = outcome.error_message or "AI provider failed"
    task.error_category = outcome.error_category or "non_retryable"
    attempt.status = AI_TASK_STATUS_FAILED
    attempt.error_category = task.error_category
    attempt.error_message = task.error_message

    if outcome.error_code == "output_validation_failed":
        task.status = AI_TASK_STATUS_OUTPUT_INVALID
        task.finished_at = finished
        task.updated_at = finished
        await _after_task_failure(session, task=task)
        await session.commit()
        return {"status": AI_TASK_STATUS_OUTPUT_INVALID, "attempt_no": attempt_no}

    if should_auto_retry(
        error_category=task.error_category,
        cycle_attempt_count=task.cycle_attempt_count,
    ):
        countdown = retry_countdown_seconds(task.cycle_attempt_count) or 10
        task.status = AI_TASK_STATUS_PENDING
        task.finished_at = None
        task.updated_at = finished
        await session.commit()
        process_ai_task.apply_async(args=[str(task.id)], countdown=countdown)
        return {
            "status": AI_TASK_STATUS_PENDING,
            "retry_countdown": countdown,
            "attempt_no": attempt_no,
            "retry_cycle_no": retry_cycle_no,
            "cycle_attempt_no": cycle_attempt_no,
        }

    task.status = AI_TASK_STATUS_FAILED
    task.finished_at = finished
    task.updated_at = finished
    await _after_task_failure(session, task=task)
    await session.commit()
    return {"status": AI_TASK_STATUS_FAILED, "attempt_no": attempt_no}


async def _purge_expired_raw_async() -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            cutoff = datetime.now(UTC) - timedelta(
                days=settings.AI_RAW_PAYLOAD_RETENTION_DAYS
            )
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
                    if attempt.raw_response is not None:
                        attempt.raw_response = None
                        attempt_purged += 1
                    if attempt.response_purged_at is None:
                        attempt.response_purged_at = now
            aged_attempts = (
                await session.execute(
                    select(AITaskAttempt).where(
                        AITaskAttempt.raw_response.is_not(None),
                        AITaskAttempt.created_at < cutoff,
                        AITaskAttempt.response_purged_at.is_(None),
                    )
                )
            ).scalars().all()
            for attempt in aged_attempts:
                attempt.raw_response = None
                attempt.response_purged_at = now
                attempt_purged += 1
            if purged or attempt_purged:
                await session.commit()
            return {
                "purged": purged,
                "attempt_purged": attempt_purged,
                "cutoff": cutoff.isoformat(),
            }
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.ai_tasks.process_ai_task", bind=True)
def process_ai_task(self, task_id: str) -> dict:  # noqa: ARG001
    return asyncio.run(_process_ai_task_async(UUID(task_id)))


@celery_app.task(name="app.workers.ai_tasks.purge_expired_ai_raw_payloads")
def purge_expired_ai_raw_payloads() -> dict:
    return asyncio.run(_purge_expired_raw_async())
