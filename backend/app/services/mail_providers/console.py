"""Console mail provider — structured result only; no network outbound."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.mail_providers.base import MailSendResult

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _sanitize_error(detail: str | None) -> str:
    text = (detail or "console provider failed").strip()
    text = _EMAIL_RE.sub("[redacted]", text)
    for banned in ("SECRET_SUBJECT", "SECRET_BODY", "subject=", "body_html="):
        text = text.replace(banned, "[redacted]")
    return text[:512]


class ConsoleMailProvider:
    """Unique stage-10 provider: logs safe metadata only."""

    def send(self, context: dict[str, Any]) -> MailSendResult:
        safe_log = {
            "attempt_id": context.get("attempt_id"),
            "offer_id": context.get("offer_id"),
            "version_no": context.get("version_no"),
            "recipient_email_masked": context.get("recipient_email_masked"),
            "content_hash": context.get("content_hash"),
            "provider": "console",
        }
        if context.get("force_fail"):
            message = _sanitize_error(str(context.get("fail_detail") or ""))
            logger.info("console_mail_failed %s", {**safe_log, "result": "failed"})
            return MailSendResult(
                success=False,
                error_code="console_error",
                error_message_safe=message or "console provider failed",
            )
        logger.info("console_mail_sent %s", {**safe_log, "result": "succeeded"})
        return MailSendResult(success=True)
