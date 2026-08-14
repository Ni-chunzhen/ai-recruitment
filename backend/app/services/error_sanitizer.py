from __future__ import annotations

import re

_MAX_SUMMARY_LENGTH = 240

_REDACT_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*\S+"),
    re.compile(r"(?i)password\s*[=:]\s*\S+"),
    re.compile(r"(?i)(?:access_)?token\s*[=:]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9\-]+"),
    re.compile(r"(?i)resume(?:_text)?\s*[=:]\s*\S+"),
)
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
    re.compile(r"(?:/home|/usr|/opt|/var|/tmp|/app)/[^\s\"']+"),
)


def sanitize_error_message(
    message: str | None, *, max_length: int = _MAX_SUMMARY_LENGTH
) -> str | None:
    if message is None:
        return None
    text = str(message)
    if "Traceback (most recent call last)" in text:
        text = text.split("Traceback (most recent call last)", 1)[0].strip()
        if not text:
            text = "internal error"
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    for pattern in _PATH_PATTERNS:
        text = pattern.sub("[path]", text)
    text = " ".join(text.split())
    if len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text
