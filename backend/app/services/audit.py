from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.audit_logs import create_audit_log


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    ip_address: str | None = None


SENSITIVE_VALUE_MARKERS = (
    "password",
    "token",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "bearer",
    "enc:v1:",
)

# Field-name scrub (locked): exact match or suffix "_<name>".
# Intentionally does NOT substring-match "secret" so metadata keys like
# secret_keys_updated (list of key names) remain visible.
SENSITIVE_KEY_NAMES = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "access_key",
        "secret_key",
        "authorization",
        "cookie",
        "ciphertext",
        "secret_ciphertext",
        "bearer",
    }
)

# Metadata keys whose values are key-name lists, not secret payloads.
_AUDIT_KEY_NAME_LIST_KEYS = frozenset(
    {
        "secret_keys_updated",
        "updated_keys",
    }
)


def _key_is_sensitive(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered in _AUDIT_KEY_NAME_LIST_KEYS:
        return False
    if lowered in SENSITIVE_KEY_NAMES:
        return True
    return any(lowered.endswith(f"_{name}") for name in SENSITIVE_KEY_NAMES)


def _scrub_value(value: object, *, skip_value_markers: bool = False) -> object:
    if isinstance(value, dict):
        scrubbed: dict = {}
        for key, item in value.items():
            key_l = key.lower() if isinstance(key, str) else ""
            if key_l in _AUDIT_KEY_NAME_LIST_KEYS:
                # Key-name lists must stay readable; do not value-marker scrub items.
                scrubbed[key] = _scrub_value(item, skip_value_markers=True)
            elif _key_is_sensitive(key):
                scrubbed[key] = "[redacted]"
            else:
                scrubbed[key] = _scrub_value(item)
        return scrubbed
    if isinstance(value, (list, tuple)):
        return [
            _scrub_value(item, skip_value_markers=skip_value_markers) for item in value
        ]
    if isinstance(value, str) and not skip_value_markers:
        lowered = value.lower()
        if any(marker in lowered for marker in SENSITIVE_VALUE_MARKERS):
            return "[redacted]"
    return value


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    result: str,
    resource_type: str,
    request_context: RequestContext,
    actor_user_id: UUID | None = None,
    resource_id: str | None = None,
    changes: dict | None = None,
) -> None:
    scrubbed_changes = None
    if changes is not None:
        scrubbed_changes = _scrub_value(changes)
    await create_audit_log(
        session,
        action=action,
        result=result,
        resource_type=resource_type,
        request_id=request_context.request_id,
        actor_user_id=actor_user_id,
        resource_id=resource_id,
        ip_address=request_context.ip_address,
        changes=scrubbed_changes,
    )
