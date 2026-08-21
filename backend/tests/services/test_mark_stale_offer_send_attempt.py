"""Offer send-attempt mark-stale-failed (independent of AI admin API)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.offer import (
    OFFER_ATTEMPT_STATUS_DEAD,
    OFFER_ATTEMPT_STATUS_RUNNING,
    OFFER_STATUS_FAILED,
    OFFER_STATUS_SENDING,
)
from app.services.audit import RequestContext


@pytest.fixture
def lifespan_patches():
    from app.api.dependencies.auth import get_current_user, get_db_session
    from app.main import app

    with (
        patch("app.main.create_database_engine"),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_client", return_value=AsyncMock()),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.dispose_database", new_callable=AsyncMock),
    ):
        yield
    app.dependency_overrides.clear()


def _actor():
    return SimpleNamespace(id=uuid4(), permission_codes=["recruitment.manage"])


def _ctx() -> RequestContext:
    return RequestContext(request_id="stale-mail", ip_address="127.0.0.1")


def test_mark_stale_schema_only_expected_updated_at() -> None:
    from app.schemas.offer import MarkStaleOfferAttemptIn

    assert set(MarkStaleOfferAttemptIn.model_fields) == {"expected_updated_at"}
    ts = datetime.now(UTC)
    ok = MarkStaleOfferAttemptIn(expected_updated_at=ts)
    assert ok.expected_updated_at == ts
    with pytest.raises(ValidationError):
        MarkStaleOfferAttemptIn.model_validate(
            {"expected_updated_at": ts.isoformat(), "reason": "nope"}
        )


def test_mark_stale_out_fields_minimal_no_body() -> None:
    from app.schemas.offer import MarkStaleOfferAttemptOut

    fields = set(MarkStaleOfferAttemptOut.model_fields)
    assert fields == {
        "offer_id",
        "attempt_id",
        "offer_status",
        "attempt_status",
        "error_code",
        "updated_at",
        "finished_at",
    }
    assert "subject" not in fields
    assert "body_html" not in fields
    assert "recipient_email" not in fields


@pytest.mark.asyncio
async def test_mark_stale_rejects_age_under_5_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    now = datetime.now(UTC)
    started = now - timedelta(minutes=2)
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=uuid4(),
        status=OFFER_ATTEMPT_STATUS_RUNNING,
        started_at=started,
        finished_at=None,
        error_code=None,
        error_message_safe=None,
    )
    offer = SimpleNamespace(
        id=attempt.offer_id,
        status=OFFER_STATUS_SENDING,
        updated_at=started,
        lock_version=2,
    )
    monkeypatch.setattr(
        svc, "get_offer_send_attempt_for_update", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(
        svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer)
    )
    with pytest.raises(svc.OfferStateError, match="not stale enough"):
        await svc.mark_stale_failed_offer_send_attempt(
            AsyncMock(),
            offer_id=offer.id,
            attempt_id=attempt.id,
            expected_updated_at=started,
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_mark_stale_success_marks_dead_and_offer_failed_no_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    now = datetime.now(UTC)
    started = now - timedelta(minutes=10)
    attempt = SimpleNamespace(
        id=uuid4(),
        offer_id=uuid4(),
        status=OFFER_ATTEMPT_STATUS_RUNNING,
        started_at=started,
        finished_at=None,
        error_code=None,
        error_message_safe=None,
    )
    offer = SimpleNamespace(
        id=attempt.offer_id,
        status=OFFER_STATUS_SENDING,
        updated_at=started,
        lock_version=3,
    )
    audits: list = []
    enqueues: list = []

    class _Result:
        def __init__(self, rowcount: int):
            self.rowcount = rowcount

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_Result(1), _Result(1)])
    session.commit = AsyncMock()

    monkeypatch.setattr(
        svc, "get_offer_send_attempt_for_update", AsyncMock(return_value=attempt)
    )
    monkeypatch.setattr(
        svc, "get_offer_by_id_for_update", AsyncMock(return_value=offer)
    )
    monkeypatch.setattr(
        svc,
        "record_audit",
        AsyncMock(side_effect=lambda *a, **k: audits.append(k)),
    )
    monkeypatch.setattr(
        svc, "enqueue_mail_send_attempt", lambda *a, **k: enqueues.append(1)
    )

    out = await svc.mark_stale_failed_offer_send_attempt(
        session,
        offer_id=offer.id,
        attempt_id=attempt.id,
        expected_updated_at=started,
        actor=_actor(),
        request_context=_ctx(),
    )
    assert out.error_code == "stale_send_attempt_recovered"
    assert out.attempt_status == OFFER_ATTEMPT_STATUS_DEAD
    assert out.offer_status == OFFER_STATUS_FAILED
    assert enqueues == []
    assert audits[-1]["action"] == "offer.stale_send_attempt_recovered"
    blob = str(audits[-1]["changes"]).lower()
    assert "subject" not in blob
    assert "body" not in blob


def test_mark_stale_api_manage_only_and_path(lifespan_patches) -> None:
    from fastapi.testclient import TestClient

    from app.api.dependencies.auth import get_current_user, get_db_session
    from app.main import app
    from app.models import Permission, Role, User
    from app.services.offers import MarkStaleOfferAttemptResult

    ENDPOINT = "app.api.v1.endpoints.offers"
    offer_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)

    def _user(*codes: str) -> User:
        user = User(
            id=uuid4(),
            username="stale",
            username_normalized="stale",
            display_name="S",
            password_hash="x",
            is_active=True,
            must_change_password=False,
            token_version=1,
        )
        role = Role(name="r", description="r")
        role.permissions = [Permission(code=c, description=c) for c in codes]
        user.roles = [role]
        return user

    def _client_for(user: User) -> TestClient:
        async def override_user() -> User:
            return user

        async def override_db():
            yield AsyncMock()

        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_db_session] = override_db
        return TestClient(app)

    out = MarkStaleOfferAttemptResult(
        offer_id=offer_id,
        attempt_id=attempt_id,
        offer_status=OFFER_STATUS_FAILED,
        attempt_status=OFFER_ATTEMPT_STATUS_DEAD,
        error_code="stale_send_attempt_recovered",
        updated_at=now,
        finished_at=now,
    )

    with _client_for(_user("interview.execute")) as client:
        denied = client.post(
            f"/api/v1/offers/{offer_id}/attempts/{attempt_id}/mark-stale-failed",
            json={"expected_updated_at": now.isoformat()},
        )
    assert denied.status_code == 403

    with (
        _client_for(_user("recruitment.manage")) as client,
        patch(
            f"{ENDPOINT}.mark_stale_failed_offer_send_attempt",
            new=AsyncMock(return_value=out),
        ),
    ):
        ok = client.post(
            f"/api/v1/offers/{offer_id}/attempts/{attempt_id}/mark-stale-failed",
            json={"expected_updated_at": now.isoformat()},
        )
    assert ok.status_code == 200
    body = ok.json()
    assert body["error_code"] == "stale_send_attempt_recovered"
    assert "subject" not in body
    assert "body_html" not in body

    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "v1"
        / "endpoints"
        / "offers.py"
    ).read_text(encoding="utf-8")
    assert "mark-stale-failed" in text
    assert "mark_stale_failed_offer_send_attempt" in text
