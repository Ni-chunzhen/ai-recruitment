from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Role, User, normalize_username


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id)
    )


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    normalized = normalize_username(username)
    return await session.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.username_normalized == normalized)
    )


async def list_users(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    username: str | None = None,
    is_active: bool | None = None,
    role_name: str | None = None,
) -> tuple[list[User], int]:
    query = select(User).options(selectinload(User.roles))
    count_query = select(func.count()).select_from(User)

    if username:
        query = query.where(User.username.ilike(f"%{username}%"))
        count_query = count_query.where(User.username.ilike(f"%{username}%"))
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)
    if role_name:
        query = query.join(User.roles).where(Role.name == role_name)
        count_query = count_query.join(User.roles).where(Role.name == role_name)

    total = await session.scalar(count_query) or 0
    result = await session.scalars(
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.all()), int(total)


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    roles: list[Role],
    must_change_password: bool = True,
) -> User:
    normalized = normalize_username(username)
    existing = await session.scalar(
        select(User).where(User.username_normalized == normalized)
    )
    if existing is not None:
        raise UserAlreadyExistsError("username already exists")

    user = User(
        username=username.strip(),
        username_normalized=normalized,
        display_name=display_name,
        password_hash=password_hash,
        roles=roles,
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.flush()
    return user


async def update_user(
    session: AsyncSession,
    user: User,
    *,
    display_name: str | None = None,
    is_active: bool | None = None,
) -> User:
    if display_name is not None:
        user.display_name = display_name
    if is_active is not None:
        user.is_active = is_active
    await session.flush()
    return user
