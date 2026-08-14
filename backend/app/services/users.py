
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import User
from app.repositories.rbac import (
    count_active_system_admins,
    get_roles_by_names,
    replace_user_roles,
)
from app.repositories.users import (
    create_user,
    update_user,
)
from app.schemas.user import UserListItem
from app.services.audit import RequestContext, record_audit
from app.services.auth import generate_temporary_password
from app.services.sessions import SessionService, validate_password_strength


def to_user_list_item(user: User) -> UserListItem:
    return UserListItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=[role.name for role in user.roles],
    )


async def create_managed_user(
    session: AsyncSession,
    session_service: SessionService,
    *,
    username: str,
    display_name: str,
    role_names: list[str],
    request_context: RequestContext,
    actor: User,
) -> tuple[UserListItem, str]:
    roles = await get_roles_by_names(session, role_names)
    if len(roles) != len(set(role_names)):
        raise ValueError("invalid role")

    temporary_password = generate_temporary_password()
    validate_password_strength(temporary_password)

    user = await create_user(
        session,
        username=username,
        display_name=display_name,
        password_hash=hash_password(temporary_password),
        roles=roles,
        must_change_password=True,
    )
    await session_service.revoke_user_sessions(user.id)
    await record_audit(
        session,
        action="account.create",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(user.id),
        changes={"username": user.username, "roles": role_names},
    )
    await session.commit()
    return to_user_list_item(user), temporary_password


async def reset_user_password(
    session: AsyncSession,
    session_service: SessionService,
    *,
    user: User,
    request_context: RequestContext,
    actor: User,
) -> str:
    temporary_password = generate_temporary_password()
    validate_password_strength(temporary_password)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.token_version += 1
    await session.flush()
    await session_service.revoke_user_sessions(user.id)
    await record_audit(
        session,
        action="account.reset_password",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(user.id),
    )
    await session.commit()
    return temporary_password


async def update_managed_user(
    session: AsyncSession,
    session_service: SessionService,
    *,
    user: User,
    actor: User,
    request_context: RequestContext,
    display_name: str | None = None,
    is_active: bool | None = None,
) -> UserListItem:
    if is_active is False and user.id == actor.id:
        raise ValueError("cannot deactivate self")

    if is_active is False:
        admin_count = await count_active_system_admins(session)
        is_last_admin = any(role.name == "system_admin" for role in user.roles)
        if is_last_admin and admin_count <= 1:
            raise ValueError("cannot deactivate last system admin")

    updated = await update_user(
        session,
        user,
        display_name=display_name,
        is_active=is_active,
    )
    if is_active is False:
        await session_service.revoke_user_sessions(updated.id)

    await record_audit(
        session,
        action="account.update",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(updated.id),
        changes={
            "display_name": display_name,
            "is_active": is_active,
        },
    )
    await session.commit()
    return to_user_list_item(updated)


async def replace_managed_user_roles(
    session: AsyncSession,
    session_service: SessionService,
    *,
    user: User,
    actor: User,
    role_names: list[str],
    request_context: RequestContext,
) -> UserListItem:
    if user.id == actor.id and "system_admin" not in role_names:
        admin_count = await count_active_system_admins(session)
        if admin_count <= 1:
            raise ValueError("cannot remove last system admin role from self")

    roles = await get_roles_by_names(session, role_names)
    if len(roles) != len(set(role_names)):
        raise ValueError("invalid role")

    if user.id != actor.id:
        was_admin = any(role.name == "system_admin" for role in user.roles)
        will_be_admin = "system_admin" in role_names
        if was_admin and not will_be_admin:
            admin_count = await count_active_system_admins(session)
            if admin_count <= 1:
                raise ValueError("cannot remove last system admin")

    updated = await replace_user_roles(session, user, roles)
    await session_service.revoke_user_sessions(updated.id)
    await record_audit(
        session,
        action="role.assign",
        result="success",
        resource_type="user",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(updated.id),
        changes={"roles": role_names},
    )
    await session.commit()
    return to_user_list_item(updated)
