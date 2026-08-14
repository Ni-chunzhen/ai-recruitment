"""add candidates and job_applications

Revision ID: 005_candidates
Revises: 004_ai_tasks
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "005_candidates"
down_revision: str | None = "004_ai_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidates_name", "candidates", ["name"])

    op.create_table(
        "job_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("job_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("interview_started", sa.Boolean(), nullable=False),
        sa.Column("interview_task_state", sa.String(length=32), nullable=False),
        sa.Column("close_action", sa.String(length=32), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("transferred_to_job_id", sa.Uuid(), nullable=True),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.Column("migration_reason", sa.Text(), nullable=True),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migrated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "timeline_events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["job_version_id"], ["job_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["transferred_to_job_id"], ["jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"], ["job_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["migrated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_applications_job_id", "job_applications", ["job_id"])
    op.create_index(
        "ix_job_applications_job_version_id",
        "job_applications",
        ["job_version_id"],
    )
    op.create_index(
        "ix_job_applications_status",
        "job_applications",
        ["status"],
    )
    op.create_index(
        "ix_job_applications_job_status",
        "job_applications",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_applications_job_status", table_name="job_applications")
    op.drop_index("ix_job_applications_status", table_name="job_applications")
    op.drop_index("ix_job_applications_job_version_id", table_name="job_applications")
    op.drop_index("ix_job_applications_job_id", table_name="job_applications")
    op.drop_table("job_applications")
    op.drop_index("ix_candidates_name", table_name="candidates")
    op.drop_table("candidates")
