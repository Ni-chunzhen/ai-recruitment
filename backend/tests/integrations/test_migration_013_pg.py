"""Live 012→013 tests on recruit_test only + isolated URL safety unit tests.

Corrected for dimension_key-independent schema locks, version-layer input FKs,
sensitive attempt encrypted columns, and ck_ai_tasks_task_type introduction.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.engine.url import make_url

from alembic import command
from alembic.script import ScriptDirectory
from app.core.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_DB_NAMES = frozenset({"recruit", "postgres", "template0", "template1"})
REVISION = "013_stage8_interview_ai_foundation"
DOWN_REVISION = "012_transcript_workflow"
REQUIRED_DATABASE = "recruit_test"

EXPECTED_TABLES = (
    "interview_question_sets",
    "interview_question_versions",
    "interview_question_items",
    "interview_round_analyses",
    "interview_round_analysis_versions",
    "interview_round_analysis_dimensions",
    "interview_round_analysis_evidence",
)

TRANSCRIPT_TABLES_012 = (
    "interview_transcripts",
    "interview_transcript_versions",
    "interview_transcript_segments",
)

EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    # table -> column -> (data_type, is_nullable YES/NO)
    "interview_question_sets": {
        "id": ("uuid", "NO"),
        "interview_round_id": ("uuid", "NO"),
        "current_version_id": ("uuid", "YES"),
        "status": ("character varying", "NO"),
        "confirmed_by": ("uuid", "YES"),
        "confirmed_at": ("timestamp with time zone", "YES"),
        "created_by": ("uuid", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    },
    "interview_question_versions": {
        "id": ("uuid", "NO"),
        "question_set_id": ("uuid", "NO"),
        "version_no": ("integer", "NO"),
        "version_label": ("character varying", "NO"),
        "source_type": ("character varying", "NO"),
        "ai_task_id": ("uuid", "YES"),
        "job_version_id": ("uuid", "NO"),
        "resume_version_id": ("uuid", "NO"),
        "input_snapshot_hash": ("character varying", "NO"),
        "created_by": ("uuid", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
    },
    "interview_question_items": {
        "id": ("uuid", "NO"),
        "question_version_id": ("uuid", "NO"),
        "dimension_key": ("character varying", "NO"),
        "question_encrypted": ("text", "NO"),
        "purpose_encrypted": ("text", "NO"),
        "evidence_source": ("character varying", "NO"),
        "resume_evidence_encrypted": ("text", "YES"),
        "follow_up_prompts_encrypted": ("text", "NO"),
        "risk_flags_encrypted": ("text", "NO"),
        "display_order": ("integer", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    },
    "interview_round_analyses": {
        "id": ("uuid", "NO"),
        "interview_round_id": ("uuid", "NO"),
        "current_version_id": ("uuid", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
        "updated_at": ("timestamp with time zone", "NO"),
    },
    "interview_round_analysis_versions": {
        "id": ("uuid", "NO"),
        "analysis_id": ("uuid", "NO"),
        "version_no": ("integer", "NO"),
        "version_label": ("character varying", "NO"),
        "transcript_version_id": ("uuid", "NO"),
        "job_version_id": ("uuid", "NO"),
        "ai_task_id": ("uuid", "NO"),
        "dimensions_snapshot": ("jsonb", "NO"),
        "overall_score": ("numeric", "YES"),
        "overall_summary_encrypted": ("text", "NO"),
        "created_by": ("uuid", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
    },
    "interview_round_analysis_dimensions": {
        "id": ("uuid", "NO"),
        "analysis_version_id": ("uuid", "NO"),
        "dimension_key": ("character varying", "NO"),
        "dimension_name": ("character varying", "NO"),
        "weight": ("numeric", "NO"),
        "score": ("integer", "YES"),
        "analysis_encrypted": ("text", "NO"),
        "strengths_encrypted": ("text", "NO"),
        "risks_encrypted": ("text", "NO"),
        "insufficient_information_encrypted": ("text", "YES"),
        "suggested_follow_ups_encrypted": ("text", "NO"),
        "display_order": ("integer", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    },
    "interview_round_analysis_evidence": {
        "id": ("uuid", "NO"),
        "analysis_dimension_id": ("uuid", "NO"),
        "transcript_segment_id": ("uuid", "NO"),
        "segment_no": ("integer", "NO"),
        "quote_encrypted": ("text", "NO"),
        "created_at": ("timestamp with time zone", "NO"),
    },
}


def _isolated_url() -> str:
    """Always require database name recruit_test — no exceptions."""
    explicit = os.environ.get("TEST_DATABASE_URL", "").strip()
    if explicit:
        url = make_url(explicit)
    else:
        url = make_url(get_settings().database_url).set(database=REQUIRED_DATABASE)
    database = url.database or ""
    if database != REQUIRED_DATABASE:
        raise RuntimeError(
            f"refusing migrations on database {database!r}; "
            f"only {REQUIRED_DATABASE!r} is allowed"
        )
    if database in BUSINESS_DB_NAMES:
        raise RuntimeError(
            f"refusing destructive migrations on business database {database!r}"
        )
    return url.render_as_string(hide_password=False)


def _assert_url_is_recruit_test(url_str: str) -> None:
    database = make_url(url_str).database
    assert database == REQUIRED_DATABASE


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(_alembic_config())


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
    _assert_url_is_recruit_test(url_str)
    url = make_url(url_str)
    return await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
    )


async def _ensure_isolated_database(url_str: str) -> None:
    _assert_url_is_recruit_test(url_str)
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
    _assert_url_is_recruit_test(url)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()


def _reset_public_schema(url_str: str) -> None:
    _assert_url_is_recruit_test(url_str)

    async def _run() -> None:
        conn = await _connect(url_str)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("GRANT ALL ON SCHEMA public TO public")
        finally:
            await conn.close()

    asyncio.run(_run())


# ---- database safety unit tests (must PASS without 013) ----


def test_isolated_url_rejects_recruit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/recruit",
    )
    with pytest.raises(RuntimeError, match="recruit_test"):
        _isolated_url()


def test_isolated_url_rejects_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/postgres",
    )
    with pytest.raises(RuntimeError, match="recruit_test"):
        _isolated_url()


def test_isolated_url_rejects_other_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/other_test",
    )
    with pytest.raises(RuntimeError, match="recruit_test"):
        _isolated_url()


def test_isolated_url_accepts_recruit_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/recruit_test",
    )
    url = _isolated_url()
    assert make_url(url).database == "recruit_test"


async def _table_names(url: str) -> set[str]:
    conn = await _connect(url)
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        return {row["tablename"] for row in rows}
    finally:
        await conn.close()


async def _assert_at_012_without_013(url: str) -> None:
    tables = await _table_names(url)
    for name in TRANSCRIPT_TABLES_012:
        assert name in tables
    for name in EXPECTED_TABLES:
        assert name not in tables
    conn = await _connect(url)
    try:
        # 012 baseline: no task_type check constraint
        ck = await conn.fetchval(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'ck_ai_tasks_task_type'
            """
        )
        assert ck is None
        # documenting real 012 behavior: novel task_type strings are allowed
        task_id = uuid.uuid4()
        now = datetime.now(UTC)
        await conn.execute(
            """
            INSERT INTO ai_tasks (
                id, task_type, status, business_type, business_id, input_snapshot,
                attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
            ) VALUES (
                $1, 'INTERVIEW_QUESTION_GENERATE', 'pending', 'interview_round', $2,
                '{}'::jsonb, 0, 0, 0, $3, $3
            )
            """,
            task_id,
            uuid.uuid4(),
            now,
        )
        await conn.execute("DELETE FROM ai_tasks WHERE id = $1", task_id)
        sens = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_task_attempts'
              AND column_name = 'sensitive_request_encrypted'
            """
        )
        assert sens is None
    finally:
        await conn.close()


async def _assert_at_013(url: str) -> None:
    tables = await _table_names(url)
    for name in EXPECTED_TABLES:
        assert name in tables
    for name in TRANSCRIPT_TABLES_012:
        assert name in tables

    conn = await _connect(url)
    try:
        for table, cols in EXPECTED_COLUMNS.items():
            rows = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                """,
                table,
            )
            actual = {
                r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows
            }
            for col, expected in cols.items():
                assert col in actual, f"{table}.{col} missing"
                data_type, nullable = actual[col]
                exp_type, exp_null = expected
                assert data_type == exp_type, f"{table}.{col} type {data_type}"
                assert nullable == exp_null, f"{table}.{col} nullability"

        # attempt encrypted infrastructure columns
        for col in ("sensitive_request_encrypted", "sensitive_response_encrypted"):
            row = await conn.fetchrow(
                """
                SELECT data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'ai_task_attempts' AND column_name = $1
                """,
                col,
            )
            assert row is not None
            assert row["data_type"] == "text"
            assert row["is_nullable"] == "YES"

        # no PG enums created by 013 for these tables
        enums = await conn.fetch(
            """
            SELECT t.typname
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname LIKE 'interview_%'
            """
        )
        assert enums == []

        fks = await conn.fetch(
            """
            SELECT c.conname, c.confdeltype::text AS confdeltype,
                   rel_from.relname AS from_table,
                   rel_to.relname AS to_table
            FROM pg_constraint c
            JOIN pg_class rel_from ON rel_from.oid = c.conrelid
            JOIN pg_class rel_to ON rel_to.oid = c.confrelid
            WHERE c.contype = 'f'
              AND rel_from.relname = ANY($1::text[])
            """,
            list(EXPECTED_TABLES),
        )
        fk_names = {r["conname"] for r in fks}
        assert "fk_question_sets_current_version" in fk_names
        assert "fk_round_analyses_current_version" in fk_names
        for r in fks:
            assert len(r["conname"]) <= 63
            if r["conname"] in {
                "fk_question_sets_current_version",
                "fk_round_analyses_current_version",
            }:
                assert r["confdeltype"] == "n"  # SET NULL

        checks = await conn.fetch(
            """
            SELECT conname FROM pg_constraint
            WHERE contype = 'c' AND conname = 'ck_ai_tasks_task_type'
            """
        )
        assert len(checks) == 1

        # 013 rejects unknown task types; accepts stage8 + legacy
        now = datetime.now(UTC)
        ok_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ai_tasks (
                id, task_type, status, business_type, business_id, input_snapshot,
                attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
            ) VALUES (
                $1, 'INTERVIEW_ROUND_ANALYZE', 'pending', 'interview_round', $2,
                '{}'::jsonb, 0, 0, 0, $3, $3
            )
            """,
            ok_id,
            uuid.uuid4(),
            now,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO ai_tasks (
                    id, task_type, status, business_type, business_id, input_snapshot,
                    attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
                ) VALUES (
                    $1, 'NOT_A_REAL_TYPE', 'pending', 'interview_round', $2,
                    '{}'::jsonb, 0, 0, 0, $3, $3
                )
                """,
                uuid.uuid4(),
                uuid.uuid4(),
                now,
            )
        resume_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ai_tasks (
                id, task_type, status, business_type, business_id, input_snapshot,
                attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
            ) VALUES (
                $1, 'RESUME_SCORE', 'pending', 'application', $2,
                '{}'::jsonb, 0, 0, 0, $3, $3
            )
            """,
            resume_id,
            uuid.uuid4(),
            now,
        )
        await conn.execute("DELETE FROM ai_tasks WHERE id = ANY($1::uuid[])", [ok_id, resume_id])
    finally:
        await conn.close()


async def _seed_graph(conn: asyncpg.Connection) -> dict[str, Any]:
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_version_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    resume_id = uuid.uuid4()
    resume_version_id = uuid.uuid4()
    application_id = uuid.uuid4()
    round_id = uuid.uuid4()
    transcript_id = uuid.uuid4()
    transcript_version_id = uuid.uuid4()
    segment_id = uuid.uuid4()
    segment_id_b = uuid.uuid4()
    ai_task_id = uuid.uuid4()
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
        f"ai_{suffix}",
        f"ai_{suffix}",
        "AI Tester",
        "hash",
        now,
    )
    await conn.execute(
        """
        INSERT INTO jobs (
            id, code, status, name, department, location, owner_name,
            created_at, updated_at
        ) VALUES ($1, $2, 'open', 'Job', 'Dept', 'Loc', 'Owner', $3, $3)
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
        ) VALUES ($1, $2, 1, 0, 'published', 'jd', '{}'::jsonb, '[]'::jsonb, $3, $3)
        """,
        job_version_id,
        job_id,
        now,
    )
    await conn.execute(
        "INSERT INTO candidates (id, name, created_at, updated_at) VALUES ($1, 'Cand', $2, $2)",
        candidate_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO resumes (id, candidate_id, is_void, created_at, updated_at)
        VALUES ($1, $2, false, $3, $3)
        """,
        resume_id,
        candidate_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO resume_versions (
            id, resume_id, kind, version_label, status, created_at, updated_at
        ) VALUES ($1, $2, 'confirmed', 'C1', 'confirmed', $3, $3)
        """,
        resume_version_id,
        resume_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO job_applications (
            id, candidate_id, job_id, job_version_id, status, pipeline_status,
            resume_version_id, lock_version, interview_started, interview_task_state,
            timeline_events, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, 'in_progress', 'interviewing',
            $5, 1, true, 'active', '[]'::jsonb, $6, $6
        )
        """,
        application_id,
        candidate_id,
        job_id,
        job_version_id,
        resume_version_id,
        now,
    )
    await conn.execute(
        """
        INSERT INTO interview_rounds (
            id, application_id, job_version_id, name, sequence_no, status, format,
            owner_id, version, created_by, created_at, updated_at,
            transcript_completion_mode
        ) VALUES (
            $1, $2, $3, 'R1', 1, 'COMPLETED', 'ONLINE',
            $4, 1, $4, $5, $5, 'CONFIRMED_TRANSCRIPT'
        )
        """,
        round_id,
        application_id,
        job_version_id,
        user_id,
        now,
    )
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
    await conn.execute(
        """
        INSERT INTO interview_transcript_versions (
            id, transcript_id, version_type, version_no, version_label, status,
            raw_text_encrypted, source_method, source_sha256,
            created_by, created_at, updated_by, updated_at, version
        ) VALUES (
            $1, $2, 'CONFIRMED', 1, 'C1', 'IMMUTABLE',
            'enc:v1:raw', 'PASTE', 'abc',
            $3, $4, $3, $4, 1
        )
        """,
        transcript_version_id,
        transcript_id,
        user_id,
        now,
    )
    await conn.execute(
        """
        UPDATE interview_transcripts
        SET current_confirmed_version_id = $1, original_version_id = $1
        WHERE id = $2
        """,
        transcript_version_id,
        transcript_id,
    )
    for seg_id, seg_no in ((segment_id, 1), (segment_id_b, 2)):
        await conn.execute(
            """
            INSERT INTO interview_transcript_segments (
                id, transcript_version_id, segment_no, speaker_key, speaker_name,
                speaker_role, text_encrypted, source_type, source_segment_refs,
                is_included_in_analysis, is_unclear, created_at
            ) VALUES (
                $1, $2, $3, 'S1', 'Cand', 'CANDIDATE', 'enc:v1:text', 'ORIGINAL',
                '[]'::jsonb, true, false, $4
            )
            """,
            seg_id,
            transcript_version_id,
            seg_no,
            now,
        )
    await conn.execute(
        """
        INSERT INTO ai_tasks (
            id, task_type, status, business_type, business_id, input_snapshot,
            attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
        ) VALUES (
            $1, 'INTERVIEW_QUESTION_GENERATE', 'succeeded', 'interview_round', $2,
            '{"schema_version":"1.0"}'::jsonb, 1, 0, 1, $3, $3
        )
        """,
        ai_task_id,
        round_id,
        now,
    )
    return {
        "user_id": user_id,
        "job_version_id": job_version_id,
        "resume_version_id": resume_version_id,
        "round_id": round_id,
        "transcript_version_id": transcript_version_id,
        "segment_id": segment_id,
        "segment_id_b": segment_id_b,
        "ai_task_id": ai_task_id,
        "now": now,
    }


