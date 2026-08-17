"""Service tests for manual invitation workflow."""

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
    InterviewRoundInterviewer,
    InterviewSchedule,
)
from app.models.invitation import (
    INVITATION_AUDIENCE_CANDIDATE,
    INVITATION_AUDIENCE_INTERVIEWER,
    INVITATION_EVENT_INITIAL,
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
    InterviewInvitationMessage,
    InterviewInvitationVersion,
)
from app.schemas.invitation import (
    ConfirmInvitationRequest,
    CopyAuditRequest,
    GenerateInvitationsRequest,
    RecordSentRequest,
    UpdateInvitationMessageRequest,
)
from app.services.audit import RequestContext
from app.services.crypto import encrypt_secret
from app.services.interviews import (
    InterviewOptimisticLockError,
    InterviewValidationError,
)
from app.services.invitations import (
    audit_copy,
    confirm_invitation,
    generate_invitations,
    record_sent,
    update_invitation,
)


def _actor(*, manage: bool = True, execute: bool = True, user_id=None):
    user = SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        display_name="HR管理员",
        email="hr@example.com",
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
    return RequestContext(request_id="req-invite-1", ip_address="127.0.0.1")


def _make_round(*, status: str = INTERVIEW_STATUS_SCHEDULED, version: int = 1):
    round_id = uuid4()
    interviewer_a = uuid4()
    interviewer_b = uuid4()
    schedule = InterviewSchedule(
        id=uuid4(),
        interview_round_id=round_id,
        schedule_version=1,
        status=SCHEDULE_STATUS_ACTIVE,
        start_at_utc=datetime.now(UTC) + timedelta(days=1),
        end_at_utc=datetime.now(UTC) + timedelta(days=1, hours=1),
        timezone="Asia/Shanghai",
        format="ONLINE",
        meeting_mode="MANUAL",
        meeting_url="https://meet.example.com/x",
        meeting_no="10086",
        meeting_password_encrypted=encrypt_secret("pw-secret"),
        location=None,
        contact_name=None,
        contact_phone=None,
        reschedule_reason=None,
        created_by=uuid4(),
    )
    round_ = InterviewRound(
        id=round_id,
        application_id=uuid4(),
        job_version_id=uuid4(),
        name="技术一面",
        sequence_no=1,
        status=status,
        format="ONLINE",
        owner_id=uuid4(),
        current_schedule_id=schedule.id,
        version=version,
    )
    round_.interviewers = [
        InterviewRoundInterviewer(
            interview_round_id=round_id,
            interviewer_id=interviewer_a,
            is_primary=True,
        ),
        InterviewRoundInterviewer(
            interview_round_id=round_id,
            interviewer_id=interviewer_b,
            is_primary=False,
        ),
    ]
    round_.schedules = [schedule]
    return round_, schedule, interviewer_a, interviewer_b


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, round_, schedule, users, app):
    added_messages: list[InterviewInvitationMessage] = []
    added_versions: list[InterviewInvitationVersion] = []
    audits: list[dict] = []

    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_by_id",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.invitations.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: key),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_application_by_id",
        AsyncMock(return_value=app),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_job_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(), name="后端工程师", versions=[]
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version_by_id",
        lambda _job, _vid: SimpleNamespace(version_label="v2"),
    )
    monkeypatch.setattr(
        "app.services.invitations._load_users",
        AsyncMock(return_value=users),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_message",
        AsyncMock(return_value=None),
    )

    async def _add_message(_s, message):
        added_messages.append(message)
        return message

    async def _add_version(_s, version):
        added_versions.append(version)
        return version

    async def _next_version_no(_s, message_id):
        return 1 + sum(1 for item in added_versions if item.message_id == message_id)

    async def _get_version(_s, version_id):
        for item in added_versions:
            if item.id == version_id:
                return item
        return None

    monkeypatch.setattr(
        "app.services.invitations.add_message", AsyncMock(side_effect=_add_message)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_version", AsyncMock(side_effect=_add_version)
    )
    monkeypatch.setattr(
        "app.services.invitations.next_version_no",
        AsyncMock(side_effect=_next_version_no),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version", AsyncMock(side_effect=_get_version)
    )
    monkeypatch.setattr(
        "app.services.invitations.record_audit",
        AsyncMock(side_effect=lambda *_a, **kwargs: audits.append(kwargs)),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_round",
        AsyncMock(side_effect=lambda *_a, **_k: list(added_messages)),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_schedule",
        AsyncMock(side_effect=lambda *_a, **_k: list(added_messages)),
    )
    return added_messages, added_versions, audits


