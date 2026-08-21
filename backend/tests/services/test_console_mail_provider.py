"""ConsoleMailProvider unit tests (Task 3)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

CONSOLE_SRC = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "mail_providers"
    / "console.py"
)


def test_console_module_source_forbids_smtp() -> None:
    assert CONSOLE_SRC.is_file()
    text = CONSOLE_SRC.read_text(encoding="utf-8").lower()
    assert "smtp" not in text
    assert "smtplib" not in text
    assert "socket" not in text


def test_console_provider_success_and_safe_failure() -> None:
    from app.services.mail_providers.console import ConsoleMailProvider

    provider = ConsoleMailProvider()
    ctx = {
        "attempt_id": str(uuid4()),
        "offer_id": str(uuid4()),
        "version_no": 1,
        "recipient_email_masked": "a***@example.com",
        "content_hash": "abc123",
        "subject": "SECRET_SUBJECT_SHOULD_NOT_LEAK",
        "body_html": "<p>SECRET_BODY</p>",
        "recipient_email": "alice@example.com",
    }
    ok = provider.send(ctx)
    assert ok.success is True
    assert ok.error_code is None
    assert ok.error_message_safe is None

    fail = provider.send(
        {
            **ctx,
            "force_fail": True,
            "fail_detail": "smtp AUTH alice@example.com boom",
        }
    )
    assert fail.success is False
    assert fail.error_code
    assert fail.error_message_safe is not None
    assert len(fail.error_message_safe) <= 512
    assert "alice@example.com" not in fail.error_message_safe
    assert "SECRET_SUBJECT" not in fail.error_message_safe
    assert "SECRET_BODY" not in (fail.error_message_safe or "")
