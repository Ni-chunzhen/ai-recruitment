"""Service-layer tests for post-interview HiringDecision (Task 2)."""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
)
from app.models.resume import (
    HIRING_HOLD,
    HIRING_REASON_MEETS_ROLE_BAR,
    HIRING_REASON_NEED_ANOTHER_ROUND,
    HIRING_REASON_SKILL_GAP,
    HIRING_RECOMMEND_HIRE,
    HIRING_REJECT,
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_OFFER,
    PIPELINE_REJECTED,
    HiringDecision,
)
from app.services.audit import RequestContext

MODULE = "app.services.hiring_decisions"

ALLOWED_AUDIT_KEYS = frozenset(
    {
        "decision",
        "reason_code",
        "from",
        "to",
        "lock_version",
        "analysis_version_id",
        "round_id",
        "overall_score",
        "idempotency_key",
    }
)


def _actor(user_id=None):
    return SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        permission_codes=["recruitment.manage"],
    )


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-hd-1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def _application(
    *,
    pipeline: str = PIPELINE_INTERVIEWING,
    status: str = APPLICATION_STATUS_IN_PROGRESS,
    lock_version: int = 3,
):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        pipeline_status=pipeline,
        lock_version=lock_version,
        close_action=None,
        close_reason=None,
        updated_at=_now(),
    )


def _version(*, analysis_id=None, transcript_version_id=None, version_no: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        analysis_id=analysis_id or uuid4(),
        version_no=version_no,
        overall_score=4.2,
        transcript_version_id=transcript_version_id or uuid4(),
        job_version_id=uuid4(),
    )


def _analysis(*, current_version_id, round_id, analysis_id=None):
    return SimpleNamespace(
        id=analysis_id or uuid4(),
        interview_round_id=round_id,
        current_version_id=current_version_id,
    )


def _round(*, application_id, round_id=None):
    return SimpleNamespace(id=round_id or uuid4(), application_id=application_id)


def _transcript(*, confirmed_version_id):
    return SimpleNamespace(current_confirmed_version_id=confirmed_version_id)


def _payload(
    *,
    decision: str,
    reason_code: str,
    analysis_version_id,
    lock_version: int = 3,
    idempotency_key: str | None = None,
):
    from app.services.hiring_decisions import HiringDecisionRequestData

    return HiringDecisionRequestData(
        decision=decision,
        reason_code=reason_code,
        analysis_version_id=analysis_version_id,
        lock_version=lock_version,
        idempotency_key=idempotency_key,
    )


def _patch_happy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    application,
    version,
    round_,
    analysis,
    transcript,
    existing_idempotency=None,
):
    from app.services import hiring_decisions as svc

    version.analysis_id = analysis.id

    audits: list[dict] = []
    decisions: list[HiringDecision] = []
    logs: list = []

    async def fake_add_decision(_session, row: HiringDecision):
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        if getattr(row, "created_at", None) is None:
            row.created_at = _now()
        decisions.append(row)
        return row

    async def fake_add_log(_session, log):
        logs.append(log)
        return log

    async def fake_audit(_session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(svc, "get_application_by_id", AsyncMock(return_value=application))
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "find_hiring_by_idempotency", AsyncMock(return_value=existing_idempotency)
    )
    monkeypatch.setattr(svc, "add_hiring_decision", fake_add_decision)
    monkeypatch.setattr(svc, "add_status_log", fake_add_log)
    monkeypatch.setattr(svc, "record_audit", fake_audit)
    monkeypatch.setattr(
        svc, "get_analysis_version_by_pk", AsyncMock(return_value=version)
    )
    monkeypatch.setattr(svc, "get_analysis_by_id", AsyncMock(return_value=analysis))
    monkeypatch.setattr(svc, "get_round_by_id", AsyncMock(return_value=round_))
    monkeypatch.setattr(
        svc, "get_transcript_by_round_id", AsyncMock(return_value=transcript)
    )
    session = AsyncMock()
    session.commit = AsyncMock()
    return session, audits, decisions, logs


@pytest.mark.asyncio
async def test_recommend_hire_moves_to_pending_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import create_hiring_decision

    application = _application()
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, audits, decisions, logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    result = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_RECOMMEND_HIRE,
            reason_code=HIRING_REASON_MEETS_ROLE_BAR,
            analysis_version_id=version.id,
            lock_version=3,
        ),
        actor=_actor(),
        request_context=_ctx(),
    )

    assert application.pipeline_status == PIPELINE_PENDING_OFFER
    assert application.status == APPLICATION_STATUS_IN_PROGRESS
    assert application.status != "hired"
    assert application.lock_version == 4
    assert result.to_pipeline_status == PIPELINE_PENDING_OFFER
    assert len(decisions) == 1
    assert decisions[0].reason_code == HIRING_REASON_MEETS_ROLE_BAR
    assert getattr(decisions[0], "reason", None) is None or not hasattr(
        decisions[0], "reason"
    )
    assert len(logs) == 1
    assert logs[0].from_status == PIPELINE_INTERVIEWING
    assert logs[0].to_status == PIPELINE_PENDING_OFFER
    assert len(audits) == 1
    assert audits[0]["action"] == "application.hiring_decision"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_closes_application(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.hiring_decisions import create_hiring_decision

    application = _application()
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _audits, _decisions, logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_REJECT,
            reason_code=HIRING_REASON_SKILL_GAP,
            analysis_version_id=version.id,
        ),
        actor=_actor(),
        request_context=_ctx(),
    )

    assert application.pipeline_status == PIPELINE_REJECTED
    assert application.status == APPLICATION_STATUS_REJECTED
    assert application.close_action == "reject"
    assert application.close_reason == HIRING_REASON_SKILL_GAP
    assert logs[0].to_status == PIPELINE_REJECTED


