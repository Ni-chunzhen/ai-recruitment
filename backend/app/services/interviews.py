from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import (
    ABNORMAL_REASON_CATALOG,
    CANCEL_REASON_CATALOG,
    INTERVIEW_FORMAT_OFFLINE,
    INTERVIEW_FORMAT_ONLINE,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_SCHEDULED,
    MEETING_MODE_ADAPTER,
    MEETING_MODE_MANUAL,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_CANCELLED,
    SCHEDULE_STATUS_SUPERSEDED,
    TERMINAL_ROUND_STATUSES,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
    InterviewSchedule,
    list_interview_reason_catalog,
)
from app.models.resume import PIPELINE_INTERVIEWING
from app.repositories.interviews import (
    InterviewNotFoundError,
    actor_assigned_to_application,
    actor_assigned_to_round,
    add_idempotency,
    add_round,
    add_schedule,
    find_candidate_conflicts,
    find_idempotency,
    find_interviewer_conflicts,
    get_active_schedule_for_update,
    get_round_by_id,
    get_round_for_update,
    get_users_by_ids,
    list_rounds_for_application,
    list_rounds_for_application_for_update,
    list_users_with_permission,
    next_sequence_no,
)
from app.repositories.jobs import get_job_by_id, get_version_by_id
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id
from app.schemas.interview import (
    InterviewAbnormalEndRequest,
    InterviewCancelRequest,
    InterviewConflictCheckRequest,
    InterviewConflictItemOut,
    InterviewConflictOut,
    InterviewerAssignmentIn,
    InterviewerOut,
    InterviewReasonCodeItem,
    InterviewReasonCodeListResponse,
    InterviewRescheduleRequest,
    InterviewRoundActionOut,
    InterviewRoundActionRequest,
    InterviewRoundCreate,
    InterviewRoundOut,
    InterviewRoundReorderRequest,
    InterviewRoundUpdate,
    InterviewScheduleCreate,
    InterviewScheduleSummaryOut,
    InterviewStaffItemOut,
    InterviewStaffListResponse,
    InterviewTimelineOut,
)
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, encrypt_secret
from app.services.interview_conflicts import (
    validate_schedule_window,
)
from app.services.interview_state import (
    InterviewStateError,
    filter_actions_for_actor,
    next_status,
)

OPTIMISTIC_LOCK_MESSAGE = "面试信息已被其他人员更新，请刷新后重试"
CANCEL_REASON_CODES = {code for code, _ in CANCEL_REASON_CATALOG}
ABNORMAL_REASON_CODES = {code for code, _ in ABNORMAL_REASON_CATALOG}


class InterviewValidationError(Exception):
    pass


class InterviewForbiddenError(Exception):
    pass


class InterviewOptimisticLockError(Exception):
    pass


class InterviewIdempotencyConflictError(Exception):
    pass


class InterviewConflictError(Exception):
    def __init__(self, message: str, payload: InterviewConflictOut | None = None):
        super().__init__(message)
        self.payload = payload


def list_interview_reason_codes() -> InterviewReasonCodeListResponse:
    return InterviewReasonCodeListResponse(
        items=[
            InterviewReasonCodeItem.model_validate(item)
            for item in list_interview_reason_catalog()
        ]
    )


async def _has_permission(actor: User, code: str) -> bool:
    codes = getattr(actor, "permission_codes", None)
    if codes is not None:
        return code in codes
    return await user_has_permission(actor, code)


async def _require_manage(actor: User) -> None:
    if not await _has_permission(actor, "recruitment.manage"):
        raise InterviewForbiddenError("forbidden")


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_meeting_password_encrypted(
    payload: InterviewScheduleCreate,
    previous_encrypted: str | None = None,
) -> str | None:
    if payload.clear_meeting_password:
        if payload.meeting_password:
            raise InterviewValidationError(
                "cannot set and clear meeting password together"
            )
        return None
    if payload.meeting_password:
        try:
            return encrypt_secret(payload.meeting_password)
        except EncryptionError as exc:
            raise InterviewValidationError(
                "meeting password encryption failed"
            ) from exc
    return previous_encrypted


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = phone.strip()
    if len(digits) < 7:
        return "****"
    return f"{digits[:3]}****{digits[-4:]}"


