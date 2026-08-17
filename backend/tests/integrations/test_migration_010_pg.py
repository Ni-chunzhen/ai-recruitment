"""Live 009→010 upgrade/downgrade against an isolated PostgreSQL database.

Never points at the development business database (`recruit`).
Creates `recruit_test` when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.engine.url import make_url

from alembic import command
from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_DB_NAMES = frozenset({"recruit", "postgres", "template0", "template1"})

REQUIRED_TABLES = {
    "interview_invitation_messages",
    "interview_invitation_versions",
    "interview_invitation_send_records",
}
REQUIRED_INDEXES = {
    "uq_invitation_msg_schedule_event_audience_recipient",
    "uq_invitation_version_message_version_no",
    "ix_invitation_messages_round_id",
    "ix_invitation_messages_schedule_id",
    "ix_invitation_messages_schedule_version",
    "ix_invitation_messages_event_type",
    "ix_invitation_messages_audience_type",
    "ix_invitation_messages_status",
    "ix_invitation_versions_message_id",
    "ix_invitation_versions_created_at",
    "ix_invitation_send_records_message_id",
    "ix_invitation_send_records_created_at",
}
REQUIRED_FKS = {
    "fk_invitation_messages_current_version",
}


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
            db_name = url.database
            owner = url.username
            await admin.execute(f'CREATE DATABASE "{db_name}" OWNER "{owner}"')
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


def test_009_to_010_upgrade_downgrade_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = _isolated_url()
    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, "009_stage7_interview_foundation")
    command.upgrade(config, "010_stage7_manual_invitations")

    async def _assert_schema() -> None:
        conn = await _connect(url)
        try:
            tables = {
                row["tablename"]
                for row in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }
            assert REQUIRED_TABLES.issubset(tables)

            indexes = {
                row["indexname"]
                for row in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
                )
            }
            assert REQUIRED_INDEXES.issubset(indexes)

            fks = {
                row["conname"]
                for row in await conn.fetch(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE contype = 'f' AND connamespace = 'public'::regnamespace
                    """
                )
            }
            assert REQUIRED_FKS.issubset(fks)

            email_col = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'email'
                """
            )
            assert email_col == 1

            confirmed_cols = {
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
            }.issubset(confirmed_cols)
            assert "invitation_confirmation_summary" not in confirmed_cols

            email_nullable = await conn.fetchval(
                """
                SELECT is_nullable FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'email'
                """
            )
            assert email_nullable == "YES"

            email_unique = await conn.fetchval(
                """
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'ix_users_email'
                  AND indexdef ILIKE '%UNIQUE%'
                """
            )
            assert email_unique is None
        finally:
            await conn.close()

    asyncio.run(_assert_schema())

    command.downgrade(config, "009_stage7_interview_foundation")

    async def _assert_downgraded() -> None:
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

            email_col = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'email'
                """
            )
            assert email_col is None

            confirmed_cols = await conn.fetchval(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'interview_rounds'
                  AND column_name LIKE 'invitation_confirm%'
                """
            )
            assert confirmed_cols == 0
        finally:
            await conn.close()

    asyncio.run(_assert_downgraded())

    command.upgrade(config, "010_stage7_manual_invitations")
    asyncio.run(_assert_schema())
    asyncio.run(_assert_unique_and_cycle_constraints(url))


async def _assert_unique_and_cycle_constraints(url: str) -> None:
    conn = await _connect(url)
    try:
        # Minimal parent rows for FK integrity
        user_id = uuid4()
        now = datetime.now(UTC)
        await conn.execute(
            """
            INSERT INTO users (
                id, username, username_normalized, display_name,
                password_hash, is_active, must_change_password, token_version,
                created_at, updated_at, email
            ) VALUES ($1, $2, $3, $4, $5, true, false, 0, $6, $6, $7)
            """,
            user_id,
            f"u-{user_id.hex[:8]}",
            f"u-{user_id.hex[:8]}",
            "Tester",
            "hash",
            now,
            "tester@example.com",
        )

        # Reuse fixtures from 009 live test shape when possible — create
        # application/job graph is heavy; uniqueness can be validated via
        # constraint catalog + a focused insert after foundation rows exist.
        # Here we verify constraint names and circular FK presence.
        cycle = await conn.fetchval(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_invitation_messages_current_version'
            """
        )
        assert cycle == 1

        uq_msg = await conn.fetchval(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_invitation_msg_schedule_event_audience_recipient'
            """
        )
        assert uq_msg == 1

        uq_ver = await conn.fetchval(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_invitation_version_message_version_no'
            """
        )
        assert uq_ver == 1
    finally:
        await conn.close()
