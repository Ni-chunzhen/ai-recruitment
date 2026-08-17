"""Manual invitation workflow: generate, edit, copy-audit, record-sent, confirm."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.interview import (
    CANCEL_REASON_CATALOG,
    INTERVIEW_FORMAT_ONLINE,
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_SCHEDULED,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_SUPERSEDED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewSchedule,
)
from app.models.invitation import (
    INVITATION_AUDIENCE_CANDIDATE,
    INVITATION_AUDIENCE_INTERVIEWER,
    INVITATION_EVENT_CANCELLATION,
    INVITATION_EVENT_INITIAL,
    INVITATION_EVENT_RESCHEDULE,
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
    TEMPLATE_CANDIDATE_CANCELLATION,
    TEMPLATE_CANDIDATE_INITIAL,
    TEMPLATE_CANDIDATE_RESCHEDULE,
    TEMPLATE_INTERVIEWER_CANCELLATION,
    TEMPLATE_INTERVIEWER_INITIAL,
    TEMPLATE_INTERVIEWER_RESCHEDULE,
    TEMPLATE_VERSION,
    InterviewInvitationMessage,
    InterviewInvitationSendRecord,
    InterviewInvitationVersion,
)
from app.repositories.interviews import (
    actor_assigned_to_round,
    find_idempotency,
    add_idempotency,
    get_active_schedule_for_update,
    get_round_by_id,
    get_round_for_update,
)
from app.repositories.invitations import (
    add_message,
    add_send_record,
    add_version,
    count_by_status_for_round,
    count_by_status_for_schedule,
    find_message,
    get_message_by_id,
    get_message_for_update,
    get_version,
    list_messages_for_round,
    list_messages_for_schedule,
    next_version_no,
    status_counts_to_out,
)
from app.repositories.jobs import get_job_by_id, get_version_by_id
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id
from app.schemas.invitation import (
    ConfirmInvitationRequest,
    ConfirmInvitationResponse,
    CopyAuditRequest,
    GenerateInvitationsRequest,
    GenerateInvitationsResponse,
    InvitationListResponse,
    InvitationMessageDetailOut,
    InvitationMessageSummaryOut,
    InvitationStatusCountsOut,
    RecordSentRequest,
    RecordSentResponse,
    UpdateInvitationMessageRequest,
)
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, decrypt_secret, encrypt_secret
from app.services.interview_state import InterviewStateError, next_status
from app.services.invitation_templates import (
    InvitationTemplateContext,
    render_invitation_template,
    sanitize_invitation_html,
)
from app.services.interviews import (
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
    OPTIMISTIC_LOCK_MESSAGE,
)

_CANCEL_LABELS = {code: label for code, label in CANCEL_REASON_CATALOG}

_TEMPLATE_MAP = {
    (INVITATION_EVENT_INITIAL, INVITATION_AUDIENCE_CANDIDATE): TEMPLATE_CANDIDATE_INITIAL,
    (INVITATION_EVENT_INITIAL, INVITATION_AUDIENCE_INTERVIEWER): TEMPLATE_INTERVIEWER_INITIAL,
    (INVITATION_EVENT_RESCHEDULE, INVITATION_AUDIENCE_CANDIDATE): TEMPLATE_CANDIDATE_RESCHEDULE,
    (
        INVITATION_EVENT_RESCHEDULE,
        INVITATION_AUDIENCE_INTERVIEWER,
    ): TEMPLATE_INTERVIEWER_RESCHEDULE,
    (
        INVITATION_EVENT_CANCELLATION,
        INVITATION_AUDIENCE_CANDIDATE,
    ): TEMPLATE_CANDIDATE_CANCELLATION,
    (
        INVITATION_EVENT_CANCELLATION,
        INVITATION_AUDIENCE_INTERVIEWER,
    ): TEMPLATE_INTERVIEWER_CANCELLATION,
}


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


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().partition("@")
    if not local or not domain:
        return None
    return f"{local[0]}***@{domain}"


def _content_hash(subject: str, body_html: str, body_text: str) -> str:
    payload = json.dumps(
        {"subject": subject, "body_html": body_html, "body_text": body_text},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
                    "subject",
                    "body_html",
                    "body_text",
                    "recipient_email",
                    "email",
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


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _format_local(dt: datetime, timezone: str) -> str:
    try:
        local = dt.astimezone(ZoneInfo(timezone))
    except Exception:
        local = dt.astimezone(UTC)
    return local.strftime("%Y-%m-%d %H:%M")


def _template_code(event_type: str, audience_type: str) -> str:
    code = _TEMPLATE_MAP.get((event_type, audience_type))
    if code is None:
        raise InterviewValidationError("unsupported invitation template")
    return code


def _infer_event_type(
    round_: InterviewRound, schedule: InterviewSchedule, explicit: str | None
) -> str:
    if explicit:
        return explicit
    if round_.status == INTERVIEW_STATUS_CANCELLED:
        return INVITATION_EVENT_CANCELLATION
    if schedule.schedule_version > 1 or (schedule.reschedule_reason or "").strip():
        return INVITATION_EVENT_RESCHEDULE
    return INVITATION_EVENT_INITIAL


def _assert_event_allowed(round_: InterviewRound, event_type: str) -> None:
    if event_type == INVITATION_EVENT_CANCELLATION:
        if round_.status != INTERVIEW_STATUS_CANCELLED:
            raise InterviewValidationError(
                "cancellation invitations require CANCELLED round"
            )
        return
    if round_.status != INTERVIEW_STATUS_SCHEDULED:
        raise InterviewValidationError(
            "invitations can only be generated for SCHEDULED rounds"
        )


async def _consume_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
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
    return existing


async def _store_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str,
    request_payload: dict[str, Any],
    round_id: UUID,
) -> InterviewIdempotencyKey:
    record = InterviewIdempotencyKey(
        actor_id=actor.id,
        action=action,
        scope_id=scope_id,
        idempotency_key=key,
        request_hash=_canonical_hash(request_payload),
        result_round_id=round_id,
    )
    await add_idempotency(session, record)
    return record


async def _load_users(session: AsyncSession, user_ids: list[UUID]) -> dict[UUID, User]:
    from sqlalchemy import select

    if not user_ids:
        return {}
    result = await session.scalars(select(User).where(User.id.in_(user_ids)))
    return {user.id: user for user in result.all()}


def _previous_schedule(
    round_: InterviewRound, current: InterviewSchedule
) -> InterviewSchedule | None:
    superseded = [
        item
        for item in round_.schedules
        if item.status == SCHEDULE_STATUS_SUPERSEDED
        and item.schedule_version < current.schedule_version
    ]
    if not superseded:
        return None
    return max(superseded, key=lambda item: item.schedule_version)


def _build_context(
    *,
    round_: InterviewRound,
    schedule: InterviewSchedule,
    application: Any,
    job_title: str,
    job_version: str,
    owner_name: str,
    interviewer_name: str,
    meeting_password: str | None,
    previous: InterviewSchedule | None,
) -> InvitationTemplateContext:
    duration = max(
        1, int((schedule.end_at_utc - schedule.start_at_utc).total_seconds() // 60)
    )
    candidate = getattr(application, "candidate", None)
    cancel_label = None
    if round_.cancellation_reason_code:
        cancel_label = _CANCEL_LABELS.get(
            round_.cancellation_reason_code, round_.cancellation_reason_code
        )
    return InvitationTemplateContext(
        candidate_name=(candidate.name if candidate else "") or "",
        job_title=job_title,
        job_version=job_version or "",
        round_name=round_.name,
        start_at_display=_format_local(schedule.start_at_utc, schedule.timezone),
        end_at_display=_format_local(schedule.end_at_utc, schedule.timezone),
        timezone=schedule.timezone,
        duration_minutes=duration,
        format=schedule.format,
        meeting_url=schedule.meeting_url,
        meeting_no=schedule.meeting_no,
        meeting_password=meeting_password,
        location=schedule.location,
        contact_name=schedule.contact_name,
        contact_phone=schedule.contact_phone,
        owner_name=owner_name,
        interviewer_name=interviewer_name,
        previous_start_at_display=(
            _format_local(previous.start_at_utc, previous.timezone) if previous else None
        ),
        previous_end_at_display=(
            _format_local(previous.end_at_utc, previous.timezone) if previous else None
        ),
        reschedule_reason=schedule.reschedule_reason,
        cancellation_reason=cancel_label,
        cancellation_description=round_.cancellation_description,
    )


def _resolve_status(missing_fields: list[str], has_email: bool) -> str:
    if missing_fields or not has_email:
        return INVITATION_STATUS_DRAFT
    return INVITATION_STATUS_READY


async def _summary_out(
    session: AsyncSession, message: InterviewInvitationMessage
) -> InvitationMessageSummaryOut:
    version = None
    if message.current_version_id:
        version = await get_version(session, message.current_version_id)
    now = _now()
    return InvitationMessageSummaryOut(
        id=message.id,
        interview_round_id=message.interview_round_id,
        schedule_id=message.schedule_id,
        schedule_version=message.schedule_version,
        event_type=message.event_type,
        audience_type=message.audience_type,
        recipient_user_id=message.recipient_user_id,
        recipient_key=message.recipient_key,
        recipient_name=message.recipient_name,
        recipient_email_masked=message.recipient_email_masked,
        status=message.status,
        current_version_id=message.current_version_id,
        current_version_no=version.version_no if version else None,
        template_code=version.template_code if version else None,
        version=message.version,
        missing_fields=[],
        created_at=message.created_at or now,
        updated_at=message.updated_at or now,
    )


async def _detail_out(
    session: AsyncSession, message: InterviewInvitationMessage
) -> InvitationMessageDetailOut:
    if message.current_version_id is None:
        raise InterviewValidationError("invitation has no content version")
    version = await get_version(session, message.current_version_id)
    if version is None:
        raise InterviewValidationError("invitation content version not found")
    try:
        subject = decrypt_secret(version.subject_encrypted) or ""
        body_html = decrypt_secret(version.body_html_encrypted) or ""
        body_text = decrypt_secret(version.body_text_encrypted) or ""
    except EncryptionError as exc:
        raise InterviewValidationError("invitation content decryption failed") from exc
    summary = await _summary_out(session, message)
    data = summary.model_dump()
    data.update(
        {
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
            "template_code": version.template_code,
            "template_version": version.template_version,
            "content_hash": version.content_hash,
            "current_version_no": version.version_no,
        }
    )
    return InvitationMessageDetailOut.model_validate(data)


async def _assert_can_read_message(
    session: AsyncSession, *, message: InterviewInvitationMessage, actor: User
) -> None:
    if await _has_permission(actor, "recruitment.manage"):
        return
    if not await _has_permission(actor, "interview.execute"):
        raise InterviewNotFoundError("invitation not found")
    assigned = await actor_assigned_to_round(
        session, round_id=message.interview_round_id, user_id=actor.id
    )
    if not assigned:
        raise InterviewNotFoundError("invitation not found")
    if message.audience_type != INVITATION_AUDIENCE_INTERVIEWER:
        raise InterviewNotFoundError("invitation not found")
    if message.recipient_user_id != actor.id:
        raise InterviewNotFoundError("invitation not found")


async def _create_content_version(
    session: AsyncSession,
    *,
    message: InterviewInvitationMessage,
    subject: str,
    body_html: str,
    body_text: str,
    template_code: str,
    actor: User,
) -> InterviewInvitationVersion:
    try:
        subject_enc = encrypt_secret(subject)
        html_enc = encrypt_secret(body_html)
        text_enc = encrypt_secret(body_text)
    except EncryptionError as exc:
        raise InterviewValidationError("invitation content encryption failed") from exc
    if not subject_enc or not html_enc or not text_enc:
        raise InterviewValidationError("invitation content encryption failed")
    version_no = await next_version_no(session, message.id)
    version = InterviewInvitationVersion(
        id=uuid4(),
        message_id=message.id,
        version_no=version_no,
        subject_encrypted=subject_enc,
        body_html_encrypted=html_enc,
        body_text_encrypted=text_enc,
        template_code=template_code,
        template_version=TEMPLATE_VERSION,
        content_hash=_content_hash(subject, body_html, body_text),
        created_by=actor.id,
    )
    await add_version(session, version)
    message.current_version_id = version.id
    return version


async def generate_invitations(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: GenerateInvitationsRequest,
    actor: User,
    request_context: RequestContext,
) -> GenerateInvitationsResponse:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="invite_generate",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        messages = await list_messages_for_round(session, round_id)
        return GenerateInvitationsResponse(
            items=[await _summary_out(session, item) for item in messages]
        )

    schedule = await get_active_schedule_for_update(session, round_.id)
    if schedule is None:
        # cancellation may keep last schedule cancelled; fall back to current_schedule_id
        if round_.current_schedule_id is None:
            raise InterviewValidationError("no schedule available for invitations")
        schedule = next(
            (item for item in round_.schedules if item.id == round_.current_schedule_id),
            None,
        )
        if schedule is None:
            raise InterviewValidationError("no schedule available for invitations")

    event_type = _infer_event_type(round_, schedule, payload.event_type)
    _assert_event_allowed(round_, event_type)

    application = await get_application_by_id(session, round_.application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    job = await get_job_by_id(session, application.job_id)
    version = get_version_by_id(job, application.job_version_id) if job else None
    job_title = (job.name if job else "") or ""
    job_version_label = (version.version_label if version else "") or ""

    interviewer_ids = [item.interviewer_id for item in round_.interviewers]
    users = await _load_users(session, [round_.owner_id, *interviewer_ids])
    owner = users.get(round_.owner_id)
    owner_name = owner.display_name if owner else ""
    previous = _previous_schedule(round_, schedule)

    meeting_password = None
    if schedule.format == INTERVIEW_FORMAT_ONLINE and schedule.meeting_password_encrypted:
        try:
            meeting_password = decrypt_secret(schedule.meeting_password_encrypted)
        except EncryptionError as exc:
            raise InterviewValidationError(
                "meeting password decryption failed"
            ) from exc

    created: list[InterviewInvitationMessage] = []
    recipients: list[tuple[str, str, UUID | None, str, str | None]] = []
    candidate = getattr(application, "candidate", None)
    candidate_id = application.candidate_id
    recipients.append(
        (
            INVITATION_AUDIENCE_CANDIDATE,
            str(candidate_id),
            None,
            (candidate.name if candidate else "") or "候选人",
            getattr(candidate, "email", None) if candidate else None,
        )
    )
    for assignment in round_.interviewers:
        user = users.get(assignment.interviewer_id)
        recipients.append(
            (
                INVITATION_AUDIENCE_INTERVIEWER,
                str(assignment.interviewer_id),
                assignment.interviewer_id,
                (user.display_name if user else "") or "面试官",
                getattr(user, "email", None) if user else None,
            )
        )

    for audience, recipient_key, recipient_user_id, recipient_name, email in recipients:
        existing = await find_message(
            session, schedule.id, event_type, audience, recipient_key
        )
        if existing is not None:
            created.append(existing)
            continue

        template_code = _template_code(event_type, audience)
        ctx = _build_context(
            round_=round_,
            schedule=schedule,
            application=application,
            job_title=job_title,
            job_version=job_version_label,
            owner_name=owner_name,
            interviewer_name=(
                recipient_name if audience == INVITATION_AUDIENCE_INTERVIEWER else ""
            ),
            meeting_password=meeting_password,
            previous=previous,
        )
        rendered = render_invitation_template(template_code, ctx)
        missing = list(rendered.missing_fields)
        if not (email or "").strip():
            missing.append("recipient_email")
        missing = sorted(set(missing))
        status = _resolve_status(missing, bool((email or "").strip()))

        message = InterviewInvitationMessage(
            id=uuid4(),
            interview_round_id=round_.id,
            schedule_id=schedule.id,
            schedule_version=schedule.schedule_version,
            event_type=event_type,
            audience_type=audience,
            recipient_user_id=recipient_user_id,
            recipient_key=recipient_key,
            recipient_name=recipient_name,
            recipient_email_masked=_mask_email(email),
            status=status,
            version=1,
            created_by=actor.id,
            updated_by=actor.id,
        )
        await add_message(session, message)
        await _create_content_version(
            session,
            message=message,
            subject=rendered.subject,
            body_html=rendered.body_html,
            body_text=rendered.body_text,
            template_code=template_code,
            actor=actor,
        )
        # Re-evaluate after content exists: missing fields still drive DRAFT.
        message.status = status
        created.append(message)

    await _store_idempotency(
        session,
        actor=actor,
        action="invite_generate",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_invitation.generate",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "schedule_id": str(schedule.id),
            "schedule_version": schedule.schedule_version,
            "event_type": event_type,
            "message_ids": [str(item.id) for item in created],
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    items = [await _summary_out(session, item) for item in created]
    # Attach missing_fields for freshly generated (recompute lightly)
    for item, message in zip(items, created, strict=False):
        if message.status == INVITATION_STATUS_DRAFT and not message.recipient_email_masked:
            item.missing_fields = ["recipient_email"]
    return GenerateInvitationsResponse(items=items)


async def list_invitations(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> InvitationListResponse:
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    can_manage = await _has_permission(actor, "recruitment.manage")
    if not can_manage:
        if not await _has_permission(actor, "interview.execute"):
            raise InterviewNotFoundError("interview round not found")
        assigned = await actor_assigned_to_round(
            session, round_id=round_id, user_id=actor.id
        )
        if not assigned:
            raise InterviewNotFoundError("interview round not found")
    messages = await list_messages_for_round(session, round_id)
    if not can_manage:
        messages = [
            item
            for item in messages
            if item.audience_type == INVITATION_AUDIENCE_INTERVIEWER
            and item.recipient_user_id == actor.id
        ]
    current_schedule_id = round_.current_schedule_id
    counts_raw = (
        await count_by_status_for_schedule(session, current_schedule_id)
        if current_schedule_id
        else {}
    )
    # Current-arrangement buckets exclude VOIDED. Voided count comes from
    # round history so the UI can show失效历史 without treating them as current.
    current_counts = status_counts_to_out(
        {
            key: value
            for key, value in counts_raw.items()
            if key != INVITATION_STATUS_VOIDED
        }
    )
    round_counts = await count_by_status_for_round(session, round_id)
    current_counts["voided"] = int(round_counts.get(INVITATION_STATUS_VOIDED, 0))
    counts = InvitationStatusCountsOut.model_validate(current_counts)
    return InvitationListResponse(
        items=[await _summary_out(session, item) for item in messages],
        counts=counts,
    )


async def get_invitation_detail(
    session: AsyncSession, *, message_id: UUID, actor: User
) -> InvitationMessageDetailOut:
    message = await get_message_by_id(session, message_id)
    if message is None:
        raise InterviewNotFoundError("invitation not found")
    await _assert_can_read_message(session, message=message, actor=actor)
    return await _detail_out(session, message)


async def update_invitation(
    session: AsyncSession,
    *,
    message_id: UUID,
    payload: UpdateInvitationMessageRequest,
    actor: User,
    request_context: RequestContext,
) -> InvitationMessageDetailOut:
    await _require_manage(actor)
    message = await get_message_for_update(session, message_id)
    if message is None:
        raise InterviewNotFoundError("invitation not found")
    if message.status == INVITATION_STATUS_VOIDED:
        raise InterviewValidationError("voided invitation cannot be edited")
    if message.version != payload.version:
        raise InterviewOptimisticLockError(OPTIMISTIC_LOCK_MESSAGE)

    body_html = sanitize_invitation_html(payload.body_html)
    body_text = (payload.body_text or "").strip() or _html_to_text(body_html)
    subject = payload.subject.strip()
    if not subject or not body_html:
        raise InterviewValidationError("subject and body_html are required")

    current = (
        await get_version(session, message.current_version_id)
        if message.current_version_id
        else None
    )
    template_code = current.template_code if current else TEMPLATE_CANDIDATE_INITIAL
    version = await _create_content_version(
        session,
        message=message,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        template_code=template_code,
        actor=actor,
    )
    has_email = bool(message.recipient_email_masked)
    message.status = (
        INVITATION_STATUS_READY if has_email else INVITATION_STATUS_DRAFT
    )
    message.version += 1
    message.updated_by = actor.id
    message.updated_at = _now()

    await record_audit(
        session,
        action="interview_invitation.update",
        result="success",
        resource_type="interview_invitation",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(message.id),
        changes={
            "message_id": str(message.id),
            "content_version_id": str(version.id),
            "version_no": version.version_no,
            "template_code": template_code,
            "content_hash": version.content_hash,
            "status": message.status,
        },
    )
    await session.commit()
    refreshed = await get_message_by_id(session, message.id)
    assert refreshed is not None
    return await _detail_out(session, refreshed)


async def audit_copy(
    session: AsyncSession,
    *,
    message_id: UUID,
    payload: CopyAuditRequest,
    actor: User,
    request_context: RequestContext,
) -> InvitationMessageSummaryOut:
    await _require_manage(actor)
    message = await get_message_by_id(session, message_id)
    if message is None:
        raise InterviewNotFoundError("invitation not found")
    if message.status == INVITATION_STATUS_VOIDED:
        raise InterviewValidationError("voided invitation cannot be copied")
    await record_audit(
        session,
        action="interview_invitation.copy",
        result="success",
        resource_type="interview_invitation",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(message.id),
        changes={
            "message_id": str(message.id),
            "copy_type": payload.copy_type,
            "content_version_id": (
                str(message.current_version_id) if message.current_version_id else None
            ),
            "status": message.status,
        },
    )
    await session.commit()
    return await _summary_out(session, message)


async def record_sent(
    session: AsyncSession,
    *,
    message_id: UUID,
    payload: RecordSentRequest,
    actor: User,
    request_context: RequestContext,
) -> RecordSentResponse:
    await _require_manage(actor)
    message = await get_message_for_update(session, message_id)
    if message is None:
        raise InterviewNotFoundError("invitation not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="invite_record_sent",
        scope_id=message_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        return RecordSentResponse(
            id=reused.id,
            message_id=message.id,
            message_version_id=payload.message_version_id,
            sent_at=payload.sent_at,
            channel_type=payload.channel_type,
            channel_note=payload.channel_note,
            recipient_email_masked=message.recipient_email_masked,
            status=message.status,
        )

    if message.status == INVITATION_STATUS_VOIDED:
        raise InterviewValidationError("voided invitation cannot be recorded as sent")
    if message.status not in {
        INVITATION_STATUS_READY,
        INVITATION_STATUS_RECORDED_SENT,
    }:
        raise InterviewValidationError("only READY invitations can be recorded as sent")

    version = await get_version(session, payload.message_version_id)
    if version is None or version.message_id != message.id:
        raise InterviewValidationError("message_version_id does not belong to message")

    idem = await _store_idempotency(
        session,
        actor=actor,
        action="invite_record_sent",
        scope_id=message_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=message.interview_round_id,
    )
    record = InterviewInvitationSendRecord(
        id=uuid4(),
        message_id=message.id,
        message_version_id=version.id,
        recorded_by=actor.id,
        sent_at=payload.sent_at,
        channel_type=payload.channel_type,
        channel_note=payload.channel_note,
        recipient_email_masked=message.recipient_email_masked,
        idempotency_key_id=idem.id,
    )
    await add_send_record(session, record)
    message.status = INVITATION_STATUS_RECORDED_SENT
    message.updated_by = actor.id
    message.updated_at = _now()

    await record_audit(
        session,
        action="interview_invitation.record_sent",
        result="success",
        resource_type="interview_invitation",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(message.id),
        changes={
            "message_id": str(message.id),
            "message_version_id": str(version.id),
            "channel_type": payload.channel_type,
            "idempotency_key": payload.idempotency_key,
            "status": INVITATION_STATUS_RECORDED_SENT,
        },
    )
    await session.commit()
    return RecordSentResponse(
        id=record.id,
        message_id=message.id,
        message_version_id=version.id,
        sent_at=record.sent_at,
        channel_type=record.channel_type,
        channel_note=record.channel_note,
        recipient_email_masked=record.recipient_email_masked,
        status=INVITATION_STATUS_RECORDED_SENT,
    )


async def confirm_invitation(
    session: AsyncSession,
    *,
    round_id: UUID,
    payload: ConfirmInvitationRequest,
    actor: User,
    request_context: RequestContext,
) -> ConfirmInvitationResponse:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="invite_confirm",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        loaded = await get_round_by_id(session, round_id)
        assert loaded is not None
        users = await _load_users(
            session,
            [loaded.invitation_confirmed_by]
            if loaded.invitation_confirmed_by
            else [],
        )
        confirmer = (
            users.get(loaded.invitation_confirmed_by)
            if loaded.invitation_confirmed_by
            else None
        )
        return ConfirmInvitationResponse(
            round_id=loaded.id,
            status=loaded.status,
            schedule_version=loaded.invitation_confirmed_schedule_version or 0,
            version=loaded.version,
            confirmed_at=loaded.invitation_confirmed_at,
            confirmed_by_name=confirmer.display_name if confirmer else None,
            confirmation_summary=loaded.invitation_confirmation_summary,
        )

    if round_.status == INTERVIEW_STATUS_CONFIRMED:
        # Already confirmed with different key/payload handled above; same state.
        raise InterviewStateError("round is already CONFIRMED")
    if round_.status != INTERVIEW_STATUS_SCHEDULED:
        raise InterviewStateError("only SCHEDULED rounds can confirm invitation")
    if round_.version != payload.version:
        raise InterviewOptimisticLockError(OPTIMISTIC_LOCK_MESSAGE)

    active = await get_active_schedule_for_update(session, round_.id)
    if active is None or active.status != SCHEDULE_STATUS_ACTIVE:
        raise InterviewOptimisticLockError("schedule version conflict, please refresh")
    if active.schedule_version != payload.schedule_version:
        raise InterviewOptimisticLockError("schedule version conflict, please refresh")

    messages = await list_messages_for_schedule(session, active.id)
    active_messages = [
        item for item in messages if item.status != INVITATION_STATUS_VOIDED
    ]
    unrecorded = [
        item
        for item in active_messages
        if item.status != INVITATION_STATUS_RECORDED_SENT
    ]
    if unrecorded and not (payload.send_summary or "").strip():
        raise InterviewValidationError(
            "send_summary is required when unrecorded invitations exist"
        )

    now = _now()
    round_.status = next_status(round_.status, "confirm_invitation")
    round_.invitation_confirmed_at = now
    round_.invitation_confirmed_by = actor.id
    round_.invitation_confirmed_schedule_version = active.schedule_version
    round_.invitation_confirmation_summary = (payload.send_summary or "").strip() or None
    round_.version += 1
    round_.updated_by = actor.id
    round_.updated_at = now

    await _store_idempotency(
        session,
        actor=actor,
        action="invite_confirm",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_round.confirm_invitation",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_.id),
        changes={
            "schedule_version": active.schedule_version,
            "status": INTERVIEW_STATUS_CONFIRMED,
            "unrecorded_count": len(unrecorded),
            "has_send_summary": bool((payload.send_summary or "").strip()),
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    return ConfirmInvitationResponse(
        round_id=round_.id,
        status=round_.status,
        schedule_version=active.schedule_version,
        version=round_.version,
        confirmed_at=now,
        confirmed_by_name=actor.display_name,
        confirmation_summary=round_.invitation_confirmation_summary,
    )


__all__ = [
    "audit_copy",
    "confirm_invitation",
    "generate_invitations",
    "get_invitation_detail",
    "list_invitations",
    "record_sent",
    "update_invitation",
]
