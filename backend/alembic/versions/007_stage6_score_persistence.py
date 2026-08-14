"""stage 6 score persistence and screening gap closure

Revision ID: 007_stage6_score_persistence
Revises: 006_resume_scoring
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007_stage6_score_persistence"
down_revision: str | None = "006_resume_scoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_tasks",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_ai_tasks_idempotency",
        "ai_tasks",
        ["created_by", "business_id", "task_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.add_column(
        "ai_results",
        sa.Column(
            "schema_version",
            sa.String(length=16),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "ai_results",
        sa.Column("model_total_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "ai_results",
        sa.Column("calculated_total_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "ai_results",
        sa.Column("score_difference", sa.Float(), nullable=True),
    )
    op.add_column(
        "ai_results",
        sa.Column(
            "validation_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_ai_results_current",
        "ai_results",
        ["application_id", "result_type"],
        unique=True,
        postgresql_where=sa.text("is_current = true AND application_id IS NOT NULL"),
    )

    op.add_column(
        "screening_decisions",
        sa.Column("reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "screening_decisions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_screening_decisions_idempotency",
        "screening_decisions",
        ["application_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_screening_decisions_idempotency", table_name="screening_decisions"
    )
    op.drop_column("screening_decisions", "idempotency_key")
    op.drop_column("screening_decisions", "reason_code")
    op.drop_index("uq_ai_results_current", table_name="ai_results")
    op.drop_column("ai_results", "validation_warnings")
    op.drop_column("ai_results", "score_difference")
    op.drop_column("ai_results", "calculated_total_score")
    op.drop_column("ai_results", "model_total_score")
    op.drop_column("ai_results", "schema_version")
    op.drop_index("uq_ai_tasks_idempotency", table_name="ai_tasks")
    op.drop_column("ai_tasks", "idempotency_key")
