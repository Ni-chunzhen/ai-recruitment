"""stage 5 resume versions, scoring results, screening

Revision ID: 006_resume_scoring
Revises: 005_candidates
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "006_resume_scoring"
down_revision: str | None = "005_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("current_file_version_id", sa.Uuid(), nullable=True),
        sa.Column("current_confirmed_version_id", sa.Uuid(), nullable=True),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resumes_candidate_id", "resumes", ["candidate_id"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_file_version_id", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("ai_structured", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("draft_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "confirmed_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("standardized_text", sa.Text(), nullable=True),
        sa.Column("parse_task_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_file_version_id"], ["resume_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["parse_task_id"], ["ai_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resume_versions_resume_id", "resume_versions", ["resume_id"])
    op.create_index("ix_resume_versions_status", "resume_versions", ["status"])

    op.add_column(
        "job_applications",
        sa.Column(
            "pipeline_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending_hr_screen",
        ),
    )
    op.add_column(
        "job_applications",
        sa.Column("resume_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_foreign_key(
        "fk_job_applications_resume_version_id",
        "job_applications",
        "resume_versions",
        ["resume_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_job_applications_pipeline_status",
        "job_applications",
        ["pipeline_status"],
    )

    op.create_table(
        "ai_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("result_type", sa.String(length=64), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("job_version_id", sa.Uuid(), nullable=True),
        sa.Column("resume_version_id", sa.Uuid(), nullable=True),
        sa.Column("raw_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "normalized_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ai_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["job_applications.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["candidates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["job_version_id"], ["job_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["resume_version_id"], ["resume_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_results_application_id", "ai_results", ["application_id"])
    op.create_index("ix_ai_results_task_id", "ai_results", ["task_id"])

    op.create_table(
        "screening_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("from_pipeline_status", sa.String(length=32), nullable=False),
        sa.Column("to_pipeline_status", sa.String(length=32), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("ai_result_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["job_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ai_result_id"], ["ai_results.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_screening_decisions_application_id",
        "screening_decisions",
        ["application_id"],
    )

    op.create_table(
        "application_status_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["job_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_status_logs_application_id",
        "application_status_logs",
        ["application_id"],
    )

    op.create_index(
        "ix_candidates_phone",
        "candidates",
        ["phone"],
    )
    op.create_index(
        "ix_candidates_email",
        "candidates",
        ["email"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_email", table_name="candidates")
    op.drop_index("ix_candidates_phone", table_name="candidates")
    op.drop_index(
        "ix_application_status_logs_application_id",
        table_name="application_status_logs",
    )
    op.drop_table("application_status_logs")
    op.drop_index(
        "ix_screening_decisions_application_id", table_name="screening_decisions"
    )
    op.drop_table("screening_decisions")
    op.drop_index("ix_ai_results_task_id", table_name="ai_results")
    op.drop_index("ix_ai_results_application_id", table_name="ai_results")
    op.drop_table("ai_results")
    op.drop_index("ix_job_applications_pipeline_status", table_name="job_applications")
    op.drop_constraint(
        "fk_job_applications_resume_version_id",
        "job_applications",
        type_="foreignkey",
    )
    op.drop_column("job_applications", "lock_version")
    op.drop_column("job_applications", "resume_version_id")
    op.drop_column("job_applications", "pipeline_status")
    op.drop_index("ix_resume_versions_status", table_name="resume_versions")
    op.drop_index("ix_resume_versions_resume_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("ix_resumes_candidate_id", table_name="resumes")
    op.drop_table("resumes")
