"""API tests for Offer console delivery endpoints (Task 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, get_db_session
from app.main import app
from app.models import Permission, Role, User
from app.services.offers import (
    OfferDetail,
    OfferResult,
    OfferSendResult,
    OfferSummary,
)

ENDPOINT = "app.api.v1.endpoints.offers"
OFFERS_SRC = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "api"
    / "v1"
    / "endpoints"
    / "offers.py"
)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
APP_ID = uuid4()
OFFER_ID = uuid4()
VERSION_ID = uuid4()
ATTEMPT_ID = uuid4()
ACTOR_ID = uuid4()
DECISION_ID = uuid4()


def _user(*permission_codes: str) -> User:
    user = User(
        id=ACTOR_ID,
        username="offer-api",
        username_normalized="offer-api",
        display_name="Offer API",
        password_hash="x",
        is_active=True,
        must_change_password=False,
        token_version=1,
    )
    role = Role(name="role", description="role")
    role.permissions = [
        Permission(code=code, description=code) for code in permission_codes
    ]
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


@pytest.fixture
def lifespan_patches():
    with (
        patch("app.main.create_database_engine"),
        patch("app.main.create_session_factory"),
        patch("app.main.create_redis_client", return_value=AsyncMock()),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.dispose_database", new_callable=AsyncMock),
    ):
        yield
    app.dependency_overrides.clear()


def _result(**overrides) -> OfferResult:
    payload = dict(
        id=OFFER_ID,
        application_id=APP_ID,
        status="draft",
        hiring_decision_id=DECISION_ID,
        recipient_email_masked="a***@example.com",
        recipient_name="张三",
        lock_version=1,
        version_id=VERSION_ID,
        version_no=1,
        content_hash="hash1",
        frozen=False,
        created_at=NOW,
        updated_at=NOW,
    )
    payload.update(overrides)
    return OfferResult(**payload)


def _detail(**overrides) -> OfferDetail:
    payload = dict(
        id=OFFER_ID,
        application_id=APP_ID,
        status="ready",
        hiring_decision_id=DECISION_ID,
        recipient_email_masked="a***@example.com",
        recipient_name="张三",
        lock_version=2,
        version_id=VERSION_ID,
        version_no=1,
        content_hash="hash1",
        frozen=True,
        subject="录用通知",
        body_html="<p>您好</p>",
        body_text="您好",
        template_code="offer_console_v1",
        template_version="1",
        created_at=NOW,
        updated_at=NOW,
    )
    payload.update(overrides)
    return OfferDetail(**payload)


def _summary(**overrides) -> OfferSummary:
    payload = dict(
        id=OFFER_ID,
        application_id=APP_ID,
        status="draft",
        hiring_decision_id=DECISION_ID,
        recipient_email_masked="a***@example.com",
        recipient_name="张三",
        lock_version=1,
        version_no=1,
        content_hash="hash1",
        frozen=False,
        created_at=NOW,
        updated_at=NOW,
    )
    payload.update(overrides)
    return OfferSummary(**payload)


def _send_result(**overrides) -> OfferSendResult:
    payload = dict(
        offer_id=OFFER_ID,
        attempt_id=ATTEMPT_ID,
        status="sending",
        attempt_status="pending",
        attempt_no=1,
        lock_version=3,
        version_id=VERSION_ID,
        provider="console",
    )
    payload.update(overrides)
    return OfferSendResult(**payload)


def test_anonymous_401(lifespan_patches) -> None:
    from fastapi import HTTPException

    async def deny_anonymous() -> User:
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
        )

    app.dependency_overrides[get_current_user] = deny_anonymous

    async def override_db():
        yield AsyncMock()

    app.dependency_overrides[get_db_session] = override_db
    client = TestClient(app)
    r = client.get(f"/api/v1/applications/{APP_ID}/offers")
    assert r.status_code == 401


def test_execute_all_offer_routes_403(lifespan_patches) -> None:
    client = _client_for(_user("interview.execute"))
    routes = [
        ("post", f"/api/v1/applications/{APP_ID}/offers", {"idempotency_key": "c1"}),
        ("get", f"/api/v1/applications/{APP_ID}/offers", None),
        ("get", f"/api/v1/offers/{OFFER_ID}", None),
        (
            "patch",
            f"/api/v1/offers/{OFFER_ID}",
            {
                "subject": "s",
                "body_html": "h",
                "body_text": "t",
                "lock_version": 1,
                "idempotency_key": "u1",
            },
        ),
        (
            "post",
            f"/api/v1/offers/{OFFER_ID}/ready",
            {"lock_version": 1, "idempotency_key": "r1"},
        ),
        (
            "post",
            f"/api/v1/offers/{OFFER_ID}/send",
            {
                "offer_version_id": str(VERSION_ID),
                "lock_version": 2,
                "idempotency_key": "s1",
            },
        ),
        (
            "post",
            f"/api/v1/offers/{OFFER_ID}/retry",
            {"lock_version": 4, "idempotency_key": "t1"},
        ),
        (
            "post",
            f"/api/v1/offers/{OFFER_ID}/void",
            {"lock_version": 1, "void_reason_code": "withdrawn", "idempotency_key": "v1"},
        ),
        ("get", f"/api/v1/offers/{OFFER_ID}/attempts", None),
    ]
    for method, path, body in routes:
        fn = getattr(client, method)
        resp = fn(path, json=body) if body is not None else fn(path)
        assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"


def test_manage_can_crud_and_send(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    create = AsyncMock(return_value=_result())
    update = AsyncMock(return_value=_result(status="draft", lock_version=2))
    ready = AsyncMock(return_value=_result(status="ready", lock_version=3, frozen=True))
    detail = AsyncMock(return_value=_detail())
    send = AsyncMock(return_value=_send_result())

    with (
        patch(f"{ENDPOINT}.create_offer", new=create),
        patch(f"{ENDPOINT}.update_offer_draft", new=update),
        patch(f"{ENDPOINT}.mark_offer_ready", new=ready),
        patch(f"{ENDPOINT}.get_offer_detail", new=detail),
        patch(f"{ENDPOINT}.confirm_offer_send", new=send),
    ):
        r_create = client.post(
            f"/api/v1/applications/{APP_ID}/offers",
            json={"idempotency_key": "create-1"},
        )
        assert r_create.status_code == 201
        assert r_create.json()["id"] == str(OFFER_ID)

        r_patch = client.patch(
            f"/api/v1/offers/{OFFER_ID}",
            json={
                "subject": "录用通知",
                "body_html": "<p>您好</p>",
                "body_text": "您好",
                "lock_version": 1,
                "idempotency_key": "upd-1",
            },
        )
        assert r_patch.status_code == 200

        r_ready = client.post(
            f"/api/v1/offers/{OFFER_ID}/ready",
            json={"lock_version": 2, "idempotency_key": "ready-1"},
        )
        assert r_ready.status_code == 200
        assert r_ready.json()["status"] == "ready"

        r_get = client.get(f"/api/v1/offers/{OFFER_ID}")
        assert r_get.status_code == 200
        assert r_get.headers.get("cache-control") == "no-store"
        assert r_get.json()["subject"] == "录用通知"

        r_send = client.post(
            f"/api/v1/offers/{OFFER_ID}/send",
            json={
                "offer_version_id": str(VERSION_ID),
                "lock_version": 3,
                "idempotency_key": "send-1",
            },
        )
        assert r_send.status_code == 202
        assert r_send.json()["attempt_id"] == str(ATTEMPT_ID)
        assert r_send.json()["status"] == "sending"
        send.assert_awaited_once()


def test_list_and_attempts_have_no_body_or_plaintext_email(lifespan_patches) -> None:
    from app.services.offers import OfferAttemptSummary

    client = _client_for(_user("recruitment.manage"))
    items = [_summary()]
    attempts = [
        OfferAttemptSummary(
            id=ATTEMPT_ID,
            offer_id=OFFER_ID,
            offer_version_id=VERSION_ID,
            provider="console",
            status="failed",
            attempt_no=1,
            error_code="console_error",
            error_message_safe="console failed",
            started_at=NOW,
            finished_at=NOW,
            next_retry_at=None,
            created_at=NOW,
        )
    ]
    with (
        patch(
            f"{ENDPOINT}.list_offers_for_application",
            new=AsyncMock(return_value=items),
        ),
        patch(
            f"{ENDPOINT}.list_offer_attempts",
            new=AsyncMock(return_value=attempts),
        ),
    ):
        r_list = client.get(f"/api/v1/applications/{APP_ID}/offers")
        assert r_list.status_code == 200
        blob = r_list.text.lower()
        assert "subject" not in blob or '"subject"' not in r_list.text
        assert "body_html" not in blob
        assert "body_text" not in blob
        assert "alice@" not in blob
        assert "recipient_email_masked" in blob
        assert "a***@example.com" in r_list.text

        r_att = client.get(f"/api/v1/offers/{OFFER_ID}/attempts")
        assert r_att.status_code == 200
        att_blob = r_att.text.lower()
        assert "body_html" not in att_blob
        assert "body_text" not in att_blob
        assert "subject" not in att_blob
        assert "alice@" not in att_blob
        assert "error_message_safe" in att_blob


def test_detail_returns_body_only_on_get_offer(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.get_offer_detail", new=AsyncMock(return_value=_detail())
    ):
        r = client.get(f"/api/v1/offers/{OFFER_ID}")
    assert r.status_code == 200
    data = r.json()
    assert data["subject"] == "录用通知"
    assert data["body_html"] == "<p>您好</p>"
    assert data["body_text"] == "您好"
    assert data["recipient_email_masked"] == "a***@example.com"
    assert "recipient_email" not in data
    assert r.headers.get("cache-control") == "no-store"


def test_send_requires_idempotency_and_lock(lifespan_patches) -> None:
    from app.services.offers import OfferConflictError

    client = _client_for(_user("recruitment.manage"))
    missing = client.post(
        f"/api/v1/offers/{OFFER_ID}/send",
        json={"offer_version_id": str(VERSION_ID), "lock_version": 1},
    )
    assert missing.status_code == 422

    with patch(
        f"{ENDPOINT}.confirm_offer_send",
        new=AsyncMock(side_effect=OfferConflictError("lock mismatch")),
    ):
        bad_lock = client.post(
            f"/api/v1/offers/{OFFER_ID}/send",
            json={
                "offer_version_id": str(VERSION_ID),
                "lock_version": 99,
                "idempotency_key": "send-x",
            },
        )
    assert bad_lock.status_code == 409


def test_send_idempotent_http(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    send = AsyncMock(return_value=_send_result())
    with patch(f"{ENDPOINT}.confirm_offer_send", new=send):
        body = {
            "offer_version_id": str(VERSION_ID),
            "lock_version": 3,
            "idempotency_key": "send-same",
        }
        r1 = client.post(f"/api/v1/offers/{OFFER_ID}/send", json=body)
        r2 = client.post(f"/api/v1/offers/{OFFER_ID}/send", json=body)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["attempt_id"] == r2.json()["attempt_id"] == str(ATTEMPT_ID)
    assert send.await_count == 2


def test_response_never_contains_hired_transition(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    with patch(
        f"{ENDPOINT}.confirm_offer_send",
        new=AsyncMock(return_value=_send_result(status="sending")),
    ):
        r = client.post(
            f"/api/v1/offers/{OFFER_ID}/send",
            json={
                "offer_version_id": str(VERSION_ID),
                "lock_version": 3,
                "idempotency_key": "send-hired-check",
            },
        )
    assert r.status_code == 202
    text = r.text.lower()
    assert "hired" not in text
    assert '"status":"sending"' in r.text.replace(" ", "")


def test_send_enqueue_intent_monkeypatched_not_redis(
    lifespan_patches,
) -> None:
    """Confirm path: monkeypatch Celery apply_async; never touch Redis/broker."""
    from app.services import offers as offers_svc

    client = _client_for(_user("recruitment.manage"))
    published: list[dict] = []

    class _Task:
        name = "app.workers.mail_tasks.process_mail_send_attempt"

        @staticmethod
        def apply_async(*, args, countdown=0):
            published.append({"args": args, "countdown": countdown, "name": _Task.name})

    async def fake_confirm(session, **kwargs):
        attempt_id = ATTEMPT_ID
        offers_svc.enqueue_mail_send_attempt(attempt_id, countdown=0)
        return _send_result()

    with (
        patch(f"{ENDPOINT}.confirm_offer_send", new=fake_confirm),
        patch.object(offers_svc, "process_mail_send_attempt", _Task),
    ):
        r = client.post(
            f"/api/v1/offers/{OFFER_ID}/send",
            json={
                "offer_version_id": str(VERSION_ID),
                "lock_version": 3,
                "idempotency_key": "send-intent",
            },
        )
    assert r.status_code == 202
    assert len(published) == 1
    assert published[0]["args"] == [str(ATTEMPT_ID)]
    assert published[0]["name"] == "app.workers.mail_tasks.process_mail_send_attempt"


def test_retry_failed_offer(lifespan_patches) -> None:
    client = _client_for(_user("recruitment.manage"))
    retry = AsyncMock(
        return_value=_send_result(attempt_id=uuid4(), attempt_no=1, status="sending")
    )
    with patch(f"{ENDPOINT}.retry_offer_send", new=retry):
        r = client.post(
            f"/api/v1/offers/{OFFER_ID}/retry",
            json={"lock_version": 5, "idempotency_key": "retry-1"},
        )
    assert r.status_code == 202
    assert r.json()["status"] == "sending"
    retry.assert_awaited_once()


def test_api_module_source_forbids_smtp_dify(lifespan_patches) -> None:
    assert OFFERS_SRC.is_file()
    text = OFFERS_SRC.read_text(encoding="utf-8").lower()
    assert "smtp" not in text
    assert "dify" not in text
    assert "process_ai_task" not in text
    assert "enqueue_mail_send_attempt" not in text
    assert "apply_async" not in text
    assert "redis" not in text
