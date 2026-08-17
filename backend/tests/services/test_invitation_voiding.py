"""Database-state assertions for invitation voiding on reschedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.interview import (
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_SCHEDULED,
    SCHEDULE_STATUS_ACTIVE,
    InterviewRound,
    InterviewSchedule,
)
from app.models.invitation import (
    INVITATION_AUDIENCE_CANDIDATE,
    INVITATION_EVENT_INITIAL,
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
    InterviewInvitationMessage,
    InterviewInvitationSendRecord,
    InterviewInvitationVersion,
)
from app.repositories.invitations import void_open_messages_for_schedule
from app.schemas.interview import InterviewRescheduleRequest
from app.services.audit import RequestContext
from app.services.interviews import reschedule_interview_round


def _now() -> datetime:
    return datetime.now(UTC)


def _actor():
    return SimpleNamespace(
        id=uuid4(),
        username="hr",
        display_name="HR",
        permission_codes=["recruitment.manage", "interview.execute"],
    )


@pytest.mark.asyncio
async def test_void_open_messages_voids_draft_ready_and_recorded_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule_id = uuid4()
    draft = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=uuid4(),
        schedule_id=schedule_id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c1",
        recipient_name="张三",
        status=INVITATION_STATUS_DRAFT,
        version=1,
    )
    ready = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=draft.interview_round_id,
        schedule_id=schedule_id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c2",
        recipient_name="李四",
        status=INVITATION_STATUS_READY,
        version=1,
    )
    recorded = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=draft.interview_round_id,
        schedule_id=schedule_id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c3",
        recipient_name="王五",
        status=INVITATION_STATUS_RECORDED_SENT,
        version=1,
    )
    version = InterviewInvitationVersion(
        id=uuid4(),
        message_id=recorded.id,
        version_no=1,
        subject_encrypted="enc:v1:x",
        body_html_encrypted="enc:v1:y",
        body_text_encrypted="enc:v1:z",
        template_code="candidate_initial",
        template_version="1",
        content_hash="h",
    )
    send_record = InterviewInvitationSendRecord(
        id=uuid4(),
        message_id=recorded.id,
        message_version_id=version.id,
        recorded_by=uuid4(),
        sent_at=_now(),
        channel_type="CORPORATE_EMAIL",
    )
    recorded.current_version_id = version.id

    class _Result:
        def all(self):
            return [draft, ready, recorded]

    session = AsyncMock()
    session.scalars = AsyncMock(return_value=_Result())
    voided = await void_open_messages_for_schedule(session, schedule_id)
    assert {item.status for item in voided} == {INVITATION_STATUS_VOIDED}
    assert draft.status == INVITATION_STATUS_VOIDED
    assert ready.status == INVITATION_STATUS_VOIDED
    assert recorded.status == INVITATION_STATUS_VOIDED
    assert send_record.message_version_id == version.id
    assert version.message_id == recorded.id
    assert recorded.current_version_id == version.id


@pytest.mark.asyncio
async def test_confirmed_reschedule_voids_old_schedule_mails_and_keeps_new_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_id = uuid4()
    old_schedule = InterviewSchedule(
        id=uuid4(),
        interview_round_id=round_id,
        schedule_version=1,
        status=SCHEDULE_STATUS_ACTIVE,
        start_at_utc=_now() + timedelta(days=1),
        end_at_utc=_now() + timedelta(days=1, hours=1),
        timezone="Asia/Shanghai",
        format="ONLINE",
        meeting_mode="MANUAL",
        meeting_url="https://meet.example.com/old",
        created_at=_now(),
        created_by=uuid4(),
    )
    round_ = InterviewRound(
        id=round_id,
        application_id=uuid4(),
        job_version_id=uuid4(),
        name="技术一面",
        sequence_no=1,
        status=INTERVIEW_STATUS_CONFIRMED,
        format="ONLINE",
        owner_id=uuid4(),
        current_schedule_id=old_schedule.id,
        version=2,
        invitation_confirmed_at=_now(),
        invitation_confirmed_by=uuid4(),
        invitation_confirmed_schedule_version=1,
        invitation_confirmation_summary="已发送",
        created_at=_now(),
        updated_at=_now(),
    )
    round_.interviewers = []
    round_.schedules = [old_schedule]

    old_ready = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_id,
        schedule_id=old_schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="cand",
        recipient_name="张三",
        status=INVITATION_STATUS_READY,
        version=1,
    )
    old_recorded = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_id,
        schedule_id=old_schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="cand2",
        recipient_name="李四",
        status=INVITATION_STATUS_RECORDED_SENT,
        version=1,
    )
    old_version = InterviewInvitationVersion(
        id=uuid4(),
        message_id=old_recorded.id,
        version_no=1,
        subject_encrypted="enc:v1:s",
        body_html_encrypted="enc:v1:h",
        body_text_encrypted="enc:v1:t",
        template_code="candidate_initial",
        template_version="1",
        content_hash="hash",
    )
    old_recorded.current_version_id = old_version.id
    old_send = InterviewInvitationSendRecord(
        id=uuid4(),
        message_id=old_recorded.id,
        message_version_id=old_version.id,
        recorded_by=uuid4(),
        sent_at=_now(),
        channel_type="CORPORATE_EMAIL",
    )

    async def _void(_session, schedule_id):
        assert schedule_id == old_schedule.id
        for message in (old_ready, old_recorded):
            message.status = INVITATION_STATUS_VOIDED
        return [old_ready, old_recorded]

    added_schedules: list[InterviewSchedule] = []

    async def _add_schedule(_session, schedule: InterviewSchedule):
        schedule.id = uuid4()
        schedule.created_at = _now()
        added_schedules.append(schedule)
        round_.schedules.append(schedule)
        return schedule

    monkeypatch.setattr(
        "app.services.interviews.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_round_by_id",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.interviews.find_idempotency",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.interviews.add_idempotency",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_active_schedule_for_update",
        AsyncMock(return_value=old_schedule),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_application_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id=round_.application_id,
                candidate_id=uuid4(),
                job_id=uuid4(),
                job_version_id=round_.job_version_id,
                pipeline_status="interviewing",
                status="in_progress",
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.interviews.find_candidate_conflicts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.interviews.find_interviewer_conflicts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.interviews.add_schedule",
        _add_schedule,
    )
    monkeypatch.setattr(
        "app.services.interviews.record_audit",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.interviews.get_users_by_ids",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.repositories.invitations.void_open_messages_for_schedule",
        _void,
    )

    session = AsyncMock()
    start = _now() + timedelta(hours=8)
    result = await reschedule_interview_round(
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
                "reschedule_reason": "确认后改期",
                "version": 2,
                "idempotency_key": "void-db-1",
            }
        ),
        actor=_actor(),
        request_context=RequestContext(request_id="req", ip_address="127.0.0.1"),
    )

    assert result.status == INTERVIEW_STATUS_SCHEDULED
    assert round_.status == INTERVIEW_STATUS_SCHEDULED
    assert round_.invitation_confirmed_at is None
    assert round_.invitation_confirmed_by is None
    assert round_.invitation_confirmed_schedule_version is None
    assert round_.invitation_confirmation_summary is None
    assert old_ready.status == INVITATION_STATUS_VOIDED
    assert old_recorded.status == INVITATION_STATUS_VOIDED
    assert old_version.id == old_recorded.current_version_id
    assert old_send.message_version_id == old_version.id
    assert added_schedules[0].schedule_version == 2
    assert added_schedules[0].id != old_schedule.id
    # New arrangement mails are not auto-created here; old logic mails stay on old schedule_id.
    assert old_ready.schedule_id == old_schedule.id
    assert old_recorded.schedule_id == old_schedule.id