@pytest.mark.asyncio
async def test_hold_keeps_interviewing_and_increments_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import create_hiring_decision

    application = _application(lock_version=5)
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    first = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_HOLD,
            reason_code=HIRING_REASON_NEED_ANOTHER_ROUND,
            analysis_version_id=version.id,
            lock_version=5,
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert application.pipeline_status == PIPELINE_INTERVIEWING
    assert application.lock_version == 6
    assert logs[0].from_status == PIPELINE_INTERVIEWING
    assert logs[0].to_status == PIPELINE_INTERVIEWING

    second = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_HOLD,
            reason_code=HIRING_REASON_NEED_ANOTHER_ROUND,
            analysis_version_id=version.id,
            lock_version=6,
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert application.lock_version == 7
    assert len(decisions) == 2
    assert first.id != second.id


@pytest.mark.asyncio
async def test_rejects_when_pipeline_pending_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import (
        HiringStateError,
        create_hiring_decision,
    )

    application = _application(pipeline=PIPELINE_PENDING_OFFER)
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringStateError):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_HOLD,
                reason_code=HIRING_REASON_NEED_ANOTHER_ROUND,
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_when_pipeline_rejected_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import (
        HiringStateError,
        create_hiring_decision,
    )

    application = _application(
        pipeline=PIPELINE_REJECTED, status=APPLICATION_STATUS_REJECTED
    )
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringStateError):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_HOLD,
                reason_code=HIRING_REASON_NEED_ANOTHER_ROUND,
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []


@pytest.mark.asyncio
async def test_rejects_stale_analysis_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.hiring_decisions import (
        HiringStateError,
        create_hiring_decision,
    )

    application = _application()
    round_ = _round(application_id=application.id)
    version = _version(transcript_version_id=uuid4())
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=uuid4())  # mismatch → stale
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringStateError, match=re.compile("stale", re.I)):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_RECOMMEND_HIRE,
                reason_code=HIRING_REASON_MEETS_ROLE_BAR,
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []


@pytest.mark.asyncio
async def test_rejects_non_current_analysis_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import (
        HiringStateError,
        create_hiring_decision,
    )

    application = _application()
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=uuid4(), round_id=round_.id)  # not current
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringStateError, match=re.compile("current", re.I)):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_RECOMMEND_HIRE,
                reason_code=HIRING_REASON_MEETS_ROLE_BAR,
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []


@pytest.mark.asyncio
async def test_rejects_analysis_from_other_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import (
        HiringValidationError,
        create_hiring_decision,
    )

    application = _application()
    other_app = uuid4()
    round_ = _round(application_id=other_app)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringValidationError):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_RECOMMEND_HIRE,
                reason_code=HIRING_REASON_MEETS_ROLE_BAR,
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []


@pytest.mark.asyncio
async def test_lock_version_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.hiring_decisions import (
        HiringConflictError,
        create_hiring_decision,
    )

    application = _application(lock_version=9)
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringConflictError, match="refresh and retry"):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_HOLD,
                reason_code=HIRING_REASON_NEED_ANOTHER_ROUND,
                analysis_version_id=version.id,
                lock_version=8,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotency_returns_same_row_without_second_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import create_hiring_decision

    application = _application(lock_version=2)
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    first = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_RECOMMEND_HIRE,
            reason_code=HIRING_REASON_MEETS_ROLE_BAR,
            analysis_version_id=version.id,
            lock_version=2,
            idempotency_key="idem-hd-1",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert application.pipeline_status == PIPELINE_PENDING_OFFER
    assert application.lock_version == 3
    assert len(decisions) == 1
    assert len(logs) == 1

    existing = decisions[0]
    from app.services import hiring_decisions as svc

    monkeypatch.setattr(
        svc, "find_hiring_by_idempotency", AsyncMock(return_value=existing)
    )

    second = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_RECOMMEND_HIRE,
            reason_code=HIRING_REASON_MEETS_ROLE_BAR,
            analysis_version_id=version.id,
            lock_version=3,
            idempotency_key="idem-hd-1",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert second.id == first.id == existing.id
    assert application.lock_version == 3
    assert len(decisions) == 1
    assert len(logs) == 1
    assert application.pipeline_status == PIPELINE_PENDING_OFFER


