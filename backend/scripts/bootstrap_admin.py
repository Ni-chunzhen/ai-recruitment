"""Seed RBAC and create first administrator."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import User, normalize_username
from app.services.bootstrap import get_role_by_name, seed_rbac
from app.services.sessions import validate_password_strength


async def seed_only() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await seed_rbac(session)
        print(
            f"RBAC seed complete: roles_created={result.roles_created}, "
            f"permissions_created={result.permissions_created}"
        )
    await engine.dispose()


async def create_admin(username: str, display_name: str, password: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed_rbac(session)
        normalized = normalize_username(username)
        existing = await session.scalar(
            select(User).where(User.username_normalized == normalized)
        )
        if existing is not None:
            raise SystemExit("administrator already exists")

        role = await get_role_by_name(session, "system_admin")
        if role is None:
            raise SystemExit("system_admin role missing; run seed first")

        user = User(
            username=username.strip(),
            username_normalized=normalized,
            display_name=display_name,
            password_hash=hash_password(password),
            roles=[role],
            must_change_password=True,
        )
        session.add(user)
        await session.commit()
        print(f"Administrator '{username}' created.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap RBAC and administrator")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    args = parser.parse_args()

    if args.seed_only:
        asyncio.run(seed_only())
        return

    if not args.username or not args.display_name:
        raise SystemExit("--username and --display-name are required")

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("passwords do not match")
    validate_password_strength(password)

    asyncio.run(create_admin(args.username, args.display_name, password))


if __name__ == "__main__":
    main()