def _canonical_hash(payload: dict[str, Any]) -> str:
    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: _strip(item)
                for key, item in value.items()
                if key
                not in {
                    "meeting_password",
                    "meeting_password_encrypted",
                    "contact_phone",
                }
            }
        if isinstance(value, list):
            return [_strip(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    canonical = json.dumps(_strip(payload), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _consume_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str | None,
    request_payload: dict[str, Any],
) -> InterviewRound | None:
    if not key:
        return None
    request_hash = _canonical_hash(request_payload)
    existing = await find_idempotency(
        session,
        actor_id=actor.id,
        action=action,
        scope_id=scope_id,
        idempotency_key=key,
    )
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise InterviewIdempotencyConflictError("idempotency conflict")
    if existing.result_round_id is None:
        raise InterviewIdempotencyConflictError("idempotency conflict")
    round_ = await get_round_by_id(session, existing.result_round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    return round_


async def _store_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str | None,
    request_payload: dict[str, Any],
    round_id: UUID,
) -> None:
    if not key:
        return
    await add_idempotency(
        session,
        InterviewIdempotencyKey(
            actor_id=actor.id,
            action=action,
            scope_id=scope_id,
            idempotency_key=key,
            request_hash=_canonical_hash(request_payload),
            result_round_id=round_id,
        ),
    )


def _assert_version(round_: InterviewRound, version: int | None) -> None:
    if version is None:
        raise InterviewValidationError("version is required")
    if round_.version != version:
        raise InterviewOptimisticLockError(OPTIMISTIC_LOCK_MESSAGE)


def _bump(round_: InterviewRound, actor: User) -> None:
    round_.version += 1
    round_.updated_by = actor.id
    round_.updated_at = _now()


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _validate_schedule_fields(payload: InterviewScheduleCreate) -> None:
    if payload.meeting_mode == MEETING_MODE_ADAPTER:
        raise InterviewValidationError("meeting adapter is not available in this batch")
    if payload.format == INTERVIEW_FORMAT_ONLINE:
        if not (payload.meeting_url or "").strip():
            raise InterviewValidationError(
                "ONLINE schedule requires a manual meeting url"
            )
        if payload.meeting_mode not in {None, MEETING_MODE_MANUAL}:
            raise InterviewValidationError("only MANUAL meeting mode is supported")
    if payload.format == INTERVIEW_FORMAT_OFFLINE and not (
        payload.location or ""
    ).strip():
        raise InterviewValidationError("OFFLINE schedule requires location")
    try:
        validate_schedule_window(
            payload.start_at_utc, payload.end_at_utc, payload.timezone
        )
    except ValueError as exc:
        raise InterviewValidationError(str(exc)) from exc


def _schedule_from_payload(
    *,
    round_id: UUID,
    payload: InterviewScheduleCreate,
    version: int,
    actor_id: UUID,
    reschedule_reason: str | None = None,
    previous_encrypted: str | None = None,
) -> InterviewSchedule:
    start, end, tz = validate_schedule_window(
        payload.start_at_utc, payload.end_at_utc, payload.timezone
    )
    return InterviewSchedule(
        interview_round_id=round_id,
        schedule_version=version,
        status=SCHEDULE_STATUS_ACTIVE,
        start_at_utc=_to_utc(start),
        end_at_utc=_to_utc(end),
        timezone=tz,
        format=payload.format,
        meeting_mode=payload.meeting_mode or (
            MEETING_MODE_MANUAL if payload.format == INTERVIEW_FORMAT_ONLINE else None
        ),
        meeting_provider=payload.meeting_provider,
        meeting_url=payload.meeting_url,
        meeting_no=payload.meeting_no,
        meeting_password_encrypted=_resolve_meeting_password_encrypted(
            payload, previous_encrypted=previous_encrypted
        ),
        location=payload.location,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        reschedule_reason=reschedule_reason,
        created_by=actor_id,
    )


def _schedule_out(
    schedule: InterviewSchedule | None,
) -> InterviewScheduleSummaryOut | None:
    if schedule is None:
        return None
    return InterviewScheduleSummaryOut(
        id=schedule.id,
        schedule_version=schedule.schedule_version,
        status=schedule.status,
        start_at_utc=schedule.start_at_utc,
        end_at_utc=schedule.end_at_utc,
        timezone=schedule.timezone,
        format=schedule.format,
        meeting_mode=schedule.meeting_mode,
        meeting_provider=schedule.meeting_provider,
        meeting_url=schedule.meeting_url,
        meeting_no=schedule.meeting_no,
        has_meeting_password=bool(schedule.meeting_password_encrypted),
        location=schedule.location,
        contact_name=schedule.contact_name,
        contact_phone_masked=_mask_phone(schedule.contact_phone),
        reschedule_reason=schedule.reschedule_reason,
        created_at=schedule.created_at,
    )


async def _names_for_users(
    session: AsyncSession, user_ids: list[UUID]
) -> dict[UUID, str]:
    users = await get_users_by_ids(session, user_ids)
    return {user.id: user.display_name for user in users}


async def _to_round_out(
    session: AsyncSession,
    round_: InterviewRound,
    *,
    actor: User,
) -> InterviewRoundOut:
    can_manage = await _has_permission(actor, "recruitment.manage")
    can_execute = await _has_permission(actor, "interview.execute")
    assigned = any(item.interviewer_id == actor.id for item in round_.interviewers)
    actions = filter_actions_for_actor(
        round_.status,
        can_manage=can_manage,
        can_execute=can_execute and (can_manage or assigned),
    )
    user_ids = [round_.owner_id, *[item.interviewer_id for item in round_.interviewers]]
    names = await _names_for_users(session, user_ids)
    current = None
    history: list[InterviewScheduleSummaryOut] = []
    for schedule in sorted(round_.schedules, key=lambda item: item.schedule_version):
        item = _schedule_out(schedule)
        if item is None:
            continue
        history.append(item)
        if schedule.id == round_.current_schedule_id or (
            current is None and schedule.status == SCHEDULE_STATUS_ACTIVE
        ):
            current = item
    return InterviewRoundOut(
        id=round_.id,
        application_id=round_.application_id,
        job_version_id=round_.job_version_id,
        name=round_.name,
        sequence_no=round_.sequence_no,
        status=round_.status,
        format=round_.format,
        owner_id=round_.owner_id,
        owner_name=names.get(round_.owner_id, ""),
        interviewers=[
            InterviewerOut(
                interviewer_id=item.interviewer_id,
                display_name=names.get(item.interviewer_id, ""),
                is_primary=item.is_primary,
            )
            for item in round_.interviewers
        ],
        current_schedule=current,
        schedule_history=history,
        version=round_.version,
        allowed_actions=actions,
        cancellation_reason_code=round_.cancellation_reason_code,
        cancellation_description=round_.cancellation_description,
        abnormal_reason_code=round_.abnormal_reason_code,
        abnormal_description=round_.abnormal_description,
        started_at=round_.started_at,
        finished_at=round_.finished_at,
        cancelled_at=round_.cancelled_at,
        created_at=round_.created_at,
        updated_at=round_.updated_at,
    )


async def _timeline_from_rounds(
    session: AsyncSession,
    application,
    rounds: list[InterviewRound],
    *,
    actor: User,
) -> InterviewTimelineOut:
    job = await get_job_by_id(session, application.job_id)
    version = get_version_by_id(job, application.job_version_id) if job else None
    candidate = getattr(application, "candidate", None)
    outs = [await _to_round_out(session, round_, actor=actor) for round_ in rounds]
    completed = sum(1 for item in outs if item.status == INTERVIEW_STATUS_COMPLETED)
    return InterviewTimelineOut(
        application_id=application.id,
        candidate_id=application.candidate_id,
        candidate_name=candidate.name if candidate else "",
        job_id=application.job_id,
        job_name=job.name if job else None,
        job_version_id=application.job_version_id,
        job_version_label=(
            (version.version_label or f"V{version.major}.{version.minor}")
            if version is not None
            else None
        ),
        pipeline_status=application.pipeline_status,
        application_status=application.status,
        completed_round_count=completed,
        total_round_count=len(outs),
        rounds=outs,
    )


async def _load_application(session: AsyncSession, application_id: UUID):
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    return application


async def _assert_can_read_application(
    session: AsyncSession, *, application, actor: User
) -> None:
    if await _has_permission(actor, "recruitment.manage"):
        return
    assigned = await actor_assigned_to_application(
        session, application_id=application.id, user_id=actor.id
    )
    if await _has_permission(actor, "interview.execute") and assigned:
        return
    raise InterviewNotFoundError("application not found")


async def _assert_can_execute_round(
    session: AsyncSession, *, round_: InterviewRound, actor: User
) -> None:
    if await _has_permission(actor, "recruitment.manage"):
        return
    assigned = await actor_assigned_to_round(
        session, round_id=round_.id, user_id=actor.id
    )
    if await _has_permission(actor, "interview.execute") and assigned:
        return
    raise InterviewNotFoundError("interview round not found")


def _replace_interviewers(
    round_: InterviewRound,
    assignments: list[InterviewerAssignmentIn],
    actor_id: UUID,
) -> None:
    round_.interviewers.clear()
    for item in assignments:
        round_.interviewers.append(
            InterviewRoundInterviewer(
                interviewer_id=item.interviewer_id,
                is_primary=item.is_primary,
                created_by=actor_id,
            )
        )


async def _conflict_payload(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    interviewer_ids: list[UUID],
    start_at: datetime,
    end_at: datetime,
    exclude_round_id: UUID | None,
) -> InterviewConflictOut:
    names = await _names_for_users(session, interviewer_ids)
    candidate_rows = await find_candidate_conflicts(
        session,
        candidate_id=candidate_id,
        start_at=start_at,
        end_at=end_at,
        exclude_round_id=exclude_round_id,
    )
    interviewer_rows = await find_interviewer_conflicts(
        session,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
        exclude_round_id=exclude_round_id,
    )
    candidate_conflicts = [
        InterviewConflictItemOut(
            round_id=round_.id,
            round_name=round_.name,
            start_at_utc=schedule.start_at_utc,
            end_at_utc=schedule.end_at_utc,
        )
        for schedule, round_ in candidate_rows
    ]
    interviewer_conflicts = [
        InterviewConflictItemOut(
            interviewer_id=interviewer_id,
            interviewer_name=names.get(interviewer_id, ""),
            round_id=round_.id,
            round_name=round_.name,
            start_at_utc=schedule.start_at_utc,
            end_at_utc=schedule.end_at_utc,
        )
        for schedule, round_, interviewer_id in interviewer_rows
    ]
    return InterviewConflictOut(
        has_candidate_conflict=bool(candidate_conflicts),
        has_interviewer_conflict=bool(interviewer_conflicts),
        candidate_conflicts=candidate_conflicts,
        interviewer_conflicts=interviewer_conflicts,
    )


async def _assert_no_blocking_conflicts(
    session: AsyncSession,
    *,
    application,
    interviewer_ids: list[UUID],
    start_at: datetime,
    end_at: datetime,
    exclude_round_id: UUID | None,
    override_interviewer_conflict: bool = False,
    override_reason: str | None = None,
    actor: User,
    request_context: RequestContext,
    round_id: UUID | None = None,
) -> InterviewConflictOut:
    payload = await _conflict_payload(
        session,
        candidate_id=application.candidate_id,
        interviewer_ids=interviewer_ids,
        start_at=start_at,
        end_at=end_at,
        exclude_round_id=exclude_round_id,
    )
    if payload.has_candidate_conflict:
        raise InterviewConflictError("candidate conflict", payload)
    if payload.has_interviewer_conflict:
        if not override_interviewer_conflict:
            raise InterviewConflictError("interviewer conflict", payload)
        if not (override_reason or "").strip():
            raise InterviewValidationError("override_reason is required")
        await record_audit(
            session,
            action="interview_conflict.override",
            result="success",
            resource_type="interview_round",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(round_id or application.id),
            changes={
                "application_id": str(application.id),
                "override_reason": override_reason,
                "interviewer_round_ids": [
                    str(item.round_id) for item in payload.interviewer_conflicts
                ],
            },
        )
    return payload


async def get_interview_timeline(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor: User,
) -> InterviewTimelineOut:
    application = await _load_application(session, application_id)
    await _assert_can_read_application(session, application=application, actor=actor)
    rounds = await list_rounds_for_application(session, application_id)
    if not await _has_permission(actor, "recruitment.manage"):
        rounds = [
            round_
            for round_ in rounds
            if any(item.interviewer_id == actor.id for item in round_.interviewers)
        ]
    return await _timeline_from_rounds(session, application, rounds, actor=actor)


async def create_interview_round(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: InterviewRoundCreate,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundOut:
    await _require_manage(actor)
    application = await _load_application(session, application_id)
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise InterviewValidationError(
            "closed application cannot create interview rounds"
        )
    if application.pipeline_status != PIPELINE_INTERVIEWING:
        raise InterviewValidationError("application must be interviewing")

    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="create",
        scope_id=application_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return await _to_round_out(session, reused, actor=actor)

    sequence_no = payload.sequence_no or await next_sequence_no(session, application_id)
    status = INTERVIEW_STATUS_DRAFT
    if payload.schedule is not None:
        _validate_schedule_fields(payload.schedule)
        status = INTERVIEW_STATUS_SCHEDULED

    round_ = InterviewRound(
        application_id=application.id,
        job_version_id=application.job_version_id,
        name=payload.name.strip(),
        sequence_no=sequence_no,
        status=status,
        format=payload.format,
        owner_id=payload.owner_id,
        version=1,
        created_by=actor.id,
        updated_by=actor.id,
    )
    _replace_interviewers(round_, payload.interviewers, actor.id)
    try:
        await add_round(session, round_)
        if payload.schedule is not None:
            start, end, _tz = validate_schedule_window(
                payload.schedule.start_at_utc,
                payload.schedule.end_at_utc,
                payload.schedule.timezone,
            )
            await _assert_no_blocking_conflicts(
                session,
                application=application,
                interviewer_ids=[item.interviewer_id for item in payload.interviewers],
                start_at=_to_utc(start),
                end_at=_to_utc(end),
                exclude_round_id=None,
                actor=actor,
                request_context=request_context,
                round_id=round_.id,
            )
            schedule = _schedule_from_payload(
                round_id=round_.id,
                payload=payload.schedule,
                version=1,
                actor_id=actor.id,
            )
            await add_schedule(session, schedule)
            round_.current_schedule_id = schedule.id
            round_.schedules.append(schedule)
        await _store_idempotency(
            session,
            actor=actor,
            action="create",
            scope_id=application_id,
            key=payload.idempotency_key,
            request_payload=request_payload,
            round_id=round_.id,
        )
    except IntegrityError as exc:
        raise InterviewValidationError("sequence_no already exists") from exc

    await record_audit(
        session,
        action="interview_round.create",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "application_id": str(application.id),
            "name": round_.name,
            "status": round_.status,
            "sequence_no": round_.sequence_no,
            "format": round_.format,
            "idempotency_key": payload.idempotency_key,
            "before": None,
            "after": {"status": round_.status, "sequence_no": round_.sequence_no},
        },
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return await _to_round_out(session, loaded, actor=actor)


async def update_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewRoundUpdate,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundOut:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    if round_.status in TERMINAL_ROUND_STATUSES or round_.status not in {
        INTERVIEW_STATUS_DRAFT,
        INTERVIEW_STATUS_SCHEDULED,
        "CONFIRMED",
    }:
        raise InterviewValidationError("completed rounds cannot be modified")
    _assert_version(round_, payload.version)
    before = {
        "name": round_.name,
        "format": round_.format,
        "owner_id": str(round_.owner_id),
    }
    if payload.name is not None:
        round_.name = payload.name.strip()
    if payload.format is not None:
        round_.format = payload.format
    if payload.owner_id is not None:
        round_.owner_id = payload.owner_id
    if payload.interviewers is not None:
        _replace_interviewers(round_, payload.interviewers, actor.id)
    _bump(round_, actor)
    await record_audit(
        session,
        action="interview_round.update",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "application_id": str(round_.application_id),
            "before": before,
            "after": {
                "name": round_.name,
                "format": round_.format,
                "owner_id": str(round_.owner_id),
            },
        },
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return await _to_round_out(session, loaded, actor=actor)


async def schedule_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewScheduleCreate,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="schedule",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return InterviewRoundActionOut.model_validate(
            (await _to_round_out(session, reused, actor=actor)).model_dump()
        )
    _assert_version(round_, payload.version)
    _validate_schedule_fields(payload)
    if any(item.status == SCHEDULE_STATUS_ACTIVE for item in round_.schedules):
        raise InterviewValidationError("active schedule already exists")
    application = await _load_application(session, round_.application_id)
    start, end, _tz = validate_schedule_window(
        payload.start_at_utc, payload.end_at_utc, payload.timezone
    )
    await _assert_no_blocking_conflicts(
        session,
        application=application,
        interviewer_ids=[item.interviewer_id for item in round_.interviewers],
        start_at=_to_utc(start),
        end_at=_to_utc(end),
        exclude_round_id=round_.id,
        actor=actor,
        request_context=request_context,
        round_id=round_.id,
    )
    before = round_.status
    target = next_status(round_.status, "schedule")
    if target != INTERVIEW_STATUS_SCHEDULED:
        raise InterviewStateError("schedule must enter SCHEDULED")
    schedule = _schedule_from_payload(
        round_id=round_.id, payload=payload, version=1, actor_id=actor.id
    )
    await add_schedule(session, schedule)
    round_.current_schedule_id = schedule.id
    round_.status = target
    _bump(round_, actor)
    await _store_idempotency(
        session,
        actor=actor,
        action="schedule",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_round.schedule",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "application_id": str(round_.application_id),
            "before": {"status": before},
            "after": {"status": round_.status, "schedule_version": 1},
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return InterviewRoundActionOut.model_validate(
        (await _to_round_out(session, loaded, actor=actor)).model_dump()
    )


async def reschedule_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewRescheduleRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="reschedule",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return InterviewRoundActionOut.model_validate(
            (await _to_round_out(session, reused, actor=actor)).model_dump()
        )
    if round_.status not in {INTERVIEW_STATUS_SCHEDULED, "CONFIRMED"}:
        raise InterviewStateError(
            "only SCHEDULED or CONFIRMED rounds can be rescheduled"
        )
    _assert_version(round_, payload.version)
    _validate_schedule_fields(payload)
    active = await get_active_schedule_for_update(session, round_.id)
    if active is None:
        raise InterviewValidationError("no active schedule")
    application = await _load_application(session, round_.application_id)
    start, end, _tz = validate_schedule_window(
        payload.start_at_utc, payload.end_at_utc, payload.timezone
    )
    await _assert_no_blocking_conflicts(
        session,
        application=application,
        interviewer_ids=[item.interviewer_id for item in round_.interviewers],
        start_at=_to_utc(start),
        end_at=_to_utc(end),
        exclude_round_id=round_.id,
        override_interviewer_conflict=payload.override_interviewer_conflict,
        override_reason=payload.override_reason,
        actor=actor,
        request_context=request_context,
        round_id=round_.id,
    )
    now = _now()
    old_version = active.schedule_version
    active.status = SCHEDULE_STATUS_SUPERSEDED
    active.superseded_at = now
    new_version = old_version + 1
    schedule = _schedule_from_payload(
        round_id=round_.id,
        payload=payload,
        version=new_version,
        actor_id=actor.id,
        reschedule_reason=payload.reschedule_reason,
        previous_encrypted=active.meeting_password_encrypted,
    )
    await add_schedule(session, schedule)
    round_.current_schedule_id = schedule.id
    _bump(round_, actor)
    await _store_idempotency(
        session,
        actor=actor,
        action="reschedule",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_round.reschedule",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "application_id": str(round_.application_id),
            "before": {"schedule_version": old_version, "status": round_.status},
            "after": {"schedule_version": new_version, "status": round_.status},
            "reschedule_reason": payload.reschedule_reason,
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return InterviewRoundActionOut.model_validate(
        (await _to_round_out(session, loaded, actor=actor)).model_dump()
    )


async def cancel_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewCancelRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="cancel",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return InterviewRoundActionOut.model_validate(
            (await _to_round_out(session, reused, actor=actor)).model_dump()
        )
    if payload.reason_code not in CANCEL_REASON_CODES:
        raise InterviewValidationError("invalid reason_code")
    _assert_version(round_, payload.version)
    before = round_.status
    target = next_status(round_.status, "cancel")
    active = await get_active_schedule_for_update(session, round_.id)
    if active is not None:
        active.status = SCHEDULE_STATUS_CANCELLED
        active.superseded_at = _now()
    round_.status = target
    round_.cancellation_reason_code = payload.reason_code
    round_.cancellation_description = payload.description
    round_.cancelled_at = _now()
    _bump(round_, actor)
    await _store_idempotency(
        session,
        actor=actor,
        action="cancel",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_round.cancel",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "application_id": str(round_.application_id),
            "before": {"status": before},
            "after": {"status": round_.status},
            "reason_code": payload.reason_code,
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return InterviewRoundActionOut.model_validate(
        (await _to_round_out(session, loaded, actor=actor)).model_dump()
    )


async def _status_action(
    session: AsyncSession,
    *,
    round_id: UUID,
    action: str,
    payload: InterviewRoundActionRequest,
    actor: User,
    request_context: RequestContext,
    extra_changes: dict[str, Any] | None = None,
    after_hook=None,
) -> InterviewRoundActionOut:
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _assert_can_execute_round(session, round_=round_, actor=actor)
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action=action,
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return InterviewRoundActionOut.model_validate(
            (await _to_round_out(session, reused, actor=actor)).model_dump()
        )
    _assert_version(round_, payload.version)
    before = round_.status
    target = next_status(round_.status, action)
    round_.status = target
    if after_hook is not None:
        after_hook(round_)
    _bump(round_, actor)
    await _store_idempotency(
        session,
        actor=actor,
        action=action,
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    changes = {
        "application_id": str(round_.application_id),
        "before": {"status": before},
        "after": {"status": round_.status},
        "idempotency_key": payload.idempotency_key,
    }
    if extra_changes:
        changes.update(extra_changes)
    await record_audit(
        session,
        action=f"interview_round.{action}",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes=changes,
    )
    await session.commit()
    loaded = await get_round_by_id(session, round_.id)
    assert loaded is not None
    return InterviewRoundActionOut.model_validate(
        (await _to_round_out(session, loaded, actor=actor)).model_dump()
    )


async def start_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    def _hook(round_: InterviewRound) -> None:
        round_.started_at = _now()

    return await _status_action(
        session,
        round_id=round_id,
        action="start",
        payload=payload,
        actor=actor,
        request_context=request_context,
        after_hook=_hook,
    )


async def finish_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    def _hook(round_: InterviewRound) -> None:
        round_.finished_at = _now()

    return await _status_action(
        session,
        round_id=round_id,
        action="finish",
        payload=payload,
        actor=actor,
        request_context=request_context,
        after_hook=_hook,
    )


async def complete_interview_round(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewRoundActionRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    await _require_manage(actor)
    application_status_before = None

    async def _run() -> InterviewRoundActionOut:
        nonlocal application_status_before
        round_ = await get_round_for_update(session, round_id)
        if round_ is None:
            raise InterviewNotFoundError("interview round not found")
        application = await _load_application(session, round_.application_id)
        application_status_before = (application.pipeline_status, application.status)
        result = await _status_action(
            session,
            round_id=round_id,
            action="complete",
            payload=payload,
            actor=actor,
            request_context=request_context,
        )
        application_after = await _load_application(session, round_.application_id)
        if (
            application_after.pipeline_status,
            application_after.status,
        ) != application_status_before:
            raise InterviewValidationError(
                "complete must not change application decision"
            )
        return result

    return await _run()


async def end_interview_abnormally(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: InterviewAbnormalEndRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewRoundActionOut:
    if payload.reason_code not in ABNORMAL_REASON_CODES:
        raise InterviewValidationError("invalid reason_code")
    round_probe = await get_round_by_id(session, round_id)
    if round_probe is None:
        raise InterviewNotFoundError("interview round not found")
    await _assert_can_execute_round(session, round_=round_probe, actor=actor)
    action_payload = InterviewRoundActionRequest(
        version=payload.version, idempotency_key=payload.idempotency_key
    )

    def _hook(round_: InterviewRound) -> None:
        round_.abnormal_reason_code = payload.reason_code
        round_.abnormal_description = payload.description
        round_.finished_at = _now()

    return await _status_action(
        session,
        round_id=round_id,
        action="end_abnormally",
        payload=action_payload,
        actor=actor,
        request_context=request_context,
        extra_changes={"reason_code": payload.reason_code},
        after_hook=_hook,
    )


async def reorder_interview_rounds(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: InterviewRoundReorderRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewTimelineOut:
    await _require_manage(actor)
    application = await _load_application(session, application_id)
    rounds = await list_rounds_for_application_for_update(session, application_id)
    if {item.id for item in rounds} != set(payload.round_ids) or len(
        payload.round_ids
    ) != len(rounds):
        raise InterviewValidationError("reorder must include every round exactly once")
    by_id = {item.id: item for item in rounds}
    old_order = [item.id for item in rounds]
    for index, round_id in enumerate(payload.round_ids, start=1):
        round_ = by_id[round_id]
        if round_.sequence_no != index and round_.status in TERMINAL_ROUND_STATUSES | {
            "IN_PROGRESS",
            "PENDING_TRANSCRIPT",
        }:
            raise InterviewValidationError("completed rounds cannot be reordered")
    # two-phase to satisfy unique constraint
    for offset, round_ in enumerate(rounds):
        round_.sequence_no = 10000 + offset
    await session.flush()
    for index, round_id in enumerate(payload.round_ids, start=1):
        by_id[round_id].sequence_no = index
        by_id[round_id].updated_by = actor.id
        by_id[round_id].updated_at = _now()
        by_id[round_id].version += 1
    await record_audit(
        session,
        action="interview_round.reorder",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "application_id": str(application.id),
            "before": {"round_ids": [str(item) for item in old_order]},
            "after": {"round_ids": [str(item) for item in payload.round_ids]},
        },
    )
    await session.commit()
    return await get_interview_timeline(
        session, application_id=application_id, actor=actor
    )


async def check_interview_conflicts(
    session: AsyncSession,
    *,
    payload: InterviewConflictCheckRequest,
    actor: User,
    request_context: RequestContext,
) -> InterviewConflictOut:
    await _require_manage(actor)
    application = await _load_application(session, payload.application_id)
    try:
        start, end, _tz = validate_schedule_window(
            payload.start_at_utc, payload.end_at_utc, payload.timezone
        )
    except ValueError as exc:
        raise InterviewValidationError(str(exc)) from exc
    result = await _conflict_payload(
        session,
        candidate_id=application.candidate_id,
        interviewer_ids=payload.interviewer_ids,
        start_at=_to_utc(start),
        end_at=_to_utc(end),
        exclude_round_id=payload.exclude_round_id,
    )
    if result.has_candidate_conflict:
        raise InterviewConflictError("candidate conflict", result)
    if result.has_interviewer_conflict and not payload.override_interviewer_conflict:
        raise InterviewConflictError("interviewer conflict", result)
    if result.has_interviewer_conflict and payload.override_interviewer_conflict:
        if not (payload.override_reason or "").strip():
            raise InterviewValidationError("override_reason is required")
        await record_audit(
            session,
            action="interview_conflict.override",
            result="success",
            resource_type="job_application",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(application.id),
            changes={
                "application_id": str(application.id),
                "override_reason": payload.override_reason,
            },
        )
        await session.commit()
    return result


async def list_interview_staff(
    session: AsyncSession, actor: User
) -> InterviewStaffListResponse:
    await _require_manage(actor)
    users = await list_users_with_permission(session, "interview.execute")
    return InterviewStaffListResponse(
        items=[
            InterviewStaffItemOut(
                id=user.id, display_name=user.display_name, username=user.username
            )
            for user in users
        ]
    )


# re-export for tests/monkeypatch
__all__ = [
    "InterviewConflictError",
    "InterviewForbiddenError",
    "InterviewIdempotencyConflictError",
    "InterviewNotFoundError",
    "InterviewOptimisticLockError",
    "InterviewStateError",
    "InterviewValidationError",
    "cancel_interview_round",
    "check_interview_conflicts",
    "complete_interview_round",
    "create_interview_round",
    "end_interview_abnormally",
    "finish_interview_round",
    "get_interview_timeline",
    "get_round_for_update",
    "list_interview_reason_codes",
    "list_interview_staff",
    "reorder_interview_rounds",
    "reschedule_interview_round",
    "schedule_interview_round",
    "start_interview_round",
    "update_interview_round",
]
