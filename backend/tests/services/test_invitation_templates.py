"""RED/GREEN tests for invitation email templates and HTML sanitization."""

from __future__ import annotations

import pytest

from app.services.invitation_templates import (
    TEMPLATE_CANDIDATE_CANCELLATION,
    TEMPLATE_CANDIDATE_INITIAL,
    TEMPLATE_CANDIDATE_RESCHEDULE,
    TEMPLATE_INTERVIEWER_CANCELLATION,
    TEMPLATE_INTERVIEWER_INITIAL,
    TEMPLATE_INTERVIEWER_RESCHEDULE,
    InvitationTemplateContext,
    render_invitation_template,
    sanitize_invitation_html,
)


def _base_ctx(**overrides: object) -> InvitationTemplateContext:
    data = {
        "candidate_name": "张三",
        "job_title": "后端工程师",
        "job_version": "v2",
        "round_name": "技术一面",
        "start_at_display": "2026-08-20 14:00",
        "end_at_display": "2026-08-20 15:00",
        "timezone": "Asia/Shanghai",
        "duration_minutes": 60,
        "format": "ONLINE",
        "meeting_url": "https://meeting.example.com/abc",
        "meeting_no": "123456",
        "meeting_password": "secret-pw",
        "location": None,
        "contact_name": None,
        "contact_phone": None,
        "owner_name": "李招聘",
        "interviewer_name": "王面试",
        "previous_start_at_display": None,
        "previous_end_at_display": None,
        "reschedule_reason": None,
        "cancellation_reason": None,
        "cancellation_description": None,
    }
    data.update(overrides)
    return InvitationTemplateContext(**data)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "code",
    [
        TEMPLATE_CANDIDATE_INITIAL,
        TEMPLATE_INTERVIEWER_INITIAL,
        TEMPLATE_CANDIDATE_RESCHEDULE,
        TEMPLATE_INTERVIEWER_RESCHEDULE,
        TEMPLATE_CANDIDATE_CANCELLATION,
        TEMPLATE_INTERVIEWER_CANCELLATION,
    ],
)
def test_six_templates_render_subject_html_and_text(code: str) -> None:
    ctx = _base_ctx(
        previous_start_at_display="2026-08-18 10:00",
        previous_end_at_display="2026-08-18 11:00",
        reschedule_reason="面试官出差",
        cancellation_reason="候选人退出",
        cancellation_description="个人原因",
    )
    rendered = render_invitation_template(code, ctx)
    assert rendered.subject
    assert rendered.body_html
    assert rendered.body_text
    assert rendered.missing_fields == []
    assert "张三" in rendered.body_text or "王面试" in rendered.body_text
    assert "后端工程师" in rendered.body_text


def test_online_includes_meeting_entry_offline_includes_location() -> None:
    online = render_invitation_template(
        TEMPLATE_CANDIDATE_INITIAL, _base_ctx(format="ONLINE")
    )
    assert "meeting.example.com" in online.body_text
    assert "123456" in online.body_text
    assert "secret-pw" in online.body_text

    offline = render_invitation_template(
        TEMPLATE_CANDIDATE_INITIAL,
        _base_ctx(
            format="OFFLINE",
            meeting_url=None,
            meeting_no=None,
            meeting_password=None,
            location="上海办公室 A座",
            contact_name="前台小张",
            contact_phone="13800138000",
        ),
    )
    assert "上海办公室" in offline.body_text
    assert "前台小张" in offline.body_text
    assert "meeting.example.com" not in offline.body_text


def test_reschedule_includes_old_new_and_reason() -> None:
    rendered = render_invitation_template(
        TEMPLATE_CANDIDATE_RESCHEDULE,
        _base_ctx(
            previous_start_at_display="2026-08-18 10:00",
            previous_end_at_display="2026-08-18 11:00",
            reschedule_reason="面试官出差",
        ),
    )
    assert "2026-08-18 10:00" in rendered.body_text
    assert "2026-08-20 14:00" in rendered.body_text
    assert "面试官出差" in rendered.body_text


def test_cancellation_includes_reason() -> None:
    rendered = render_invitation_template(
        TEMPLATE_CANDIDATE_CANCELLATION,
        _base_ctx(
            cancellation_reason="候选人退出",
            cancellation_description="个人原因",
        ),
    )
    assert "候选人退出" in rendered.body_text
    assert "个人原因" in rendered.body_text


def test_missing_required_fields_are_reported() -> None:
    rendered = render_invitation_template(
        TEMPLATE_CANDIDATE_INITIAL,
        _base_ctx(
            candidate_name="",
            meeting_url=None,
            meeting_no=None,
            meeting_password=None,
        ),
    )
    assert "candidate_name" in rendered.missing_fields
    assert "meeting_url" in rendered.missing_fields or "meeting_no" in rendered.missing_fields


def test_html_sanitizer_strips_dangerous_content() -> None:
    dirty = (
        '<p>Hello</p><script>alert(1)</script><iframe src="x"></iframe>'
        '<a href="javascript:alert(1)">x</a>'
        '<img src="https://tracker.example/pixel.gif" onerror="alert(1)">'
        '<form action="/x"><input name="a"></form>'
        '<p onclick="evil()">ok</p>'
    )
    clean = sanitize_invitation_html(dirty)
    assert "<script" not in clean.lower()
    assert "<iframe" not in clean.lower()
    assert "javascript:" not in clean.lower()
    assert "onerror" not in clean.lower()
    assert "onclick" not in clean.lower()
    assert "<form" not in clean.lower()
    assert "<input" not in clean.lower()
    assert "<img" not in clean.lower()
    assert "tracker.example" not in clean
    assert "Hello" in clean
    assert "ok" in clean


def test_missing_required_fields_cannot_become_ready() -> None:
    rendered = render_invitation_template(
        TEMPLATE_CANDIDATE_INITIAL,
        _base_ctx(candidate_name="", meeting_url=None, meeting_no=None),
    )
    assert rendered.missing_fields
    # Service layer maps missing_fields to DRAFT; template reports gaps explicitly.
    assert "candidate_name" in rendered.missing_fields


def test_sanitized_html_and_text_are_semantically_aligned() -> None:
    rendered = render_invitation_template(
        TEMPLATE_CANDIDATE_INITIAL, _base_ctx()
    )
    assert "后端工程师" in rendered.body_html
    assert "后端工程师" in rendered.body_text
    assert "技术一面" in rendered.body_html
    assert "技术一面" in rendered.body_text
    assert "meeting.example.com" in rendered.body_html
    assert "meeting.example.com" in rendered.body_text
