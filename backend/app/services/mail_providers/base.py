"""Mail provider result types (console-only in stage 10)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MailSendResult:
    success: bool
    error_code: str | None = None
    error_message_safe: str | None = None
