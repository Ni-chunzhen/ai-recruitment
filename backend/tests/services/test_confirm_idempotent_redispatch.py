"""confirm/retry idempotent pending re-dispatch (R3)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.offer import (
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_FAILED,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUS_SENDING,
)
from app.services.audit import RequestContext


def _actor():
    return SimpleNamespace(
        id=uuid4(), username="hr", permission_codes=["recruitment.manage"]
    )


def _ctx() -> RequestContext:
    return RequestContext(request_id="idem-dispatch", ip_address="127.0.0.1")


@pytest.mark.asyncio
async def test_confirm_idempotent_pending_requeues_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    attempt_id = uuid4()
    attempt = SimpleNamespace(
        id=attempt_id,
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        provider="console",
        idempotency_key="send-pending",
    )
    offer = SimpleNamespace(
        id=offer_id,
        status=OFFER_STATUS_SENDING,
        lock_version=3,
        current_version_id=version_id,
    )
    enqueues: list = []
    monkeypatch.setattr(
        svc, "find_attempt_by_idempotency", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=offer))
    monkeypatch.setattr(
        svc,
        "enqueue_mail_send_attempt",
        lambda aid, countdown=0: enqueues.append((aid, countdown)),
    )
    monkeypatch.setattr(svc, "add_offer_send_attempt", AsyncMock())

    r1 = await svc.confirm_offer_send(
        AsyncMock(),
        offer_id=offer_id,
        offer_version_id=version_id,
        lock_version=3,
        idempotency_key="send-pending",
        actor=_actor(),
        request_context=_ctx(),
    )
    r2 = await svc.confirm_offer_send(
        AsyncMock(),
        offer_id=offer_id,
        offer_version_id=version_id,
        lock_version=3,
        idempotency_key="send-pending",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert r1.attempt_id == r2.attempt_id == attempt_id
    assert len(enqueues) == 2
    assert all(e[0] == attempt_id and e[1] == 0 for e in enqueues)
    svc.add_offer_send_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_idempotent_non_pending_never_reenqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    for status in (
        OFFER_ATTEMPT_STATUS_RUNNING,
        OFFER_ATTEMPT_STATUS_SUCCEEDED,
        OFFER_ATTEMPT_STATUS_FAILED,
        OFFER_ATTEMPT_STATUS_DEAD,
    ):
        enqueues: list = []
        attempt = SimpleNamespace(
            id=uuid4(),
            offer_id=offer_id,
            offer_version_id=version_id,
            status=status,
            attempt_no=1,
            provider="console",
            idempotency_key=f"k-{status}",
        )
        offer = SimpleNamespace(
            id=offer_id, status=OFFER_STATUS_SENDING, lock_version=1
        )
        monkeypatch.setattr(
            svc, "find_attempt_by_idempotency", AsyncMock(return_value=attempt)
        )
        monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=offer))
        monkeypatch.setattr(
            svc,
            "enqueue_mail_send_attempt",
            lambda *a, **k: enqueues.append(1),
        )
        await svc.confirm_offer_send(
            AsyncMock(),
            offer_id=offer_id,
            offer_version_id=version_id,
            lock_version=1,
            idempotency_key=f"k-{status}",
            actor=_actor(),
            request_context=_ctx(),
        )
        assert enqueues == [], status


@pytest.mark.asyncio
async def test_retry_idempotent_pending_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    offer_id = uuid4()
    version_id = uuid4()
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        provider="console",
        idempotency_key="retry-pending",
    )
    offer = SimpleNamespace(
        id=offer_id, status=OFFER_STATUS_SENDING, lock_version=5
    )
    enqueues: list = []
    monkeypatch.setattr(
        svc, "find_attempt_by_idempotency", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(svc, "get_offer_by_id", AsyncMock(return_value=offer))
    monkeypatch.setattr(
        svc,
        "enqueue_mail_send_attempt",
        lambda aid, countdown=0: enqueues.append(aid),
    )
    result = await svc.retry_offer_send(
        AsyncMock(),
        offer_id=offer_id,
        lock_version=5,
        idempotency_key="retry-pending",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.attempt_id == attempt.id
    assert enqueues == [attempt.id]


@pytest.mark.asyncio
async def test_confirm_keeps_pending_when_first_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit 成功后 enqueue 抛错：attempt 保持 pending，同 key 可补投。"""
    from app.models.offer import OFFER_STATUS_READY
    from app.services import offers as svc

    application = SimpleNamespace(
        id=uuid4(),
        status="in_progress",
        pipeline_status="pending_offer",
        candidate_id=uuid4(),
    )
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
        updated_by=None,
        updated_at=datetime.now(UTC),
    )
    version = SimpleNamespace(
        id=version_id,
        offer_id=offer_id,
        version_no=1,
        frozen=False,
        content_hash="h",
    )
    stored: list = []

    monkeypatch.setattr(svc, "find_attempt_by_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(svc, "get_offer_version", AsyncMock(return_value=version))
    monkeypatch.setattr(svc, "_assert_application_offer_gate", lambda *_: None)
    monkeypatch.setattr(
        svc,
        "add_offer_send_attempt",
        AsyncMock(side_effect=lambda _s, row: stored.append(row) or row),
    )
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    session = AsyncMock()
    session.commit = AsyncMock()

    def _boom(*_a, **_k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(svc, "enqueue_mail_send_attempt", _boom)

    with pytest.raises(RuntimeError, match="broker down"):
        await svc.confirm_offer_send(
            session,
            offer_id=offer_id,
            offer_version_id=version_id,
            lock_version=1,
            idempotency_key="dispatch-fail",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert session.commit.await_count == 1
    assert len(stored) == 1
    assert stored[0].status == OFFER_ATTEMPT_STATUS_PENDING
