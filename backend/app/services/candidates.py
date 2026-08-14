from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.ai_task import TASK_TYPE_RESUME_SCORE
from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
    APPLICATION_STATUS_TERMINATED,
    APPLICATION_STATUS_TRANSFERRED,
    CLOSE_ACTION_REJECT,
    CLOSE_ACTION_TERMINATE,
    CLOSE_ACTION_TRANSFER,
    INTERVIEW_TASK_ACTIVE,
    INTERVIEW_TASK_NONE,
    INTERVIEW_TASK_PENDING_CANCEL,
    INTERVIEW_TASK_PENDING_REBUILD,
    TIMELINE_EVENT_VERSION_MIGRATED,
    JobApplication,
)
from app.models.job import (
    JOB_STATUS_OPEN,
    VERSION_STATUS_PUBLISHED,
)
from app.repositories.candidates import (
    CandidateNotFoundError,
    count_in_flight_applications,
    create_application,
    create_candidate,
    get_application_by_id,
    list_applications_for_job,
)
from app.repositories.jobs import JobNotFoundError, get_job_by_id, get_version_by_id
from app.repositories.resumes import mark_current_results_stale
from app.schemas.candidate import (
    ClosePreviewItem,
    ClosePreviewResponse,
    CreateCandidateRequest,
    JobApplicationListResponse,
    JobApplicationOut,
    MigrateVersionRequest,
    ResolveCloseRequest,
)
from app.services.audit import RequestContext, record_audit


class CandidateStateError(Exception):
    pass


def _interview_task_state_for_create(*, interview_started: bool) -> str:
    if interview_started:
        return INTERVIEW_TASK_ACTIVE
    return INTERVIEW_TASK_NONE


def to_application_out(application: JobApplication) -> JobApplicationOut:
    candidate = application.candidate
    return JobApplicationOut(
        id=application.id,
        candidate_id=application.candidate_id,
        candidate_name=candidate.name if candidate else "",
        candidate_phone=candidate.phone if candidate else None,
        candidate_email=candidate.email if candidate else None,
        job_id=application.job_id,
        job_version_id=application.job_version_id,
        status=application.status,  # type: ignore[arg-type]
        pipeline_status=getattr(application, "pipeline_status", "pending_hr_screen"),
        resume_version_id=getattr(application, "resume_version_id", None),
        lock_version=getattr(application, "lock_version", 1),
        interview_started=application.interview_started,
        interview_task_state=application.interview_task_state,  # type: ignore[arg-type]
        close_action=application.close_action,
        close_reason=application.close_reason,
        transferred_to_job_id=application.transferred_to_job_id,
        previous_version_id=application.previous_version_id,
        migration_reason=application.migration_reason,
        migrated_at=application.migrated_at,
        migrated_by=application.migrated_by,
        timeline_events=list(application.timeline_events or []),
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


def _append_timeline_event(
    application: JobApplication,
    *,
    event_type: str,
    actor_id: UUID | None,
    from_version_id: UUID | None,
    to_version_id: UUID | None,
    reason: str | None,
) -> None:
    events = list(application.timeline_events or [])
    events.append(
        {
            "type": event_type,
            "at": datetime.now(UTC).isoformat(),
            "actor_id": str(actor_id) if actor_id else None,
            "from_version_id": str(from_version_id) if from_version_id else None,
            "to_version_id": str(to_version_id) if to_version_id else None,
            "reason": reason,
        }
    )
    application.timeline_events = events


def _mark_interview_pending_cancel(application: JobApplication) -> None:
    if application.interview_started:
        application.interview_task_state = INTERVIEW_TASK_PENDING_CANCEL


async def create_job_candidate(
    session: AsyncSession,
    *,
    job_id: UUID,
    payload: CreateCandidateRequest,
    actor: User,
    request_context: RequestContext,
) -> JobApplicationOut:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")

    bound_version_id = job.current_version_id or job.draft_version_id
    if bound_version_id is None:
        raise CandidateStateError("job has no version to bind")

    version = get_version_by_id(job, bound_version_id)
    if version is None:
        raise CandidateStateError("bound version not found")

    candidate = await create_candidate(
        session,
        name=payload.name.strip(),
        phone=payload.phone,
        email=payload.email,
    )
    application = await create_application(
        session,
        candidate_id=candidate.id,
        job_id=job.id,
        job_version_id=bound_version_id,
        interview_started=payload.interview_started,
        interview_task_state=_interview_task_state_for_create(
            interview_started=payload.interview_started
        ),
    )
    await session.refresh(application, attribute_names=["candidate"])

    await record_audit(
        session,
        action="candidate.create",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "job_id": str(job.id),
            "candidate_id": str(candidate.id),
            "job_version_id": str(bound_version_id),
            "interview_started": payload.interview_started,
        },
    )
    await session.commit()
    refreshed = await get_application_by_id(session, application_id=application.id)
    assert refreshed is not None
    return to_application_out(refreshed)


