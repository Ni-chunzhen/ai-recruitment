"""Mail worker terminal ownership: late responses must not overwrite Offer finals."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.offer import (
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_FAILED,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
)
from app.services.mail_providers.base import MailSendResult


class _FakeAsyncSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return None


class _FakeWorkerEngine:
    async def dispose(self) -> None:
        return None


def _patch_mail_db(monkeypatch, worker_mod, session=None):
    if session is None:
        session = MagicMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
    monkeypatch.setattr(
        worker_mod, "create_database_engine", lambda *_a, **_k: _FakeWorkerEngine()
    )
    monkeypatch.setattr(
        worker_mod,
        "create_session_factory",
        lambda *_a, **_k: (lambda: _FakeAsyncSessionCM(session)),
    )
    return session


@pytest.mark.asyncio
async def test_late_failure_does_not_revert_sent_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import mail_tasks as worker_mod

    offer_id = uuid4()
    version_id = uuid4()
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        provider="console",
        error_code=None,
        error_message_safe=None,
        started_at=None,
        finished_at=None,
        next_retry_at=None,
        idempotency_key="late-fail",
        created_by=None,
    )
    offer = SimpleNamespace(
        id=offer_id,
        status=OFFER_STATUS_SENDING,
        current_version_id=version_id,
        recipient_email_masked="a***@example.com",
        application_id=uuid4(),
        updated_at=datetime.now(UTC),
    )
    version = SimpleNamespace(
        id=version_id, version_no=1, content_hash="h", frozen=True
    )
    enqueues: list = []
    created: list = []
    send_calls = {"n": 0}

    class _Fail:
        def send(self, _ctx):
            send_calls["n"] += 1
            # Concurrent winner already finalized Offer before our write.
            offer.status = OFFER_STATUS_SENT
            attempt.status = OFFER_ATTEMPT_STATUS_RUNNING
            return MailSendResult(
                success=False,
                error_code="console_error",
                error_message_safe="late fail",
            )

    async def get_attempt(_s, _id):
        return attempt

    async def get_offer(_s, _oid):
        return offer

    monkeypatch.setattr(worker_mod, "get_offer_send_attempt_for_update", get_attempt)
    monkeypatch.setattr(worker_mod, "get_offer_by_id_for_update", get_offer)
    monkeypatch.setattr(
        worker_mod, "get_offer_version", AsyncMock(return_value=version)
    )
    monkeypatch.setattr(
        worker_mod,
        "add_offer_send_attempt",
        AsyncMock(side_effect=lambda _s, row: created.append(row) or row),
    )
    monkeypatch.setattr(worker_mod, "record_audit", AsyncMock())
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", _Fail)
    monkeypatch.setattr(
        worker_mod,
        "enqueue_mail_send_attempt",
        lambda *a, **k: enqueues.append(1),
    )
    _patch_mail_db(monkeypatch, worker_mod)

    result = await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert result["status"] == "skipped_stale_owner"
    assert offer.status == OFFER_STATUS_SENT
    assert attempt.status == OFFER_ATTEMPT_STATUS_RUNNING
    assert attempt.status != OFFER_ATTEMPT_STATUS_FAILED
    assert attempt.status != OFFER_ATTEMPT_STATUS_DEAD
    assert created == []
    assert enqueues == []


@pytest.mark.asyncio
async def test_late_success_does_not_resurrect_failed_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import mail_tasks as worker_mod

    offer_id = uuid4()
    version_id = uuid4()
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_PENDING,
        attempt_no=1,
        provider="console",
        error_code=None,
        error_message_safe=None,
        started_at=None,
        finished_at=None,
        next_retry_at=None,
        idempotency_key="late-ok",
        created_by=None,
    )
    offer = SimpleNamespace(
        id=offer_id,
        status=OFFER_STATUS_SENDING,
        current_version_id=version_id,
        recipient_email_masked="a***@example.com",
        application_id=uuid4(),
        updated_at=datetime.now(UTC),
    )
    version = SimpleNamespace(
        id=version_id, version_no=1, content_hash="h", frozen=True
    )

    class _Ok:
        def send(self, _ctx):
            offer.status = OFFER_STATUS_FAILED
            attempt.status = OFFER_ATTEMPT_STATUS_RUNNING
            return MailSendResult(success=True)

    monkeypatch.setattr(
        worker_mod, "get_offer_send_attempt_for_update", AsyncMock(side_effect=lambda *_: attempt)
    )
    monkeypatch.setattr(
        worker_mod, "get_offer_by_id_for_update", AsyncMock(side_effect=lambda *_: offer)
    )
    monkeypatch.setattr(
        worker_mod, "get_offer_version", AsyncMock(return_value=version)
    )
    monkeypatch.setattr(worker_mod, "record_audit", AsyncMock())
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", _Ok)
    monkeypatch.setattr(worker_mod, "enqueue_mail_send_attempt", lambda *a, **k: None)
    _patch_mail_db(monkeypatch, worker_mod)

    result = await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert result["status"] == "skipped_stale_owner"
    assert offer.status == OFFER_STATUS_FAILED
    assert attempt.status != OFFER_ATTEMPT_STATUS_SUCCEEDED


@pytest.mark.asyncio
async def test_stale_owner_skips_without_second_console_or_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import mail_tasks as worker_mod

    offer_id = uuid4()
    version_id = uuid4()
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=offer_id,
        offer_version_id=version_id,
        status=OFFER_ATTEMPT_STATUS_RUNNING,
        attempt_no=2,
        provider="console",
        started_at=datetime.now(UTC),
        finished_at=None,
        error_code=None,
        error_message_safe=None,
        next_retry_at=None,
        idempotency_key="dup",
        created_by=None,
    )
    offer = SimpleNamespace(
        id=offer_id,
        status=OFFER_STATUS_SENT,
        current_version_id=version_id,
        recipient_email_masked="a***@example.com",
        application_id=uuid4(),
        updated_at=datetime.now(UTC),
    )
    send_mock = MagicMock()
    created: list = []
    enqueues: list = []

    monkeypatch.setattr(
        worker_mod,
        "get_offer_send_attempt_for_update",
        AsyncMock(return_value=attempt),
    )
    monkeypatch.setattr(
        worker_mod, "get_offer_by_id_for_update", AsyncMock(return_value=offer)
    )
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", lambda: send_mock)
    monkeypatch.setattr(
        worker_mod,
        "add_offer_send_attempt",
        AsyncMock(side_effect=lambda _s, row: created.append(row)),
    )
    monkeypatch.setattr(
        worker_mod, "enqueue_mail_send_attempt", lambda *a, **k: enqueues.append(1)
    )
    _patch_mail_db(monkeypatch, worker_mod)

    result = await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert result["status"] in {"skipped", "skipped_stale_owner"}
    send_mock.send.assert_not_called()
    assert created == []
    assert enqueues == []
    assert offer.status == OFFER_STATUS_SENT
