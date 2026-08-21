"""post-interview hiring decisions (immutable history + pending_offer prep)

Revision ID: 014_hiring_decisions
Revises: 013_stage8_interview_ai_foundation
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014_hiring_decisions"
down_revision: str | None = "013_stage8_interview_ai_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hiring_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_version_id", sa.Uuid(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("analysis_version_no", sa.Integer(), nullable=True),
        sa.Column("transcript_version_id", sa.Uuid(), nullable=True),
        sa.Column("job_version_id", sa.Uuid(), nullable=True),
        sa.Column("from_pipeline_status", sa.String(length=32), nullable=False),
        sa.Column("to_pipeline_status", sa.String(length=32), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["job_applications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"],
            ["interview_rounds.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_version_id"],
            ["interview_round_analysis_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hiring_decisions_application_id",
        "hiring_decisions",
        ["application_id"],
    )
    op.create_index(
        "uq_hiring_decisions_idempotency",
        "hiring_decisions",
        ["application_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_hiring_decisions_idempotency",
        table_name="hiring_decisions",
    )
    op.drop_index(
        "ix_hiring_decisions_application_id",
        table_name="hiring_decisions",
    )
    op.drop_table("hiring_decisions")
