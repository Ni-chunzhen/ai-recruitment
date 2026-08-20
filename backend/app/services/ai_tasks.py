from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_CANCELLED,
    AI_TASK_STATUS_FAILED,
    AI_TASK_STATUS_OUTPUT_INVALID,
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_JOB,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
    TASK_TYPES,
    AITask,
    AITaskAttempt,
)
from app.models.job import JOB_STATUS_CLOSED, JOB_STATUS_DRAFT
from app.repositories.ai_tasks import (
    AITaskNotFoundError,
    add_ai_task,
    count_ai_tasks_by_business,
    get_ai_task_by_id,
    list_ai_tasks_by_business,
)
from app.repositories.ai_tasks import (
    list_admin_ai_tasks as list_admin_ai_tasks_rows,
)
from app.repositories.jobs import (
    JobNotFoundError,
    create_draft_version_from_base,
    get_job_by_id,
    get_version_by_id,
)
from app.schemas.ai_task import (
    AITaskAdminDetailOut,
    AITaskAdminListItemOut,
    AITaskAdminListResponse,
    AITaskAttemptAdminOut,
    AITaskAttemptOut,
    AITaskListResponse,
    AITaskSummaryOut,
    CreateAITaskRequest,
    MarkStaleFailedAITaskOut,
)
from app.schemas.job import JobDetail, structured_jd_to_dict
from app.services.audit import RequestContext, record_audit
from app.services.error_sanitizer import sanitize_error_message
from app.services.jobs import to_job_detail


class AITaskValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AITaskStateError(Exception):
    pass


STALE_RUNNING_MIN_AGE = timedelta(minutes=5)
STALE_RUNNING_RECOVERED_MESSAGE = "stale running recovered"


def enqueue_ai_task(task_id: UUID, *, countdown: int = 0) -> None:
    """Enqueue Celery job. Patchable in unit tests."""
    from app.workers.ai_tasks import process_ai_task

    process_ai_task.apply_async(args=[str(task_id)], countdown=countdown)


def enqueue_sensitive_question_task(task_id: UUID, *, countdown: int = 0) -> None:
    """Enqueue INTERVIEW_QUESTION_GENERATE onto the sensitive Celery task."""
    from app.workers.ai_tasks import process_sensitive_ai_task

    process_sensitive_ai_task.apply_async(args=[str(task_id)], countdown=countdown)


def to_ai_task_out(task: AITask, *, include_attempts: bool = True) -> AITaskSummaryOut:
    attempts: list[AITaskAttemptOut] = []
    if include_attempts:
        for attempt in task.attempts or []:
            attempts.append(
                AITaskAttemptOut(
                    id=attempt.id,
                    attempt_no=attempt.attempt_no,
                    retry_cycle_no=attempt.retry_cycle_no,
                    cycle_attempt_no=attempt.cycle_attempt_no,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    finished_at=attempt.finished_at,
                    duration_ms=attempt.duration_ms,
                    http_status=attempt.http_status,
                    error_category=attempt.error_category,
                    error_message=attempt.error_message,
                    created_at=attempt.created_at,
                )
            )
    return AITaskSummaryOut(
        id=task.id,
        task_type=task.task_type,  # type: ignore[arg-type]
        status=task.status,  # type: ignore[arg-type]
        business_type=task.business_type,
        business_id=task.business_id,
        version_id=task.version_id,
        created_by=task.created_by,
        error_code=task.error_code,
        error_message=task.error_message,
        error_category=task.error_category,  # type: ignore[arg-type]
        attempt_count=task.attempt_count,
        retry_cycle_no=task.retry_cycle_no,
        cycle_attempt_count=task.cycle_attempt_count,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        attempts=attempts,
    )


async def _ensure_job_draft(
    session: AsyncSession,
    *,
    job_id: UUID,
    actor: User,
) -> tuple[Any, Any]:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status == JOB_STATUS_CLOSED:
        raise AITaskStateError("closed job cannot run AI tasks")

    draft = get_version_by_id(job, job.draft_version_id)
    if draft is None:
        if job.status == JOB_STATUS_DRAFT:
            raise AITaskStateError("draft version missing")
        current = get_version_by_id(job, job.current_version_id)
        if current is None:
            raise AITaskStateError("current version missing")
        await create_draft_version_from_base(
            session, job=job, base=current, actor_id=actor.id
        )
        await session.refresh(job, attribute_names=["versions"])
        draft = get_version_by_id(job, job.draft_version_id)
        if draft is None:
            raise AITaskStateError("failed to create draft version")
    return job, draft


