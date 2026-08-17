"""Invitation email templates and HTML whitelist sanitizer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.models.invitation import (
    TEMPLATE_CANDIDATE_CANCELLATION,
    TEMPLATE_CANDIDATE_INITIAL,
    TEMPLATE_CANDIDATE_RESCHEDULE,
    TEMPLATE_INTERVIEWER_CANCELLATION,
    TEMPLATE_INTERVIEWER_INITIAL,
    TEMPLATE_INTERVIEWER_RESCHEDULE,
    TEMPLATE_VERSION,
)

ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "strong",
        "em",
        "b",
        "i",
        "u",
        "ul",
        "ol",
        "li",
        "a",
        "span",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
)
ALLOWED_ATTRS = {
    "a": frozenset({"href", "title"}),
    "span": frozenset({"style"}),
    "p": frozenset({"style"}),
    "div": frozenset({"style"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}
SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto"})


@dataclass(frozen=True)
class InvitationTemplateContext:
    candidate_name: str
    job_title: str
    job_version: str
    round_name: str
    start_at_display: str
    end_at_display: str
    timezone: str
    duration_minutes: int
    format: str
    meeting_url: str | None = None
    meeting_no: str | None = None
    meeting_password: str | None = None
    location: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    owner_name: str = ""
    interviewer_name: str = ""
    previous_start_at_display: str | None = None
    previous_end_at_display: str | None = None
    reschedule_reason: str | None = None
    cancellation_reason: str | None = None
    cancellation_description: str | None = None


@dataclass(frozen=True)
class RenderedInvitation:
    template_code: str
    template_version: str
    subject: str
    body_html: str
    body_text: str
    missing_fields: list[str] = field(default_factory=list)


class _WhitelistHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            return
        if tag == "br":
            self._parts.append("<br>")
            return
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        kept: list[str] = []
        for key, value in attrs:
            if key.lower().startswith("on"):
                continue
            if key not in allowed or value is None:
                continue
            if key == "href" and not _is_safe_url(value):
                continue
            if key == "src":
                continue
            kept.append(f'{key}="{_escape_attr(value)}"')
        attr_str = (" " + " ".join(kept)) if kept else ""
        self._parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_TAGS and tag != "br":
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(_escape_text(data))

    def handle_entityref(self, name: str) -> None:
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self._parts)


def _escape_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _is_safe_url(value: str) -> bool:
    stripped = value.strip()
    if not stripped or stripped.startswith("#"):
        return True
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", stripped)
    if match is None:
        return True
    return match.group(1).lower() in SAFE_URL_SCHEMES


def sanitize_invitation_html(html: str) -> str:
    parser = _WhitelistHtmlParser()
    parser.feed(html or "")
    parser.close()
    return parser.get_html()


def _required_common(ctx: InvitationTemplateContext) -> list[str]:
    missing: list[str] = []
    if not (ctx.candidate_name or "").strip():
        missing.append("candidate_name")
    if not (ctx.job_title or "").strip():
        missing.append("job_title")
    if not (ctx.round_name or "").strip():
        missing.append("round_name")
    if not (ctx.start_at_display or "").strip():
        missing.append("start_at_display")
    if not (ctx.end_at_display or "").strip():
        missing.append("end_at_display")
    if not (ctx.timezone or "").strip():
        missing.append("timezone")
    if ctx.format == "ONLINE":
        if not (ctx.meeting_url or "").strip() and not (ctx.meeting_no or "").strip():
            missing.append("meeting_url")
            missing.append("meeting_no")
    elif ctx.format == "OFFLINE":
        if not (ctx.location or "").strip():
            missing.append("location")
    else:
        missing.append("format")
    return missing


def _schedule_block_text(ctx: InvitationTemplateContext) -> str:
    lines = [
        f"岗位：{ctx.job_title}（{ctx.job_version}）",
        f"轮次：{ctx.round_name}",
        f"时间：{ctx.start_at_display} - {ctx.end_at_display}（{ctx.timezone}）",
        f"预计时长：{ctx.duration_minutes} 分钟",
    ]
    if ctx.format == "ONLINE":
        if ctx.meeting_url:
            lines.append(f"会议链接：{ctx.meeting_url}")
        if ctx.meeting_no:
            lines.append(f"会议号：{ctx.meeting_no}")
        if ctx.meeting_password:
            lines.append(f"会议密码：{ctx.meeting_password}")
    else:
        if ctx.location:
            lines.append(f"地点：{ctx.location}")
        if ctx.contact_name:
            lines.append(f"到访联系人：{ctx.contact_name}")
        if ctx.contact_phone:
            lines.append(f"联系方式：{ctx.contact_phone}")
    if ctx.owner_name:
        lines.append(f"面试负责人：{ctx.owner_name}")
    return "\n".join(lines)


def _schedule_block_html(ctx: InvitationTemplateContext) -> str:
    return "<br>".join(_escape_text(line) for line in _schedule_block_text(ctx).split("\n"))


def _subject_for(code: str, ctx: InvitationTemplateContext) -> str:
    job = ctx.job_title or "岗位"
    if code in {TEMPLATE_CANDIDATE_INITIAL, TEMPLATE_INTERVIEWER_INITIAL}:
        return f"【面试邀请】{job} - {ctx.round_name}"
    if code in {TEMPLATE_CANDIDATE_RESCHEDULE, TEMPLATE_INTERVIEWER_RESCHEDULE}:
        return f"【面试改期】{job} - {ctx.round_name}"
    return f"【面试取消】{job} - {ctx.round_name}"


def _greeting(code: str, ctx: InvitationTemplateContext) -> str:
    if code.startswith("interviewer_"):
        name = ctx.interviewer_name or "面试官"
        return f"{name}您好："
    name = ctx.candidate_name or "候选人"
    return f"{name}您好："


def _body_parts(code: str, ctx: InvitationTemplateContext) -> tuple[str, str]:
    greeting = _greeting(code, ctx)
    schedule = _schedule_block_text(ctx)
    schedule_html = _schedule_block_html(ctx)

    if code in {TEMPLATE_CANDIDATE_INITIAL, TEMPLATE_INTERVIEWER_INITIAL}:
        intro = (
            "诚邀您参加以下面试安排。"
            if code == TEMPLATE_CANDIDATE_INITIAL
            else "请您按以下安排参加面试。"
        )
        text = f"{greeting}\n\n{intro}\n\n{schedule}\n\n如有疑问，请联系面试负责人。"
        html = (
            f"<p>{_escape_text(greeting)}</p>"
            f"<p>{_escape_text(intro)}</p>"
            f"<p>{schedule_html}</p>"
            f"<p>{_escape_text('如有疑问，请联系面试负责人。')}</p>"
        )
        return text, html

    if code in {TEMPLATE_CANDIDATE_RESCHEDULE, TEMPLATE_INTERVIEWER_RESCHEDULE}:
        old = (
            f"原安排：{ctx.previous_start_at_display or '-'} - "
            f"{ctx.previous_end_at_display or '-'}"
        )
        reason = f"改期原因：{ctx.reschedule_reason or '-'}"
        text = (
            f"{greeting}\n\n面试安排已更新，请以新安排为准。\n\n"
            f"{old}\n新安排如下：\n{schedule}\n\n{reason}"
        )
        html = (
            f"<p>{_escape_text(greeting)}</p>"
            f"<p>{_escape_text('面试安排已更新，请以新安排为准。')}</p>"
            f"<p>{_escape_text(old)}</p>"
            f"<p>{_escape_text('新安排如下：')}</p>"
            f"<p>{schedule_html}</p>"
            f"<p>{_escape_text(reason)}</p>"
        )
        return text, html

    reason = f"取消原因：{ctx.cancellation_reason or '-'}"
    desc = (
        f"说明：{ctx.cancellation_description}"
        if ctx.cancellation_description
        else ""
    )
    text = f"{greeting}\n\n原定面试安排已取消。\n\n{schedule}\n\n{reason}"
    if desc:
        text += f"\n{desc}"
    html = (
        f"<p>{_escape_text(greeting)}</p>"
        f"<p>{_escape_text('原定面试安排已取消。')}</p>"
        f"<p>{schedule_html}</p>"
        f"<p>{_escape_text(reason)}</p>"
    )
    if desc:
        html += f"<p>{_escape_text(desc)}</p>"
    return text, html


def _extra_missing(code: str, ctx: InvitationTemplateContext) -> list[str]:
    missing: list[str] = []
    if code.startswith("interviewer_") and not (ctx.interviewer_name or "").strip():
        missing.append("interviewer_name")
    if code in {TEMPLATE_CANDIDATE_RESCHEDULE, TEMPLATE_INTERVIEWER_RESCHEDULE}:
        if not (ctx.previous_start_at_display or "").strip():
            missing.append("previous_start_at_display")
        if not (ctx.reschedule_reason or "").strip():
            missing.append("reschedule_reason")
    if code in {TEMPLATE_CANDIDATE_CANCELLATION, TEMPLATE_INTERVIEWER_CANCELLATION}:
        if not (ctx.cancellation_reason or "").strip():
            missing.append("cancellation_reason")
    return missing


def render_invitation_template(
    template_code: str, ctx: InvitationTemplateContext
) -> RenderedInvitation:
    known = {
        TEMPLATE_CANDIDATE_INITIAL,
        TEMPLATE_INTERVIEWER_INITIAL,
        TEMPLATE_CANDIDATE_RESCHEDULE,
        TEMPLATE_INTERVIEWER_RESCHEDULE,
        TEMPLATE_CANDIDATE_CANCELLATION,
        TEMPLATE_INTERVIEWER_CANCELLATION,
    }
    if template_code not in known:
        raise ValueError(f"unknown invitation template: {template_code}")

    missing = _required_common(ctx) + _extra_missing(template_code, ctx)
    subject = _subject_for(template_code, ctx)
    body_text, body_html = _body_parts(template_code, ctx)
    body_html = sanitize_invitation_html(body_html)
    return RenderedInvitation(
        template_code=template_code,
        template_version=TEMPLATE_VERSION,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        missing_fields=sorted(set(missing)),
    )