@pytest.mark.asyncio
async def test_audit_changes_exclude_quote_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import create_hiring_decision

    application = _application()
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, audits, _d, _l = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_RECOMMEND_HIRE,
            reason_code=HIRING_REASON_MEETS_ROLE_BAR,
            analysis_version_id=version.id,
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    changes = audits[0]["changes"]
    assert set(changes.keys()) <= ALLOWED_AUDIT_KEYS
    assert "quote" not in changes
    assert "summary" not in changes
    assert "reason" not in changes


@pytest.mark.asyncio
async def test_rejects_reason_code_not_allowed_for_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.hiring_decisions import (
        HiringValidationError,
        create_hiring_decision,
    )

    application = _application()
    round_ = _round(application_id=application.id)
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    analysis = _analysis(current_version_id=version.id, round_id=round_.id)
    transcript = _transcript(confirmed_version_id=confirmed)
    session, _a, decisions, _logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    with pytest.raises(HiringValidationError):
        await create_hiring_decision(
            session,
            application_id=application.id,
            payload=_payload(
                decision=HIRING_RECOMMEND_HIRE,
                reason_code=HIRING_REASON_SKILL_GAP,  # reject-only code
                analysis_version_id=version.id,
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert decisions == []


def test_create_hiring_decision_source_has_no_ai_or_dify_calls() -> None:
    from app.services.hiring_decisions import create_hiring_decision

    source = inspect.getsource(create_hiring_decision)
    assert "enqueue_" not in source
    assert "run_dify" not in source
    assert "process_sensitive" not in source
    assert '"hired"' not in source
    assert "APPLICATION_STATUS_HIRED" not in source


def test_create_hiring_decision_loads_application_with_row_lock() -> None:
    from app.services import hiring_decisions as svc

    source = inspect.getsource(svc.create_hiring_decision)
    assert (
        "get_application_by_id_for_update" in source
        or "with_for_update" in source
    ), "create_hiring_decision must lock the application row"


@pytest.mark.asyncio
async def test_idempotency_integrity_error_returns_existing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.services import hiring_decisions as svc
    from app.services.hiring_decisions import create_hiring_decision

    application = _application(lock_version=2)
    version = _version()
    round_ = _round(application_id=application.id)
    analysis = _analysis(
        current_version_id=version.id, round_id=round_.id, analysis_id=version.analysis_id
    )
    transcript = _transcript(confirmed_version_id=version.transcript_version_id)
    existing = HiringDecision(
        id=uuid4(),
        application_id=application.id,
        decision=HIRING_RECOMMEND_HIRE,
        reason_code=HIRING_REASON_MEETS_ROLE_BAR,
        round_id=round_.id,
        analysis_version_id=version.id,
        overall_score=4.2,
        analysis_version_no=1,
        from_pipeline_status=PIPELINE_INTERVIEWING,
        to_pipeline_status=PIPELINE_PENDING_OFFER,
        decided_by=uuid4(),
        idempotency_key="idem-race",
        created_at=_now(),
    )

    session, audits, decisions, logs = _patch_happy(
        monkeypatch,
        application=application,
        version=version,
        round_=round_,
        analysis=analysis,
        transcript=transcript,
    )

    async def boom_add(_session, _row):
        # Winner committed: application advanced; our insert hits unique index.
        application.lock_version = 3
        application.pipeline_status = PIPELINE_PENDING_OFFER
        raise IntegrityError("INSERT", {}, Exception("uq_hiring_decisions_idempotency"))

    find = AsyncMock(side_effect=[None, existing])
    monkeypatch.setattr(svc, "add_hiring_decision", boom_add)
    monkeypatch.setattr(svc, "find_hiring_by_idempotency", find)
    session.rollback = AsyncMock()

    result = await create_hiring_decision(
        session,
        application_id=application.id,
        payload=_payload(
            decision=HIRING_RECOMMEND_HIRE,
            reason_code=HIRING_REASON_MEETS_ROLE_BAR,
            analysis_version_id=version.id,
            lock_version=2,
            idempotency_key="idem-race",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )

    assert result.id == existing.id
    assert result.decision == HIRING_RECOMMEND_HIRE
    assert result.lock_version == 3
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()
    assert decisions == []
    assert logs == []
    assert audits == []


def test_is_analysis_version_stale_matches_private_helper() -> None:
    from app.services.interview_analyses import (
        _is_stale,
        is_analysis_version_stale,
    )

    confirmed = uuid4()
    version = SimpleNamespace(transcript_version_id=confirmed)
    fresh = SimpleNamespace(current_confirmed_version_id=confirmed)
    stale_t = SimpleNamespace(current_confirmed_version_id=uuid4())
    assert is_analysis_version_stale(version, fresh) is False
    assert _is_stale(version, fresh) is False
    assert is_analysis_version_stale(version, stale_t) is True
    assert _is_stale(version, stale_t) is True
    assert is_analysis_version_stale(version, None) is True
    assert _is_stale(version, None) is True
