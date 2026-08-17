"""Live 011→012 upgrade/downgrade against an isolated PostgreSQL database.

Never points at the development business database (`recruit`).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
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


async def _assert_at_011(url: str) -> None:
    """Downgrade/011 baseline: no 012 transcript objects on interview_rounds/tables."""
    conn = await _connect(url)
    try:
        tables = {
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert "interview_transcripts" not in tables
        assert "interview_transcript_versions" not in tables
        assert "interview_transcript_segments" not in tables
        mode = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'interview_rounds'
              AND column_name = 'transcript_completion_mode'
            """
        )
        assert mode is None
        # 012-only FK / check names must be gone after downgrade.
        constraints = {
            row["conname"]
            for row in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE contype IN ('f', 'c')"
            )
        }
        assert "fk_interview_rounds_transcript_completed_by" not in constraints
        assert "ck_interview_rounds_transcript_completion_mode" not in constraints
        assert "fk_transcripts_original_version" not in constraints
    finally:
        await conn.close()


async def _assert_at_012(url: str) -> None:
    """012 upgrade restores transcript tables, round columns, indexes and circular FKs."""
    conn = await _connect(url)
    try:
        tables = {
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert {
            "interview_transcripts",
            "interview_transcript_versions",
            "interview_transcript_segments",
        }.issubset(tables)

        columns = {
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'interview_rounds'
                  AND column_name LIKE 'transcript_%'
                """
            )
        }
        assert {
            "transcript_completion_mode",
            "transcript_completion_reason_code",
            "transcript_completion_reason_description",
            "transcript_completed_by",
            "transcript_completed_at",
        }.issubset(columns)

        indexes = {
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        }
        assert "uq_transcript_one_original" in indexes
        assert "uq_transcript_one_editing_draft" in indexes
        assert "uq_transcript_version_label" in indexes or any(
            "uq_transcript_version_label" in name for name in indexes
        )

        await assert_circular_version_fks_exist(conn)
        fks = {
            row["conname"]
            for row in await conn.fetch(
                """
                SELECT conname FROM pg_constraint
                WHERE contype = 'f'
                """
            )
        }
        assert "fk_interview_rounds_transcript_completed_by" in fks
    finally:
        await conn.close()


async def _seed_minimal_graph(conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    version_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    application_id = uuid.uuid4()
    round_id = uuid.uuid4()
    now = datetime.now(UTC)
    suffix = user_id.hex[:8]

    await conn.execute(
        """
        INSERT INTO users (
            id, username, username_normalized, display_name, password_hash,
            is_active, must_change_password, token_version, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, true, false, 0, $6, $6)
        """,
        user_id,
        f"u_{suffix}",
        f"u_{suffix}",
        "Transcript Tester",
        "hash",
        now,
    )
    await conn.execute(
        """
        INSERT INTO jobs (
            id, code, status, name, department, location, owner_name,
            created_at, updated_at
        ) VALUES ($1, $2, 'draft', 'Job', 'Dept', 'Loc', 'Owner', $3, $3)
        """,
        job_id,
        f"J{suffix[:6]}",
        now,
    )
    await conn.execute(
        """
        INSERT INTO job_versions (
            id, job_id, major, minor, status, raw_jd_text, structured_jd,
            score_dimensions, created_at, updated_at
        ) VALUES ($1, $2, 1, 0, 'draft', '', '{}'::jsonb, '[]'::jsonb, $3, $3)
        """,
        version_id,
        job_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO candidates (id, name, created_at, updated_at)
        VALUES ($1, 'Cand', $2, $2)
        """,
        candidate_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO job_applications (
            id, candidate_id, job_id, job_version_id, status, pipeline_status,
            lock_version, interview_started, interview_task_state, timeline_events,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, 'in_progress', 'pending_parse',
            1, false, 'none', '[]'::jsonb, $5, $5
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
            id, application_id, job_version_id, name, sequence_no, status, format,
            owner_id, version, created_by, created_at, updated_at
        ) VALUES ($1, $2, $3, 'R1', 1, 'PENDING_TRANSCRIPT', 'ONLINE', $4, 1, $4, $5, $5)
        """,
        round_id,
        application_id,
        version_id,
        user_id,
        now,
    )
    return {"user_id": user_id, "round_id": round_id}


async def assert_one_transcript_per_round(
    conn: asyncpg.Connection,
    *,
    round_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    transcript_id: uuid.UUID,
) -> None:
    await conn.execute(
        """
        INSERT INTO interview_transcripts (
            id, interview_round_id, version, created_by, created_at, updated_by, updated_at
        ) VALUES ($1, $2, 1, $3, $4, $3, $4)
        """,
        transcript_id,
        round_id,
        user_id,
        now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcripts (
                id, interview_round_id, version, created_by, created_at, updated_by, updated_at
            ) VALUES ($1, $2, 1, $3, $4, $3, $4)
            """,
            uuid.uuid4(),
            round_id,
            user_id,
            now,
        )


async def assert_one_original_per_transcript(
    conn: asyncpg.Connection,
    *,
    transcript_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    t1_id: uuid.UUID,
) -> None:
    await conn.execute(
        """
        INSERT INTO interview_transcript_versions (
            id, transcript_id, version_type, version_no, version_label, status,
            raw_text_encrypted, source_method, source_sha256,
            created_by, created_at, updated_by, updated_at, version
        ) VALUES (
            $1, $2, 'ORIGINAL', 1, 'T1', 'IMMUTABLE',
            'enc:v1:x', 'PASTE', 'abc',
            $3, $4, $3, $4, 1
        )
        """,
        t1_id,
        transcript_id,
        user_id,
        now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_versions (
                id, transcript_id, version_type, version_no, version_label, status,
                raw_text_encrypted, source_method, source_sha256,
                created_by, created_at, updated_by, updated_at, version
            ) VALUES (
                $1, $2, 'ORIGINAL', 2, 'T2', 'IMMUTABLE',
                'enc:v1:y', 'PASTE', 'def',
                $3, $4, $3, $4, 1
            )
            """,
            uuid.uuid4(),
            transcript_id,
            user_id,
            now,
        )


async def assert_one_editing_draft_per_transcript(
    conn: asyncpg.Connection,
    *,
    transcript_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    draft_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO interview_transcript_versions (
            id, transcript_id, version_type, version_no, version_label, status,
            raw_text_encrypted, source_method, source_sha256,
            created_by, created_at, updated_by, updated_at, version
        ) VALUES (
            $1, $2, 'DRAFT', 1, 'D1', 'EDITING',
            'enc:v1:d', 'PASTE', 'ghi',
            $3, $4, $3, $4, 1
        )
        """,
        draft_id,
        transcript_id,
        user_id,
        now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_versions (
                id, transcript_id, version_type, version_no, version_label, status,
                raw_text_encrypted, source_method, source_sha256,
                created_by, created_at, updated_by, updated_at, version
            ) VALUES (
                $1, $2, 'DRAFT', 2, 'D2', 'EDITING',
                'enc:v1:e', 'PASTE', 'jkl',
                $3, $4, $3, $4, 1
            )
            """,
            uuid.uuid4(),
            transcript_id,
            user_id,
            now,
        )
    return draft_id


async def assert_version_label_unique(
    conn: asyncpg.Connection,
    *,
    transcript_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> None:
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_versions (
                id, transcript_id, version_type, version_no, version_label, status,
                raw_text_encrypted, source_method, source_sha256,
                created_by, created_at, updated_by, updated_at, version
            ) VALUES (
                $1, $2, 'CONFIRMED', 1, 'D1', 'IMMUTABLE',
                'enc:v1:c', 'PASTE', 'mno',
                $3, $4, $3, $4, 1
            )
            """,
            uuid.uuid4(),
            transcript_id,
            user_id,
            now,
        )


async def assert_segment_no_unique(
    conn: asyncpg.Connection,
    *,
    version_id: uuid.UUID,
    now: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO interview_transcript_segments (
            id, transcript_version_id, segment_no, speaker_key, speaker_name,
            speaker_role, text_encrypted, source_type, source_segment_refs,
            is_included_in_analysis, is_unclear, created_at
        ) VALUES (
            $1, $2, 1, 's1', '面试官', 'INTERVIEWER', 'enc:v1:t1',
            'ORIGINAL', '[]'::jsonb, true, false, $3
        )
        """,
        uuid.uuid4(),
        version_id,
        now,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_segments (
                id, transcript_version_id, segment_no, speaker_key, speaker_name,
                speaker_role, text_encrypted, source_type, source_segment_refs,
                is_included_in_analysis, is_unclear, created_at
            ) VALUES (
                $1, $2, 1, 's2', '候选人', 'CANDIDATE', 'enc:v1:t2',
                'ORIGINAL', '[]'::jsonb, true, false, $3
            )
            """,
            uuid.uuid4(),
            version_id,
            now,
        )


async def assert_segment_no_positive(
    conn: asyncpg.Connection,
    *,
    version_id: uuid.UUID,
    now: datetime,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_segments (
                id, transcript_version_id, segment_no, speaker_key, speaker_name,
                speaker_role, text_encrypted, source_type, source_segment_refs,
                is_included_in_analysis, is_unclear, created_at
            ) VALUES (
                $1, $2, 0, 's1', '面试官', 'INTERVIEWER', 'enc:v1:t',
                'ORIGINAL', '[]'::jsonb, true, false, $3
            )
            """,
            uuid.uuid4(),
            version_id,
            now,
        )


async def assert_time_range_check(
    conn: asyncpg.Connection,
    *,
    version_id: uuid.UUID,
    now: datetime,
) -> None:
    # Both null OK
    await conn.execute(
        """
        INSERT INTO interview_transcript_segments (
            id, transcript_version_id, segment_no, speaker_key, speaker_name,
            speaker_role, start_time_ms, end_time_ms, text_encrypted, source_type,
            source_segment_refs, is_included_in_analysis, is_unclear, created_at
        ) VALUES (
            $1, $2, 2, 's1', '面试官', 'INTERVIEWER', NULL, NULL, 'enc:v1:ok',
            'ORIGINAL', '[]'::jsonb, true, false, $3
        )
        """,
        uuid.uuid4(),
        version_id,
        now,
    )
    # start >= end fails
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_segments (
                id, transcript_version_id, segment_no, speaker_key, speaker_name,
                speaker_role, start_time_ms, end_time_ms, text_encrypted, source_type,
                source_segment_refs, is_included_in_analysis, is_unclear, created_at
            ) VALUES (
                $1, $2, 3, 's1', '面试官', 'INTERVIEWER', 100, 50, 'enc:v1:t',
                'ORIGINAL', '[]'::jsonb, true, false, $3
            )
            """,
            uuid.uuid4(),
            version_id,
            now,
        )
    # Only start set fails (check requires both null or both set with start < end)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO interview_transcript_segments (
                id, transcript_version_id, segment_no, speaker_key, speaker_name,
                speaker_role, start_time_ms, end_time_ms, text_encrypted, source_type,
                source_segment_refs, is_included_in_analysis, is_unclear, created_at
            ) VALUES (
                $1, $2, 4, 's1', '面试官', 'INTERVIEWER', 10, NULL, 'enc:v1:t',
                'ORIGINAL', '[]'::jsonb, true, false, $3
            )
            """,
            uuid.uuid4(),
            version_id,
            now,
        )


async def assert_circular_version_fks_exist(conn: asyncpg.Connection) -> None:
    fks = {
        row["conname"]
        for row in await conn.fetch(
            """
            SELECT conname FROM pg_constraint
            WHERE contype = 'f'
              AND conname IN (
                'fk_transcripts_original_version',
                'fk_transcripts_current_draft_version',
                'fk_transcripts_current_confirmed_version'
              )
            """
        )
    }
    assert "fk_transcripts_original_version" in fks
    assert "fk_transcripts_current_draft_version" in fks
    assert "fk_transcripts_current_confirmed_version" in fks


async def assert_transcript_completed_by_on_delete_set_null(
    conn: asyncpg.Connection,
    *,
    round_id: uuid.UUID,
) -> None:
    completed_by = uuid.uuid4()
    now = datetime.now(UTC)
    suffix = completed_by.hex[:8]
    await conn.execute(
        """
        INSERT INTO users (
            id, username, username_normalized, display_name, password_hash,
            is_active, must_change_password, token_version, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, true, false, 0, $6, $6)
        """,
        completed_by,
        f"done_{suffix}",
        f"done_{suffix}",
        "Completed By",
        "hash",
        now,
    )
    await conn.execute(
        """
        UPDATE interview_rounds
        SET transcript_completed_by = $1,
            transcript_completed_at = $2,
            transcript_completion_mode = 'WITHOUT_TRANSCRIPT'
        WHERE id = $3
        """,
        completed_by,
        now,
        round_id,
    )
    await conn.execute("DELETE FROM users WHERE id = $1", completed_by)
    value = await conn.fetchval(
        "SELECT transcript_completed_by FROM interview_rounds WHERE id = $1",
        round_id,
    )
    assert value is None


async def _assert_constraints(url: str) -> None:
    conn = await _connect(url)
    try:
        ids = await _seed_minimal_graph(conn)
        round_id = ids["round_id"]
        user_id = ids["user_id"]
        now = datetime.now(UTC)
        transcript_id = uuid.uuid4()
        t1_id = uuid.uuid4()

        await assert_one_transcript_per_round(
            conn,
            round_id=round_id,
            user_id=user_id,
            now=now,
            transcript_id=transcript_id,
        )
        await assert_one_original_per_transcript(
            conn,
            transcript_id=transcript_id,
            user_id=user_id,
            now=now,
            t1_id=t1_id,
        )
        await assert_one_editing_draft_per_transcript(
            conn,
            transcript_id=transcript_id,
            user_id=user_id,
            now=now,
        )
        await assert_version_label_unique(
            conn,
            transcript_id=transcript_id,
            user_id=user_id,
            now=now,
        )
        await assert_segment_no_unique(conn, version_id=t1_id, now=now)
        await assert_segment_no_positive(conn, version_id=t1_id, now=now)
        await assert_time_range_check(conn, version_id=t1_id, now=now)
        await assert_circular_version_fks_exist(conn)
        await assert_transcript_completed_by_on_delete_set_null(
            conn, round_id=round_id
        )
    finally:
        await conn.close()


def test_011_to_012_roundtrip_and_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _isolated_url()
    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, "011_stage7_invitation_confirmation_summary")
    asyncio.run(_assert_at_011(url))

    command.upgrade(config, "012_transcript_workflow")
    asyncio.run(_assert_at_012(url))

    command.downgrade(config, "011_stage7_invitation_confirmation_summary")
    asyncio.run(_assert_at_011(url))

    command.upgrade(config, "012_transcript_workflow")
    asyncio.run(_assert_at_012(url))
    asyncio.run(_assert_constraints(url))
