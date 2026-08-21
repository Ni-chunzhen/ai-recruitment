"""Email masking for outbound mail metadata (Offer / invitations)."""

from __future__ import annotations


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().partition("@")
    if not local or not domain:
        return None
    return f"{local[0]}***@{domain}"
