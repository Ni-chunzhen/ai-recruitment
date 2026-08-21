"""Real AsyncSession: late mail worker must not overwrite Offer terminal state."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base
from app.models.offer import (
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    Offer,
    OfferSendAttempt,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


MEMORY_URL = "sqlite+aiosqlite:///:memory:"


def _assert_safe_url(url: str) -> None:
    assert url.startswith("sqlite+aiosqlite://")
    assert "/recruit" not in url


@pytest.mark.asyncio
async def test_late_failure_does_not_revert_sent_offer_real_txn() -> None:
    from app.workers import mail_tasks as worker

    _assert_safe_url(MEMORY_URL)
    engine = create_async_engine(MEMORY_URL)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[Offer.__table__, OfferSendAttempt.__table__],
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    offer_id = uuid4()
    attempt_id = uuid4()
    version_id = uuid4()
    app_id = uuid4()
    decision_id = uuid4()
    now = datetime.now(UTC)

    async with factory() as s1:
        s1.add(
            Offer(
                id=offer_id,
                application_id=app_id,
                hiring_decision_id=decision_id,
                status=OFFER_STATUS_SENDING,
                current_version_id=version_id,
                recipient_name="A",
                recipient_email_masked="a***@e.com",
                lock_version=1,
            )
        )
        await s1.flush()
        s1.add(
            OfferSendAttempt(
                id=attempt_id,
                offer_id=offer_id,
                offer_version_id=version_id,
                provider="console",
                status=OFFER_ATTEMPT_STATUS_RUNNING,
                attempt_no=1,
                idempotency_key=f"txn-{attempt_id}",
                started_at=now,
            )
        )
        await s1.commit()

    # Concurrent winner: Offer sent + another attempt succeeded
    async with factory() as winner:
        offer = (
            await winner.execute(select(Offer).where(Offer.id == offer_id).with_for_update())
        ).scalar_one()
        offer.status = OFFER_STATUS_SENT
        await winner.commit()

    async with factory() as late:
        owned = await worker._reassert_sending_ownership_for_terminal(
            late, attempt_id=attempt_id
        )
        assert owned is None
        offer = (
            await late.execute(select(Offer).where(Offer.id == offer_id))
        ).scalar_one()
        attempt = (
            await late.execute(
                select(OfferSendAttempt).where(OfferSendAttempt.id == attempt_id)
            )
        ).scalar_one()
        assert offer.status == OFFER_STATUS_SENT
        assert attempt.status == OFFER_ATTEMPT_STATUS_RUNNING

    await engine.dispose()


@pytest.mark.asyncio
async def test_late_success_does_not_resurrect_failed_offer_real_txn() -> None:
    from app.workers import mail_tasks as worker

    _assert_safe_url(MEMORY_URL)
    engine = create_async_engine(MEMORY_URL)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[Offer.__table__, OfferSendAttempt.__table__],
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    offer_id = uuid4()
    attempt_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)

    async with factory() as s1:
        s1.add(
            Offer(
                id=offer_id,
                application_id=uuid4(),
                hiring_decision_id=uuid4(),
                status=OFFER_STATUS_FAILED,
                current_version_id=version_id,
                recipient_name="A",
                recipient_email_masked="a***@e.com",
                lock_version=2,
            )
        )
        await s1.flush()
        s1.add(
            OfferSendAttempt(
                id=attempt_id,
                offer_id=offer_id,
                offer_version_id=version_id,
                provider="console",
                status=OFFER_ATTEMPT_STATUS_RUNNING,
                attempt_no=4,
                idempotency_key=f"txn-fail-{attempt_id}",
                started_at=now,
                error_code="stale_send_attempt_recovered",
            )
        )
        await s1.commit()

    async with factory() as late:
        owned = await worker._reassert_sending_ownership_for_terminal(
            late, attempt_id=attempt_id
        )
        assert owned is None
        offer = (
            await late.execute(select(Offer).where(Offer.id == offer_id))
        ).scalar_one()
        assert offer.status == OFFER_STATUS_FAILED

    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_idempotent_double_message_single_execution_real_txn() -> None:
    """Two workers claim same pending attempt; only one transitions to running."""
    from app.workers import mail_tasks as worker

    _assert_safe_url(MEMORY_URL)
    engine = create_async_engine(MEMORY_URL)
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn,
                tables=[Offer.__table__, OfferSendAttempt.__table__],
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    offer_id = uuid4()
    attempt_id = uuid4()
    version_id = uuid4()

    async with factory() as s1:
        s1.add(
            Offer(
                id=offer_id,
                application_id=uuid4(),
                hiring_decision_id=uuid4(),
                status=OFFER_STATUS_SENDING,
                current_version_id=version_id,
                recipient_name="A",
                recipient_email_masked="a***@e.com",
                lock_version=1,
            )
        )
        await s1.flush()
        s1.add(
            OfferSendAttempt(
                id=attempt_id,
                offer_id=offer_id,
                offer_version_id=version_id,
                provider="console",
                status="pending",
                attempt_no=1,
                idempotency_key=f"claim-{attempt_id}",
            )
        )
        await s1.commit()

    async with factory() as w1:
        a1 = await worker._claim_pending_attempt(w1, attempt_id)
        assert a1 is not None
        assert a1.status == OFFER_ATTEMPT_STATUS_RUNNING
        await w1.commit()

    async with factory() as w2:
        a2 = await worker._claim_pending_attempt(w2, attempt_id)
        assert a2 is None
        row = (
            await w2.execute(
                select(OfferSendAttempt).where(OfferSendAttempt.id == attempt_id)
            )
        ).scalar_one()
        assert row.status == OFFER_ATTEMPT_STATUS_RUNNING

    await engine.dispose()
