"""comprehensive interview analysis tables + expand ai_tasks task_type check

Revision ID: 015_comprehensive_interview_analysis
Revises: 014_hiring_decisions
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "015_comprehensive_interview_analysis"
down_revision: str | None = "014_hiring_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_TYPE_CHECK_SQL_SEVEN = (
    "task_type IN ("
    "'JD_PARSE', "
    "'SCORE_DIMENSION_RECOMMEND', "
    "'RESUME_PARSE', "
    "'RESUME_SCORE', "
    "'INTERVIEW_QUESTION_GENERATE', "
    "'INTERVIEW_ROUND_ANALYZE', "
    "'INTERVIEW_COMPREHENSIVE_ANALYZE'"
    ")"
)

TASK_TYPE_CHECK_SQL_SIX = (
    "task_type IN ("
    "'JD_PARSE', "
    "'SCORE_DIMENSION_RECOMMEND', "
    "'RESUME_PARSE', "
    "'RESUME_SCORE', "
    "'INTERVIEW_QUESTION_GENERATE', "
    "'INTERVIEW_ROUND_ANALYZE'"
    ")"
)


def upgrade() -> None:
    op.create_table(
        "application_comprehensive_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["job_applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            name="uq_comprehensive_analyses_application_id",
        ),
    )

    op.create_table(
        "application_comprehensive_analysis_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=False),
        sa.Column("ai_task_id", sa.Uuid(), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "round_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "coverage_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("overall_summary_encrypted", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_no > 0",
            name="ck_comprehensive_versions_no_positive",
        ),
        sa.CheckConstraint(
            "overall_score IS NULL OR ("
            "overall_score >= 1 AND overall_score <= 5"
            ")",
            name="ck_comprehensive_versions_overall_score",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["application_comprehensive_analyses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_task_id"],
            ["ai_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_id",
            "version_no",
            name="uq_comprehensive_versions_analysis_no",
        ),
        sa.UniqueConstraint(
            "analysis_id",
            "version_label",
            name="uq_comprehensive_versions_analysis_label",
        ),
        sa.UniqueConstraint(
            "ai_task_id",
            name="uq_comprehensive_versions_ai_task",
        ),
    )

    op.create_foreign_key(
        "fk_comprehensive_analyses_current_version",
        "application_comprehensive_analyses",
        "application_comprehensive_analysis_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_comprehensive_versions_analysis_id",
        "application_comprehensive_analysis_versions",
        ["analysis_id"],
        unique=False,
    )

    op.drop_constraint(
        "ck_ai_tasks_task_type",
        "ai_tasks",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_tasks_task_type",
        "ai_tasks",
        TASK_TYPE_CHECK_SQL_SEVEN,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_comprehensive_analyses_current_version",
        "application_comprehensive_analyses",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_comprehensive_versions_analysis_id",
        table_name="application_comprehensive_analysis_versions",
    )
    op.drop_table("application_comprehensive_analysis_versions")
    op.drop_table("application_comprehensive_analyses")

    op.execute(
        sa.text(
            """
            DELETE FROM ai_task_attempts
            WHERE task_id IN (
                SELECT id FROM ai_tasks
                WHERE task_type = 'INTERVIEW_COMPREHENSIVE_ANALYZE'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM ai_tasks
            WHERE task_type = 'INTERVIEW_COMPREHENSIVE_ANALYZE'
            """
        )
    )

    op.drop_constraint(
        "ck_ai_tasks_task_type",
        "ai_tasks",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_tasks_task_type",
        "ai_tasks",
        TASK_TYPE_CHECK_SQL_SIX,
    )
