"""stage 8 interview AI foundation: question/analysis tables + task_type check

Revision ID: 013_stage8_interview_ai_foundation
Revises: 012_transcript_workflow
Create Date: 2026-08-17
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "013_stage8_interview_ai_foundation"
down_revision: str | None = "012_transcript_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLOWED_TASK_TYPES = (
    "JD_PARSE",
    "SCORE_DIMENSION_RECOMMEND",
    "RESUME_PARSE",
    "RESUME_SCORE",
    "INTERVIEW_QUESTION_GENERATE",
    "INTERVIEW_ROUND_ANALYZE",
)

TASK_TYPE_CHECK_SQL = (
    "task_type IN ("
    "'JD_PARSE', "
    "'SCORE_DIMENSION_RECOMMEND', "
    "'RESUME_PARSE', "
    "'RESUME_SCORE', "
    "'INTERVIEW_QUESTION_GENERATE', "
    "'INTERVIEW_ROUND_ANALYZE'"
    ")"
)

def _quoted_literals(sql: str) -> set[str]:
    return set(re.findall(r"'([^']*)'", sql))


def _precheck_existing_task_types() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT DISTINCT task_type FROM ai_tasks")).all()
    allowed = set(ALLOWED_TASK_TYPES)
    unexpected = sorted({row[0] for row in rows if row[0] not in allowed})
    if unexpected:
        raise RuntimeError(
            "013 abort: unexpected ai_tasks.task_type values: "
            + ", ".join(unexpected)
        )


def _ensure_task_type_check() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text(
            """
            SELECT rel.relname AS table_name,
                   pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c
            JOIN pg_class rel ON rel.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE c.conname = 'ck_ai_tasks_task_type'
              AND c.contype = 'c'
              AND n.nspname = 'public'
            """
        )
    ).mappings().all()

    if not existing:
        op.create_check_constraint(
            "ck_ai_tasks_task_type",
            "ai_tasks",
            TASK_TYPE_CHECK_SQL,
        )
        return

    if len(existing) != 1:
        raise RuntimeError(
            "013 abort: multiple ck_ai_tasks_task_type constraints found"
        )

    row = existing[0]
    table_name = row["table_name"]
    definition = row["definition"] or ""
    if table_name != "ai_tasks":
        raise RuntimeError(
            "013 abort: ck_ai_tasks_task_type exists on unexpected table "
            f"{table_name}"
        )

    found_values = _quoted_literals(definition)
    expected_values = set(ALLOWED_TASK_TYPES)
    if found_values != expected_values:
        extra = sorted(found_values - expected_values)
        missing = sorted(expected_values - found_values)
        details: list[str] = []
        if extra:
            details.append("extra=" + ", ".join(extra))
        if missing:
            details.append("missing=" + ", ".join(missing))
        raise RuntimeError(
            "013 abort: existing ck_ai_tasks_task_type does not match 013 ("
            + "; ".join(details)
            + ")"
        )


def upgrade() -> None:
    _precheck_existing_task_types()

    op.add_column(
        "ai_task_attempts",
        sa.Column("sensitive_request_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "ai_task_attempts",
        sa.Column("sensitive_response_encrypted", sa.Text(), nullable=True),
    )

    _ensure_task_type_check()

    op.create_table(
        "interview_question_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'READY', 'ARCHIVED')",
            name="ck_interview_question_sets_status",
        ),
        sa.CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)",
            name="ck_interview_question_sets_confirmed_pair",
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR ("
            "current_version_id IS NOT NULL "
            "AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL"
            ")",
            name="ck_interview_question_sets_ready_requires_confirm",
        ),
        sa.ForeignKeyConstraint(
            ["interview_round_id"],
            ["interview_rounds.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id", name="uq_interview_question_sets_round_id"
        ),
    )

    op.create_table(
        "interview_question_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_set_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("ai_task_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ai_task_id"],
            ["ai_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.Column("job_version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_version_id"],
            ["job_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.Column("resume_version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["resume_version_id"],
            ["resume_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_no > 0", name="ck_question_versions_no_positive"
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_GENERATED', 'MANUAL_EDIT')",
            name="ck_question_versions_source_type",
        ),
        sa.CheckConstraint(
            "("
            "source_type = 'AI_GENERATED' AND ai_task_id IS NOT NULL"
            ") OR ("
            "source_type = 'MANUAL_EDIT' AND ai_task_id IS NULL"
            ")",
            name="ck_question_versions_source_ai_task",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_id"],
            ["interview_question_sets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_set_id",
            "version_no",
            name="uq_question_versions_set_no",
        ),
        sa.UniqueConstraint(
            "question_set_id",
            "version_label",
            name="uq_question_versions_set_label",
        ),
        sa.UniqueConstraint("ai_task_id", name="uq_question_versions_ai_task"),
    )

    op.create_table(
        "interview_question_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_key", sa.String(length=128), nullable=False),
        sa.Column("question_encrypted", sa.Text(), nullable=False),
        sa.Column("purpose_encrypted", sa.Text(), nullable=False),
        sa.Column("evidence_source", sa.String(length=32), nullable=False),
        sa.Column("resume_evidence_encrypted", sa.Text(), nullable=True),
        sa.Column("follow_up_prompts_encrypted", sa.Text(), nullable=False),
        sa.Column("risk_flags_encrypted", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "display_order > 0",
            name="ck_question_items_display_order_positive",
        ),
        sa.CheckConstraint(
            "evidence_source IN ("
            "'JOB_REQUIREMENT', 'RESUME_EXPERIENCE', 'GENERAL'"
            ")",
            name="ck_question_items_evidence_source",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["interview_question_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_version_id",
            "display_order",
            name="uq_question_items_version_order",
        ),
    )

    op.create_table(
        "interview_round_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["interview_round_id"],
            ["interview_rounds.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id", name="uq_round_analyses_round_id"
        ),
    )

    op.create_table(
        "interview_round_analysis_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=False),
        sa.Column("transcript_version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["transcript_version_id"],
            ["interview_transcript_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.Column("job_version_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_version_id"],
            ["job_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.Column("ai_task_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ai_task_id"],
            ["ai_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.Column(
            "dimensions_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("overall_summary_encrypted", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_no > 0", name="ck_analysis_versions_no_positive"
        ),
        sa.CheckConstraint(
            "overall_score IS NULL OR ("
            "overall_score >= 1 AND overall_score <= 5"
            ")",
            name="ck_analysis_versions_overall_score",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["interview_round_analyses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_id",
            "version_no",
            name="uq_analysis_versions_analysis_no",
        ),
        sa.UniqueConstraint(
            "analysis_id",
            "version_label",
            name="uq_analysis_versions_analysis_label",
        ),
        sa.UniqueConstraint("ai_task_id", name="uq_analysis_versions_ai_task"),
    )

    op.create_table(
        "interview_round_analysis_dimensions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("analysis_version_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_key", sa.String(length=128), nullable=False),
        sa.Column("dimension_name", sa.String(length=128), nullable=False),
        sa.Column("weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("analysis_encrypted", sa.Text(), nullable=False),
        sa.Column("strengths_encrypted", sa.Text(), nullable=False),
        sa.Column("risks_encrypted", sa.Text(), nullable=False),
        sa.Column("insufficient_information_encrypted", sa.Text(), nullable=True),
        sa.Column("suggested_follow_ups_encrypted", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "display_order > 0", name="ck_analysis_dims_order_positive"
        ),
        sa.CheckConstraint(
            "weight > 0 AND weight <= 100",
            name="ck_analysis_dims_weight",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 1 AND score <= 5)",
            name="ck_analysis_dims_score",
        ),
        sa.CheckConstraint(
            "("
            "score IS NOT NULL AND insufficient_information_encrypted IS NULL"
            ") OR ("
            "score IS NULL AND insufficient_information_encrypted IS NOT NULL"
            ")",
            name="ck_analysis_dims_score_info_mutex",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_version_id"],
            ["interview_round_analysis_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_version_id",
            "dimension_key",
            name="uq_analysis_dims_version_key",
        ),
        sa.UniqueConstraint(
            "analysis_version_id",
            "display_order",
            name="uq_analysis_dims_version_order",
        ),
    )

    op.create_table(
        "interview_round_analysis_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("analysis_dimension_id", sa.Uuid(), nullable=False),
        sa.Column("transcript_segment_id", sa.Uuid(), nullable=False),
        sa.Column("segment_no", sa.Integer(), nullable=False),
        sa.Column("quote_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "segment_no > 0",
            name="ck_analysis_evidence_segment_no_positive",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_dimension_id"],
            ["interview_round_analysis_dimensions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transcript_segment_id"],
            ["interview_transcript_segments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_dimension_id",
            "transcript_segment_id",
            name="uq_analysis_evidence_dim_segment",
        ),
    )

    op.create_foreign_key(
        "fk_question_sets_current_version",
        "interview_question_sets",
        "interview_question_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_round_analyses_current_version",
        "interview_round_analyses",
        "interview_round_analysis_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_question_versions_set_id",
        "interview_question_versions",
        ["question_set_id"],
        unique=False,
    )
    op.create_index(
        "ix_question_items_version_id",
        "interview_question_items",
        ["question_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_versions_analysis_id",
        "interview_round_analysis_versions",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_dims_version_id",
        "interview_round_analysis_dimensions",
        ["analysis_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_dim_id",
        "interview_round_analysis_evidence",
        ["analysis_dimension_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_segment_id",
        "interview_round_analysis_evidence",
        ["transcript_segment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_question_sets_current_version",
        "interview_question_sets",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_round_analyses_current_version",
        "interview_round_analyses",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_analysis_evidence_segment_id",
        table_name="interview_round_analysis_evidence",
    )
    op.drop_index(
        "ix_analysis_evidence_dim_id",
        table_name="interview_round_analysis_evidence",
    )
    op.drop_table("interview_round_analysis_evidence")
    op.drop_index(
        "ix_analysis_dims_version_id",
        table_name="interview_round_analysis_dimensions",
    )
    op.drop_table("interview_round_analysis_dimensions")
    op.drop_index(
        "ix_analysis_versions_analysis_id",
        table_name="interview_round_analysis_versions",
    )
    op.drop_table("interview_round_analysis_versions")
    op.drop_table(
        "interview_round_analyses"
    )

    op.drop_index(
        "ix_question_items_version_id",
        table_name="interview_question_items",
    )
    op.drop_table("interview_question_items")
    op.drop_index(
        "ix_question_versions_set_id",
        table_name="interview_question_versions",
    )
    op.drop_table("interview_question_versions")
    op.drop_table(
        "interview_question_sets"
    )

    op.execute(
        sa.text(
            """
            DELETE FROM ai_task_attempts
            WHERE task_id IN (
                SELECT id FROM ai_tasks
                WHERE task_type IN (
                    'INTERVIEW_QUESTION_GENERATE',
                    'INTERVIEW_ROUND_ANALYZE'
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM ai_tasks
            WHERE task_type IN (
                'INTERVIEW_QUESTION_GENERATE',
                'INTERVIEW_ROUND_ANALYZE'
            )
            """
        )
    )

    op.drop_constraint(
        "ck_ai_tasks_task_type",
        "ai_tasks",
        type_="check",
    )
    op.drop_column("ai_task_attempts", "sensitive_request_encrypted")
    op.drop_column("ai_task_attempts", "sensitive_response_encrypted")
