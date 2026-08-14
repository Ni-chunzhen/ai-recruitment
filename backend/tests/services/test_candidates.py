from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    APPLICATION_STATUS_REJECTED,
    APPLICATION_STATUS_TRANSFERRED,
    CLOSE_ACTION_REJECT,
    CLOSE_ACTION_TRANSFER,
    INTERVIEW_TASK_NONE,
    INTERVIEW_TASK_PENDING_CANCEL,
    INTERVIEW_TASK_PENDING_REBUILD,
    TIMELINE_EVENT_VERSION_MIGRATED,
)
from app.models.job import (
    JOB_STATUS_OPEN,
    VERSION_STATUS_PUBLISHED,
    VERSION_STATUS_SUPERSEDED,
)
from app.schemas.candidate import (
    CreateCandidateRequest,
    MigrateVersionRequest,
    ResolveCloseRequest,
)
from app.services.audit import RequestContext
from app.services.candidates import (
    CandidateStateError,
    assert_job_can_close,
    migrate_application_version,
    resolve_close_application,
)
from app.services.jobs import JobStateError, _to_version_out, close_job


def _actor() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4())


def _request_context() -> RequestContext:
    return RequestContext(request_id="test-req", ip_address="127.0.0.1")


def _job(
    *,
    status: str = JOB_STATUS_OPEN,
    versions: list | None = None,
) -> SimpleNamespace:
    job_id = uuid4()
    version_id = uuid4()
    version = SimpleNamespace(
        id=version_id,
        job_id=job_id,
        status=VERSION_STATUS_PUBLISHED,
        version_label="V1.0",
        major=1,
        minor=0,
        upgrade_type="initial",
        change_summary=None,
        raw_jd_text="",
        structured_jd={
            "responsibilities": [],
            "requirements": [],
            "must_have": [],
            "nice_to_have": [],
            "skills": [],
        },
        score_dimensions=[],
        job_snapshot=None,
        base_version_id=None,
        published_at=datetime.now(UTC),
        published_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    versions = versions or [version]
    return SimpleNamespace(
        id=job_id,
        status=status,
        current_version_id=versions[0].id,
        draft_version_id=None,
        versions=versions,
        close_reason=None,
        closed_at=None,
        pause_reason=None,
        updated_by=None,
        updated_at=datetime.now(UTC),
    )


def _application(
    *,
    job_id,
    version_id,
    interview_started: bool = False,
    status: str = APPLICATION_STATUS_IN_PROGRESS,
) -> SimpleNamespace:
    candidate = SimpleNamespace(
        id=uuid4(),
        name="张三",
        phone="13800000000",
        email=None,
    )
    return SimpleNamespace(
        id=uuid4(),
        candidate_id=candidate.id,
        candidate=candidate,
        job_id=job_id,
        job_version_id=version_id,
        status=status,
        interview_started=interview_started,
        interview_task_state=INTERVIEW_TASK_NONE,
        close_action=None,
        close_reason=None,
        transferred_to_job_id=None,
        previous_version_id=None,
        migration_reason=None,
        migrated_at=None,
        migrated_by=None,
        timeline_events=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_close_allowed_when_no_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.jobs.get_job_by_id",
        AsyncMock(side_effect=[job, job]),
    )
    monkeypatch.setattr(
        "app.services.jobs.assert_job_can_close",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.jobs.record_audit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.jobs.to_job_detail",
        AsyncMock(return_value=SimpleNamespace(id=job.id, status="closed")),
    )

    result = await close_job(
        session,
        job_id=job.id,
        reason="招满",
        actor=actor,  # type: ignore[arg-type]
        request_context=_request_context(),
    )
    assert result.status == "closed"
    assert job.status == "closed"


@pytest.mark.asyncio
async def test_close_blocked_when_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.jobs.get_job_by_id",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "app.services.jobs.assert_job_can_close",
        AsyncMock(
            side_effect=CandidateStateError(
                "cannot close job with 1 in-flight candidates; resolve them first"
            )
        ),
    )

    with pytest.raises(CandidateStateError, match="in-flight"):
        await close_job(
            session,
            job_id=job.id,
            reason="招满",
            actor=actor,  # type: ignore[arg-type]
            request_context=_request_context(),
        )
    assert job.status == JOB_STATUS_OPEN


