"""Offer draft / version service (Task 2: no send, no Celery, no hired)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import InterviewIdempotencyKey
from app.models.offer import (
    MAIL_PROVIDER_CONSOLE,
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_STATUS_DRAFT,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_READY,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    OFFER_STATUS_VOIDED,
    OFFER_TEMPLATE_CODE,
    OFFER_TEMPLATE_VERSION,
    Offer,
    OfferSendAttempt,
    OfferVersion,
)
from app.models.resume import PIPELINE_PENDING_OFFER
from app.repositories.interviews import add_idempotency, find_idempotency
from app.repositories.offers import (
    add_offer,
    add_offer_send_attempt,
    add_offer_version,
    find_active_offer_for_application,
    find_attempt_by_idempotency,
    get_offer_by_id,
    get_offer_by_id_for_update,
    get_offer_send_attempt_for_update,
    get_offer_version,
    list_offer_send_attempts,
    list_offers_by_application,
    list_recommend_hire_decisions,
    next_version_no,
)
from app.repositories.resumes import (
    get_application_by_id,
    get_application_by_id_for_update,
)
from app.services.audit import RequestContext, record_audit
from app.services.crypto import CIPHER_PREFIX, EncryptionError, decrypt_secret, encrypt_secret
from app.services.mail_masking import mask_email

_AUDIT_CHANGE_KEYS = frozenset(
    {
        "application_id",
        "offer_id",
        "hiring_decision_id",
        "lock_version",
        "version_no",
        "version_id",
        "content_hash",
        "status",
        "from_status",
        "to_status",
        "recipient_email_masked",
        "idempotency_key",
        "void_reason_code",
        "attempt_id",
        "attempt_no",
        "provider",
        "attempt_status",
        "error_code",
        "offer_status",
        "started_at",
        "age_seconds",
        "expected_updated_at",
        "finished_at",
    }
)

STALE_SEND_ATTEMPT_MIN_AGE = timedelta(minutes=5)
STALE_SEND_ATTEMPT_ERROR_CODE = "stale_send_attempt_recovered"

_EDITABLE_STATUSES = frozenset({OFFER_STATUS_DRAFT, OFFER_STATUS_READY})
_VOIDABLE_STATUSES = frozenset(
    {OFFER_STATUS_DRAFT, OFFER_STATUS_READY, OFFER_STATUS_FAILED}
)


class OfferNotFoundError(Exception):
    pass


class OfferStateError(Exception):
    pass


class OfferValidationError(Exception):
    pass


class OfferConflictError(Exception):
    pass


@dataclass(frozen=True)
class OfferSummary:
    id: UUID
    application_id: UUID
    status: str
    hiring_decision_id: UUID
    recipient_email_masked: str | None
    recipient_name: str
    lock_version: int
    version_no: int | None
    content_hash: str | None
    frozen: bool | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OfferDetail:
    id: UUID
    application_id: UUID
    status: str
    hiring_decision_id: UUID
    recipient_email_masked: str | None
    recipient_name: str
    lock_version: int
    version_id: UUID | None
    version_no: int | None
    content_hash: str | None
    frozen: bool | None
    subject: str
    body_html: str
    body_text: str
    template_code: str | None
    template_version: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OfferResult:
    id: UUID
    application_id: UUID
    status: str
    hiring_decision_id: UUID
    recipient_email_masked: str | None
    recipient_name: str
    lock_version: int
    version_id: UUID | None
    version_no: int | None
    content_hash: str | None
    frozen: bool | None
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _content_hash(subject: str, body_html: str, body_text: str) -> str:
    payload = json.dumps(
        {"subject": subject, "body_html": body_html, "body_text": body_text},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encrypt_required(plain: str) -> str:
    """Encrypt including empty string (column is NOT NULL)."""
    cipher = encrypt_secret(plain)
    if cipher is not None:
        return cipher
    # encrypt_secret returns None for ""; Fernet-encrypt empty bytes directly.
    from app.services.crypto import _load_fernet

    token = _load_fernet().encrypt((plain or "").encode("utf-8"))
    return CIPHER_PREFIX + token.decode("ascii")


def _decrypt_required(cipher: str | None) -> str:
    if not cipher:
        return ""
    try:
        return decrypt_secret(cipher) or ""
    except EncryptionError as exc:
        raise OfferValidationError("unable to decrypt offer body") from exc


def _canonical_hash(payload: dict[str, Any]) -> str:
    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: _strip(item)
                for key, item in value.items()
                if key
                not in {
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

    return hashlib.sha256(
        json.dumps(_strip(payload), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _assert_audit_keys(changes: dict[str, Any]) -> None:
    assert set(changes.keys()) <= _AUDIT_CHANGE_KEYS


async def _consume_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str | None,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
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
        raise OfferConflictError("idempotency conflict")
    return existing


async def _store_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str | None,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
    if not key:
        return None
    record = InterviewIdempotencyKey(
        actor_id=actor.id,
        action=action,
        scope_id=scope_id,
        idempotency_key=key,
        request_hash=_canonical_hash(request_payload),
        result_round_id=None,
    )
    await add_idempotency(session, record)
    return record


def _to_result(offer: Offer, version: OfferVersion | None) -> OfferResult:
    return OfferResult(
        id=offer.id,
        application_id=offer.application_id,
        status=offer.status,
        hiring_decision_id=offer.hiring_decision_id,
        recipient_email_masked=offer.recipient_email_masked,
        recipient_name=offer.recipient_name,
        lock_version=offer.lock_version,
        version_id=version.id if version else offer.current_version_id,
        version_no=version.version_no if version else None,
        content_hash=version.content_hash if version else None,
        frozen=version.frozen if version else None,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


def _to_summary(offer: Offer, version: OfferVersion | None) -> OfferSummary:
    return OfferSummary(
        id=offer.id,
        application_id=offer.application_id,
        status=offer.status,
        hiring_decision_id=offer.hiring_decision_id,
        recipient_email_masked=offer.recipient_email_masked,
        recipient_name=offer.recipient_name,
        lock_version=offer.lock_version,
        version_no=version.version_no if version else None,
        content_hash=version.content_hash if version else None,
        frozen=version.frozen if version else None,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


def _assert_application_offer_gate(application: Any) -> None:
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise OfferStateError("closed application cannot receive offer draft")
    if application.pipeline_status != PIPELINE_PENDING_OFFER:
        raise OfferStateError(
            "only pending_offer applications can create or edit offers"
        )


async def create_offer(
    session: AsyncSession,
    *,
    application_id: UUID,
    actor: User,
    request_context: RequestContext,
    idempotency_key: str | None = None,
) -> OfferResult:
    request_payload = {
        "application_id": str(application_id),
        "idempotency_key": idempotency_key,
    }
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="offer.create",
        scope_id=application_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        existing = await find_active_offer_for_application(
            session, application_id=application_id
        )
        if existing is None:
            raise OfferConflictError("idempotency conflict")
        version = None
        if existing.current_version_id:
            version = await get_offer_version(session, existing.current_version_id)
        return _to_result(existing, version)

    application = await get_application_by_id_for_update(session, application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    _assert_application_offer_gate(application)

    decisions = await list_recommend_hire_decisions(
        session, application_id=application.id
    )
    if not decisions:
        raise OfferValidationError("latest recommend_hire decision is required")
    latest = decisions[0]

    active = await find_active_offer_for_application(
        session, application_id=application.id
    )
    if active is not None:
        raise OfferConflictError("active offer already exists for application")

    candidate = getattr(application, "candidate", None)
    email = getattr(candidate, "email", None) if candidate else None
    masked = mask_email(email)
    if masked is None:
        raise OfferValidationError("candidate email is required to create offer")
    name = (getattr(candidate, "name", None) if candidate else None) or ""
    if not name:
        raise OfferValidationError("candidate name is required")

    now = _now()
    offer = Offer(
        id=uuid4(),
        application_id=application.id,
        hiring_decision_id=latest.id,
        status=OFFER_STATUS_DRAFT,
        current_version_id=None,
        recipient_email_masked=masked,
        recipient_name=name,
        lock_version=1,
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    try:
        await add_offer(session, offer)
        subject, body_html, body_text = "", "", ""
        version = OfferVersion(
            id=uuid4(),
            offer_id=offer.id,
            version_no=1,
            subject_encrypted=_encrypt_required(subject),
            body_html_encrypted=_encrypt_required(body_html),
            body_text_encrypted=_encrypt_required(body_text),
            content_hash=_content_hash(subject, body_html, body_text),
            template_code=OFFER_TEMPLATE_CODE,
            template_version=OFFER_TEMPLATE_VERSION,
            frozen=False,
            created_by=actor.id,
            created_at=now,
        )
        await add_offer_version(session, version)
        offer.current_version_id = version.id
        await _store_idempotency(
            session,
            actor=actor,
            action="offer.create",
            scope_id=application_id,
            key=idempotency_key,
            request_payload=request_payload,
        )
        changes = {
            "application_id": str(application.id),
            "offer_id": str(offer.id),
            "hiring_decision_id": str(latest.id),
            "lock_version": offer.lock_version,
            "version_no": version.version_no,
            "status": offer.status,
            "recipient_email_masked": masked,
            "idempotency_key": idempotency_key,
        }
        _assert_audit_keys(changes)
        await record_audit(
            session,
            action="offer.created",
            result="success",
            resource_type="offer",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(offer.id),
            changes=changes,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise OfferConflictError(
            "active offer already exists for application"
        ) from exc
    return _to_result(offer, version)


async def update_offer_draft(
    session: AsyncSession,
    *,
    offer_id: UUID,
    subject: str,
    body_html: str,
    body_text: str,
    lock_version: int,
    actor: User,
    request_context: RequestContext,
    idempotency_key: str | None = None,
) -> OfferResult:
    request_payload = {
        "offer_id": str(offer_id),
        "lock_version": lock_version,
        "content_hash": _content_hash(subject, body_html, body_text),
        "idempotency_key": idempotency_key,
    }
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="offer.update",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        offer = await get_offer_by_id(session, offer_id)
        if offer is None:
            raise OfferNotFoundError("offer not found")
        version = None
        if offer.current_version_id:
            version = await get_offer_version(session, offer.current_version_id)
        return _to_result(offer, version)

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.lock_version != lock_version:
        raise OfferConflictError(
            "offer was updated by another user; refresh and retry"
        )
    if offer.status not in _EDITABLE_STATUSES:
        raise OfferStateError("offer content cannot be edited in current status")

    application = await get_application_by_id_for_update(session, offer.application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    _assert_application_offer_gate(application)

    # Never mutate a frozen version in place — always insert a new version.
    if offer.current_version_id:
        current = await get_offer_version(session, offer.current_version_id)
        if current is None:
            raise OfferNotFoundError("offer version not found")
    else:
        current = None

    version_no = await next_version_no(session, offer_id=offer.id)
    now = _now()
    version = OfferVersion(
        id=uuid4(),
        offer_id=offer.id,
        version_no=version_no,
        subject_encrypted=_encrypt_required(subject),
        body_html_encrypted=_encrypt_required(body_html),
        body_text_encrypted=_encrypt_required(body_text),
        content_hash=_content_hash(subject, body_html, body_text),
        template_code=OFFER_TEMPLATE_CODE,
        template_version=OFFER_TEMPLATE_VERSION,
        frozen=False,
        created_by=actor.id,
        created_at=now,
    )
    await add_offer_version(session, version)
    offer.current_version_id = version.id
    if offer.status == OFFER_STATUS_READY:
        offer.status = OFFER_STATUS_DRAFT
    offer.lock_version += 1
    offer.updated_by = actor.id
    offer.updated_at = now

    await _store_idempotency(
        session,
        actor=actor,
        action="offer.update",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    changes = {
        "offer_id": str(offer.id),
        "version_no": version.version_no,
        "version_id": str(version.id),
        "content_hash": version.content_hash,
        "lock_version": offer.lock_version,
        "status": offer.status,
        "recipient_email_masked": offer.recipient_email_masked,
        "idempotency_key": idempotency_key,
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.updated",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer.id),
        changes=changes,
    )
    await session.commit()
    return _to_result(offer, version)


async def mark_offer_ready(
    session: AsyncSession,
    *,
    offer_id: UUID,
    lock_version: int,
    actor: User,
    request_context: RequestContext,
    idempotency_key: str | None = None,
) -> OfferResult:
    request_payload = {
        "offer_id": str(offer_id),
        "lock_version": lock_version,
        "idempotency_key": idempotency_key,
    }
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="offer.ready",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        offer = await get_offer_by_id(session, offer_id)
        if offer is None:
            raise OfferNotFoundError("offer not found")
        version = None
        if offer.current_version_id:
            version = await get_offer_version(session, offer.current_version_id)
        return _to_result(offer, version)

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.lock_version != lock_version:
        raise OfferConflictError(
            "offer was updated by another user; refresh and retry"
        )
    if offer.status != OFFER_STATUS_DRAFT:
        raise OfferStateError("only draft offers can be marked ready")
    if not offer.recipient_email_masked:
        raise OfferValidationError("recipient email masked is required")
    if not offer.current_version_id:
        raise OfferValidationError("offer version is required")

    application = await get_application_by_id_for_update(session, offer.application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    _assert_application_offer_gate(application)

    version = await get_offer_version(session, offer.current_version_id)
    if version is None:
        raise OfferNotFoundError("offer version not found")
    subject = _decrypt_required(version.subject_encrypted)
    body_html = _decrypt_required(version.body_html_encrypted)
    body_text = _decrypt_required(version.body_text_encrypted)
    if not (subject.strip() or body_html.strip() or body_text.strip()):
        raise OfferValidationError("offer body is empty")

    from_status = offer.status
    version.frozen = True
    offer.status = OFFER_STATUS_READY
    offer.lock_version += 1
    offer.updated_by = actor.id
    offer.updated_at = _now()

    await _store_idempotency(
        session,
        actor=actor,
        action="offer.ready",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    changes = {
        "offer_id": str(offer.id),
        "version_id": str(version.id),
        "version_no": version.version_no,
        "content_hash": version.content_hash,
        "lock_version": offer.lock_version,
        "from_status": from_status,
        "to_status": offer.status,
        "status": offer.status,
        "recipient_email_masked": offer.recipient_email_masked,
        "idempotency_key": idempotency_key,
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.marked_ready",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer.id),
        changes=changes,
    )
    await session.commit()
    return _to_result(offer, version)


async def void_offer(
    session: AsyncSession,
    *,
    offer_id: UUID,
    void_reason_code: str,
    lock_version: int,
    actor: User,
    request_context: RequestContext,
    idempotency_key: str | None = None,
) -> OfferResult:
    if not (void_reason_code or "").strip():
        raise OfferValidationError("void_reason_code is required")
    request_payload = {
        "offer_id": str(offer_id),
        "lock_version": lock_version,
        "void_reason_code": void_reason_code,
        "idempotency_key": idempotency_key,
    }
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="offer.void",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        offer = await get_offer_by_id(session, offer_id)
        if offer is None:
            raise OfferNotFoundError("offer not found")
        version = None
        if offer.current_version_id:
            version = await get_offer_version(session, offer.current_version_id)
        return _to_result(offer, version)

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.lock_version != lock_version:
        raise OfferConflictError(
            "offer was updated by another user; refresh and retry"
        )
    if offer.status not in _VOIDABLE_STATUSES:
        raise OfferStateError("offer cannot be voided in current status")

    from_status = offer.status
    offer.status = OFFER_STATUS_VOIDED
    offer.void_reason_code = void_reason_code.strip()
    offer.voided_at = _now()
    offer.lock_version += 1
    offer.updated_by = actor.id
    offer.updated_at = offer.voided_at

    version = None
    if offer.current_version_id:
        version = await get_offer_version(session, offer.current_version_id)

    await _store_idempotency(
        session,
        actor=actor,
        action="offer.void",
        scope_id=offer_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    changes = {
        "offer_id": str(offer.id),
        "lock_version": offer.lock_version,
        "from_status": from_status,
        "to_status": offer.status,
        "status": offer.status,
        "void_reason_code": offer.void_reason_code,
        "recipient_email_masked": offer.recipient_email_masked,
        "idempotency_key": idempotency_key,
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.voided",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer.id),
        changes=changes,
    )
    await session.commit()
    return _to_result(offer, version)


async def get_offer_detail(
    session: AsyncSession, *, offer_id: UUID
) -> OfferDetail:
    offer = await get_offer_by_id(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    version = None
    subject = body_html = body_text = ""
    if offer.current_version_id:
        version = await get_offer_version(session, offer.current_version_id)
        if version is not None:
            subject = _decrypt_required(version.subject_encrypted)
            body_html = _decrypt_required(version.body_html_encrypted)
            body_text = _decrypt_required(version.body_text_encrypted)
    return OfferDetail(
        id=offer.id,
        application_id=offer.application_id,
        status=offer.status,
        hiring_decision_id=offer.hiring_decision_id,
        recipient_email_masked=offer.recipient_email_masked,
        recipient_name=offer.recipient_name,
        lock_version=offer.lock_version,
        version_id=version.id if version else None,
        version_no=version.version_no if version else None,
        content_hash=version.content_hash if version else None,
        frozen=version.frozen if version else None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        template_code=version.template_code if version else None,
        template_version=version.template_version if version else None,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


async def list_offers_for_application(
    session: AsyncSession, *, application_id: UUID
) -> list[OfferSummary]:
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    rows = await list_offers_by_application(session, application_id=application_id)
    items: list[OfferSummary] = []
    for row in rows:
        version = None
        if row.current_version_id:
            version = await get_offer_version(session, row.current_version_id)
        items.append(_to_summary(row, version))
    return items


@dataclass(frozen=True)
class OfferSendResult:
    offer_id: UUID
    attempt_id: UUID
    status: str
    attempt_status: str
    attempt_no: int
    lock_version: int
    version_id: UUID
    provider: str


@dataclass(frozen=True)
class OfferAttemptSummary:
    id: UUID
    offer_id: UUID
    offer_version_id: UUID
    provider: str
    status: str
    attempt_no: int
    error_code: str | None
    error_message_safe: str | None
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime


async def list_offer_attempts(
    session: AsyncSession, *, offer_id: UUID
) -> list[OfferAttemptSummary]:
    offer = await get_offer_by_id(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    rows = await list_offer_send_attempts(session, offer_id=offer_id)
    return [
        OfferAttemptSummary(
            id=row.id,
            offer_id=row.offer_id,
            offer_version_id=row.offer_version_id,
            provider=row.provider,
            status=row.status,
            attempt_no=row.attempt_no,
            error_code=row.error_code,
            error_message_safe=row.error_message_safe,
            started_at=row.started_at,
            finished_at=row.finished_at,
            next_retry_at=row.next_retry_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


class _MailTaskProxy:
    """Lazy Celery task handle so tests can monkeypatch ``process_mail_send_attempt``."""

    def apply_async(self, *args, **kwargs):
        from app.workers.mail_tasks import process_mail_send_attempt as task

        return task.apply_async(*args, **kwargs)


process_mail_send_attempt = _MailTaskProxy()


def enqueue_mail_send_attempt(attempt_id: UUID, *, countdown: int = 0) -> None:
    process_mail_send_attempt.apply_async(
        args=[str(attempt_id)], countdown=countdown
    )


async def confirm_offer_send(
    session: AsyncSession,
    *,
    offer_id: UUID,
    offer_version_id: UUID,
    lock_version: int,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> OfferSendResult:
    if not (idempotency_key or "").strip():
        raise OfferValidationError("idempotency_key is required")

    existing_attempt = await find_attempt_by_idempotency(
        session, offer_id=offer_id, idempotency_key=idempotency_key
    )
    if existing_attempt is not None:
        offer = await get_offer_by_id(session, offer_id)
        if offer is None:
            raise OfferNotFoundError("offer not found")
        # Pending-only re-dispatch: recover commit-succeeded / enqueue-failed.
        if existing_attempt.status == OFFER_ATTEMPT_STATUS_PENDING:
            enqueue_mail_send_attempt(existing_attempt.id, countdown=0)
        return OfferSendResult(
            offer_id=offer.id,
            attempt_id=existing_attempt.id,
            status=offer.status,
            attempt_status=existing_attempt.status,
            attempt_no=existing_attempt.attempt_no,
            lock_version=offer.lock_version,
            version_id=existing_attempt.offer_version_id,
            provider=existing_attempt.provider,
        )

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.lock_version != lock_version:
        raise OfferConflictError(
            "offer was updated by another user; refresh and retry"
        )
    if offer.status not in {OFFER_STATUS_READY, OFFER_STATUS_FAILED}:
        raise OfferStateError("only ready or failed offers can be sent")
    if offer.current_version_id != offer_version_id:
        raise OfferValidationError("offer_version_id must be current version")

    application = await get_application_by_id_for_update(session, offer.application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    _assert_application_offer_gate(application)

    version = await get_offer_version(session, offer_version_id)
    if version is None or version.offer_id != offer.id:
        raise OfferValidationError("offer_version_id does not belong to offer")

    version.frozen = True
    from_status = offer.status
    offer.status = OFFER_STATUS_SENDING
    offer.lock_version += 1
    offer.updated_by = actor.id
    offer.updated_at = _now()

    attempt = OfferSendAttempt(
        id=uuid4(),
        offer_id=offer.id,
        offer_version_id=version.id,
        provider=MAIL_PROVIDER_CONSOLE,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        idempotency_key=idempotency_key,
        created_by=actor.id,
        created_at=_now(),
    )
    await add_offer_send_attempt(session, attempt)

    changes = {
        "offer_id": str(offer.id),
        "version_id": str(version.id),
        "attempt_id": str(attempt.id),
        "attempt_no": attempt.attempt_no,
        "provider": attempt.provider,
        "idempotency_key": idempotency_key,
        "lock_version": offer.lock_version,
        "from_status": from_status,
        "to_status": offer.status,
        "status": offer.status,
        "recipient_email_masked": offer.recipient_email_masked,
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.send_confirmed",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer.id),
        changes=changes,
    )
    await session.commit()
    # Enqueue after commit; failure leaves pending for same-key re-dispatch.
    enqueue_mail_send_attempt(attempt.id, countdown=0)
    return OfferSendResult(
        offer_id=offer.id,
        attempt_id=attempt.id,
        status=offer.status,
        attempt_status=attempt.status,
        attempt_no=attempt.attempt_no,
        lock_version=offer.lock_version,
        version_id=version.id,
        provider=attempt.provider,
    )


async def retry_offer_send(
    session: AsyncSession,
    *,
    offer_id: UUID,
    lock_version: int,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> OfferSendResult:
    if not (idempotency_key or "").strip():
        raise OfferValidationError("idempotency_key is required")

    existing_attempt = await find_attempt_by_idempotency(
        session, offer_id=offer_id, idempotency_key=idempotency_key
    )
    if existing_attempt is not None:
        offer = await get_offer_by_id(session, offer_id)
        if offer is None:
            raise OfferNotFoundError("offer not found")
        if existing_attempt.status == OFFER_ATTEMPT_STATUS_PENDING:
            enqueue_mail_send_attempt(existing_attempt.id, countdown=0)
        return OfferSendResult(
            offer_id=offer.id,
            attempt_id=existing_attempt.id,
            status=offer.status,
            attempt_status=existing_attempt.status,
            attempt_no=existing_attempt.attempt_no,
            lock_version=offer.lock_version,
            version_id=existing_attempt.offer_version_id,
            provider=existing_attempt.provider,
        )

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.lock_version != lock_version:
        raise OfferConflictError(
            "offer was updated by another user; refresh and retry"
        )
    if offer.status != OFFER_STATUS_FAILED:
        raise OfferStateError("only failed offers can be manually retried")
    if not offer.current_version_id:
        raise OfferValidationError("frozen version is required")

    application = await get_application_by_id_for_update(session, offer.application_id)
    if application is None:
        raise OfferNotFoundError("application not found")
    _assert_application_offer_gate(application)

    version = await get_offer_version(session, offer.current_version_id)
    if version is None or not version.frozen:
        raise OfferValidationError("retry requires frozen current version")

    from_status = offer.status
    offer.status = OFFER_STATUS_SENDING
    offer.lock_version += 1
    offer.updated_by = actor.id
    offer.updated_at = _now()

    attempt = OfferSendAttempt(
        id=uuid4(),
        offer_id=offer.id,
        offer_version_id=version.id,
        provider=MAIL_PROVIDER_CONSOLE,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        idempotency_key=idempotency_key,
        created_by=actor.id,
        created_at=_now(),
    )
    await add_offer_send_attempt(session, attempt)

    changes = {
        "offer_id": str(offer.id),
        "version_id": str(version.id),
        "attempt_id": str(attempt.id),
        "attempt_no": attempt.attempt_no,
        "provider": attempt.provider,
        "idempotency_key": idempotency_key,
        "lock_version": offer.lock_version,
        "from_status": from_status,
        "to_status": offer.status,
        "status": offer.status,
        "recipient_email_masked": offer.recipient_email_masked,
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.retry_requested",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer.id),
        changes=changes,
    )
    await session.commit()
    enqueue_mail_send_attempt(attempt.id, countdown=0)
    return OfferSendResult(
        offer_id=offer.id,
        attempt_id=attempt.id,
        status=offer.status,
        attempt_status=attempt.status,
        attempt_no=attempt.attempt_no,
        lock_version=offer.lock_version,
        version_id=version.id,
        provider=attempt.provider,
    )


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class MarkStaleOfferAttemptResult:
    offer_id: UUID
    attempt_id: UUID
    offer_status: str
    attempt_status: str
    error_code: str
    updated_at: datetime
    finished_at: datetime


async def mark_stale_failed_offer_send_attempt(
    session: AsyncSession,
    *,
    offer_id: UUID,
    attempt_id: UUID,
    expected_updated_at: datetime,
    actor: User,
    request_context: RequestContext,
) -> MarkStaleOfferAttemptResult:
    """Manage-only reclaim of stuck running send attempt. Zero enqueue / Console."""
    now = _now()
    expected = _normalize_utc(expected_updated_at)

    attempt = await get_offer_send_attempt_for_update(session, attempt_id)
    if attempt is None or attempt.offer_id != offer_id:
        raise OfferNotFoundError("offer send attempt not found")
    if attempt.status != OFFER_ATTEMPT_STATUS_RUNNING:
        raise OfferStateError("only running attempts can be marked stale-failed")
    if attempt.started_at is None:
        raise OfferStateError("running attempt missing started_at")

    started = _normalize_utc(attempt.started_at)
    if started != expected:
        raise OfferStateError("expected_updated_at mismatch")
    if started > now - STALE_SEND_ATTEMPT_MIN_AGE:
        raise OfferStateError("running attempt is not stale enough")

    offer = await get_offer_by_id_for_update(session, offer_id)
    if offer is None:
        raise OfferNotFoundError("offer not found")
    if offer.status != OFFER_STATUS_SENDING:
        raise OfferStateError("only sending offers can reclaim stale attempts")

    age_seconds = int((now - started).total_seconds())
    from_offer = offer.status
    from_attempt = attempt.status

    claimed_attempt = await session.execute(
        update(OfferSendAttempt)
        .where(
            OfferSendAttempt.id == attempt_id,
            OfferSendAttempt.offer_id == offer_id,
            OfferSendAttempt.status == OFFER_ATTEMPT_STATUS_RUNNING,
            OfferSendAttempt.started_at == expected,
            OfferSendAttempt.started_at <= now - STALE_SEND_ATTEMPT_MIN_AGE,
        )
        .values(
            status=OFFER_ATTEMPT_STATUS_DEAD,
            error_code=STALE_SEND_ATTEMPT_ERROR_CODE,
            error_message_safe="stale send attempt recovered",
            finished_at=now,
        )
    )
    if claimed_attempt.rowcount != 1:
        raise OfferStateError("stale send attempt recovery conflict")

    claimed_offer = await session.execute(
        update(Offer)
        .where(
            Offer.id == offer_id,
            Offer.status == OFFER_STATUS_SENDING,
        )
        .values(
            status=OFFER_STATUS_FAILED,
            updated_at=now,
            updated_by=actor.id,
            lock_version=int(offer.lock_version) + 1,
        )
    )
    if claimed_offer.rowcount != 1:
        raise OfferStateError("stale send attempt recovery conflict")

    changes = {
        "offer_id": str(offer_id),
        "attempt_id": str(attempt_id),
        "from_status": from_offer,
        "to_status": OFFER_STATUS_FAILED,
        "attempt_status": OFFER_ATTEMPT_STATUS_DEAD,
        "offer_status": OFFER_STATUS_FAILED,
        "error_code": STALE_SEND_ATTEMPT_ERROR_CODE,
        "started_at": started.isoformat(),
        "age_seconds": age_seconds,
        "expected_updated_at": expected.isoformat(),
        "finished_at": now.isoformat(),
    }
    _assert_audit_keys(changes)
    await record_audit(
        session,
        action="offer.stale_send_attempt_recovered",
        result="success",
        resource_type="offer",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(offer_id),
        changes=changes,
    )
    await session.commit()

    return MarkStaleOfferAttemptResult(
        offer_id=offer_id,
        attempt_id=attempt_id,
        offer_status=OFFER_STATUS_FAILED,
        attempt_status=OFFER_ATTEMPT_STATUS_DEAD,
        error_code=STALE_SEND_ATTEMPT_ERROR_CODE,
        updated_at=now,
        finished_at=now,
    )
