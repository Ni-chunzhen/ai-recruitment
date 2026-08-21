"""Screening must not rewrite pending_offer applications."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.resume import (
    PIPELINE_PENDING_OFFER,
    SCREENING_ENTER_INTERVIEW,
)
from app.schemas.resume import ScreeningDecisionRequest
from app.services.audit import RequestContext


def _actor():
    return SimpleNamespace(id=uuid4(), username="hr")


@pytest.mark.asyncio
async def test_screening_rejects_pending_offer_without_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import resumes as svc

    application = SimpleNamespace(
        id=uuid4(),
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_PENDING_OFFER,
        lock_version=4,
        interview_started=True,
        close_action=None,
        close_reason=None,
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        svc, "get_application_by_id", AsyncMock(return_value=application)
    )
    add_decision = AsyncMock()
    monkeypatch.setattr(svc, "add_screening_decision", add_decision)
    monkeypatch.setattr(svc, "add_status_log", AsyncMock())
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(
        svc, "get_current_ai_result", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "find_screening_by_idempotency", AsyncMock(return_value=None)
    )

    session = AsyncMock()
    session.commit = AsyncMock()

    with pytest.raises(svc.ResumeStateError, match="(?i)pending_offer|screen"):
        await svc.create_screening_decision(
            session,
            application_id=application.id,
            payload=ScreeningDecisionRequest(
                decision=SCREENING_ENTER_INTERVIEW,
                lock_version=4,
            ),
            actor=_actor(),
            request_context=RequestContext(request_id="scr-po-1"),
        )

    assert application.pipeline_status == PIPELINE_PENDING_OFFER
    assert application.lock_version == 4
    add_decision.assert_not_awaited()
    session.commit.assert_not_awaited()
