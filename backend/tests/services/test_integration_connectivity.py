"""Integration connectivity probes with injectable mocks (Task 3 RED)."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.audit import RequestContext

MODULE = "app.services.integrations"


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-conn-1", ip_address="127.0.0.1")


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        DIFY_API_BASE_URL="",
        dify_api_key="",
        dify_jd_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_score_dimension_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_parse_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_resume_score_api_key_secret=SimpleNamespace(get_secret_value=lambda: ""),
        dify_interview_question_generate_api_key_secret=SimpleNamespace(
            get_secret_value=lambda: ""
        ),
        DIFY_JD_PARSE_WORKFLOW_ID="",
        DIFY_SCORE_DIMENSION_WORKFLOW_ID="",
        DIFY_RESUME_PARSE_WORKFLOW_ID="",
        DIFY_RESUME_SCORE_WORKFLOW_ID="",
        dify_interview_question_generate_workflow_id="",
        AI_PROVIDER="mock",
        dify_interview_question_live_enabled=False,
        MINIO_ENDPOINT="",
        MINIO_ACCESS_KEY="",
        minio_access_key="",
        minio_secret_key="",
        MINIO_BUCKET="",
        MINIO_SECURE=False,
        MINIO_PRESIGN_SECONDS=600,
        celery_mail_queue_name="mail_outbound",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_dify_test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import integrations as svc
    from app.services.integration_config import IntegrationOverlay

    monkeypatch.setattr(
        svc, "load_integration_overlay", AsyncMock(return_value=IntegrationOverlay.empty())
    )
    audits: list = []

    async def capture(session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(svc, "record_audit", capture)
    result = await svc.test_dify(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(),
    )
    assert result.ok is False
    assert result.error_code == "not_configured"
    assert isinstance(result.latency_ms, int)
    data = asdict(result)
    assert set(data.keys()) == {"ok", "error_code", "latency_ms"}
    assert audits[0]["action"] == "integration.connectivity_tested"


@pytest.mark.asyncio
async def test_dify_test_success_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import integrations as svc
    from app.services.integration_config import IntegrationOverlay

    monkeypatch.setattr(
        svc, "load_integration_overlay", AsyncMock(return_value=IntegrationOverlay.empty())
    )
    monkeypatch.setattr(svc, "record_audit", AsyncMock())

    class FakeResponse:
        status_code = 200
        text = "SHOULD-NOT-LEAK-BODY"
        headers = {"Authorization": "Bearer leak"}

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    client = FakeClient()
    result = await svc.test_dify(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(
            DIFY_API_BASE_URL="https://dify.example/v1",
            dify_api_key="super-secret-key",
        ),
        http_client=client,
    )
    assert result.ok is True
    assert result.error_code is None
    assert set(asdict(result).keys()) == {"ok", "error_code", "latency_ms"}
    assert len(client.calls) == 1
    # body must never be returned
    assert not hasattr(result, "body")
    rendered = json.dumps(asdict(result))
    assert "SHOULD-NOT-LEAK" not in rendered
    assert "super-secret-key" not in rendered
    assert "Authorization" not in rendered


@pytest.mark.asyncio
async def test_dify_test_timeout_stable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import integrations as svc
    from app.services.integration_config import IntegrationOverlay

    monkeypatch.setattr(
        svc, "load_integration_overlay", AsyncMock(return_value=IntegrationOverlay.empty())
    )
    monkeypatch.setattr(svc, "record_audit", AsyncMock())

    class TimeoutClient:
        async def request(self, *args, **kwargs):
            raise TimeoutError("raw timeout with secret=abc")

    result = await svc.test_dify(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(
            DIFY_API_BASE_URL="https://dify.example/v1",
            dify_api_key="k",
        ),
        http_client=TimeoutClient(),
    )
    assert result.ok is False
    assert result.error_code == "timeout"
    assert "secret=abc" not in str(result)


@pytest.mark.asyncio
async def test_minio_test_uses_bucket_exists_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import integrations as svc
    from app.services.integration_config import IntegrationOverlay

    monkeypatch.setattr(
        svc, "load_integration_overlay", AsyncMock(return_value=IntegrationOverlay.empty())
    )
    monkeypatch.setattr(svc, "record_audit", AsyncMock())

    client = MagicMock()
    client.bucket_exists.return_value = True
    client.put_object = MagicMock(side_effect=AssertionError("put forbidden"))
    client.get_object = MagicMock(side_effect=AssertionError("get forbidden"))
    client.presigned_get_object = MagicMock(side_effect=AssertionError("presign forbidden"))

    result = await svc.test_minio(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(
            MINIO_ENDPOINT="127.0.0.1:9000",
            minio_access_key="ak",
            minio_secret_key="sk",
            MINIO_BUCKET="resumes",
        ),
        minio_client=client,
    )
    assert result.ok is True
    assert result.error_code is None
    client.bucket_exists.assert_called_once_with("resumes")
    client.put_object.assert_not_called()
    client.get_object.assert_not_called()
    client.presigned_get_object.assert_not_called()


@pytest.mark.asyncio
async def test_mail_test_console_noop_no_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import integrations as svc

    audits: list = []

    async def capture(session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(svc, "record_audit", capture)
    result = await svc.test_mail(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(),
    )
    assert result.ok is True
    assert result.error_code is None
    assert set(asdict(result).keys()) == {"ok", "error_code", "latency_ms"}
    changes = audits[0]["changes"]
    assert changes["provider"] == "mail"
    assert "smtp" not in json.dumps(changes).lower()


@pytest.mark.asyncio
async def test_connectivity_never_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import integrations as svc
    from app.services.integration_config import IntegrationOverlay

    monkeypatch.setattr(
        svc, "load_integration_overlay", AsyncMock(return_value=IntegrationOverlay.empty())
    )
    monkeypatch.setattr(svc, "record_audit", AsyncMock())

    def boom(*args, **kwargs):
        raise AssertionError("apply_async must not be called")

    monkeypatch.setattr("celery.app.task.Task.apply_async", boom, raising=False)
    # also guard common enqueue symbols if imported later
    for path in (
        "app.workers.ai_tasks.execute_ai_task.apply_async",
        "app.workers.mail_tasks.send_offer_mail.apply_async",
    ):
        try:
            monkeypatch.setattr(path, boom)
        except Exception:
            pass

    class FakeClient:
        async def request(self, *a, **k):
            return SimpleNamespace(status_code=200, text="", headers={})

    await svc.test_dify(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(
            DIFY_API_BASE_URL="https://dify.example/v1",
            dify_api_key="k",
        ),
        http_client=FakeClient(),
    )
    client = MagicMock()
    client.bucket_exists.return_value = True
    await svc.test_minio(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(
            MINIO_ENDPOINT="127.0.0.1:9000",
            minio_access_key="a",
            minio_secret_key="s",
            MINIO_BUCKET="b",
        ),
        minio_client=client,
    )
    await svc.test_mail(
        AsyncMock(),
        actor_user_id=uuid4(),
        request_context=_ctx(),
        settings=_settings(),
    )
