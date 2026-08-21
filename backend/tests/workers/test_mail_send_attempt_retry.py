"""Mail send attempt worker retry schedule (Task 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.offer import (
    MAIL_MAX_AUTO_ATTEMPTS,
    MAIL_RETRY_COUNTDOWNS_SECONDS,
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_FAILED,
    OFFER_ATTEMPT_STATUS_PENDING,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_ATTEMPT_STATUS_SUCCEEDED,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_SENDING,
    OFFER_STATUS_SENT,
)


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
async def test_retry_countdowns_60_300_1800_then_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MAIL_RETRY_COUNTDOWNS_SECONDS == {1: 60, 2: 300, 3: 1800}
    assert MAIL_MAX_AUTO_ATTEMPTS == 4

    from app.workers import mail_tasks as worker_mod

    enqueues: list[tuple] = []
    offer_id = uuid4()
    version_id = uuid4()
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

    attempts: dict[int, SimpleNamespace] = {}
    created_attempts: list = []

    def _make_attempt(no: int, status: str = OFFER_ATTEMPT_STATUS_PENDING):
        row = SimpleNamespace(
            id=uuid4(),
            offer_id=offer_id,
            offer_version_id=version_id,
            status=status,
            attempt_no=no,
            provider="console",
            error_code=None,
            error_message_safe=None,
            started_at=None,
            finished_at=None,
            next_retry_at=None,
            idempotency_key=f"auto-{no}",
            created_by=None,
            created_at=datetime.now(UTC),
        )
        attempts[no] = row
        return row

    current_no = {"n": 1}
    _make_attempt(1)

    async def get_attempt(_s, attempt_id):
        for row in list(attempts.values()) + created_attempts:
            if row.id == attempt_id:
                return row
        return attempts[current_no["n"]]

    async def get_offer(_s, _oid):
        return offer

    async def get_version(_s, _vid):
        return version

    async def add_attempt(_s, row):
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        created_attempts.append(row)
        attempts[row.attempt_no] = row
        return row

    class _FailProvider:
        def send(self, _ctx):
            from app.services.mail_providers.base import MailSendResult

            return MailSendResult(
                success=False,
                error_code="console_error",
                error_message_safe="console failed",
            )

    monkeypatch.setattr(worker_mod, "get_offer_send_attempt_for_update", get_attempt)
    monkeypatch.setattr(worker_mod, "get_offer_by_id_for_update", get_offer)
    monkeypatch.setattr(worker_mod, "get_offer_version", get_version)
    monkeypatch.setattr(worker_mod, "add_offer_send_attempt", add_attempt)
    monkeypatch.setattr(worker_mod, "record_audit", AsyncMock())
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", _FailProvider)
    monkeypatch.setattr(
        worker_mod,
        "enqueue_mail_send_attempt",
        lambda aid, countdown=0: enqueues.append((aid, countdown)),
    )
    _patch_mail_db(monkeypatch, worker_mod)

    # Fail attempt 1 → enqueue attempt 2 with countdown 60
    current_no["n"] = 1
    await worker_mod._process_mail_send_attempt_async(str(attempts[1].id))
    assert attempts[1].status == OFFER_ATTEMPT_STATUS_FAILED
    assert len(enqueues) == 1
    assert enqueues[0][1] == 60
    assert offer.status == OFFER_STATUS_SENDING

    # Fail attempt 2 → enqueue attempt 3 with countdown 300
    current_no["n"] = 2
    attempts[2] = created_attempts[0]
    await worker_mod._process_mail_send_attempt_async(str(attempts[2].id))
    assert attempts[2].status == OFFER_ATTEMPT_STATUS_FAILED
    assert enqueues[-1][1] == 300
    assert offer.status == OFFER_STATUS_SENDING

    # Fail attempt 3 → enqueue attempt 4 with countdown 1800 (30 min)
    current_no["n"] = 3
    attempts[3] = created_attempts[1]
    await worker_mod._process_mail_send_attempt_async(str(attempts[3].id))
    assert attempts[3].status == OFFER_ATTEMPT_STATUS_FAILED
    assert enqueues[-1][1] == 1800
    assert offer.status == OFFER_STATUS_SENDING
    assert created_attempts[2].attempt_no == 4

    # Fail attempt 4 → dead, Offer failed, no further enqueue
    current_no["n"] = 4
    attempts[4] = created_attempts[2]
    before = len(enqueues)
    await worker_mod._process_mail_send_attempt_async(str(attempts[4].id))
    assert attempts[4].status == OFFER_ATTEMPT_STATUS_DEAD
    assert offer.status == OFFER_STATUS_FAILED
    assert len(enqueues) == before


@pytest.mark.asyncio
async def test_worker_success_sets_offer_sent_without_mutating_terminal_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import mail_tasks as worker_mod
    from app.services.mail_providers.base import MailSendResult

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
        idempotency_key="k1",
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

    class _Ok:
        def send(self, _ctx):
            return MailSendResult(success=True)

    async def get_attempt(_s, _id):
        return attempt

    monkeypatch.setattr(worker_mod, "get_offer_send_attempt_for_update", get_attempt)
    monkeypatch.setattr(
        worker_mod, "get_offer_by_id_for_update", AsyncMock(return_value=offer)
    )
    monkeypatch.setattr(
        worker_mod, "get_offer_version", AsyncMock(return_value=version)
    )
    monkeypatch.setattr(worker_mod, "record_audit", AsyncMock())
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", _Ok)
    monkeypatch.setattr(
        worker_mod, "enqueue_mail_send_attempt", lambda *a, **k: enqueues.append(1)
    )
    _patch_mail_db(monkeypatch, worker_mod)

    await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert attempt.status == OFFER_ATTEMPT_STATUS_SUCCEEDED
    assert offer.status == OFFER_STATUS_SENT
    assert enqueues == []

    # late / duplicate delivery: already succeeded → no-op
    attempt.status = OFFER_ATTEMPT_STATUS_SUCCEEDED
    offer.status = OFFER_STATUS_SENT
    await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert attempt.status == OFFER_ATTEMPT_STATUS_SUCCEEDED
    assert offer.status == OFFER_STATUS_SENT


@pytest.mark.asyncio
async def test_worker_skips_non_pending_running_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import mail_tasks as worker_mod

    offer_id = uuid4()
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=offer_id,
        status=OFFER_ATTEMPT_STATUS_RUNNING,
        attempt_no=1,
        started_at=datetime.now(UTC),
    )
    offer = SimpleNamespace(id=offer_id, status=OFFER_STATUS_SENDING)
    monkeypatch.setattr(
        worker_mod,
        "get_offer_send_attempt_for_update",
        AsyncMock(return_value=attempt),
    )
    monkeypatch.setattr(
        worker_mod,
        "get_offer_by_id_for_update",
        AsyncMock(return_value=offer),
    )
    provider = MagicMock()
    monkeypatch.setattr(worker_mod, "ConsoleMailProvider", lambda: provider)
    _patch_mail_db(monkeypatch, worker_mod)
    result = await worker_mod._process_mail_send_attempt_async(str(attempt.id))
    assert result.get("status") in {"skipped", "running", "noop"}
    provider.send.assert_not_called()
