"""Independent mail outbound Celery tasks (not AI / not default celery queue)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models.offer import (
    MAIL_MAX_AUTO_ATTEMPTS,
    MAIL_PROVIDER_CONSOLE,
    MAIL_RETRY_COUNTDOWNS_SECONDS,
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_FAILED,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    Offer,
    OfferSendAttempt,
)
from app.repositories.offers import (
    add_offer_send_attempt,
    get_offer_by_id_for_update,
    get_offer_send_attempt_for_update,
    get_offer_version,
)
from app.services.audit import RequestContext, record_audit
from app.services.mail_providers.console import ConsoleMailProvider
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_TERMINAL_ATTEMPT = frozenset(
    {
        OFFER_ATTEMPT_STATUS_SUCCEEDED,
        OFFER_ATTEMPT_STATUS_DEAD,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def _worker_request_context() -> RequestContext:
    return RequestContext(request_id="mail-worker", ip_address=None)


def enqueue_mail_send_attempt(attempt_id: UUID, *, countdown: int = 0) -> None:
    process_mail_send_attempt.apply_async(
        args=[str(attempt_id)], countdown=countdown
    )


async def _claim_pending_attempt(
    session: AsyncSession, attempt_id: UUID
) -> OfferSendAttempt | None:
    """Claim pending → running. Returns None if not pending (idempotent skip)."""
    attempt = await get_offer_send_attempt_for_update(session, attempt_id)
    if attempt is None:
        return None
    if attempt.status != OFFER_ATTEMPT_STATUS_PENDING:
        return None
    attempt.status = OFFER_ATTEMPT_STATUS_RUNNING
    attempt.started_at = _now()
    return attempt


async def _reassert_sending_ownership_for_terminal(
    session: AsyncSession,
    *,
    attempt_id: UUID,
) -> tuple[OfferSendAttempt, Offer] | None:
    """Re-lock attempt + offer; require attempt=running and offer=sending."""
    attempt = await get_offer_send_attempt_for_update(session, attempt_id)
    if attempt is None or attempt.status != OFFER_ATTEMPT_STATUS_RUNNING:
        observed = attempt.status if attempt is not None else "missing"
        logger.info(
            "mail attempt %s skip terminal write: attempt_status=%s error_type=stale_owner",
            attempt_id,
            observed,
        )
        return None
    offer = await get_offer_by_id_for_update(session, attempt.offer_id)
    if offer is None or offer.status != OFFER_STATUS_SENDING:
        observed = offer.status if offer is not None else "missing"
        logger.info(
            "mail attempt %s skip terminal write: offer_status=%s error_type=stale_owner",
            attempt_id,
            observed,
        )
        return None
    return attempt, offer


async def _skipped_stale_owner_result(
    *,
    attempt_status: str | None,
    offer_status: str | None,
) -> dict:
    return {
        "status": "skipped_stale_owner",
        "attempt_status": attempt_status,
        "offer_status": offer_status,
    }


async def _process_mail_send_attempt_async(attempt_id: str) -> dict:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await _handle_mail_attempt(session, UUID(attempt_id))
    finally:
        await engine.dispose()


async def _handle_mail_attempt(session, attempt_id: UUID) -> dict:
    attempt = await get_offer_send_attempt_for_update(session, attempt_id)
    if attempt is None:
        return {"status": "missing"}

    if attempt.status in _TERMINAL_ATTEMPT:
        return {"status": "noop", "attempt_status": attempt.status}

    if attempt.status == OFFER_ATTEMPT_STATUS_FAILED:
        # Failed attempts are not re-executed; auto-retry creates a new attempt.
        return {"status": "noop", "attempt_status": attempt.status}

    if attempt.status == OFFER_ATTEMPT_STATUS_RUNNING:
        # Duplicate / late delivery while claim already held — never Console again.
        offer = await get_offer_by_id_for_update(session, attempt.offer_id)
        if offer is None or offer.status != OFFER_STATUS_SENDING:
            return await _skipped_stale_owner_result(
                attempt_status=attempt.status,
                offer_status=offer.status if offer is not None else None,
            )
        return {"status": "skipped", "attempt_status": attempt.status}

    if attempt.status != OFFER_ATTEMPT_STATUS_PENDING:
        return {"status": "noop", "attempt_status": attempt.status}

    claimed = await _claim_pending_attempt(session, attempt_id)
    if claimed is None:
        # Lost race to another claimer.
        return {"status": "skipped", "attempt_status": "non_pending"}
    attempt = claimed
    await session.commit()

    offer = await get_offer_by_id_for_update(session, attempt.offer_id)
    if offer is None:
        attempt = await get_offer_send_attempt_for_update(session, attempt_id)
        if attempt is not None and attempt.status == OFFER_ATTEMPT_STATUS_RUNNING:
            attempt.status = OFFER_ATTEMPT_STATUS_DEAD
            attempt.error_code = "offer_missing"
            attempt.error_message_safe = "offer missing"
            attempt.finished_at = _now()
            await session.commit()
        return {"status": "dead"}
    if offer.status != OFFER_STATUS_SENDING:
        return await _skipped_stale_owner_result(
            attempt_status=attempt.status,
            offer_status=offer.status,
        )

    version = await get_offer_version(session, attempt.offer_version_id)
    if version is None:
        owned = await _reassert_sending_ownership_for_terminal(
            session, attempt_id=attempt_id
        )
        if owned is None:
            return await _skipped_stale_owner_result(
                attempt_status=OFFER_ATTEMPT_STATUS_RUNNING,
                offer_status=None,
            )
        attempt, offer = owned
        attempt.status = OFFER_ATTEMPT_STATUS_DEAD
        attempt.error_code = "version_missing"
        attempt.error_message_safe = "version missing"
        attempt.finished_at = _now()
        offer.status = OFFER_STATUS_FAILED
        offer.updated_at = _now()
        await session.commit()
        return {"status": "dead"}

    provider = ConsoleMailProvider()
    result = provider.send(
        {
            "attempt_id": str(attempt.id),
            "offer_id": str(offer.id),
            "version_no": version.version_no,
            "recipient_email_masked": offer.recipient_email_masked,
            "content_hash": version.content_hash,
        }
    )

    # Re-lock before success / failure / auto-retry writes.
    owned = await _reassert_sending_ownership_for_terminal(
        session, attempt_id=attempt_id
    )
    if owned is None:
        attempt = await get_offer_send_attempt_for_update(session, attempt_id)
        offer_obs = None
        if attempt is not None:
            offer_obs = await get_offer_by_id_for_update(session, attempt.offer_id)
        return await _skipped_stale_owner_result(
            attempt_status=attempt.status if attempt else None,
            offer_status=offer_obs.status if offer_obs else None,
        )
    attempt, offer = owned

    if result.success:
        attempt.status = OFFER_ATTEMPT_STATUS_SUCCEEDED
        attempt.finished_at = _now()
        attempt.error_code = None
        attempt.error_message_safe = None
        offer.status = OFFER_STATUS_SENT
        offer.updated_at = _now()
        await record_audit(
            session,
            action="offer.send_attempt_finished",
            result="success",
            resource_type="offer",
            request_context=_worker_request_context(),
            actor_user_id=None,
            resource_id=str(offer.id),
            changes={
                "offer_id": str(offer.id),
                "attempt_id": str(attempt.id),
                "attempt_status": attempt.status,
                "offer_status": offer.status,
                "provider": MAIL_PROVIDER_CONSOLE,
            },
        )
        await session.commit()
        return {"status": "succeeded"}

    # Failure path
    attempt.status = OFFER_ATTEMPT_STATUS_FAILED
    attempt.error_code = result.error_code or "console_error"
    attempt.error_message_safe = (result.error_message_safe or "send failed")[:512]
    attempt.finished_at = _now()
    attempt_no = int(attempt.attempt_no)

    if attempt_no < MAIL_MAX_AUTO_ATTEMPTS:
        countdown = MAIL_RETRY_COUNTDOWNS_SECONDS.get(attempt_no) or 60
        attempt.next_retry_at = _now() + timedelta(seconds=countdown)
        next_attempt = OfferSendAttempt(
            id=uuid4(),
            offer_id=offer.id,
            offer_version_id=attempt.offer_version_id,
            provider=MAIL_PROVIDER_CONSOLE,
            status=OFFER_ATTEMPT_STATUS_PENDING,
            attempt_no=attempt_no + 1,
            idempotency_key=f"auto:{offer.id}:{attempt_no + 1}:{uuid4().hex[:8]}",
            created_by=None,
            created_at=_now(),
        )
        await add_offer_send_attempt(session, next_attempt)
        # Offer must remain sending (ownership already asserted).
        offer.updated_at = _now()
        await record_audit(
            session,
            action="offer.send_attempt_finished",
            result="failure",
            resource_type="offer",
            request_context=_worker_request_context(),
            actor_user_id=None,
            resource_id=str(offer.id),
            changes={
                "offer_id": str(offer.id),
                "attempt_id": str(attempt.id),
                "attempt_status": attempt.status,
                "error_code": attempt.error_code,
                "offer_status": offer.status,
                "provider": MAIL_PROVIDER_CONSOLE,
            },
        )
        await session.commit()
        enqueue_mail_send_attempt(next_attempt.id, countdown=countdown)
        return {
            "status": "failed_retry_scheduled",
            "countdown": countdown,
            "next_attempt_id": str(next_attempt.id),
        }

    attempt.status = OFFER_ATTEMPT_STATUS_DEAD
    offer.status = OFFER_STATUS_FAILED
    offer.updated_at = _now()
    await record_audit(
        session,
        action="offer.send_attempt_finished",
        result="failure",
        resource_type="offer",
        request_context=_worker_request_context(),
        actor_user_id=None,
        resource_id=str(offer.id),
        changes={
            "offer_id": str(offer.id),
            "attempt_id": str(attempt.id),
            "attempt_status": attempt.status,
            "error_code": attempt.error_code,
            "offer_status": offer.status,
            "provider": MAIL_PROVIDER_CONSOLE,
        },
    )
    await session.commit()
    return {"status": "dead"}


@celery_app.task(name="app.workers.mail_tasks.process_mail_send_attempt", bind=True)
def process_mail_send_attempt(self, attempt_id: str) -> dict:  # noqa: ARG001
    return asyncio.run(_process_mail_send_attempt_async(attempt_id))
