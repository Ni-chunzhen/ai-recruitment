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


def _scrub_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: _scrub_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
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
