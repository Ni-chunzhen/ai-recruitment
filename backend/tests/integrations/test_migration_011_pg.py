"""Live 010→011 upgrade/downgrade against an isolated PostgreSQL database.

Never points at the development business database (`recruit`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.engine.url import make_url

from alembic import command
from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_DB_NAMES = frozenset({"recruit", "postgres", "template0", "template1"})


def _isolated_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL", "").strip()
    if explicit:
        url = make_url(explicit)
    else:
        url = make_url(get_settings().database_url).set(database="recruit_test")
    database = url.database or ""
    if database in BUSINESS_DB_NAMES:
        raise RuntimeError(
            f"refusing destructive migrations on business database {database!r}"
        )
    return url.render_as_string(hide_password=False)


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


async def _connect_admin(url_str: str) -> asyncpg.Connection:
    url = make_url(url_str)
    return await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database="postgres",
    )


async def _connect(url_str: str) -> asyncpg.Connection:
    url = make_url(url_str)
    return await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
    )


async def _ensure_isolated_database(url_str: str) -> None:
    url = make_url(url_str)
    admin = await _connect_admin(url_str)
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", url.database
        )
        if not exists:
            await admin.execute(
                f'CREATE DATABASE "{url.database}" OWNER "{url.username}"'
            )
    finally:
        await admin.close()


def _patch_database_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()


def _reset_public_schema(url_str: str) -> None:
    async def _run() -> None:
        conn = await _connect(url_str)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("GRANT ALL ON SCHEMA public TO public")
        finally:
            await conn.close()

    asyncio.run(_run())


async def _summary_meta(url: str) -> tuple[bool, str | None, bool | None]:
    conn = await _connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'interview_rounds'
              AND column_name = 'invitation_confirmation_summary'
            """
        )
        if row is None:
            return False, None, None
        return True, row["data_type"], row["is_nullable"] == "YES"
    finally:
        await conn.close()


async def _assert_at_010(url: str) -> None:
    conn = await _connect(url)
    try:
        tables = {
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert "interview_invitation_messages" in tables
        assert "interview_invitation_versions" in tables
        assert "interview_invitation_send_records" in tables

        email = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'email'
            """
        )
        assert email == 1

        confirmed = {
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'interview_rounds'
                  AND column_name LIKE 'invitation_confirm%'
                """
            )
        }
        assert {
            "invitation_confirmed_at",
            "invitation_confirmed_by",
            "invitation_confirmed_schedule_version",
        }.issubset(confirmed)
        assert "invitation_confirmation_summary" not in confirmed
    finally:
        await conn.close()


async def _assert_at_011(url: str) -> None:
    exists, data_type, nullable = await _summary_meta(url)
    assert exists is True
    assert data_type == "text"
    assert nullable is True


def test_009_to_010_to_011_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _isolated_url()
    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, "009_stage7_interview_foundation")

    async def _assert_009() -> None:
        conn = await _connect(url)
        try:
            tables = {
                row["tablename"]
                for row in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            assert "interview_invitation_messages" not in tables
            assert "interview_invitation_versions" not in tables
            assert "interview_invitation_send_records" not in tables
            email = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'email'
                """
            )
            assert email is None
        finally:
            await conn.close()

    asyncio.run(_assert_009())

    command.upgrade(config, "010_stage7_manual_invitations")
    asyncio.run(_assert_at_010(url))

    command.upgrade(config, "011_stage7_invitation_confirmation_summary")
    asyncio.run(_assert_at_011(url))

    command.downgrade(config, "010_stage7_manual_invitations")
    asyncio.run(_assert_at_010(url))

    command.upgrade(config, "011_stage7_invitation_confirmation_summary")
    asyncio.run(_assert_at_011(url))


def test_011_is_compatible_with_manually_added_summary_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _isolated_url()
    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, "010_stage7_manual_invitations")

    async def _manual_add() -> None:
        conn = await _connect(url)
        try:
            await conn.execute(
                "ALTER TABLE interview_rounds "
                "ADD COLUMN invitation_confirmation_summary TEXT"
            )
        finally:
            await conn.close()

    asyncio.run(_manual_add())
    command.upgrade(config, "011_stage7_invitation_confirmation_summary")
    asyncio.run(_assert_at_011(url))


def test_011_fails_when_existing_summary_has_wrong_nullability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _isolated_url()
    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, "010_stage7_manual_invitations")

    async def _manual_bad() -> None:
        conn = await _connect(url)
        try:
            await conn.execute(
                "ALTER TABLE interview_rounds "
                "ADD COLUMN invitation_confirmation_summary TEXT NOT NULL "
                "DEFAULT ''"
            )
        finally:
            await conn.close()

    asyncio.run(_manual_bad())
    with pytest.raises(RuntimeError, match="NOT NULL"):
        command.upgrade(config, "011_stage7_invitation_confirmation_summary")
