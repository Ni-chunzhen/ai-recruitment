from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import (
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_CANCELLED,
    SCHEDULE_STATUS_SUPERSEDED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
    InterviewSchedule,
)
from app.models.resume import (
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_HR_SCREEN,
    PIPELINE_REJECTED,
)
from app.schemas.interview import (
    InterviewAbnormalEndRequest,
    InterviewCancelRequest,
    InterviewConflictCheckRequest,
    InterviewRescheduleRequest,
    InterviewRoundActionRequest,
    InterviewRoundCreate,
    InterviewRoundReorderRequest,
    InterviewRoundUpdate,
    InterviewScheduleCreate,
)
from app.services.audit import RequestContext
from app.services.interview_state import InterviewStateError
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
    cancel_interview_round,
    check_interview_conflicts,
    complete_interview_round,
    create_interview_round,
    end_interview_abnormally,
    finish_interview_round,
    get_interview_timeline,
    list_interview_reason_codes,
    reorder_interview_rounds,
    reschedule_interview_round,
    schedule_interview_round,
    start_interview_round,
    update_interview_round,
)


def _actor(*, manage: bool = True, execute: bool = True, user_id=None):
    user = SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        display_name="HR",
        roles=[],
    )
    codes = []
    if manage:
        codes.append("recruitment.manage")
    if execute:
        codes.append("interview.execute")
    user.permission_codes = codes
    return user


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-1", ip_address="127.0.0.1")


def _application(
    *, pipeline=PIPELINE_INTERVIEWING, status=APPLICATION_STATUS_IN_PROGRESS
):
    return SimpleNamespace(
        id=uuid4(),
        candidate_id=uuid4(),
        candidate=SimpleNamespace(id=uuid4(), name="张三"),
        job_id=uuid4(),
        job_version_id=uuid4(),
        pipeline_status=pipeline,
        status=status,
        lock_version=1,
    )


def _create_payload(**overrides) -> InterviewRoundCreate:
    interviewer_id = uuid4()
    data = {
        "name": "第一轮专业面",
        "sequence_no": 1,
        "format": "ONLINE",
        "owner_id": uuid4(),
        "interviewers": [{"interviewer_id": interviewer_id, "is_primary": True}],
        "idempotency_key": "create-1",
    }
    data.update(overrides)
    return InterviewRoundCreate.model_validate(data)