async def list_job_candidates(
    session: AsyncSession,
    *,
    job_id: UUID,
    in_flight_only: bool = False,
) -> JobApplicationListResponse:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    items = await list_applications_for_job(
        session, job_id=job_id, in_flight_only=in_flight_only
    )
    return JobApplicationListResponse(
        items=[to_application_out(item) for item in items],
        total=len(items),
    )


async def get_close_preview(
    session: AsyncSession,
    *,
    job_id: UUID,
) -> ClosePreviewResponse:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    items = await list_applications_for_job(
        session, job_id=job_id, in_flight_only=True
    )
    preview_items = [
        ClosePreviewItem(
            application_id=item.id,
            candidate_id=item.candidate_id,
            candidate_name=item.candidate.name if item.candidate else "",
            status=item.status,  # type: ignore[arg-type]
            interview_started=item.interview_started,
            job_version_id=item.job_version_id,
        )
        for item in items
    ]
    count = len(preview_items)
    return ClosePreviewResponse(
        can_close=count == 0,
        in_flight_count=count,
        items=preview_items,
    )


async def resolve_close_application(
    session: AsyncSession,
    *,
    job_id: UUID,
    application_id: UUID,
    payload: ResolveCloseRequest,
    actor: User,
    request_context: RequestContext,
) -> JobApplicationOut:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")

    application = await get_application_by_id(
        session, application_id=application_id, job_id=job_id
    )
    if application is None:
        raise CandidateNotFoundError("application not found")
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise CandidateStateError("only in-progress applications can be resolved")

    reason = payload.reason.strip()
    if not reason:
        raise CandidateStateError("reason is required")

    now = datetime.now(UTC)
    transferred_application: JobApplication | None = None

    if payload.action == CLOSE_ACTION_TRANSFER:
        if payload.target_job_id is None:
            raise CandidateStateError("target_job_id is required for transfer")
        if payload.target_job_id == job_id:
            raise CandidateStateError("cannot transfer to the same job")

        target = await get_job_by_id(session, payload.target_job_id)
        if target is None:
            raise JobNotFoundError("target job not found")
        if target.status != JOB_STATUS_OPEN:
            raise CandidateStateError("target job must be open")
        if target.current_version_id is None:
            raise CandidateStateError("target job has no current version")

        application.status = APPLICATION_STATUS_TRANSFERRED
        application.close_action = CLOSE_ACTION_TRANSFER
        application.close_reason = reason
        application.transferred_to_job_id = target.id
        _mark_interview_pending_cancel(application)
        application.updated_at = now

        transferred_application = await create_application(
            session,
            candidate_id=application.candidate_id,
            job_id=target.id,
            job_version_id=target.current_version_id,
            interview_started=False,
            interview_task_state=INTERVIEW_TASK_NONE,
            status=APPLICATION_STATUS_IN_PROGRESS,
        )
    elif payload.action == CLOSE_ACTION_REJECT:
        application.status = APPLICATION_STATUS_REJECTED
        application.close_action = CLOSE_ACTION_REJECT
        application.close_reason = reason
        _mark_interview_pending_cancel(application)
        application.updated_at = now
    elif payload.action == CLOSE_ACTION_TERMINATE:
        application.status = APPLICATION_STATUS_TERMINATED
        application.close_action = CLOSE_ACTION_TERMINATE
        application.close_reason = reason
        _mark_interview_pending_cancel(application)
        application.updated_at = now
    else:
        raise CandidateStateError(f"unsupported close action: {payload.action}")

    await session.flush()
    await record_audit(
        session,
        action="candidate.resolve_close",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "action": payload.action,
            "reason": reason,
            "job_id": str(job_id),
            "status": application.status,
            "transferred_to_job_id": (
                str(payload.target_job_id) if payload.target_job_id else None
            ),
            "new_application_id": (
                str(transferred_application.id) if transferred_application else None
            ),
        },
    )
    await session.commit()
    refreshed = await get_application_by_id(
        session, application_id=application.id, job_id=job_id
    )
    assert refreshed is not None
    return to_application_out(refreshed)


