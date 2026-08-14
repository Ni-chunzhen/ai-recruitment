"""stage 6 attempt audit: dual counters, ghost cleanup, attempt raw

Revision ID: 008_stage6_attempt_audit
Revises: 007_stage6_score_persistence
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "008_stage6_attempt_audit"
down_revision: str | None = "007_stage6_score_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_tasks",
        sa.Column(
            "retry_cycle_no",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ai_tasks",
        sa.Column(
            "cycle_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column(
            "retry_cycle_no",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column(
            "cycle_attempt_no",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column("provider_run_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column(
            "raw_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column("response_purged_at", sa.DateTime(timezone=True), nullable=True),
    )

    conn = op.get_bind()

    # Detect duplicate (task_id, attempt_no) groups and validate morphology.
    dup_groups = conn.execute(
        sa.text(
            """
            SELECT task_id::text AS task_id, attempt_no, COUNT(*) AS cnt
            FROM ai_task_attempts
            GROUP BY task_id, attempt_no
            HAVING COUNT(*) > 1
            ORDER BY task_id, attempt_no
            """
        )
    ).mappings().all()

    ghost_ids: list[str] = []
    for group in dup_groups:
        rows = conn.execute(
            sa.text(
                """
                SELECT id::text AS id, status, started_at, finished_at, created_at
                FROM ai_task_attempts
                WHERE task_id = CAST(:task_id AS uuid) AND attempt_no = :attempt_no
                ORDER BY created_at, id
                """
            ),
            {"task_id": group["task_id"], "attempt_no": group["attempt_no"]},
        ).mappings().all()
        if len(rows) != 2:
            raise RuntimeError(
                "008 abort: unexpected duplicate morphology "
                f"task_id={group['task_id']} attempt_no={group['attempt_no']} "
                f"count={len(rows)} (expected exactly 2)"
            )
        running = [
            r
            for r in rows
            if r["status"] == "running" and r["finished_at"] is None
        ]
        terminal = [r for r in rows if r["finished_at"] is not None]
        if len(running) != 1 or len(terminal) != 1:
            raise RuntimeError(
                "008 abort: duplicate group is not running+terminal twin "
                f"task_id={group['task_id']} attempt_no={group['attempt_no']} "
                f"statuses={[r['status'] for r in rows]}"
            )
        if running[0]["started_at"] != terminal[0]["started_at"]:
            raise RuntimeError(
                "008 abort: duplicate group started_at mismatch "
                f"task_id={group['task_id']} attempt_no={group['attempt_no']}"
            )
        ghost_ids.append(running[0]["id"])

    if ghost_ids:
        for gid in ghost_ids:
            conn.execute(
                sa.text("DELETE FROM ai_task_attempts WHERE id = CAST(:id AS uuid)"),
                {"id": gid},
            )
    print(f"008 cleaned ghost running attempts: {len(ghost_ids)}")

    remaining_dups = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
              SELECT task_id, attempt_no
              FROM ai_task_attempts
              GROUP BY task_id, attempt_no
              HAVING COUNT(*) > 1
            ) t
            """
        )
    ).scalar_one()
    if remaining_dups:
        raise RuntimeError(
            f"008 abort: still have {remaining_dups} duplicate "
            f"(task_id, attempt_no) groups"
        )

    # Backfill cycle fields from surviving attempts.
    conn.execute(
        sa.text(
            """
            UPDATE ai_task_attempts
            SET retry_cycle_no = 0,
                cycle_attempt_no = attempt_no
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE ai_tasks t
            SET attempt_count = COALESCE(a.max_no, 0),
                retry_cycle_no = 0,
                cycle_attempt_count = COALESCE(a.max_no, 0)
            FROM (
              SELECT task_id, MAX(attempt_no) AS max_no
              FROM ai_task_attempts
              GROUP BY task_id
            ) a
            WHERE t.id = a.task_id
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE ai_tasks
            SET attempt_count = 0,
                retry_cycle_no = 0,
                cycle_attempt_count = 0
            WHERE id NOT IN (SELECT DISTINCT task_id FROM ai_task_attempts)
            """
        )
    )

    # Best-effort backfill run ids/raw onto latest attempt from task.raw_response
    conn.execute(
        sa.text(
            """
            UPDATE ai_task_attempts a
            SET
              raw_response = t.raw_response,
              provider_run_id = CASE
                WHEN t.raw_response ? 'workflow_run_id'
                  THEN NULLIF(t.raw_response->>'workflow_run_id', '')
                WHEN jsonb_typeof(t.raw_response->'steps') = 'array'
                  THEN NULLIF(
                    t.raw_response->'steps'->-1->>'workflow_run_id',
                    ''
                  )
                ELSE NULL
              END,
              request_id = CASE
                WHEN t.raw_response ? 'task_id'
                  THEN NULLIF(t.raw_response->>'task_id', '')
                WHEN jsonb_typeof(t.raw_response->'steps') = 'array'
                  THEN NULLIF(t.raw_response->'steps'->-1->>'task_id', '')
                ELSE NULL
              END
            FROM ai_tasks t
            WHERE a.task_id = t.id
              AND a.attempt_no = (
                SELECT MAX(a2.attempt_no)
                FROM ai_task_attempts a2
                WHERE a2.task_id = t.id
              )
              AND t.raw_response IS NOT NULL
              AND a.finished_at IS NOT NULL
            """
        )
    )

    op.create_index(
        "uq_ai_task_attempts_task_attempt",
        "ai_task_attempts",
        ["task_id", "attempt_no"],
        unique=True,
    )
    op.create_index(
        "ix_ai_task_attempts_provider_run_id",
        "ai_task_attempts",
        ["provider_run_id"],
        unique=False,
        postgresql_where=sa.text("provider_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ai_task_attempts_request_id",
        "ai_task_attempts",
        ["request_id"],
        unique=False,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )

    op.alter_column("ai_tasks", "retry_cycle_no", server_default=None)
    op.alter_column("ai_tasks", "cycle_attempt_count", server_default=None)
    op.alter_column("ai_task_attempts", "retry_cycle_no", server_default=None)
    op.alter_column("ai_task_attempts", "cycle_attempt_no", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_ai_task_attempts_request_id",
        table_name="ai_task_attempts",
    )
    op.drop_index(
        "ix_ai_task_attempts_provider_run_id",
        table_name="ai_task_attempts",
    )
    op.drop_index(
        "uq_ai_task_attempts_task_attempt",
        table_name="ai_task_attempts",
    )
    op.drop_column("ai_task_attempts", "response_purged_at")
    op.drop_column("ai_task_attempts", "raw_response")
    op.drop_column("ai_task_attempts", "request_id")
    op.drop_column("ai_task_attempts", "provider_run_id")
    op.drop_column("ai_task_attempts", "cycle_attempt_no")
    op.drop_column("ai_task_attempts", "retry_cycle_no")
    op.drop_column("ai_tasks", "cycle_attempt_count")
    op.drop_column("ai_tasks", "retry_cycle_no")
    # Intentionally does NOT restore deleted ghost rows or prior attempt_count values.