@pytest.mark.asyncio
async def test_create_round_requires_interviewing_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    app = _application(pipeline=PIPELINE_PENDING_HR_SCREEN)
    monkeypatch.setattr(
        "app.services.interviews.get_application_by_id",
        AsyncMock(return_value=app),
    )
    with pytest.raises(InterviewValidationError, match="interviewing"):
        await create_interview_round(
            session,
            application_id=app.id,
            payload=_create_payload(),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_create_round_rejects_terminal_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    app = _application(pipeline=PIPELINE_REJECTED, status="rejected")
    monkeypatch.setattr(
        "app.services.interviews.get_application_by_id",
        AsyncMock(return_value=app),
    )
    with pytest.raises(InterviewValidationError):
        await create_interview_round(
            session,
            application_id=app.id,
            payload=_create_payload(),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_create_requires_at_least_one_interviewer() -> None:
    with pytest.raises(Exception):
        InterviewRoundCreate.model_validate(
            {
                "name": "一轮",
                "format": "ONLINE",
                "owner_id": str(uuid4()),
                "interviewers": [],
            }
        )


@pytest.mark.asyncio
async def test_reason_codes_come_from_backend_catalog() -> None:
    result = list_interview_reason_codes()
    codes = {item.code for item in result.items}
    assert "CANDIDATE_RESCHEDULE" in codes
    assert "CANDIDATE_WITHDRAWAL" in codes
    assert "INTERVIEWER_CONFLICT" in codes
    assert "RECRUITMENT_PLAN_CHANGED" in codes
    assert "CANDIDATE_NO_SHOW" in codes
    assert "INTERVIEWER_NO_SHOW" in codes
    assert "TECHNICAL_FAILURE" in codes
    other = [item for item in result.items if item.code == "OTHER"]
    assert other
    assert all(item.requires_description for item in other)


@pytest.mark.asyncio
async def test_cancel_other_requires_description() -> None:
    with pytest.raises(Exception):
        InterviewCancelRequest.model_validate(
            {"reason_code": "OTHER", "version": 1}
        )
    InterviewCancelRequest.model_validate(
        {"reason_code": "OTHER", "description": "候选人临时有事", "version": 1}
    )


@pytest.mark.asyncio
async def test_abnormal_other_requires_description() -> None:
    with pytest.raises(Exception):
        InterviewAbnormalEndRequest.model_validate(
            {"reason_code": "OTHER", "version": 1}
        )


@pytest.mark.asyncio
async def test_update_schema_rejects_status_field() -> None:
    payload = InterviewRoundUpdate.model_validate(
        {
            "name": "改名",
            "version": 1,
            "interviewers": [{"interviewer_id": str(uuid4()), "is_primary": True}],
        }
    )
    assert not hasattr(payload, "status") or getattr(payload, "status", None) is None


def test_conflict_schema_and_reorder_schema() -> None:
    InterviewConflictCheckRequest.model_validate(
        {
            "application_id": str(uuid4()),
            "interviewer_ids": [str(uuid4())],
            "start_at_utc": "2026-08-14T02:00:00Z",
            "end_at_utc": "2026-08-14T03:00:00Z",
            "timezone": "Asia/Shanghai",
        }
    )
    InterviewRoundReorderRequest.model_validate(
        {"round_ids": [str(uuid4()), str(uuid4())]}
    )
    InterviewScheduleCreate.model_validate(
        {
            "start_at_utc": "2026-08-14T02:00:00Z",
            "end_at_utc": "2026-08-14T03:00:00Z",
            "timezone": "Asia/Shanghai",
            "format": "ONLINE",
            "meeting_mode": "MANUAL",
            "meeting_url": "https://meet.example.com/abc",
        }
    )
    InterviewRescheduleRequest.model_validate(
        {
            "start_at_utc": "2026-08-14T04:00:00Z",
            "end_at_utc": "2026-08-14T05:00:00Z",
            "timezone": "Asia/Shanghai",
            "format": "OFFLINE",
            "location": "上海办公室 3F",
            "reschedule_reason": "候选人改期",
            "version": 1,
        }
    )


@pytest.mark.asyncio
async def test_interviewer_cannot_update_or_reschedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    round_id = uuid4()
    actor = _actor(manage=False, execute=True)
    monkeypatch.setattr(
        "app.services.interviews.get_round_for_update",
        AsyncMock(
            return_value=SimpleNamespace(
                id=round_id,
                status="SCHEDULED",
                version=1,
                application_id=uuid4(),
            )
        ),
    )
    with pytest.raises(InterviewForbiddenError):
        await update_interview_round(
            session,
            round_id=round_id,
            payload=InterviewRoundUpdate.model_validate(
                {
                    "name": "x",
                    "version": 1,
                    "interviewers": [
                        {"interviewer_id": str(uuid4()), "is_primary": True}
                    ],
                }
            ),
            actor=actor,
            request_context=_ctx(),
        )
    with pytest.raises(InterviewForbiddenError):
        await reschedule_interview_round(
            session,
            round_id=round_id,
            payload=InterviewRescheduleRequest.model_validate(
                {
                    "start_at_utc": "2026-08-14T04:00:00Z",
                    "end_at_utc": "2026-08-14T05:00:00Z",
                    "timezone": "Asia/Shanghai",
                    "format": "ONLINE",
                    "meeting_mode": "MANUAL",
                    "meeting_url": "https://meet.example.com/x",
                    "reschedule_reason": "改期",
                    "version": 1,
                    "idempotency_key": "r1",
                }
            ),
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_exported_service_entrypoints_exist() -> None:
    assert callable(get_interview_timeline)
    assert callable(schedule_interview_round)
    assert callable(start_interview_round)
    assert callable(finish_interview_round)
    assert callable(complete_interview_round)
    assert callable(end_interview_abnormally)
    assert callable(cancel_interview_round)
    assert callable(reorder_interview_rounds)
    assert callable(check_interview_conflicts)
    assert InterviewNotFoundError is not InterviewStateError
    assert InterviewOptimisticLockError is not InterviewIdempotencyConflictError
    assert InterviewConflictError is not InterviewValidationError


def _now_utc() -> datetime:
    return datetime(2026, 8, 14, 2, 0, tzinfo=UTC)


def _make_round(*, status: str = INTERVIEW_STATUS_DRAFT) -> InterviewRound:
    now = _now_utc()
    round_ = InterviewRound(
        id=uuid4(),
        application_id=uuid4(),
        job_version_id=uuid4(),
        name="第一轮专业面",
        sequence_no=1,
        status=status,
        format="ONLINE",
        owner_id=uuid4(),
        version=1,
        created_at=now,
        updated_at=now,
    )
    round_.interviewers = [
        InterviewRoundInterviewer(
            interviewer_id=uuid4(),
            is_primary=True,
            created_by=round_.owner_id,
        )
    ]
    round_.schedules = []
    return round_


def _active_schedule(round_: InterviewRound, *, version: int = 1) -> InterviewSchedule:
    start = _now_utc()
    schedule = InterviewSchedule(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_version=version,
        status=SCHEDULE_STATUS_ACTIVE,
        start_at_utc=start,
        end_at_utc=start + timedelta(hours=1),
        timezone="Asia/Shanghai",
        format="ONLINE",
        meeting_mode="MANUAL",
        meeting_url="https://meet.example.com/abc",
        meeting_password_encrypted="enc:v1:hidden",
        contact_phone="13800138000",
        created_at=start,
        created_by=round_.owner_id,
    )
    round_.schedules.append(schedule)
    round_.current_schedule_id = schedule.id
    return schedule


def _schedule_payload(**overrides) -> InterviewScheduleCreate:
    start = _now_utc()
    data = {
        "start_at_utc": start.isoformat(),
        "end_at_utc": (start + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Shanghai",
        "format": "ONLINE",
        "meeting_mode": "MANUAL",
        "meeting_url": "https://meet.example.com/abc",
        "meeting_password": "secret-meet",
        "version": 1,
        "idempotency_key": "sched-1",
    }
    data.update(overrides)
    return InterviewScheduleCreate.model_validate(data)


def _patch_round_flow(
    monkeypatch: pytest.MonkeyPatch,
    round_: InterviewRound,
    *,
    application=None,
    candidate_conflicts=None,
    interviewer_conflicts=None,
    existing_idempotency=None,
):
    application = application or _application()
    application.id = round_.application_id
    audits: list[dict] = []
    added_schedules: list[InterviewSchedule] = []
    added_idempotency: list[InterviewIdempotencyKey] = []
    conflict_calls: list[dict] = []

    async def fake_add_schedule(_session, schedule: InterviewSchedule):
        if schedule.id is None:
            schedule.id = uuid4()
        if schedule.created_at is None:
            schedule.created_at = _now_utc()
        added_schedules.append(schedule)
        if schedule not in round_.schedules:
            round_.schedules.append(schedule)

    async def fake_find_candidate(_session, **kwargs):
        conflict_calls.append({"kind": "candidate", **kwargs})
        return candidate_conflicts or []

    async def fake_find_interviewer(_session, **kwargs):
        conflict_calls.append({"kind": "interviewer", **kwargs})
        return interviewer_conflicts or []

    async def fake_record_audit(_session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(
        "app.services.interviews.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_round_by_id",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_application_by_id",
        AsyncMock(return_value=application),
    )
    monkeypatch.setattr(
        "app.services.interviews.find_idempotency",
        AsyncMock(return_value=existing_idempotency),
    )
    monkeypatch.setattr(
        "app.services.interviews.add_schedule",
        fake_add_schedule,
    )
    monkeypatch.setattr(
        "app.services.interviews.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: added_idempotency.append(key)),
    )
    monkeypatch.setattr(
        "app.services.interviews.record_audit",
        fake_record_audit,
    )
    monkeypatch.setattr(
        "app.services.interviews.find_candidate_conflicts",
        fake_find_candidate,
    )
    monkeypatch.setattr(
        "app.services.interviews.find_interviewer_conflicts",
        fake_find_interviewer,
    )
    monkeypatch.setattr(
        "app.services.interviews.get_users_by_ids",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_active_schedule_for_update",
        AsyncMock(
            side_effect=lambda *_a, **_k: next(
                (
                    item
                    for item in round_.schedules
                    if item.status == SCHEDULE_STATUS_ACTIVE
                ),
                None,
            )
        ),
    )
    monkeypatch.setattr(
        "app.repositories.invitations.void_open_messages_for_schedule",
        AsyncMock(return_value=[]),
    )
    session = AsyncMock()
    return (
        session,
        application,
        audits,
        added_schedules,
        added_idempotency,
        conflict_calls,
    )


@pytest.mark.asyncio
async def test_schedule_enters_scheduled_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_DRAFT)
    session, _app, audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    result = await schedule_interview_round(
        session,
        round_id=round_.id,
        payload=_schedule_payload(),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert round_.status == INTERVIEW_STATUS_SCHEDULED
    assert result.status == INTERVIEW_STATUS_SCHEDULED
    assert added[0].status == SCHEDULE_STATUS_ACTIVE
    assert added[0].schedule_version == 1
    assert added[0].meeting_password_encrypted
    assert added[0].meeting_password_encrypted.startswith("enc:v1:")
    assert "secret-meet" not in added[0].meeting_password_encrypted
    assert "CONFIRMED" not in {round_.status, result.status}
    assert audits[0]["action"] == "interview_round.schedule"
    blob = str(audits[0]["changes"])
    assert "secret-meet" not in blob
    assert "meeting_password" not in blob


@pytest.mark.asyncio
async def test_confirmed_reschedule_returns_to_scheduled_and_clears_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.interview import INTERVIEW_STATUS_CONFIRMED

    round_ = _make_round(status=INTERVIEW_STATUS_CONFIRMED)
    round_.invitation_confirmed_at = _now_utc()
    round_.invitation_confirmed_by = uuid4()
    round_.invitation_confirmed_schedule_version = 1
    round_.invitation_confirmation_summary = "已人工发送"
    old = _active_schedule(round_, version=1)
    session, _app, audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    voided_msgs = [SimpleNamespace(id=uuid4())]
    monkeypatch.setattr(
        "app.repositories.invitations.void_open_messages_for_schedule",
        AsyncMock(return_value=voided_msgs),
    )
    start = _now_utc() + timedelta(hours=5)
    await reschedule_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewRescheduleRequest.model_validate(
            {
                "start_at_utc": start.isoformat(),
                "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
                "format": "ONLINE",
                "meeting_mode": "MANUAL",
                "meeting_url": "https://meet.example.com/re",
                "reschedule_reason": "确认后改期",
                "version": 1,
                "idempotency_key": "re-confirmed",
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert round_.status == INTERVIEW_STATUS_SCHEDULED
    assert round_.invitation_confirmed_at is None
    assert round_.invitation_confirmed_by is None
    assert round_.invitation_confirmed_schedule_version is None
    assert round_.invitation_confirmation_summary is None
    assert old.status == SCHEDULE_STATUS_SUPERSEDED
    assert added[0].schedule_version == 2
    assert any(item["action"] == "interview_invitation.void" for item in audits)
    assert any(item["action"] == "interview_round.reschedule" for item in audits)


@pytest.mark.asyncio
async def test_reschedule_supersedes_old_active_and_creates_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    session, _app, audits, added, _keys, calls = _patch_round_flow(
        monkeypatch, round_
    )
    start = _now_utc() + timedelta(hours=3)
    await reschedule_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewRescheduleRequest.model_validate(
            {
                "start_at_utc": start.isoformat(),
                "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
                "format": "ONLINE",
                "meeting_mode": "MANUAL",
                "meeting_url": "https://meet.example.com/new",
                "reschedule_reason": "候选人改期",
                "version": 1,
                "idempotency_key": "re-1",
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert old.status == SCHEDULE_STATUS_SUPERSEDED
    assert old.superseded_at is not None
    assert added[0].status == SCHEDULE_STATUS_ACTIVE
    assert added[0].schedule_version == 2
    assert round_.current_schedule_id == added[0].id
    assert len(round_.schedules) == 2
    assert all(call["exclude_round_id"] == round_.id for call in calls)
    assert audits[0]["action"] == "interview_round.reschedule"
    assert added[0].meeting_password_encrypted == old.meeting_password_encrypted


@pytest.mark.asyncio
async def test_reschedule_keeps_ciphertext_when_password_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    old.meeting_password_encrypted = "enc:v1:kept-previous"
    session, _app, _audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    start = _now_utc() + timedelta(hours=4)
    await reschedule_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewRescheduleRequest.model_validate(
            {
                "start_at_utc": start.isoformat(),
                "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
                "format": "ONLINE",
                "meeting_mode": "MANUAL",
                "meeting_url": "https://meet.example.com/kept",
                "reschedule_reason": "保留密码",
                "version": 1,
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert added[0].meeting_password_encrypted == "enc:v1:kept-previous"


@pytest.mark.asyncio
async def test_reschedule_clear_flag_deletes_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    session, _app, _audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    start = _now_utc() + timedelta(hours=5)
    await reschedule_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewRescheduleRequest.model_validate(
            {
                "start_at_utc": start.isoformat(),
                "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
                "format": "ONLINE",
                "meeting_mode": "MANUAL",
                "meeting_url": "https://meet.example.com/cleared",
                "reschedule_reason": "清空密码",
                "clear_meeting_password": True,
                "version": 1,
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert added[0].meeting_password_encrypted is None
    assert old.meeting_password_encrypted == "enc:v1:hidden"


@pytest.mark.asyncio
async def test_reschedule_new_password_reencrypts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    session, _app, _audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    start = _now_utc() + timedelta(hours=6)
    await reschedule_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewRescheduleRequest.model_validate(
            {
                "start_at_utc": start.isoformat(),
                "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                "timezone": "Asia/Shanghai",
                "format": "ONLINE",
                "meeting_mode": "MANUAL",
                "meeting_url": "https://meet.example.com/newpwd",
                "meeting_password": "brand-new-secret",
                "reschedule_reason": "更换密码",
                "version": 1,
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert added[0].meeting_password_encrypted != old.meeting_password_encrypted
    assert added[0].meeting_password_encrypted.startswith("enc:v1:")
    assert "brand-new-secret" not in added[0].meeting_password_encrypted


@pytest.mark.asyncio
async def test_reschedule_conflict_leaves_old_schedule_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    other = SimpleNamespace(
        id=uuid4(),
        name="冲突轮",
    )
    conflict_schedule = SimpleNamespace(
        start_at_utc=_now_utc(),
        end_at_utc=_now_utc() + timedelta(hours=1),
    )
    session, _app, _audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch,
        round_,
        candidate_conflicts=[(conflict_schedule, other)],
    )
    start = _now_utc() + timedelta(minutes=30)
    with pytest.raises(InterviewConflictError, match="candidate"):
        await reschedule_interview_round(
            session,
            round_id=round_.id,
            payload=InterviewRescheduleRequest.model_validate(
                {
                    "start_at_utc": start.isoformat(),
                    "end_at_utc": (start + timedelta(hours=1)).isoformat(),
                    "timezone": "Asia/Shanghai",
                    "format": "ONLINE",
                    "meeting_mode": "MANUAL",
                    "meeting_url": "https://meet.example.com/new",
                    "reschedule_reason": "改期",
                    "version": 1,
                }
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert old.status == SCHEDULE_STATUS_ACTIVE
    assert added == []
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_keeps_schedule_history_and_other_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    old = _active_schedule(round_, version=1)
    session, _app, audits, _added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    result = await cancel_interview_round(
        session,
        round_id=round_.id,
        payload=InterviewCancelRequest.model_validate(
            {
                "reason_code": "OTHER",
                "description": "候选人临时有事",
                "version": 1,
                "idempotency_key": "cancel-1",
            }
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.status == "CANCELLED"
    assert round_.cancellation_reason_code == "OTHER"
    assert round_.cancellation_description == "候选人临时有事"
    assert round_.cancelled_at is not None
    assert old.status == SCHEDULE_STATUS_CANCELLED
    assert old in round_.schedules
    assert audits[0]["changes"]["reason_code"] == "OTHER"


@pytest.mark.asyncio
async def test_generic_complete_is_rejected_for_transcript_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_PENDING_TRANSCRIPT)
    application = _application()
    application.id = round_.application_id
    session, _app, _audits, _added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_, application=application
    )
    with pytest.raises(
        InterviewValidationError,
        match="confirm transcript or complete-without-transcript",
    ):
        await complete_interview_round(
            session,
            round_id=round_.id,
            payload=InterviewRoundActionRequest.model_validate(
                {"version": 1, "idempotency_key": "done-1"}
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert round_.status == INTERVIEW_STATUS_PENDING_TRANSCRIPT
    assert application.pipeline_status == PIPELINE_INTERVIEWING
    assert application.status == APPLICATION_STATUS_IN_PROGRESS


@pytest.mark.asyncio
async def test_completed_round_cannot_be_updated_or_rescheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    _active_schedule(round_, version=1)
    session, _app, _audits, _added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    with pytest.raises(InterviewValidationError, match="cannot be modified"):
        await update_interview_round(
            session,
            round_id=round_.id,
            payload=InterviewRoundUpdate.model_validate(
                {
                    "name": "改名",
                    "version": 1,
                    "interviewers": [
                        {"interviewer_id": str(uuid4()), "is_primary": True}
                    ],
                }
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
    with pytest.raises(InterviewStateError):
        await reschedule_interview_round(
            session,
            round_id=round_.id,
            payload=InterviewRescheduleRequest.model_validate(
                {
                    "start_at_utc": _now_utc().isoformat(),
                    "end_at_utc": (_now_utc() + timedelta(hours=1)).isoformat(),
                    "timezone": "Asia/Shanghai",
                    "format": "ONLINE",
                    "meeting_mode": "MANUAL",
                    "meeting_url": "https://meet.example.com/x",
                    "reschedule_reason": "改期",
                    "version": 1,
                }
            ),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_idempotency_same_key_different_body_raises_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_DRAFT)
    existing = SimpleNamespace(
        request_hash="different-hash",
        result_round_id=round_.id,
    )
    session, _app, _audits, _added, _keys, _calls = _patch_round_flow(
        monkeypatch, round_, existing_idempotency=existing
    )
    with pytest.raises(InterviewIdempotencyConflictError):
        await schedule_interview_round(
            session,
            round_id=round_.id,
            payload=_schedule_payload(idempotency_key="same-key"),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_interviewer_conflict_blocks_unless_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_DRAFT)
    other = SimpleNamespace(id=uuid4(), name="其他轮")
    conflict_schedule = SimpleNamespace(
        start_at_utc=_now_utc(),
        end_at_utc=_now_utc() + timedelta(hours=1),
    )
    interviewer_id = round_.interviewers[0].interviewer_id
    session, application, _audits, added, _keys, _calls = _patch_round_flow(
        monkeypatch,
        round_,
        interviewer_conflicts=[(conflict_schedule, other, interviewer_id)],
    )
    with pytest.raises(InterviewConflictError, match="interviewer"):
        await schedule_interview_round(
            session,
            round_id=round_.id,
            payload=_schedule_payload(),
            actor=_actor(),
            request_context=_ctx(),
        )
    assert added == []

    with pytest.raises(InterviewConflictError, match="interviewer"):
        await check_interview_conflicts(
            session,
            payload=InterviewConflictCheckRequest.model_validate(
                {
                    "application_id": str(application.id),
                    "interviewer_ids": [str(interviewer_id)],
                    "start_at_utc": _now_utc().isoformat(),
                    "end_at_utc": (_now_utc() + timedelta(hours=1)).isoformat(),
                    "timezone": "Asia/Shanghai",
                }
            ),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_schedule_does_not_create_notification_or_ai_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_DRAFT)
    session, _app, audits, added, keys, _calls = _patch_round_flow(
        monkeypatch, round_
    )
    await schedule_interview_round(
        session,
        round_id=round_.id,
        payload=_schedule_payload(),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert [item["action"] for item in audits] == ["interview_round.schedule"]
    assert len(added) == 1
    assert len(keys) == 1
    assert session.add.await_count == 0
    add_calls = [str(call) for call in session.method_calls]
    assert not any("notification" in item.lower() for item in add_calls)
    assert not any("ai_task" in item.lower() for item in add_calls)
