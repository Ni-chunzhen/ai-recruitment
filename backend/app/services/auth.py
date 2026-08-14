import hashlib
import secrets
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    hash_password,
    new_refresh_token,
    verify_password,
)
from app.models import User
from app.repositories.rbac import list_user_permission_codes
from app.repositories.users import get_user_by_username
from app.schemas.auth import UserSummary
from app.services.audit import RequestContext, record_audit
from app.services.sessions import (
    SessionError,
    SessionService,
    validate_password_strength,
)

DUMMY_PASSWORD_HASH = hash_password("constant-time-dummy-password-value")


def build_user_summary(user: User, permissions: list[str]) -> UserSummary:
    return UserSummary(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=[role.name for role in user.roles],
        permissions=permissions,
    )


async def authenticate_user(
    session: AsyncSession,
    username: str,
    password: str,
) -> User | None:
    user = await get_user_by_username(session, username)
    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not user.is_active:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def login_user(
    session: AsyncSession,
    session_service: SessionService,
    *,
    username: str,
    password: str,
    request_context: RequestContext,
) -> tuple[str, str, UserSummary] | None:
    user = await authenticate_user(session, username, password)
    if user is None:
        await record_audit(
            session,
            action="auth.login",
            result="failure",
            resource_type="user",
            request_context=request_context,
            changes={"username": username.strip().lower()},
        )
        await session.commit()
        return None

    permissions = await list_user_permission_codes(user)
    session_id = uuid4()
    raw_refresh, digest = new_refresh_token()
    access_token = create_access_token(user.id, session_id)

    await session_service.create_session(
        user.id,
        user.token_version,
        access_token,
        raw_refresh,
        digest,
        session_id=session_id,
    )

    await record_audit(
        session,
        action="auth.login",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=user.id,
        resource_id=str(user.id),
    )
    await session.commit()

    return access_token, raw_refresh, build_user_summary(user, permissions)


async def refresh_user(
    session: AsyncSession,
    session_service: SessionService,
    *,
    refresh_token: str,
    request_context: RequestContext,
) -> tuple[str, str] | None:
    from app.repositories.users import get_user_by_id

    old_digest = hashlib.sha256(refresh_token.encode()).hexdigest()
    old_record = await session_service.get_session_by_digest(old_digest)
    if old_record is None:
        return None

    user = await get_user_by_id(session, old_record.user_id)
    if user is None or not user.is_active:
        return None
    if old_record.token_version != user.token_version:
        return None

    new_raw, new_digest = new_refresh_token()
    access_token = create_access_token(user.id, uuid4())
    try:
        tokens = await session_service.rotate_refresh_token(
            old_digest,
            new_raw,
            new_digest,
            access_token,
        )
    except SessionError:
        await record_audit(
            session,
            action="auth.refresh_reuse",
            result="failure",
            resource_type="session",
            request_context=request_context,
            actor_user_id=user.id,
        )
        await session.commit()
        return None

    final_access = create_access_token(user.id, tokens.session_id)
    await record_audit(
        session,
        action="auth.refresh",
        result="success",
        resource_type="session",
        request_context=request_context,
        actor_user_id=user.id,
        resource_id=str(tokens.session_id),
    )
    await session.commit()
    return final_access, new_raw


async def logout_user(
    session_service: SessionService,
    *,
    session_id: UUID | None,
) -> None:
    if session_id is None:
        return
    await session_service.revoke_session(session_id)


def generate_temporary_password() -> str:
    return f"Tmp-{secrets.token_urlsafe(12)}!9"


async def change_password(
    session: AsyncSession,
    session_service: SessionService,
    *,
    user: User,
    current_password: str,
    new_password: str,
    request_context: RequestContext,
    current_session_id: UUID | None = None,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise ValueError("invalid current password")
    validate_password_strength(new_password)
    if verify_password(new_password, user.password_hash):
        raise ValueError("new password must differ from current password")

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    await session.flush()

    await session_service.revoke_user_sessions(
        user.id,
        except_session_id=current_session_id,
    )

    await record_audit(
        session,
        action="auth.change_password",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=user.id,
        resource_id=str(user.id),
    )
    await session.commit()
