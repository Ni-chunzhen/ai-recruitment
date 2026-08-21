from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Permission, Role, role_permissions

ROLE_DEFINITIONS = {
    "system_admin": "系统管理员",
    "recruiter_admin": "招聘管理员",
    "interviewer": "面试官",
}

PERMISSION_DEFINITIONS = {
    "account.read": "查看账号",
    "account.create": "创建账号",
    "account.update": "更新账号",
    "account.reset_password": "重置密码",
    "role.assign": "分配角色",
    "audit.read": "查看审计日志",
    "ai_task.manage": "管理AI任务",
    "profile.read": "查看个人资料",
    "profile.change_password": "修改密码",
    "recruitment.manage": "管理招聘",
    "interview.execute": "执行面试",
    "integration.manage": "管理第三方集成配置",
}

ROLE_PERMISSION_MATRIX: dict[str, list[str]] = {
    "system_admin": list(PERMISSION_DEFINITIONS.keys()),
    "recruiter_admin": [
        "profile.read",
        "profile.change_password",
        "recruitment.manage",
        "interview.execute",
    ],
    "interviewer": [
        "profile.read",
        "profile.change_password",
        "interview.execute",
    ],
}


@dataclass(frozen=True)
class BootstrapResult:
    roles_created: int
    permissions_created: int


async def seed_rbac(session: AsyncSession) -> BootstrapResult:
    roles_created = 0
    permissions_created = 0

    permission_by_code: dict[str, Permission] = {}
    for code, description in PERMISSION_DEFINITIONS.items():
        permission = await session.scalar(
            select(Permission).where(Permission.code == code)
        )
        if permission is None:
            permission = Permission(code=code, description=description)
            session.add(permission)
            permissions_created += 1
        else:
            permission.description = description
        permission_by_code[code] = permission

    await session.flush()

    role_by_name: dict[str, Role] = {}
    for name, description in ROLE_DEFINITIONS.items():
        role = await session.scalar(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.name == name)
        )
        if role is None:
            role = Role(name=name, description=description)
            session.add(role)
            roles_created += 1
        else:
            role.description = description
        role_by_name[name] = role

    await session.flush()

    for role_name, permission_codes in ROLE_PERMISSION_MATRIX.items():
        role = role_by_name[role_name]
        await session.execute(
            delete(role_permissions).where(role_permissions.c.role_id == role.id)
        )
        for code in permission_codes:
            await session.execute(
                role_permissions.insert().values(
                    role_id=role.id,
                    permission_id=permission_by_code[code].id,
                )
            )

    await session.commit()
    return BootstrapResult(
        roles_created=roles_created,
        permissions_created=permissions_created,
    )


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    return await session.scalar(
        select(Role)
        .options(selectinload(Role.permissions))
        .where(Role.name == name)
    )


async def list_permissions_for_user(session: AsyncSession, user_id: UUID) -> list[str]:
    from app.models import User

    user = await session.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id)
    )
    if user is None:
        return []

    codes: set[str] = set()
    for role in user.roles:
        for permission in role.permissions:
            codes.add(permission.code)
    return sorted(codes)