@pytest.mark.asyncio
async def test_generate_candidate_and_interviewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, a_id, b_id = _make_round()
    candidate_id = uuid4()
    app = SimpleNamespace(
        id=round_.application_id,
        candidate_id=candidate_id,
        job_id=uuid4(),
        job_version_id=round_.job_version_id,
        candidate=SimpleNamespace(
            id=candidate_id, name="张三", email="zhangsan@example.com"
        ),
    )
    users = {
        round_.owner_id: SimpleNamespace(
            id=round_.owner_id, display_name="李招聘", email="owner@example.com"
        ),
        a_id: SimpleNamespace(
            id=a_id, display_name="王面试", email="wang@example.com"
        ),
        b_id: SimpleNamespace(
            id=b_id, display_name="赵面试", email="zhao@example.com"
        ),
    }
    added, versions, audits = _patch_common(
        monkeypatch, round_=round_, schedule=schedule, users=users, app=app
    )
    session = AsyncMock()
    result = await generate_invitations(
        session,
        round_id=round_.id,
        payload=GenerateInvitationsRequest(idempotency_key="gen-1"),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert len(result.items) == 3
    assert len(added) == 3
    assert {item.audience_type for item in added} == {
        INVITATION_AUDIENCE_CANDIDATE,
        INVITATION_AUDIENCE_INTERVIEWER,
    }
    assert all(item.status == INVITATION_STATUS_READY for item in added)
    assert all(item.event_type == INVITATION_EVENT_INITIAL for item in added)
    assert len(versions) == 3
    assert all(v.subject_encrypted.startswith("enc:v1:") for v in versions)
    assert all(v.body_html_encrypted.startswith("enc:v1:") for v in versions)
    assert any(a["action"] == "interview_invitation.generate" for a in audits)
    for audit in audits:
        dumped = str(audit)
        assert "pw-secret" not in dumped
        assert "enc:v1:" not in dumped or "content_hash" in dumped
        assert "zhangsan@example.com" not in dumped


@pytest.mark.asyncio
async def test_generate_missing_email_stays_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, a_id, b_id = _make_round()
    candidate_id = uuid4()
    app = SimpleNamespace(
        id=round_.application_id,
        candidate_id=candidate_id,
        job_id=uuid4(),
        job_version_id=round_.job_version_id,
        candidate=SimpleNamespace(id=candidate_id, name="张三", email=None),
    )
    users = {
        round_.owner_id: SimpleNamespace(
            id=round_.owner_id, display_name="李招聘", email=None
        ),
        a_id: SimpleNamespace(id=a_id, display_name="王面试", email=None),
        b_id: SimpleNamespace(id=b_id, display_name="赵面试", email=None),
    }
    added, _versions, _audits = _patch_common(
        monkeypatch, round_=round_, schedule=schedule, users=users, app=app
    )
    session = AsyncMock()
    await generate_invitations(
        session,
        round_id=round_.id,
        payload=GenerateInvitationsRequest(idempotency_key="gen-draft"),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert all(item.status == INVITATION_STATUS_DRAFT for item in added)


@pytest.mark.asyncio
async def test_generate_idempotent_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, a_id, b_id = _make_round()
    existing_key = SimpleNamespace(
        request_hash="x",
        result_round_id=round_.id,
        id=uuid4(),
    )
    # Force hash match by patching _canonical_hash and find
    monkeypatch.setattr(
        "app.services.invitations._canonical_hash", lambda _p: "same-hash"
    )
    existing_key.request_hash = "same-hash"
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency",
        AsyncMock(return_value=existing_key),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_round",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    session = AsyncMock()
    result = await generate_invitations(
        session,
        round_id=round_.id,
        payload=GenerateInvitationsRequest(idempotency_key="gen-1"),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.items == []
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_creates_new_version_and_recorded_returns_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = uuid4()
    version_id = uuid4()
    message = InterviewInvitationMessage(
        id=message_id,
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key=str(uuid4()),
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_RECORDED_SENT,
        current_version_id=version_id,
        version=1,
    )
    old_version = InterviewInvitationVersion(
        id=version_id,
        message_id=message_id,
        version_no=1,
        subject_encrypted=encrypt_secret("old"),
        body_html_encrypted=encrypt_secret("<p>old</p>"),
        body_text_encrypted=encrypt_secret("old"),
        template_code="candidate_initial",
        template_version="1",
        content_hash="abc",
    )
    versions = [old_version]

    async def _add_version(_s, version):
        versions.append(version)
        return version

    async def _get_version(_s, vid):
        return next((item for item in versions if item.id == vid), None)

    monkeypatch.setattr(
        "app.services.invitations.get_message_for_update",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_message_by_id",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.next_version_no", AsyncMock(return_value=2)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_version", AsyncMock(side_effect=_add_version)
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version", AsyncMock(side_effect=_get_version)
    )
    monkeypatch.setattr("app.services.invitations.record_audit", AsyncMock())
    session = AsyncMock()
    result = await update_invitation(
        session,
        message_id=message_id,
        payload=UpdateInvitationMessageRequest(
            version=1,
            subject="新主题",
            body_html="<p>新正文</p><script>alert(1)</script>",
            body_text="新正文",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert message.status == INVITATION_STATUS_READY
    assert message.version == 2
    assert len(versions) == 2
    assert versions[1].version_no == 2
    assert "<script" not in result.body_html.lower()
    assert result.subject == "新主题"
    assert "enc:v1:" not in result.subject
    assert "enc:v1:" not in result.body_html


@pytest.mark.asyncio
async def test_copy_does_not_change_status(monkeypatch: pytest.MonkeyPatch) -> None:
    message = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c1",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=uuid4(),
        version=1,
    )
    monkeypatch.setattr(
        "app.services.invitations.get_message_by_id",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("app.services.invitations.record_audit", AsyncMock())
    session = AsyncMock()
    await audit_copy(
        session,
        message_id=message.id,
        payload=CopyAuditRequest(copy_type="SUBJECT"),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert message.status == INVITATION_STATUS_READY


@pytest.mark.asyncio
async def test_record_sent_sets_recorded_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = uuid4()
    version_id = uuid4()
    message = InterviewInvitationMessage(
        id=message_id,
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_INTERVIEWER,
        recipient_user_id=uuid4(),
        recipient_key="u1",
        recipient_name="王面试",
        recipient_email_masked="w***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=version_id,
        version=1,
    )
    version = InterviewInvitationVersion(
        id=version_id,
        message_id=message_id,
        version_no=1,
        subject_encrypted=encrypt_secret("s"),
        body_html_encrypted=encrypt_secret("<p>b</p>"),
        body_text_encrypted=encrypt_secret("b"),
        template_code="interviewer_initial",
        template_version="1",
        content_hash="h",
    )
    monkeypatch.setattr(
        "app.services.invitations.get_message_for_update",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: key),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version", AsyncMock(return_value=version)
    )
    monkeypatch.setattr("app.services.invitations.add_send_record", AsyncMock())
    monkeypatch.setattr("app.services.invitations.record_audit", AsyncMock())
    session = AsyncMock()
    result = await record_sent(
        session,
        message_id=message_id,
        payload=RecordSentRequest(
            sent_at=datetime.now(UTC),
            message_version_id=version_id,
            channel_type="CORPORATE_EMAIL",
            channel_note="outlook",
            idempotency_key="sent-1",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.status == INVITATION_STATUS_RECORDED_SENT
    assert message.status == INVITATION_STATUS_RECORDED_SENT
    assert "DELIVERED" not in result.status
    assert "SEND_SUCCESS" not in result.status


@pytest.mark.asyncio
async def test_confirm_requires_send_summary_when_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, _a, _b = _make_round()
    message = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_id=schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_READY,
        version=1,
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_schedule",
        AsyncMock(return_value=[message]),
    )
    session = AsyncMock()
    with pytest.raises(InterviewValidationError, match="send_summary"):
        await confirm_invitation(
            session,
            round_id=round_.id,
            payload=ConfirmInvitationRequest(
                schedule_version=1,
                version=1,
                send_summary=None,
                idempotency_key="confirm-1",
            ),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_confirm_scheduled_to_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, _a, _b = _make_round()
    message = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_id=schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_RECORDED_SENT,
        version=1,
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: key),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_schedule",
        AsyncMock(return_value=[message]),
    )
    monkeypatch.setattr("app.services.invitations.record_audit", AsyncMock())
    session = AsyncMock()
    result = await confirm_invitation(
        session,
        round_id=round_.id,
        payload=ConfirmInvitationRequest(
            schedule_version=1,
            version=1,
            send_summary=None,
            idempotency_key="confirm-2",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.status == INTERVIEW_STATUS_CONFIRMED
    assert round_.status == INTERVIEW_STATUS_CONFIRMED
    assert round_.invitation_confirmed_schedule_version == 1
    assert round_.invitation_confirmed_by is not None


@pytest.mark.asyncio
async def test_confirm_persists_schedule_version_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, _a, _b = _make_round()
    message = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_id=schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_READY,
        version=1,
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: key),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_schedule",
        AsyncMock(return_value=[message]),
    )
    audits: list[dict] = []
    monkeypatch.setattr(
        "app.services.invitations.record_audit",
        AsyncMock(side_effect=lambda *_a, **kwargs: audits.append(kwargs)),
    )
    session = AsyncMock()
    result = await confirm_invitation(
        session,
        round_id=round_.id,
        payload=ConfirmInvitationRequest(
            schedule_version=1,
            version=1,
            send_summary="候选人与面试官均已通过企业邮箱人工发送",
            idempotency_key="confirm-summary",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.schedule_version == 1
    assert round_.invitation_confirmed_schedule_version == 1
    assert (
        round_.invitation_confirmation_summary
        == "候选人与面试官均已通过企业邮箱人工发送"
    )
    assert result.confirmation_summary == round_.invitation_confirmation_summary
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_confirm_idempotent_same_key_does_not_repeat_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_, schedule, _a, _b = _make_round(status=INTERVIEW_STATUS_CONFIRMED, version=2)
    round_.invitation_confirmed_at = datetime.now(UTC)
    round_.invitation_confirmed_by = uuid4()
    round_.invitation_confirmed_schedule_version = 1
    round_.invitation_confirmation_summary = "已发送"
    existing = SimpleNamespace(
        request_hash="same",
        result_round_id=round_.id,
        id=uuid4(),
    )
    monkeypatch.setattr(
        "app.services.invitations._canonical_hash", lambda _p: "same"
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_by_id",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency",
        AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(
        "app.services.invitations._load_users",
        AsyncMock(
            return_value={
                round_.invitation_confirmed_by: SimpleNamespace(
                    id=round_.invitation_confirmed_by, display_name="HR"
                )
            }
        ),
    )
    audit = AsyncMock()
    monkeypatch.setattr("app.services.invitations.record_audit", audit)
    session = AsyncMock()
    before_version = round_.version
    result = await confirm_invitation(
        session,
        round_id=round_.id,
        payload=ConfirmInvitationRequest(
            schedule_version=1,
            version=2,
            send_summary="已发送",
            idempotency_key="confirm-idem",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert result.status == INTERVIEW_STATUS_CONFIRMED
    assert round_.version == before_version
    audit.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_record_sent_keeps_old_version_after_reedit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = uuid4()
    old_version_id = uuid4()
    message = InterviewInvitationMessage(
        id=message_id,
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=old_version_id,
        version=1,
    )
    old_version = InterviewInvitationVersion(
        id=old_version_id,
        message_id=message_id,
        version_no=1,
        subject_encrypted=encrypt_secret("old"),
        body_html_encrypted=encrypt_secret("<p>old</p>"),
        body_text_encrypted=encrypt_secret("old"),
        template_code="candidate_initial",
        template_version="1",
        content_hash="h1",
    )
    versions = [old_version]
    send_records: list = []

    async def _add_version(_s, version):
        versions.append(version)
        return version

    async def _get_version(_s, vid):
        return next((item for item in versions if item.id == vid), None)

    async def _add_send(_s, record):
        send_records.append(record)
        return record

    monkeypatch.setattr(
        "app.services.invitations.get_message_for_update",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_message_by_id",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_idempotency",
        AsyncMock(side_effect=lambda _s, key: key),
    )
    monkeypatch.setattr(
        "app.services.invitations.get_version", AsyncMock(side_effect=_get_version)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_send_record", AsyncMock(side_effect=_add_send)
    )
    monkeypatch.setattr(
        "app.services.invitations.next_version_no", AsyncMock(return_value=2)
    )
    monkeypatch.setattr(
        "app.services.invitations.add_version", AsyncMock(side_effect=_add_version)
    )
    monkeypatch.setattr("app.services.invitations.record_audit", AsyncMock())
    session = AsyncMock()
    await record_sent(
        session,
        message_id=message_id,
        payload=RecordSentRequest(
            sent_at=datetime.now(UTC),
            message_version_id=old_version_id,
            channel_type="CORPORATE_EMAIL",
            idempotency_key="sent-old",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert send_records[0].message_version_id == old_version_id
    assert message.status == INVITATION_STATUS_RECORDED_SENT

    await update_invitation(
        session,
        message_id=message_id,
        payload=UpdateInvitationMessageRequest(
            version=1,
            subject="新主题",
            body_html="<p>新正文</p>",
            body_text="新正文",
        ),
        actor=_actor(),
        request_context=_ctx(),
    )
    assert message.status == INVITATION_STATUS_READY
    assert len(versions) == 2
    assert send_records[0].message_version_id == old_version_id
    assert message.current_version_id != old_version_id


@pytest.mark.asyncio
async def test_interviewer_cannot_read_candidate_or_peer_mail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.invitations import get_invitation_detail
    from app.services.interviews import InterviewNotFoundError

    candidate_msg = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="c",
        recipient_name="张三",
        recipient_email_masked="z***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=uuid4(),
        version=1,
    )
    peer_msg = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=candidate_msg.interview_round_id,
        schedule_id=candidate_msg.schedule_id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_INTERVIEWER,
        recipient_user_id=uuid4(),
        recipient_key="peer",
        recipient_name="其他面试官",
        recipient_email_masked="p***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=uuid4(),
        version=1,
    )
    actor = _actor(manage=False, execute=True)
    monkeypatch.setattr(
        "app.services.invitations.get_message_by_id",
        AsyncMock(side_effect=[candidate_msg, peer_msg]),
    )
    monkeypatch.setattr(
        "app.services.invitations.actor_assigned_to_round",
        AsyncMock(return_value=True),
    )
    session = AsyncMock()
    with pytest.raises(InterviewNotFoundError):
        await get_invitation_detail(session, message_id=candidate_msg.id, actor=actor)
    with pytest.raises(InterviewNotFoundError):
        await get_invitation_detail(session, message_id=peer_msg.id, actor=actor)


@pytest.mark.asyncio
async def test_unassigned_interviewer_gets_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.invitations import get_invitation_detail
    from app.services.interviews import InterviewNotFoundError

    message = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=uuid4(),
        schedule_id=uuid4(),
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_INTERVIEWER,
        recipient_user_id=uuid4(),
        recipient_key="u",
        recipient_name="面试官",
        recipient_email_masked="a***@example.com",
        status=INVITATION_STATUS_READY,
        current_version_id=uuid4(),
        version=1,
    )
    actor = _actor(manage=False, execute=True)
    monkeypatch.setattr(
        "app.services.invitations.get_message_by_id",
        AsyncMock(return_value=message),
    )
    monkeypatch.setattr(
        "app.services.invitations.actor_assigned_to_round",
        AsyncMock(return_value=False),
    )
    session = AsyncMock()
    with pytest.raises(InterviewNotFoundError):
        await get_invitation_detail(session, message_id=message.id, actor=actor)


@pytest.mark.asyncio
async def test_confirm_ignores_voided_mails_from_old_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOIDED mails (including former RECORDED_SENT) must not satisfy confirm."""
    from app.services.invitations import confirm_invitation
    from app.schemas.invitation import ConfirmInvitationRequest
    from app.services.interviews import InterviewValidationError

    round_, schedule, _a, _b = _make_round()
    voided_former_sent = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_id=schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="old-sent",
        recipient_name="旧已发送",
        recipient_email_masked="o***@example.com",
        status=INVITATION_STATUS_VOIDED,
        version=1,
    )
    current_ready = InterviewInvitationMessage(
        id=uuid4(),
        interview_round_id=round_.id,
        schedule_id=schedule.id,
        schedule_version=1,
        event_type=INVITATION_EVENT_INITIAL,
        audience_type=INVITATION_AUDIENCE_CANDIDATE,
        recipient_key="new",
        recipient_name="新安排",
        recipient_email_masked="n***@example.com",
        status=INVITATION_STATUS_READY,
        version=1,
    )
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    monkeypatch.setattr(
        "app.services.invitations.list_messages_for_schedule",
        AsyncMock(return_value=[voided_former_sent, current_ready]),
    )
    session = AsyncMock()
    with pytest.raises(InterviewValidationError, match="send_summary"):
        await confirm_invitation(
            session,
            round_id=round_.id,
            payload=ConfirmInvitationRequest(
                schedule_version=1,
                version=1,
                send_summary=None,
                idempotency_key="confirm-no-reuse-old",
            ),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_confirm_schedule_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.invitations import confirm_invitation
    from app.schemas.invitation import ConfirmInvitationRequest

    round_, schedule, _a, _b = _make_round()
    monkeypatch.setattr(
        "app.services.invitations.get_round_for_update",
        AsyncMock(return_value=round_),
    )
    monkeypatch.setattr(
        "app.services.invitations.find_idempotency", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.invitations.get_active_schedule_for_update",
        AsyncMock(return_value=schedule),
    )
    session = AsyncMock()
    with pytest.raises(InterviewOptimisticLockError):
        await confirm_invitation(
            session,
            round_id=round_.id,
            payload=ConfirmInvitationRequest(
                schedule_version=99,
                version=1,
                send_summary="已全部人工发送",
                idempotency_key="confirm-3",
            ),
            actor=_actor(),
            request_context=_ctx(),
        )