@pytest.mark.asyncio
async def test_assert_job_can_close_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    job_id = uuid4()
    monkeypatch.setattr(
        "app.services.candidates.count_in_flight_applications",
        AsyncMock(return_value=0),
    )
    await assert_job_can_close(session, job_id=job_id)

    monkeypatch.setattr(
        "app.services.candidates.count_in_flight_applications",
        AsyncMock(return_value=2),
    )
    with pytest.raises(CandidateStateError, match="2 in-flight"):
        await assert_job_can_close(session, job_id=job_id)


@pytest.mark.asyncio
async def test_reject_then_can_close(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    application = _application(job_id=job.id, version_id=job.current_version_id)
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.candidates.get_job_by_id",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_application_by_id",
        AsyncMock(side_effect=[application, application]),
    )
    monkeypatch.setattr(
        "app.services.candidates.record_audit",
        AsyncMock(),
    )

    out = await resolve_close_application(
        session,
        job_id=job.id,
        application_id=application.id,
        payload=ResolveCloseRequest(action="reject", reason="不匹配"),
        actor=actor,  # type: ignore[arg-type]
        request_context=_request_context(),
    )
    assert application.status == APPLICATION_STATUS_REJECTED
    assert application.close_action == CLOSE_ACTION_REJECT
    assert out.status == "rejected"

    monkeypatch.setattr(
        "app.services.candidates.count_in_flight_applications",
        AsyncMock(return_value=0),
    )
    await assert_job_can_close(session, job_id=job.id)


@pytest.mark.asyncio
async def test_transfer_to_open_job_then_source_can_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _job()
    target = _job()
    application = _application(
        job_id=source.id,
        version_id=source.current_version_id,
        interview_started=True,
    )
    new_app = _application(
        job_id=target.id,
        version_id=target.current_version_id,
    )
    session = AsyncMock()
    actor = _actor()

    async def fake_get_job(_session, job_id):
        if job_id == source.id:
            return source
        if job_id == target.id:
            return target
        return None

    monkeypatch.setattr(
        "app.services.candidates.get_job_by_id",
        fake_get_job,
    )
    monkeypatch.setattr(
        "app.services.candidates.get_application_by_id",
        AsyncMock(side_effect=[application, application]),
    )
    monkeypatch.setattr(
        "app.services.candidates.create_application",
        AsyncMock(return_value=new_app),
    )
    monkeypatch.setattr(
        "app.services.candidates.record_audit",
        AsyncMock(),
    )

    out = await resolve_close_application(
        session,
        job_id=source.id,
        application_id=application.id,
        payload=ResolveCloseRequest(
            action="transfer",
            reason="调岗",
            target_job_id=target.id,
        ),
        actor=actor,  # type: ignore[arg-type]
        request_context=_request_context(),
    )
    assert out.status == "transferred"
    assert application.status == APPLICATION_STATUS_TRANSFERRED
    assert application.close_action == CLOSE_ACTION_TRANSFER
    assert application.transferred_to_job_id == target.id
    assert application.interview_task_state == INTERVIEW_TASK_PENDING_CANCEL

    monkeypatch.setattr(
        "app.services.candidates.count_in_flight_applications",
        AsyncMock(return_value=0),
    )
    await assert_job_can_close(session, job_id=source.id)


@pytest.mark.asyncio
async def test_migrate_without_interview_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    v1_id = uuid4()
    v2_id = uuid4()
    job_id = uuid4()
    v1 = SimpleNamespace(id=v1_id, status=VERSION_STATUS_SUPERSEDED)
    v2 = SimpleNamespace(id=v2_id, status=VERSION_STATUS_PUBLISHED)
    job = SimpleNamespace(
        id=job_id,
        current_version_id=v2_id,
        draft_version_id=None,
        versions=[v1, v2],
    )
    application = _application(job_id=job_id, version_id=v1_id, interview_started=False)
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.candidates.get_job_by_id",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_application_by_id",
        AsyncMock(side_effect=[application, application]),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_version_by_id",
        lambda j, vid: {v1_id: v1, v2_id: v2}.get(vid),
    )
    monkeypatch.setattr(
        "app.services.candidates.record_audit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.candidates.mark_current_results_stale",
        AsyncMock(return_value=1),
    )

    out = await migrate_application_version(
        session,
        job_id=job_id,
        application_id=application.id,
        payload=MigrateVersionRequest(to_version_id=v2_id),
        actor=actor,  # type: ignore[arg-type]
        request_context=_request_context(),
    )
    assert out.job_version_id == v2_id
    assert application.previous_version_id == v1_id
    assert any(
        event["type"] == TIMELINE_EVENT_VERSION_MIGRATED
        for event in application.timeline_events
    )