async def migrate_application_version(
    session: AsyncSession,
    *,
    job_id: UUID,
    application_id: UUID,
    payload: MigrateVersionRequest,
    actor: User,
    request_context: RequestContext,
) -> JobApplicationOut:
    job = await get_job_by_id(session, job_id)
    if job is None:
        raise JobNotFoundError("job not found")

    application = await get_application_by_id(
        session, application_id=application_id, job_id=job_id
    )
    if application is None:
        raise CandidateNotFoundError("application not found")
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise CandidateStateError("only in-progress applications can be migrated")

    to_version = get_version_by_id(job, payload.to_version_id)
    if to_version is None:
        raise CandidateStateError("target version not found on this job")
    is_current = job.current_version_id == to_version.id
    if to_version.status != VERSION_STATUS_PUBLISHED and not is_current:
        raise CandidateStateError(
            "target version must be published or the current version"
        )
    if to_version.id == application.job_version_id:
        raise CandidateStateError("already bound to target version")

    reason = (payload.reason or "").strip() or None
    if application.interview_started and not reason:
        raise CandidateStateError(
            "reason is required when interview has started"
        )

    from_version_id = application.job_version_id
    now = datetime.now(UTC)
    application.previous_version_id = from_version_id
    application.job_version_id = to_version.id
    application.migration_reason = reason
    application.migrated_at = now
    application.migrated_by = actor.id
    application.updated_at = now

    if application.interview_started:
        # Placeholder only: no real cancel/rebuild of interview tasks.
        application.interview_task_state = INTERVIEW_TASK_PENDING_REBUILD

    _append_timeline_event(
        application,
        event_type=TIMELINE_EVENT_VERSION_MIGRATED,
        actor_id=actor.id,
        from_version_id=from_version_id,
        to_version_id=to_version.id,
        reason=reason,
    )
    await session.flush()

    stale_count = await mark_current_results_stale(
        session,
        application_id=application.id,
        result_type=TASK_TYPE_RESUME_SCORE,
    )

    await record_audit(
        session,
        action="candidate.migrate_version",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "job_id": str(job_id),
            "from_version_id": str(from_version_id),
            "to_version_id": str(to_version.id),
            "reason": reason,
            "interview_started": application.interview_started,
            "interview_task_state": application.interview_task_state,
            "score_marked_stale": stale_count,
        },
    )
    await session.commit()
    refreshed = await get_application_by_id(
        session, application_id=application.id, job_id=job_id
    )
    assert refreshed is not None
    return to_application_out(refreshed)


async def assert_job_can_close(session: AsyncSession, *, job_id: UUID) -> None:
    in_flight = await count_in_flight_applications(session, job_id=job_id)
    if in_flight > 0:
        raise CandidateStateError(
            f"cannot close job with {in_flight} in-flight candidates; "
            "resolve them first"
        )
