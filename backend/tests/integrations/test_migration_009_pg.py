"""Live 008→009 upgrade/downgrade against an isolated PostgreSQL database.

Never points at the development business database (`recruit`).
Creates `recruit_test` when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
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
    "interview_rounds",
    "interview_round_interviewers",
    "interview_schedules",
    "interview_idempotency_keys",
}
REQUIRED_INDEXES = {
    "uq_interview_rounds_application_sequence",
    "uq_interview_schedules_one_active",
    "uq_interview_schedules_round_version",
    "uq_interview_idempotency_actor_action_scope_key",
}
REQUIRED_CHECKS = {
    "ck_interview_rounds_sequence_positive",
    "ck_interview_schedules_end_after_start",
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


def test_008_to_009_upgrade_downgrade_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = _isolated_url()
    asyncio.run(_ensure_isolated_database(isolated))
    original = os.environ.get("DATABASE_URL")
    _patch_database_url(monkeypatch, isolated)
    _reset_public_schema(isolated)
    config = _alembic_config()
    try:
        command.upgrade(config, "008_stage6_attempt_audit")
        command.upgrade(config, "009_stage7_interview_foundation")
        asyncio.run(_verify_009_schema_and_constraints(isolated))

        command.downgrade(config, "008_stage6_attempt_audit")
        asyncio.run(_assert_downgraded_to_008(isolated))

        command.upgrade(config, "009_stage7_interview_foundation")
        asyncio.run(_assert_version(isolated, "009_stage7_interview_foundation"))
    finally:
        if original is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", original)
        get_settings.cache_clear()


async def _assert_version(url_str: str, expected: str) -> None:
    conn = await _connect(url_str)
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version == expected
    finally:
        await conn.close()


async def _assert_downgraded_to_008(url_str: str) -> None:
    conn = await _connect(url_str)
    try:
        tables_after = {
            row["table_name"]
            for row in await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'interview_%'
                """
            )
        }
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert tables_after == set()
        assert version == "008_stage6_attempt_audit"
    finally:
        await conn.close()


async def _verify_009_schema_and_constraints(url_str: str) -> None:
    conn = await _connect(url_str)
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version == "009_stage7_interview_foundation"
        tables = {
            row["table_name"]
            for row in await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
        }
        assert REQUIRED_TABLES <= tables
        assert "notification_tasks" not in tables
        indexes = {
            row["indexname"]
            for row in await conn.fetch(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
        }
        assert REQUIRED_INDEXES <= indexes
        checks = {
            row["conname"]
            for row in await conn.fetch(
                """
                SELECT conname FROM pg_constraint
                WHERE contype = 'c'
                """
            )
        }
        assert REQUIRED_CHECKS <= checks
        fks = {
            row["conname"]
            for row in await conn.fetch(
                """
                SELECT conname FROM pg_constraint
                WHERE contype = 'f'
                """
            )
        }
        assert "fk_interview_rounds_current_schedule" in fks
        partial = await conn.fetchval(
            """
            SELECT pg_get_indexdef(i.oid)
            FROM pg_index x
            JOIN pg_class i ON i.oid = x.indexrelid
            WHERE i.relname = 'uq_interview_schedules_one_active'
            """
        )
        assert partial is not None
        assert "UNIQUE" in partial.upper()
        assert "ACTIVE" in partial
        await _assert_insert_constraints(conn)
    finally:
        await conn.close()


async def _assert_insert_constraints(conn: asyncpg.Connection) -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    job_id = uuid4()
    version_id = uuid4()
    candidate_id = uuid4()
    application_id = uuid4()
    round_id = uuid4()
    start = now
    end = now + timedelta(hours=1)

    await conn.execute(
        """
        INSERT INTO users (
            id, username, username_normalized, display_name, password_hash,
            is_active, must_change_password, token_version, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, true, false, 1, $6, $6)
        """,
        user_id,
        "it_user",
        "it_user",
        "IT User",
        "x",
        now,
    )
    await conn.execute(
        """
        INSERT INTO jobs (
            id, code, status, name, department, location, owner_name,
            created_at, updated_at
        ) VALUES ($1, $2, 'open', '测试岗', '研发', '上海', 'IT', $3, $3)
        """,
        job_id,
        "JOB-IT-009",
        now,
    )
    await conn.execute(
        """
        INSERT INTO job_versions (
            id, job_id, major, minor, status, raw_jd_text, structured_jd,
            score_dimensions, created_at, updated_at
        ) VALUES (
            $1, $2, 1, 0, 'published', '', '{}'::jsonb, '[]'::jsonb, $3, $3
        )
        """,
        version_id,
        job_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO candidates (id, name, created_at, updated_at)
        VALUES ($1, '测试候选人', $2, $2)
        """,
        candidate_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO job_applications (
            id, candidate_id, job_id, job_version_id, status,
            interview_started, interview_task_state, timeline_events,
            created_at, updated_at, pipeline_status, lock_version
        ) VALUES (
            $1, $2, $3, $4, 'in_progress', true, 'none', '[]'::jsonb,
            $5, $5, 'interviewing', 1
        )
        """,
        application_id,
        candidate_id,
        job_id,
        version_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO interview_rounds (
            id, application_id, job_version_id, name, sequence_no, status,
            format, owner_id, version, created_at, updated_at
        ) VALUES (
            $1, $2, $3, '一轮', 1, 'SCHEDULED', 'ONLINE', $4, 1, $5, $5
        )
        """,
        round_id,
        application_id,
        version_id,
        user_id,
        now,
    )

    first_schedule = uuid4()
    await conn.execute(
        """
        INSERT INTO interview_schedules (
            id, interview_round_id, schedule_version, status, start_at_utc,
            end_at_utc, timezone, format, created_at
        ) VALUES ($1, $2, 1, 'ACTIVE', $3, $4, 'Asia/Shanghai', 'ONLINE', $5)
        """,
        first_schedule,
        round_id,
        start,
        end,
        now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_schedules (
                id, interview_round_id, schedule_version, status, start_at_utc,
                end_at_utc, timezone, format, created_at
            ) VALUES ($1, $2, 2, 'ACTIVE', $3, $4, 'Asia/Shanghai', 'ONLINE', $5)
            """,
            uuid4(),
            round_id,
            start + timedelta(days=1),
            end + timedelta(days=1),
            now,
        )

    await conn.execute(
        """
        UPDATE interview_schedules SET status = 'SUPERSEDED' WHERE id = $1
        """,
        first_schedule,
    )
    await conn.execute(
        """
        INSERT INTO interview_schedules (
            id, interview_round_id, schedule_version, status, start_at_utc,
            end_at_utc, timezone, format, created_at
        ) VALUES ($1, $2, 2, 'ACTIVE', $3, $4, 'Asia/Shanghai', 'ONLINE', $5)
        """,
        uuid4(),
        round_id,
        start + timedelta(days=1),
        end + timedelta(days=1),
        now,
    )

    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO interview_schedules (
                id, interview_round_id, schedule_version, status, start_at_utc,
                end_at_utc, timezone, format, created_at
            ) VALUES ($1, $2, 3, 'CANCELLED', $3, $3, 'Asia/Shanghai', 'ONLINE', $4)
            """,
            uuid4(),
            round_id,
            start,
            now,
        )

    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_rounds (
                id, application_id, job_version_id, name, sequence_no, status,
                format, owner_id, version, created_at, updated_at
            ) VALUES (
                $1, $2, $3, '重复轮', 1, 'DRAFT', 'ONLINE', $4, 1, $5, $5
            )
            """,
            uuid4(),
            application_id,
            version_id,
            user_id,
            now,
        )