def _build_input_snapshot(
    *,
    task_type: str,
    job: Any,
    draft: Any,
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    override = override or {}
    structured = structured_jd_to_dict(draft.structured_jd)
    job_title = str(override.get("job_title") or job.name or "").strip()
    department = str(override.get("department") or job.department or "").strip()

    if task_type == TASK_TYPE_JD_PARSE:
        raw_jd_text = override.get("raw_jd_text")
        if raw_jd_text is None:
            raw_jd_text = draft.raw_jd_text or ""
        if not str(raw_jd_text).strip():
            raise AITaskValidationError("raw_jd_text is required for JD_PARSE")
        return {
            "raw_jd_text": str(raw_jd_text),
            "job_title": job_title or "未命名岗位",
            "department": department,
        }

    if task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        override_structured = override.get("structured_jd")
        if isinstance(override_structured, dict):
            structured = structured_jd_to_dict(override_structured)
        for key in (
            "responsibilities",
            "requirements",
            "must_have",
            "nice_to_have",
            "skills",
        ):
            if key in override and override[key] is not None:
                structured[key] = list(override[key] or [])

        jd_content = override.get("jd_content")
        if jd_content is None:
            jd_content = draft.raw_jd_text or ""

        return {
            "job_title": job_title or "未命名岗位",
            "department": department,
            "jd_content": str(jd_content or ""),
            "structured_jd": structured,
            # keep flat fields for mock provider compatibility
            "skills": list(structured.get("skills") or []),
            "requirements": list(structured.get("requirements") or []),
            "must_have": list(structured.get("must_have") or []),
            "nice_to_have": list(structured.get("nice_to_have") or []),
            "responsibilities": list(structured.get("responsibilities") or []),
        }

    raise AITaskValidationError(f"unsupported task_type: {task_type}")


async def create_ai_task(
    session: AsyncSession,
    *,
    payload: CreateAITaskRequest,
    actor: User,
    request_context: RequestContext,
) -> AITaskSummaryOut:
    if payload.task_type not in TASK_TYPES:
        raise AITaskValidationError(f"unsupported task_type: {payload.task_type}")
    if payload.business_type != BUSINESS_TYPE_JOB:
        raise AITaskValidationError("only business_type=job is supported in phase C")

    job, draft = await _ensure_job_draft(
        session, job_id=payload.business_id, actor=actor
    )
    input_snapshot = _build_input_snapshot(
        task_type=payload.task_type,
        job=job,
        draft=draft,
        override=payload.input,
    )

    now = datetime.now(UTC)
    task = AITask(
        task_type=payload.task_type,
        status=AI_TASK_STATUS_PENDING,
        business_type=payload.business_type,
        business_id=job.id,
        version_id=draft.id,
        created_by=actor.id,
        input_snapshot=input_snapshot,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    await add_ai_task(session, task)

    await record_audit(
        session,
        action="job.ai_task.create",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={
            "ai_task_id": str(task.id),
            "task_type": task.task_type,
            "version_id": str(draft.id),
            "ai_provider": get_settings().AI_PROVIDER,
        },
    )
    await session.commit()

    enqueue_ai_task(task.id)
    refreshed = await get_ai_task_by_id(session, task.id)
    assert refreshed is not None
    return to_ai_task_out(refreshed)


async def get_ai_task(session: AsyncSession, task_id: UUID) -> AITaskSummaryOut:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    return to_ai_task_out(task)


async def list_ai_tasks(
    session: AsyncSession,
    *,
    business_type: str,
    business_id: UUID,
) -> AITaskListResponse:
    items = await list_ai_tasks_by_business(
        session,
        business_type=business_type,
        business_id=business_id,
        with_attempts=True,
    )
    total = await count_ai_tasks_by_business(
        session, business_type=business_type, business_id=business_id
    )
    return AITaskListResponse(
        items=[to_ai_task_out(item) for item in items],
        total=total,
    )


async def retry_ai_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> AITaskSummaryOut:
    result = await session.execute(
        select(AITask).where(AITask.id == task_id).with_for_update()
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    if task.status not in {AI_TASK_STATUS_FAILED, AI_TASK_STATUS_OUTPUT_INVALID}:
        raise AITaskStateError("only failed or output_invalid tasks can be retried")

    now = datetime.now(UTC)
    task.status = AI_TASK_STATUS_PENDING
    # Keep global attempt_count; open a new manual retry cycle.
    task.retry_cycle_no = int(task.retry_cycle_no or 0) + 1
    task.cycle_attempt_count = 0
    task.error_code = None
    task.error_message = None
    task.error_category = None
    task.result_payload = None
    task.started_at = None
    task.finished_at = None
    task.updated_at = now
    await session.flush()

    await record_audit(
        session,
        action="job.ai_task.retry",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(task.business_id),
        changes={
            "ai_task_id": str(task.id),
            "task_type": task.task_type,
            "retry_cycle_no": task.retry_cycle_no,
            "attempt_count": task.attempt_count,
        },
    )
    await session.commit()

    if task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        enqueue_sensitive_question_task(task.id)
    else:
        enqueue_ai_task(task.id)
    refreshed = await get_ai_task_by_id(session, task.id)
    assert refreshed is not None
    return to_ai_task_out(refreshed)


async def cancel_ai_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor: User,
    request_context: RequestContext,
    reason: str | None = None,
) -> AITaskSummaryOut:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_PENDING:
        raise AITaskStateError("only pending tasks can be cancelled")

    now = datetime.now(UTC)
    message = (reason or "").strip() or "cancelled"
    claimed = await session.execute(
        update(AITask)
        .where(AITask.id == task_id, AITask.status == AI_TASK_STATUS_PENDING)
        .values(
            status=AI_TASK_STATUS_CANCELLED,
            finished_at=now,
            updated_at=now,
            error_code="cancelled",
            error_message=message,
        )
    )
    if claimed.rowcount != 1:
        raise AITaskStateError("only pending tasks can be cancelled")

    await record_audit(
        session,
        action="ai_task.cancel",
        result="success",
        resource_type="ai_task",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(task.id),
        changes={
            "task_type": task.task_type,
            "business_id": str(task.business_id),
            "reason": message,
        },
    )
    await session.commit()
    refreshed = await get_ai_task_by_id(session, task.id)
    assert refreshed is not None
    return to_ai_task_out(refreshed)


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def mark_stale_failed_ai_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    expected_updated_at: datetime,
    actor: User,
    request_context: RequestContext,
) -> MarkStaleFailedAITaskOut:
    now = datetime.now(UTC)
    expected = _normalize_utc(expected_updated_at)

    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_RUNNING:
        raise AITaskStateError("only running tasks can be marked stale-failed")
    task_updated = _normalize_utc(task.updated_at)
    if task_updated != expected:
        raise AITaskStateError("expected_updated_at mismatch")
    if task_updated > now - STALE_RUNNING_MIN_AGE:
        raise AITaskStateError("running task is not stale enough")

    previous_status = task.status
    claimed = await session.execute(
        update(AITask)
        .where(
            AITask.id == task_id,
            AITask.status == AI_TASK_STATUS_RUNNING,
            AITask.updated_at == expected,
            AITask.updated_at <= now - STALE_RUNNING_MIN_AGE,
        )
        .values(
            status=AI_TASK_STATUS_FAILED,
            error_code="stale_running_recovered",
            error_category="non_retryable",
            error_message=STALE_RUNNING_RECOVERED_MESSAGE,
            finished_at=now,
            updated_at=now,
        )
    )
    if claimed.rowcount != 1:
        raise AITaskStateError("stale running recovery conflict")

    await session.execute(
        update(AITaskAttempt)
        .where(
            AITaskAttempt.task_id == task_id,
            AITaskAttempt.status == AI_TASK_STATUS_RUNNING,
        )
        .values(
            status=AI_TASK_STATUS_FAILED,
            error_category="non_retryable",
            error_message=STALE_RUNNING_RECOVERED_MESSAGE,
            finished_at=now,
        )
    )

    await record_audit(
        session,
        action="ai_task.stale_running_recovered",
        result="success",
        resource_type="ai_task",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(task.id),
        changes={
            "ai_task_id": str(task.id),
            "task_type": task.task_type,
            "previous_status": previous_status,
            "new_status": AI_TASK_STATUS_FAILED,
            "expected_updated_at": expected.isoformat(),
            "error_code": "stale_running_recovered",
        },
    )
    await session.commit()
    refreshed = await get_ai_task_by_id(session, task.id)
    assert refreshed is not None
    return MarkStaleFailedAITaskOut(
        id=refreshed.id,
        status=refreshed.status,
        error_code=refreshed.error_code,
        updated_at=refreshed.updated_at,
        finished_at=refreshed.finished_at,
    )


async def apply_ai_task_result(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor: User,
    request_context: RequestContext,
) -> JobDetail:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_SUCCEEDED or not task.result_payload:
        raise AITaskStateError("only succeeded tasks with result can be applied")
    if task.business_type != BUSINESS_TYPE_JOB:
        raise AITaskStateError("unsupported business_type")

    job, draft = await _ensure_job_draft(
        session, job_id=task.business_id, actor=actor
    )

    if task.task_type == TASK_TYPE_JD_PARSE:
        structured = structured_jd_to_dict(task.result_payload)
        draft.structured_jd = structured
        # Do not overwrite existing raw_jd_text unless empty
        if not (draft.raw_jd_text or "").strip():
            raw_from_input = (task.input_snapshot or {}).get("raw_jd_text")
            if raw_from_input:
                draft.raw_jd_text = str(raw_from_input)
        # JD_PARSE 串联能力维度时一并写入草稿
        dims = (task.result_payload or {}).get("dimensions")
        if isinstance(dims, list) and dims:
            draft.score_dimensions = deepcopy(dims)
    elif task.task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        dimensions = task.result_payload.get("dimensions") or []
        draft.score_dimensions = deepcopy(dimensions)
    else:
        raise AITaskValidationError(f"unsupported task_type: {task.task_type}")

    draft.updated_at = datetime.now(UTC)
    job.updated_by = actor.id
    job.updated_at = datetime.now(UTC)
    task.version_id = draft.id
    await session.flush()

    await record_audit(
        session,
        action="job.ai_task.apply",
        result="success",
        resource_type="job",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(job.id),
        changes={
            "ai_task_id": str(task.id),
            "task_type": task.task_type,
            "draft_version_id": str(draft.id),
        },
    )
    await session.commit()
    refreshed = await get_job_by_id(session, job.id)
    assert refreshed is not None
    return to_job_detail(refreshed)


def _duration_ms(
    started_at: datetime | None, finished_at: datetime | None
) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return int((finished_at - started_at).total_seconds() * 1000)


def to_admin_list_item(task: AITask) -> AITaskAdminListItemOut:
    return AITaskAdminListItemOut(
        id=task.id,
        task_type=task.task_type,  # type: ignore[arg-type]
        business_type=task.business_type,
        business_id=task.business_id,
        status=task.status,  # type: ignore[arg-type]
        attempt_count=task.attempt_count,
        retry_cycle_no=task.retry_cycle_no,
        cycle_attempt_count=task.cycle_attempt_count,
        error_category=task.error_category,  # type: ignore[arg-type]
        error_code=task.error_code,
        error_message=sanitize_error_message(task.error_message),
        created_by=task.created_by,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        duration_ms=_duration_ms(task.started_at, task.finished_at),
    )


def to_admin_detail(task: AITask) -> AITaskAdminDetailOut:
    attempts: list[AITaskAttemptAdminOut] = []
    for attempt in task.attempts or []:
        attempts.append(
            AITaskAttemptAdminOut(
                id=attempt.id,
                attempt_no=attempt.attempt_no,
                retry_cycle_no=attempt.retry_cycle_no,
                cycle_attempt_no=attempt.cycle_attempt_no,
                status=attempt.status,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                duration_ms=attempt.duration_ms,
                http_status=attempt.http_status,
                error_category=attempt.error_category,
                error_message=sanitize_error_message(attempt.error_message),
                provider_run_id=attempt.provider_run_id,
                request_id=attempt.request_id,
            )
        )
    base = to_admin_list_item(task)
    return AITaskAdminDetailOut(**base.model_dump(), attempts=attempts)


async def list_admin_ai_tasks(
    session: AsyncSession,
    *,
    task_type: str | None = None,
    status: str | None = None,
    business_type: str | None = None,
    business_id: UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AITaskAdminListResponse:
    rows, total = await list_admin_ai_tasks_rows(
        session,
        task_type=task_type,
        status=status,
        business_type=business_type,
        business_id=business_id,
        created_from=created_from,
        created_to=created_to,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return AITaskAdminListResponse(
        items=[to_admin_list_item(item) for item in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_admin_ai_task(
    session: AsyncSession, task_id: UUID
) -> AITaskAdminDetailOut:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise AITaskNotFoundError("ai task not found")
    return to_admin_detail(task)
