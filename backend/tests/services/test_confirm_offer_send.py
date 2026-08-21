"""confirm_offer_send / retry_offer_send service tests (Task 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.offer import (
    MAIL_PROVIDER_CONSOLE,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_READY,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
    OfferSendAttempt,
)
from app.models.resume import PIPELINE_PENDING_OFFER
from app.services.audit import RequestContext

ALLOWED_AUDIT_KEYS = frozenset(
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
    }
)


def _actor():
    return SimpleNamespace(id=uuid4(), username="hr", permission_codes=["recruitment.manage"])


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-send-1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _application():
    return SimpleNamespace(
        id=uuid4(),
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_PENDING_OFFER,
        lock_version=9,
    )


@pytest.mark.asyncio
async def test_confirm_send_freezes_version_and_enqueues_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_READY,
        lock_version=2,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        updated_by=None,
        updated_at=_now(),
        created_at=_now(),
    )
    version = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=2,
        frozen=False,
        content_hash="hash-ready",
    )
    added: list = []
    enqueues: list = []
    audits: list = []

    async def fake_add_attempt(_s, row):
        row.id = uuid4()
        added.append(row)
        return row

    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=_application())
    )
    monkeypatch.setattr(svc, "add_offer_send_attempt", fake_add_attempt)
    monkeypatch.setattr(svc, "find_attempt_by_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(
        svc, "record_audit", AsyncMock(side_effect=lambda *a, **k: audits.append(k))
    )
    monkeypatch.setattr(
        svc,
        "enqueue_mail_send_attempt",
        lambda attempt_id, countdown=0: enqueues.append((attempt_id, countdown)),
    )

    session = AsyncMock()
    session.commit = AsyncMock()
    result = await svc.confirm_offer_send(
        session,
        offer_id=offer_id,
        offer_version_id=version_id,
        lock_version=2,
        idempotency_key="send-1",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert version.frozen is True
    assert offer.status == OFFER_STATUS_SENDING
    assert len(added) == 1
    attempt = added[0]
    assert isinstance(attempt, OfferSendAttempt)
    assert attempt.provider == MAIL_PROVIDER_CONSOLE
    assert attempt.status == OFFER_ATTEMPT_STATUS_PENDING
    assert attempt.attempt_no == 1
    assert len(enqueues) == 1
    assert enqueues[0][0] == attempt.id
    assert enqueues[0][1] == 0
    assert result.attempt_id == attempt.id
    assert set(audits[-1]["changes"].keys()) <= ALLOWED_AUDIT_KEYS
    assert "zhang@" not in str(audits)


@pytest.mark.asyncio
async def test_confirm_send_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    attempt_id = uuid4()
    existing_attempt = SimpleNamespace(
        id=attempt_id,
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        provider=MAIL_PROVIDER_CONSOLE,
        idempotency_key="send-1",
    )
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_SENDING,
        lock_version=3,
        current_version_id=version_id,
        recipient_email_masked="z***@example.com",
        recipient_name="张三",
        hiring_decision_id=uuid4(),
        created_at=_now(),
        updated_at=_now(),
    )
    version = SimpleNamespace(
        id=version_id, offer_id=offer_id, version_no=1, frozen=True, content_hash="h"
    )
    enqueues: list = []
    monkeypatch.setattr(
        svc,
        "find_attempt_by_idempotency",
        AsyncMock(return_value=existing_attempt),
    )
    monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(
        svc,
        "enqueue_mail_send_attempt",
        lambda *a, **k: enqueues.append(1),
    )
    monkeypatch.setattr(svc, "add_offer_send_attempt", AsyncMock())
    result = await svc.confirm_offer_send(
        AsyncMock(),
        offer_id=offer_id,
        offer_version_id=version_id,
        lock_version=3,
        idempotency_key="send-1",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.attempt_id == attempt_id
    assert enqueues == [1]
    svc.add_offer_send_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_keeps_pending_offer_never_hired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    application = _application()
    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=application.id,
        status=OFFER_STATUS_READY,
        lock_version=1,
        current_version_id=version_id,
        recipient_email_masked="a***@example.com",
        recipient_name="A",
        hiring_decision_id=uuid4(),
        created_at=_now(),
        updated_at=_now(),
    )
    version = SimpleNamespace(
        id=version_id, offer_id=offer_id, version_no=1, frozen=False, content_hash="h"
    )

    async def fake_add(_s, row):
        row.id = uuid4()
        return row

    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(svc, "add_offer_send_attempt", fake_add)
    monkeypatch.setattr(svc, "find_attempt_by_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "enqueue_mail_send_attempt", lambda *a, **k: None)
    session = AsyncMock()
    session.commit = AsyncMock()
    await svc.confirm_offer_send(
        session,
        offer_id=offer_id,
        offer_version_id=version_id,
        lock_version=1,
        idempotency_key="send-pipe",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert application.pipeline_status == PIPELINE_PENDING_OFFER
    assert application.status == APPLICATION_STATUS_IN_PROGRESS
    assert application.status != "hired"
    assert application.lock_version == 9


@pytest.mark.asyncio
async def test_retry_offer_send_only_from_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer = SimpleNamespace(
        id=uuid4(),
        application_id=uuid4(),
        status=OFFER_STATUS_SENT,
        lock_version=1,
        current_version_id=uuid4(),
        recipient_email_masked="a***@example.com",
        recipient_name="A",
        hiring_decision_id=uuid4(),
    )
    monkeypatch.setattr(svc, "find_attempt_by_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    with pytest.raises(svc.OfferStateError):
        await svc.retry_offer_send(
            AsyncMock(),
            offer_id=offer.id,
            lock_version=1,
            idempotency_key="retry-bad",
            actor=_actor(),
            request_context=_ctx(),
        )

    # failed → new attempt cycle
    offer_id = uuid4()
    version_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        application_id=uuid4(),
        status=OFFER_STATUS_FAILED,
        lock_version=4,
        current_version_id=version_id,
        recipient_email_masked="a***@example.com",
        recipient_name="A",
        hiring_decision_id=uuid4(),
        created_at=_now(),
        updated_at=_now(),
    )
    version = SimpleNamespace(
        id=version_id, offer_id=offer_id, version_no=1, frozen=True, content_hash="h"
    )
    added: list = []
    enqueues: list = []

    async def fake_add(_s, row):
        row.id = uuid4()
        added.append(row)
        return row

    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=_application())
    )
    monkeypatch.setattr(svc, "add_offer_send_attempt", fake_add)
    monkeypatch.setattr(svc, "find_attempt_by_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(
        svc,
        "enqueue_mail_send_attempt",
        lambda aid, countdown=0: enqueues.append((aid, countdown)),
    )
    session = AsyncMock()
    session.commit = AsyncMock()
    result = await svc.retry_offer_send(
        session,
        offer_id=offer_id,
        lock_version=4,
        idempotency_key="retry-1",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert offer.status == OFFER_STATUS_SENDING
    assert len(added) == 1
    assert added[0].attempt_no == 1
    assert added[0].offer_version_id == version_id
    assert version.frozen is True
    assert enqueues and enqueues[0][0] == added[0].id
    assert result.attempt_id == added[0].id
