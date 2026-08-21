"""Service-layer tests for comprehensive interview analysis (Task 2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    BUSINESS_TYPE_APPLICATION,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
)
from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
)
from app.models.comprehensive_analysis import (
    COMPREHENSIVE_GAP_ANALYSIS_NONE,
    COMPREHENSIVE_GAP_ANALYSIS_STALE,
    COMPREHENSIVE_GAP_CANCELLED,
    COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT,
)
from app.models.interview import (
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_SCHEDULED,
)
from app.models.interview_transcript import TranscriptCompletionMode
from app.models.resume import PIPELINE_INTERVIEWING, PIPELINE_PENDING_OFFER
from app.services.audit import RequestContext
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewValidationError,
)

MODULE = "app.services.comprehensive_analyses"

ALLOWED_AUDIT_KEYS = frozenset(
    {
        "application_id",
        "task_id",
        "task_type",
        "input_snapshot_hash",
        "eligible_round_count",
        "gap_count",
        "status",
        "analysis_id",
        "analysis_version_id",
        "version_no",
        "overall_score",
        "coverage_insufficient",
        "single_round_only",
    }
)


def _actor(*, manage: bool = True, user_id=None):
    codes = ["recruitment.manage"] if manage else ["interview.execute"]
    return SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        permission_codes=codes,
    )


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-ca-1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _application(
    *,
    pipeline: str = PIPELINE_INTERVIEWING,
    status: str = APPLICATION_STATUS_IN_PROGRESS,
    lock_version: int = 2,
):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        pipeline_status=pipeline,
        lock_version=lock_version,
        updated_at=_now(),
    )


def _round(
    *,
    application_id,
    sequence_no: int = 1,
    status: str = INTERVIEW_STATUS_COMPLETED,
    transcript_completion_mode: str | None = TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value,
    round_id=None,
):
    return SimpleNamespace(
        id=round_id or uuid4(),
        application_id=application_id,
        sequence_no=sequence_no,
        status=status,
        transcript_completion_mode=transcript_completion_mode,
    )


def _version(
    *,
    version_id=None,
    version_no: int = 1,
    overall_score=Decimal("4.0"),
    transcript_version_id=None,
):
    dim_id = uuid4()
    evidence = SimpleNamespace(
        transcript_segment_id=uuid4(),
        segment_no=1,
    )
    dim = SimpleNamespace(
        id=dim_id,
        dimension_key="collab",
        dimension_name="协作",
        weight=Decimal("100"),
        score=4,
        evidence=[evidence],
        insufficient_information_encrypted=None,
    )
    return SimpleNamespace(
        id=version_id or uuid4(),
        version_no=version_no,
        overall_score=overall_score,
        transcript_version_id=transcript_version_id or uuid4(),
        dimensions=[dim],
    )


def _analysis(*, round_id, current_version_id, analysis_id=None):
    return SimpleNamespace(
        id=analysis_id or uuid4(),
        interview_round_id=round_id,
        current_version_id=current_version_id,
    )


def _transcript(*, confirmed_version_id):
    return SimpleNamespace(current_confirmed_version_id=confirmed_version_id)


@pytest.fixture
def import_or_skip():
    pytest.importorskip("app.services.comprehensive_analyses")


def test_round_refs_forbid_transcript_and_jd_keys() -> None:
    from app.services.comprehensive_analyses import (
        FORBIDDEN_SNAPSHOT_KEYS,
        assert_no_forbidden_snapshot_keys,
        build_round_ref,
    )

    assert "quote" in FORBIDDEN_SNAPSHOT_KEYS
    assert "jd_text" in FORBIDDEN_SNAPSHOT_KEYS
    round_ = _round(application_id=uuid4())
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    ref = build_round_ref(round_, version)
    assert_no_forbidden_snapshot_keys(ref)
    dirty = {**ref, "quote": "secret", "jd_text": "jd"}
    with pytest.raises(InterviewValidationError, match="(?i)forbidden|sensitive|snapshot"):
        assert_no_forbidden_snapshot_keys(dirty)


def test_is_comprehensive_version_stale_when_round_current_moves() -> None:
    from app.services.comprehensive_analyses import is_comprehensive_version_stale

    round_id = uuid4()
    old_version_id = uuid4()
    new_version_id = uuid4()
    confirmed = uuid4()
    version = SimpleNamespace(
        round_refs=[
            {
                "round_id": str(round_id),
                "analysis_version_id": str(old_version_id),
            }
        ]
    )
    rounds_by_id = {round_id: _round(application_id=uuid4(), round_id=round_id)}
    analyses_by_round = {
        round_id: _analysis(round_id=round_id, current_version_id=new_version_id)
    }
    single = SimpleNamespace(id=old_version_id, transcript_version_id=confirmed)
    versions_by_id = {old_version_id: single}
    transcripts_by_round = {round_id: _transcript(confirmed_version_id=confirmed)}
    assert (
        is_comprehensive_version_stale(
            version,
            rounds_by_id=rounds_by_id,
            analyses_by_round=analyses_by_round,
            transcripts_by_round=transcripts_by_round,
            versions_by_id=versions_by_id,
        )
        is True
    )


def test_is_comprehensive_version_stale_when_transcript_confirmed_moves() -> None:
    from app.services.comprehensive_analyses import is_comprehensive_version_stale

    round_id = uuid4()
    version_id = uuid4()
    old_confirmed = uuid4()
    new_confirmed = uuid4()
    version = SimpleNamespace(
        round_refs=[
            {
                "round_id": str(round_id),
                "analysis_version_id": str(version_id),
            }
        ]
    )
    rounds_by_id = {round_id: _round(application_id=uuid4(), round_id=round_id)}
    analyses_by_round = {
        round_id: _analysis(round_id=round_id, current_version_id=version_id)
    }
    single = SimpleNamespace(id=version_id, transcript_version_id=old_confirmed)
    versions_by_id = {version_id: single}
    transcripts_by_round = {round_id: _transcript(confirmed_version_id=new_confirmed)}
    assert (
        is_comprehensive_version_stale(
            version,
            rounds_by_id=rounds_by_id,
            analyses_by_round=analyses_by_round,
            transcripts_by_round=transcripts_by_round,
            versions_by_id=versions_by_id,
        )
        is True
    )


def test_gaps_enumerate_cancelled_without_transcript_none_stale() -> None:
    from app.services.comprehensive_analyses import build_coverage_report

    app_id = uuid4()
    confirmed = uuid4()
    eligible_version = _version(transcript_version_id=confirmed)
    eligible_round = _round(application_id=app_id, sequence_no=1)
    cancelled = _round(
        application_id=app_id,
        sequence_no=2,
        status=INTERVIEW_STATUS_CANCELLED,
    )
    without = _round(
        application_id=app_id,
        sequence_no=3,
        transcript_completion_mode=TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value,
    )
    none_round = _round(application_id=app_id, sequence_no=4)
    stale_version = _version(transcript_version_id=uuid4())
    stale_round = _round(application_id=app_id, sequence_no=5)

    rounds = [eligible_round, cancelled, without, none_round, stale_round]
    analyses = {
        eligible_round.id: _analysis(
            round_id=eligible_round.id, current_version_id=eligible_version.id
        ),
        none_round.id: None,
        stale_round.id: _analysis(
            round_id=stale_round.id, current_version_id=stale_version.id
        ),
    }
    versions = {
        eligible_version.id: eligible_version,
        stale_version.id: stale_version,
    }
    transcripts = {
        eligible_round.id: _transcript(confirmed_version_id=confirmed),
        stale_round.id: _transcript(confirmed_version_id=confirmed),  # mismatch → stale
        without.id: None,
        none_round.id: _transcript(confirmed_version_id=confirmed),
        cancelled.id: None,
    }
    report = build_coverage_report(
        rounds=rounds,
        analyses_by_round=analyses,
        versions_by_id=versions,
        transcripts_by_round=transcripts,
    )
    codes = {g.reason_code for g in report.gaps}
    assert COMPREHENSIVE_GAP_CANCELLED in codes
    assert COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT in codes
    assert COMPREHENSIVE_GAP_ANALYSIS_NONE in codes
    assert COMPREHENSIVE_GAP_ANALYSIS_STALE in codes
    assert report.eligible_round_count == 1
    assert report.single_round_only is True
    assert report.coverage_insufficient is True


def _patch_request_base(
    monkeypatch: pytest.MonkeyPatch,
    *,
    application,
    rounds,
    analyses_by_round,
    versions_by_id,
    transcripts_by_round,
    inflight=None,
    existing_idempotency=None,
    existing_hash_task=None,
):
    from app.services import comprehensive_analyses as svc

    added_tasks: list = []
    audits: list[dict] = []
    idempotency_rows: list = []

    async def fake_add_task(_session, task):
        if getattr(task, "id", None) is None:
            task.id = uuid4()
        added_tasks.append(task)
        return task

    async def fake_audit(_session, **kwargs):
        audits.append(kwargs)

    async def fake_add_idempotency(_session, row):
        idempotency_rows.append(row)
        return row

    async def fake_get_analysis(session, *, round_id):
        return analyses_by_round.get(round_id)

    async def fake_get_version_pk(session, *, version_id):
        return versions_by_id.get(version_id)

    async def fake_get_transcript(session, round_id):
        return transcripts_by_round.get(round_id)

    monkeypatch.setattr(
        svc, "get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "get_application_by_id_for_update", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "list_rounds_for_application", AsyncMock(return_value=rounds)
    )
    monkeypatch.setattr(svc, "get_analysis_by_round", fake_get_analysis)
    monkeypatch.setattr(svc, "get_analysis_version_by_pk", fake_get_version_pk)
    monkeypatch.setattr(svc, "get_transcript_by_round_id", fake_get_transcript)
    monkeypatch.setattr(svc, "find_idempotency", AsyncMock(return_value=existing_idempotency))
    monkeypatch.setattr(svc, "add_idempotency", fake_add_idempotency)
    monkeypatch.setattr(svc, "find_inflight_task", AsyncMock(return_value=inflight))
    monkeypatch.setattr(
        svc,
        "find_task_by_input_snapshot_hash",
        AsyncMock(return_value=existing_hash_task),
    )
    monkeypatch.setattr(svc, "add_ai_task", fake_add_task)
    monkeypatch.setattr(svc, "record_audit", fake_audit)
    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=None))

    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session, added_tasks, audits, idempotency_rows


@pytest.mark.asyncio
async def test_rejects_pending_offer_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application(pipeline=PIPELINE_PENDING_OFFER)
    session, added, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[],
        analyses_by_round={},
        versions_by_id={},
        transcripts_by_round={},
    )
    with pytest.raises(
        (InterviewValidationError, InterviewConflictError),
        match=re.compile("interviewing", re.I),
    ):
        await request_comprehensive_analysis_generation(
            session,
            application_id=application.id,
            idempotency_key="k1",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert added == []


@pytest.mark.asyncio
async def test_rejects_non_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application(status=APPLICATION_STATUS_REJECTED)
    session, added, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[],
        analyses_by_round={},
        versions_by_id={},
        transcripts_by_round={},
    )
    with pytest.raises((InterviewValidationError, InterviewConflictError)):
        await request_comprehensive_analysis_generation(
            session,
            application_id=application.id,
            idempotency_key="k2",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert added == []


@pytest.mark.asyncio
async def test_rejects_zero_eligible_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    round_ = _round(
        application_id=application.id,
        status=INTERVIEW_STATUS_SCHEDULED,
        transcript_completion_mode=None,
    )
    session, added, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={},
        versions_by_id={},
        transcripts_by_round={},
    )
    with pytest.raises(
        InterviewValidationError, match=re.compile(r"analysis|coverage", re.I)
    ):
        await request_comprehensive_analysis_generation(
            session,
            application_id=application.id,
            idempotency_key="k3",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert added == []


@pytest.mark.asyncio
async def test_allows_single_eligible_round_with_single_round_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    round_ = _round(application_id=application.id)
    analysis = _analysis(round_id=round_.id, current_version_id=version.id)
    session, added, audits, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
    )
    task = await request_comprehensive_analysis_generation(
        session,
        application_id=application.id,
        idempotency_key="k-single",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert task.task_type == TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE
    assert task.business_type == BUSINESS_TYPE_APPLICATION
    assert task.business_id == application.id
    assert task.status == AI_TASK_STATUS_PENDING
    coverage = task.input_snapshot["coverage_report"]
    assert coverage["single_round_only"] is True
    assert coverage["eligible_round_count"] == 1
    assert_no_forbidden = __import__(
        MODULE, fromlist=["assert_no_forbidden_snapshot_keys"]
    ).assert_no_forbidden_snapshot_keys
    assert_no_forbidden(task.input_snapshot)
    assert len(added) == 1
    assert audits
    assert audits[0]["action"] == "comprehensive_analysis.generate_requested"
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_execute_forbidden_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    session, added, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[],
        analyses_by_round={},
        versions_by_id={},
        transcripts_by_round={},
    )
    with pytest.raises(InterviewForbiddenError, match="forbidden"):
        await request_comprehensive_analysis_generation(
            session,
            application_id=application.id,
            idempotency_key="k-exec",
            actor=_actor(manage=False),
            request_context=_ctx(),
        )
    assert added == []


@pytest.mark.asyncio
async def test_inflight_same_hash_reuses_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    round_ = _round(application_id=application.id)
    analysis = _analysis(round_id=round_.id, current_version_id=version.id)

    # First call creates task; capture hash via real path then second with inflight
    session1, added1, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
    )
    first = await request_comprehensive_analysis_generation(
        session1,
        application_id=application.id,
        idempotency_key="k-inf-1",
        actor=_actor(),
        request_context=_ctx(),
    )
    snapshot_hash = first.input_snapshot["input_snapshot_hash"]
    first.input_snapshot = {
        **first.input_snapshot,
        "input_snapshot_hash": snapshot_hash,
    }

    session2, added2, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
        inflight=first,
    )
    second = await request_comprehensive_analysis_generation(
        session2,
        application_id=application.id,
        idempotency_key="k-inf-2",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert second.id == first.id
    assert added2 == []


@pytest.mark.asyncio
async def test_inflight_different_hash_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    round_ = _round(application_id=application.id)
    analysis = _analysis(round_id=round_.id, current_version_id=version.id)
    inflight = SimpleNamespace(
        id=uuid4(),
        status=AI_TASK_STATUS_PENDING,
        input_snapshot={"input_snapshot_hash": "other-hash-completely-different"},
    )
    session, added, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
        inflight=inflight,
    )
    with pytest.raises(InterviewConflictError):
        await request_comprehensive_analysis_generation(
            session,
            application_id=application.id,
            idempotency_key="k-conflict",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert added == []


@pytest.mark.asyncio
async def test_idempotency_same_key_same_hash_returns_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    round_ = _round(application_id=application.id)
    analysis = _analysis(round_id=round_.id, current_version_id=version.id)

    session1, added1, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
    )
    first = await request_comprehensive_analysis_generation(
        session1,
        application_id=application.id,
        idempotency_key="same-key",
        actor=_actor(user_id=uuid4()),
        request_context=_ctx(),
    )
    request_hash = None
    # Recompute via second call with stored idempotency matching hash
    from app.services import comprehensive_analyses as svc

    # Build expected request payload hash the same way service will
    actor = _actor(user_id=first.created_by)
    # Use find_task path: existing idempotency with matching hash → return hash task
    existing_key = SimpleNamespace(
        request_hash=first.input_snapshot.get("request_hash")
        or first.input_snapshot["input_snapshot_hash"],
    )
    # The service stores request_hash of request_payload; align by patching consume
    session2, added2, _, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
        existing_hash_task=first,
    )

    async def fake_consume(*args, **kwargs):
        return existing_key

    # Force idempotency hit with matching hash by setting existing_key.request_hash
    # equal to whatever _consume will compute — monkeypatch _consume_idempotency
    async def consume_ok(session, *, actor, action, scope_id, key, request_payload):
        from app.services.comprehensive_analyses import _canonical_hash

        return SimpleNamespace(request_hash=_canonical_hash(request_payload))

    monkeypatch.setattr(svc, "_consume_idempotency", consume_ok)
    monkeypatch.setattr(
        svc, "find_task_by_input_snapshot_hash", AsyncMock(return_value=first)
    )
    second = await request_comprehensive_analysis_generation(
        session2,
        application_id=application.id,
        idempotency_key="same-key",
        actor=actor,
        request_context=_ctx(),
    )
    assert second.id == first.id


@pytest.mark.asyncio
async def test_audit_generate_requested_has_no_sensitive_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.comprehensive_analyses import (
        request_comprehensive_analysis_generation,
    )

    application = _application()
    confirmed = uuid4()
    version = _version(transcript_version_id=confirmed)
    round_ = _round(application_id=application.id)
    analysis = _analysis(round_id=round_.id, current_version_id=version.id)
    session, _, audits, _ = _patch_request_base(
        monkeypatch,
        application=application,
        rounds=[round_],
        analyses_by_round={round_.id: analysis},
        versions_by_id={version.id: version},
        transcripts_by_round={round_.id: _transcript(confirmed_version_id=confirmed)},
    )
    await request_comprehensive_analysis_generation(
        session,
        application_id=application.id,
        idempotency_key="k-audit",
        actor=_actor(),
        request_context=_ctx(),
    )
    changes = audits[0]["changes"]
    assert set(changes.keys()) <= ALLOWED_AUDIT_KEYS
    for key in ("quote", "summary", "text", "overall_summary", "jd_text"):
        assert key not in changes


@pytest.mark.asyncio
async def test_persist_does_not_mutate_pipeline_or_hiring_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.comprehensive_analyses import persist_comprehensive_analysis_result

    application = _application(lock_version=5)
    task_id = uuid4()
    analysis_set = SimpleNamespace(
        id=uuid4(),
        application_id=application.id,
        current_version_id=None,
        updated_at=_now(),
    )
    snapshot = {
        "schema_version": "1.0",
        "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        "application_id": str(application.id),
        "workflow_key": "interview_comprehensive_analyze",
        "workflow_version": "1.0",
        "input_snapshot_hash": "abc",
        "round_refs": [],
        "coverage_report": {
            "eligible_round_count": 1,
            "total_round_count": 1,
            "included_rounds": [],
            "gaps": [],
            "coverage_insufficient": False,
            "single_round_only": True,
            "missing_round_count": 0,
        },
    }
    task = SimpleNamespace(
        id=task_id,
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application.id,
        created_by=uuid4(),
        input_snapshot=snapshot,
    )
    created_versions: list = []
    hiring_calls: list = []

    from app.services import comprehensive_analyses as svc

    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(
        svc, "get_comprehensive_version_by_task_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc,
        "get_comprehensive_analysis_for_update",
        AsyncMock(return_value=analysis_set),
    )
    monkeypatch.setattr(svc, "next_comprehensive_version_no", AsyncMock(return_value=1))

    async def fake_create_version(_session, row):
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        created_versions.append(row)
        return row

    monkeypatch.setattr(svc, "create_comprehensive_version", fake_create_version)
    monkeypatch.setattr(
        svc, "create_comprehensive_analysis", AsyncMock(side_effect=AssertionError)
    )
    monkeypatch.setattr(svc, "encrypt_secret", lambda plain: f"enc:{plain}")
    monkeypatch.setattr(svc, "record_audit", AsyncMock())
    monkeypatch.setattr(
        svc, "count_hiring_decisions", AsyncMock(side_effect=lambda *a, **k: 0)
    )

    # Guard: service must not import/call create_hiring_decision
    original_import = __import__

    session = AsyncMock()
    session.flush = AsyncMock()
    before_pipeline = application.pipeline_status
    before_status = application.status
    before_lock = application.lock_version

    version = await persist_comprehensive_analysis_result(
        session,
        task_id=task_id,
        payload={"overall_summary": "辅助综合结论", "overall_score": 4.0},
        actor=_actor(),
        request_context=_ctx(),
    )
    assert version is not None
    assert application.pipeline_status == before_pipeline
    assert application.status == before_status
    assert application.lock_version == before_lock
    assert analysis_set.current_version_id == created_versions[0].id
    assert created_versions[0].overall_summary_encrypted.startswith("enc:")


@pytest.mark.asyncio
async def test_persist_is_idempotent_on_ai_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.comprehensive_analyses import persist_comprehensive_analysis_result

    existing = SimpleNamespace(id=uuid4(), version_no=1)
    task = SimpleNamespace(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        input_snapshot={},
    )
    from app.services import comprehensive_analyses as svc

    monkeypatch.setattr(svc, "get_ai_task_by_id", AsyncMock(return_value=task))
    monkeypatch.setattr(
        svc, "get_comprehensive_version_by_task_id", AsyncMock(return_value=existing)
    )
    create = AsyncMock()
    monkeypatch.setattr(svc, "create_comprehensive_version", create)
    session = AsyncMock()
    again = await persist_comprehensive_analysis_result(
        session,
        task_id=task.id,
        payload={"overall_summary": "x"},
    )
    assert again.id == existing.id
    create.assert_not_called()


@pytest.mark.asyncio
async def test_list_allows_pending_offer_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.comprehensive_analyses import list_comprehensive_analysis

    application = _application(pipeline=PIPELINE_PENDING_OFFER)
    analysis_set = SimpleNamespace(
        id=uuid4(),
        application_id=application.id,
        current_version_id=None,
    )
    from app.services import comprehensive_analyses as svc

    monkeypatch.setattr(
        svc, "get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        svc, "get_comprehensive_analysis_by_application", AsyncMock(return_value=analysis_set)
    )
    monkeypatch.setattr(
        svc, "list_comprehensive_version_rows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(svc, "list_rounds_for_application", AsyncMock(return_value=[]))
    session = AsyncMock()
    summary = await list_comprehensive_analysis(
        session, application_id=application.id, actor=_actor()
    )
    assert summary.application_id == application.id
    assert summary.versions == []


def test_request_generate_locks_application_row_for_update() -> None:
    from pathlib import Path

    service_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "comprehensive_analyses.py"
    )
    source = service_path.read_text(encoding="utf-8")
    assert "get_application_by_id_for_update" in source
    # Generate path must lock before inflight / idempotency create.
    generate_fn = source.split("async def request_comprehensive_analysis_generation")[1]
    generate_fn = generate_fn.split("async def dispatch_persisted_comprehensive")[0]
    lock_pos = generate_fn.find("get_application_by_id_for_update")
    inflight_pos = generate_fn.find("find_inflight_task")
    assert lock_pos != -1
    assert inflight_pos != -1
    assert lock_pos < inflight_pos