async def _assert_question_set_status_rules(
    conn: asyncpg.Connection, ids: dict[str, Any]
) -> None:
    now = ids["now"]
    qset_id = uuid.uuid4()
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            INSERT INTO interview_question_sets (
                id, interview_round_id, status, created_by, created_at, updated_at
            ) VALUES ($1, $2, 'NOPE', $3, $4, $4)
            """,
            uuid.uuid4(),
            ids["round_id"],
            ids["user_id"],
            now,
        )
    await conn.execute(
        """
        INSERT INTO interview_question_sets (
            id, interview_round_id, status, created_by, created_at, updated_at
        ) VALUES ($1, $2, 'DRAFT', $3, $4, $4)
        """,
        qset_id,
        ids["round_id"],
        ids["user_id"],
        now,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            UPDATE interview_question_sets
            SET confirmed_by = $1, confirmed_at = NULL WHERE id = $2
            """,
            ids["user_id"],
            qset_id,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            UPDATE interview_question_sets
            SET confirmed_by = NULL, confirmed_at = $1 WHERE id = $2
            """,
            now,
            qset_id,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            UPDATE interview_question_sets
            SET status = 'READY', confirmed_by = $1, confirmed_at = $2,
                current_version_id = NULL
            WHERE id = $3
            """,
            ids["user_id"],
            now,
            qset_id,
        )
    qver = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO interview_question_versions (
            id, question_set_id, version_no, version_label, source_type,
            ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
            created_by, created_at
        ) VALUES (
            $1, $2, 1, 'Q1', 'AI_GENERATED', $3, $4, $5, 'hash1', $6, $7
        )
        """,
        qver,
        qset_id,
        ids["ai_task_id"],
        ids["job_version_id"],
        ids["resume_version_id"],
        ids["user_id"],
        now,
    )
    await conn.execute(
        """
        UPDATE interview_question_sets
        SET current_version_id = $1, status = 'READY',
            confirmed_by = $2, confirmed_at = $3
        WHERE id = $4
        """,
        qver,
        ids["user_id"],
        now,
        qset_id,
    )
    # Note: DB cannot alone guarantee current_version belongs to same set — service must.
    # Deleting current version: CASCADE is from set→versions; deleting version alone SET NULLs pointer.
    await conn.execute(
        "UPDATE interview_question_sets SET status = 'DRAFT', confirmed_by = NULL, confirmed_at = NULL WHERE id = $1",
        qset_id,
    )
    await conn.execute("DELETE FROM interview_question_versions WHERE id = $1", qver)
    pointer = await conn.fetchval(
        "SELECT current_version_id FROM interview_question_sets WHERE id = $1",
        qset_id,
    )
    assert pointer is None
    return qset_id


async def _assert_013_row_constraints(url: str) -> None:
    conn = await _connect(url)
    try:
        ids = await _seed_graph(conn)
        now = ids["now"]
        qset_id = await _assert_question_set_status_rules(conn, ids)

        # recreate AI task + version for remaining tests after version delete
        ai_task_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ai_tasks (
                id, task_type, status, business_type, business_id, input_snapshot,
                attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
            ) VALUES (
                $1, 'INTERVIEW_QUESTION_GENERATE', 'succeeded', 'interview_round', $2,
                '{}'::jsonb, 1, 0, 1, $3, $3
            )
            """,
            ai_task_id,
            ids["round_id"],
            now,
        )
        qver_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO interview_question_versions (
                id, question_set_id, version_no, version_label, source_type,
                ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                created_by, created_at
            ) VALUES (
                $1, $2, 1, 'Q1', 'AI_GENERATED', $3, $4, $5, 'hash1', $6, $7
            )
            """,
            qver_id,
            qset_id,
            ai_task_id,
            ids["job_version_id"],
            ids["resume_version_id"],
            ids["user_id"],
            now,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            zero_task = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO ai_tasks (
                    id, task_type, status, business_type, business_id, input_snapshot,
                    attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
                ) VALUES (
                    $1, 'INTERVIEW_QUESTION_GENERATE', 'succeeded', 'interview_round', $2,
                    '{}'::jsonb, 1, 0, 1, $3, $3
                )
                """,
                zero_task,
                ids["round_id"],
                now,
            )
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 0, 'Q0', 'AI_GENERATED', $3, $4, $5, 'h', $6, $7
                )
                """,
                uuid.uuid4(),
                qset_id,
                zero_task,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 2, 'Q2', 'NOPE', NULL, $3, $4, 'h', $5, $6
                )
                """,
                uuid.uuid4(),
                qset_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 2, 'Q2', 'AI_GENERATED', NULL, $3, $4, 'h', $5, $6
                )
                """,
                uuid.uuid4(),
                qset_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 2, 'Q2', 'MANUAL_EDIT', $3, $4, $5, 'h', $6, $7
                )
                """,
                uuid.uuid4(),
                qset_id,
                ai_task_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 2, 'Q2', 'AI_GENERATED', $3, $4, $5, 'h', $6, $7
                )
                """,
                uuid.uuid4(),
                qset_id,
                ai_task_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 1, 'QX', 'MANUAL_EDIT', NULL, $3, $4, 'h', $5, $6
                )
                """,
                uuid.uuid4(),
                qset_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_versions (
                    id, question_set_id, version_no, version_label, source_type,
                    ai_task_id, job_version_id, resume_version_id, input_snapshot_hash,
                    created_by, created_at
                ) VALUES (
                    $1, $2, 3, 'Q1', 'MANUAL_EDIT', NULL, $3, $4, 'h', $5, $6
                )
                """,
                uuid.uuid4(),
                qset_id,
                ids["job_version_id"],
                ids["resume_version_id"],
                ids["user_id"],
                now,
            )

        await conn.execute(
            """
            INSERT INTO interview_question_items (
                id, question_version_id, dimension_key, question_encrypted,
                purpose_encrypted, evidence_source, follow_up_prompts_encrypted,
                risk_flags_encrypted, display_order, created_at
            ) VALUES (
                $1, $2, 'D001', 'enc:q', 'enc:p', 'JOB_REQUIREMENT',
                'enc:[]', 'enc:[]', 1, $3
            )
            """,
            uuid.uuid4(),
            qver_id,
            now,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_items (
                    id, question_version_id, dimension_key, question_encrypted,
                    purpose_encrypted, evidence_source, follow_up_prompts_encrypted,
                    risk_flags_encrypted, display_order, created_at
                ) VALUES (
                    $1, $2, 'D001', 'enc:q', 'enc:p', 'GENERAL',
                    'enc:[]', 'enc:[]', 0, $3
                )
                """,
                uuid.uuid4(),
                qver_id,
                now,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_items (
                    id, question_version_id, dimension_key, question_encrypted,
                    purpose_encrypted, evidence_source, follow_up_prompts_encrypted,
                    risk_flags_encrypted, display_order, created_at
                ) VALUES (
                    $1, $2, 'D002', 'enc:q', 'enc:p', 'GENERAL',
                    'enc:[]', 'enc:[]', 1, $3
                )
                """,
                uuid.uuid4(),
                qver_id,
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_question_items (
                    id, question_version_id, dimension_key, question_encrypted,
                    purpose_encrypted, evidence_source, follow_up_prompts_encrypted,
                    risk_flags_encrypted, display_order, created_at
                ) VALUES (
                    $1, $2, 'D002', 'enc:q', 'enc:p', 'BAD',
                    'enc:[]', 'enc:[]', 2, $3
                )
                """,
                uuid.uuid4(),
                qver_id,
                now,
            )

        # analysis aggregate uniqueness + dimension/evidence rules
        analysis_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO interview_round_analyses (
                id, interview_round_id, created_at, updated_at
            ) VALUES ($1, $2, $3, $3)
            """,
            analysis_id,
            ids["round_id"],
            now,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analyses (
                    id, interview_round_id, created_at, updated_at
                ) VALUES ($1, $2, $3, $3)
                """,
                uuid.uuid4(),
                ids["round_id"],
                now,
            )

        analyze_task = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO ai_tasks (
                id, task_type, status, business_type, business_id, input_snapshot,
                attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
            ) VALUES (
                $1, 'INTERVIEW_ROUND_ANALYZE', 'succeeded', 'interview_round', $2,
                '{}'::jsonb, 1, 0, 1, $3, $3
            )
            """,
            analyze_task,
            ids["round_id"],
            now,
        )
        aver_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO interview_round_analysis_versions (
                id, analysis_id, version_no, version_label, transcript_version_id,
                job_version_id, ai_task_id, dimensions_snapshot, overall_score,
                overall_summary_encrypted, created_by, created_at
            ) VALUES (
                $1, $2, 1, 'A1', $3, $4, $5, '[]'::jsonb, NULL,
                'enc:summary', $6, $7
            )
            """,
            aver_id,
            analysis_id,
            ids["transcript_version_id"],
            ids["job_version_id"],
            analyze_task,
            ids["user_id"],
            now,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            t = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO ai_tasks (
                    id, task_type, status, business_type, business_id, input_snapshot,
                    attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
                ) VALUES (
                    $1, 'INTERVIEW_ROUND_ANALYZE', 'succeeded', 'interview_round', $2,
                    '{}'::jsonb, 1, 0, 1, $3, $3
                )
                """,
                t,
                ids["round_id"],
                now,
            )
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_versions (
                    id, analysis_id, version_no, version_label, transcript_version_id,
                    job_version_id, ai_task_id, dimensions_snapshot, overall_score,
                    overall_summary_encrypted, created_by, created_at
                ) VALUES (
                    $1, $2, 0, 'A0', $3, $4, $5, '[]'::jsonb, 3,
                    'enc:summary', $6, $7
                )
                """,
                uuid.uuid4(),
                analysis_id,
                ids["transcript_version_id"],
                ids["job_version_id"],
                t,
                ids["user_id"],
                now,
            )
        for bad_score in (0.5, 9.0):
            t = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO ai_tasks (
                    id, task_type, status, business_type, business_id, input_snapshot,
                    attempt_count, retry_cycle_no, cycle_attempt_count, created_at, updated_at
                ) VALUES (
                    $1, 'INTERVIEW_ROUND_ANALYZE', 'succeeded', 'interview_round', $2,
                    '{}'::jsonb, 1, 0, 1, $3, $3
                )
                """,
                t,
                ids["round_id"],
                now,
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO interview_round_analysis_versions (
                        id, analysis_id, version_no, version_label, transcript_version_id,
                        job_version_id, ai_task_id, dimensions_snapshot, overall_score,
                        overall_summary_encrypted, created_by, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, '[]'::jsonb, $8,
                        'enc:summary', $9, $10
                    )
                    """,
                    uuid.uuid4(),
                    analysis_id,
                    10 + int(bad_score),
                    f"AB{bad_score}",
                    ids["transcript_version_id"],
                    ids["job_version_id"],
                    t,
                    bad_score,
                    ids["user_id"],
                    now,
                )

        dim_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO interview_round_analysis_dimensions (
                id, analysis_version_id, dimension_key, dimension_name, weight,
                score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                insufficient_information_encrypted, suggested_follow_ups_encrypted,
                display_order, created_at
            ) VALUES (
                $1, $2, 'D001', 'Dim A', 50.00, 4, 'enc:a', 'enc:[]', 'enc:[]',
                NULL, 'enc:[]', 1, $3
            )
            """,
            dim_id,
            aver_id,
            now,
        )
        dim_b = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO interview_round_analysis_dimensions (
                id, analysis_version_id, dimension_key, dimension_name, weight,
                score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                insufficient_information_encrypted, suggested_follow_ups_encrypted,
                display_order, created_at
            ) VALUES (
                $1, $2, 'D002', 'Dim B', 50.00, NULL, 'enc:a', 'enc:[]', 'enc:[]',
                'enc:why', 'enc:[]', 2, $3
            )
            """,
            dim_b,
            aver_id,
            now,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_dimensions (
                    id, analysis_version_id, dimension_key, dimension_name, weight,
                    score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                    insufficient_information_encrypted, suggested_follow_ups_encrypted,
                    display_order, created_at
                ) VALUES (
                    $1, $2, 'D003', 'X', 0, 3, 'enc:a', 'enc:[]', 'enc:[]',
                    NULL, 'enc:[]', 3, $3
                )
                """,
                uuid.uuid4(),
                aver_id,
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_dimensions (
                    id, analysis_version_id, dimension_key, dimension_name, weight,
                    score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                    insufficient_information_encrypted, suggested_follow_ups_encrypted,
                    display_order, created_at
                ) VALUES (
                    $1, $2, 'D003', 'X', 101, 3, 'enc:a', 'enc:[]', 'enc:[]',
                    NULL, 'enc:[]', 3, $3
                )
                """,
                uuid.uuid4(),
                aver_id,
                now,
            )
        for bad in (0, 6):
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO interview_round_analysis_dimensions (
                        id, analysis_version_id, dimension_key, dimension_name, weight,
                        score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                        insufficient_information_encrypted, suggested_follow_ups_encrypted,
                        display_order, created_at
                    ) VALUES (
                        $1, $2, $3, 'X', 10, $4, 'enc:a', 'enc:[]', 'enc:[]',
                        NULL, 'enc:[]', $5, $6
                    )
                    """,
                    uuid.uuid4(),
                    aver_id,
                    f"D1{bad}",
                    bad,
                    10 + bad,
                    now,
                )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_dimensions (
                    id, analysis_version_id, dimension_key, dimension_name, weight,
                    score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                    insufficient_information_encrypted, suggested_follow_ups_encrypted,
                    display_order, created_at
                ) VALUES (
                    $1, $2, 'D010', 'X', 10, 3, 'enc:a', 'enc:[]', 'enc:[]',
                    'enc:why', 'enc:[]', 20, $3
                )
                """,
                uuid.uuid4(),
                aver_id,
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_dimensions (
                    id, analysis_version_id, dimension_key, dimension_name, weight,
                    score, analysis_encrypted, strengths_encrypted, risks_encrypted,
                    insufficient_information_encrypted, suggested_follow_ups_encrypted,
                    display_order, created_at
                ) VALUES (
                    $1, $2, 'D011', 'X', 10, NULL, 'enc:a', 'enc:[]', 'enc:[]',
                    NULL, 'enc:[]', 21, $3
                )
                """,
                uuid.uuid4(),
                aver_id,
                now,
            )

        await conn.execute(
            """
            INSERT INTO interview_round_analysis_evidence (
                id, analysis_dimension_id, transcript_segment_id, segment_no,
                quote_encrypted, created_at
            ) VALUES ($1, $2, $3, 1, 'enc:quote', $4)
            """,
            uuid.uuid4(),
            dim_id,
            ids["segment_id"],
            now,
        )
        # same segment allowed on different dimension
        await conn.execute(
            """
            INSERT INTO interview_round_analysis_evidence (
                id, analysis_dimension_id, transcript_segment_id, segment_no,
                quote_encrypted, created_at
            ) VALUES ($1, $2, $3, 1, 'enc:quote', $4)
            """,
            uuid.uuid4(),
            dim_b,
            ids["segment_id"],
            now,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_evidence (
                    id, analysis_dimension_id, transcript_segment_id, segment_no,
                    quote_encrypted, created_at
                ) VALUES ($1, $2, $3, 1, 'enc:quote2', $4)
                """,
                uuid.uuid4(),
                dim_id,
                ids["segment_id"],
                now,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO interview_round_analysis_evidence (
                    id, analysis_dimension_id, transcript_segment_id, segment_no,
                    quote_encrypted, created_at
                ) VALUES ($1, $2, $3, 0, 'enc:quote', $4)
                """,
                uuid.uuid4(),
                dim_id,
                ids["segment_id_b"],
                now,
            )

        # user SET NULL on created_by
        await conn.execute(
            "UPDATE interview_question_sets SET created_by = $1 WHERE id = $2",
            ids["user_id"],
            qset_id,
        )
    finally:
        await conn.close()


def test_013_revision_is_registered() -> None:
    script = _script()
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == DOWN_REVISION
    assert script.get_current_head() == REVISION


def test_012_to_013_roundtrip_and_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _isolated_url()
    assert make_url(url).database == REQUIRED_DATABASE
    assert make_url(url).database != "recruit"

    asyncio.run(_ensure_isolated_database(url))
    _patch_database_url(monkeypatch, url)
    _reset_public_schema(url)

    config = _alembic_config()
    command.upgrade(config, DOWN_REVISION)
    asyncio.run(_assert_at_012_without_013(url))

    command.upgrade(config, REVISION)
    asyncio.run(_assert_at_013(url))
    asyncio.run(_assert_013_row_constraints(url))

    command.downgrade(config, DOWN_REVISION)
    asyncio.run(_assert_at_012_without_013(url))

    command.upgrade(config, REVISION)
    asyncio.run(_assert_at_013(url))

    assert make_url(url).database == REQUIRED_DATABASE
    # prove settings still point away from accidental recruit target for this test URL
    assert "recruit_test" in url
    assert urlparse(url.replace("postgresql+asyncpg", "postgresql")).path.endswith(
        "/recruit_test"
    ) or make_url(url).database == "recruit_test"