@pytest.mark.asyncio
async def test_migrate_with_interview_requires_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v1_id = uuid4()
    v2_id = uuid4()
    job_id = uuid4()
    v1 = SimpleNamespace(id=v1_id, status=VERSION_STATUS_SUPERSEDED)
    v2 = SimpleNamespace(id=v2_id, status=VERSION_STATUS_PUBLISHED)
    job = SimpleNamespace(
        id=job_id,
        current_version_id=v2_id,
        draft_version_id=None,
        versions=[v1, v2],
    )
    application = _application(job_id=job_id, version_id=v1_id, interview_started=True)
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.candidates.get_job_by_id",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_application_by_id",
        AsyncMock(return_value=application),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_version_by_id",
        lambda j, vid: {v1_id: v1, v2_id: v2}.get(vid),
    )

    with pytest.raises(CandidateStateError, match="reason is required"):
        await migrate_application_version(
            session,
            job_id=job_id,
            application_id=application.id,
            payload=MigrateVersionRequest(to_version_id=v2_id),
            actor=actor,  # type: ignore[arg-type]
            request_context=_request_context(),
        )


@pytest.mark.asyncio
async def test_migrate_with_interview_and_reason_writes_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v1_id = uuid4()
    v2_id = uuid4()
    job_id = uuid4()
    v1 = SimpleNamespace(id=v1_id, status=VERSION_STATUS_SUPERSEDED)
    v2 = SimpleNamespace(id=v2_id, status=VERSION_STATUS_PUBLISHED)
    job = SimpleNamespace(
        id=job_id,
        current_version_id=v2_id,
        draft_version_id=None,
        versions=[v1, v2],
    )
    application = _application(job_id=job_id, version_id=v1_id, interview_started=True)
    session = AsyncMock()
    actor = _actor()

    monkeypatch.setattr(
        "app.services.candidates.get_job_by_id",
        AsyncMock(return_value=job),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_application_by_id",
        AsyncMock(side_effect=[application, application]),
    )
    monkeypatch.setattr(
        "app.services.candidates.get_version_by_id",
        lambda j, vid: {v1_id: v1, v2_id: v2}.get(vid),
    )
    monkeypatch.setattr(
        "app.services.candidates.record_audit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.candidates.mark_current_results_stale",
        AsyncMock(return_value=1),
    )

    out = await migrate_application_version(
        session,
        job_id=job_id,
        application_id=application.id,
        payload=MigrateVersionRequest(to_version_id=v2_id, reason="评分维度更新"),
        actor=actor,  # type: ignore[arg-type]
        request_context=_request_context(),
    )
    assert out.job_version_id == v2_id
    assert application.interview_task_state == INTERVIEW_TASK_PENDING_REBUILD
    assert application.migration_reason == "评分维度更新"
    assert len(application.timeline_events) == 1
    event = application.timeline_events[0]
    assert event["type"] == TIMELINE_EVENT_VERSION_MIGRATED
    assert event["from_version_id"] == str(v1_id)
    assert event["to_version_id"] == str(v2_id)
    assert event["reason"] == "评分维度更新"


def test_bound_candidates_count_passed_to_version_out() -> None:
    version = SimpleNamespace(
        id=uuid4(),
        version_label="V1.0",
        major=1,
        minor=0,
        status=VERSION_STATUS_PUBLISHED,
        upgrade_type="initial",
        change_summary=None,
        raw_jd_text="",
        structured_jd={
            "responsibilities": [],
            "requirements": [],
            "must_have": [],
            "nice_to_have": [],
            "skills": [],
        },
        score_dimensions=[],
        job_snapshot=None,
        base_version_id=None,
        published_at=datetime.now(UTC),
        published_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    out = _to_version_out(version, current_version_id=version.id, bound_candidates=3)
    assert out is not None
    assert out.bound_candidates == 3
    assert out.is_current is True


def test_create_candidate_request_requires_name() -> None:
    with pytest.raises(Exception):
        CreateCandidateRequest(name="")


@pytest.mark.asyncio
async def test_close_job_still_rejects_draft_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job(status="draft")
    session = AsyncMock()
    actor = _actor()
    monkeypatch.setattr(
        "app.services.jobs.get_job_by_id",
        AsyncMock(return_value=job),
    )
    with pytest.raises(JobStateError, match="only open or paused"):
        await close_job(
            session,
            job_id=job.id,
            reason="x",
            actor=actor,  # type: ignore[arg-type]
            request_context=_request_context(),
        )
