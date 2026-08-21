"""Offer draft service must not advance hired / pipeline (Task 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.offer import OFFER_STATUS_DRAFT
from app.models.resume import HIRING_RECOMMEND_HIRE, PIPELINE_PENDING_OFFER
from app.services.audit import RequestContext


def _actor():
    return SimpleNamespace(
        id=uuid4(), username="hr", permission_codes=["recruitment.manage"]
    )


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-offer-hired", ip_address="127.0.0.1")


@pytest.mark.asyncio
async def test_create_does_not_write_hired_or_change_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import offers as svc

    candidate = SimpleNamespace(
        id=uuid4(), name="李四", email="li@example.com", phone=None
    )
    application = SimpleNamespace(
        id=uuid4(),
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_PENDING_OFFER,
        lock_version=2,
        candidate=candidate,
        candidate_id=candidate.id,
        close_action=None,
        close_reason=None,
        updated_at=datetime.now(UTC),
    )
    decision = SimpleNamespace(
        id=uuid4(),
        application_id=application.id,
        decision=HIRING_RECOMMEND_HIRE,
        created_at=datetime.now(UTC),
    )
    added: list = []

    async def fake_add_offer(_s, row):
        row.id = uuid4()
        added.append(row)
        return row

    async def fake_add_version(_s, row):
        row.id = uuid4()
        added.append(row)
        return row

    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "list_recommend_hire_decisions", AsyncMock(return_value=[decision])
    )
    monkeypatch.setattr(
        svc, "find_active_offer_for_application", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(svc, "add_offer", fake_add_offer)
    monkeypatch.setattr(svc, "add_offer_version", fake_add_version)
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "add_idempotency", AsyncMock())

    session = AsyncMock()
    session.commit = AsyncMock()
    result = await svc.create_offer(
        session,
        application_id=application.id,
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.status == OFFER_STATUS_DRAFT
    assert application.pipeline_status == PIPELINE_PENDING_OFFER
    assert application.status == APPLICATION_STATUS_IN_PROGRESS
    assert application.status != "hired"
    assert application.lock_version == 2

    source = open(svc.__file__, encoding="utf-8").read()
    assert "APPLICATION_STATUS_HIRED" not in source
    assert "create_hiring_decision" not in source
    assert "process_ai_task" not in source
    assert "apply_async" in source  # mail enqueue only via Celery task name
    assert "process_mail_send_attempt" in source


@pytest.mark.asyncio
async def test_offer_service_does_not_touch_hiring_or_comprehensive() -> None:
    from app.services import offers as svc

    source = open(svc.__file__, encoding="utf-8").read()
    assert "comprehensive_analyses" not in source
    assert "process_ai_task" not in source
    assert "ai_sensitive" not in source
