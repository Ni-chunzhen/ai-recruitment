from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Role, User


async def get_role_by_id(session: AsyncSession, role_id: UUID) -> Role | None:
    return await session.scalar(
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.id == role_id)
    )


async def get_roles_by_names(session: AsyncSession, names: list[str]) -> list[Role]:
    if not names:
        return []
    result = await session.scalars(
        select(Role).options(selectinload(Role.permissions)).where(Role.name.in_(names))
    )
    return list(result.all())


async def replace_user_roles(
    session: AsyncSession,
    user: User,
    roles: list[Role],
) -> User:
    user.roles = roles
    await session.flush()
    return user


async def count_active_system_admins(session: AsyncSession) -> int:
    result = await session.scalars(
        select(User)
        .join(User.roles)
        .where(Role.name == "system_admin", User.is_active.is_(True))
    )
    return len(list(result.all()))


async def user_has_permission(user: User, permission_code: str) -> bool:
    for role in user.roles:
        for permission in role.permissions:
            if permission.code == permission_code:
                return True
    return False


async def list_user_permission_codes(user: User) -> list[str]:
    codes: set[str] = set()
    for role in user.roles:
        for permission in role.permissions:
            codes.add(permission.code)
    return sorted(codes)
