"""stage 7 interview foundation: rounds, interviewers, schedules

Revision ID: 009_stage7_interview_foundation
Revises: 008_stage6_attempt_audit
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_stage7_interview_foundation"
down_revision: str | None = "008_stage6_attempt_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("job_version_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("current_schedule_id", sa.Uuid(), nullable=True),
        sa.Column("cancellation_reason_code", sa.String(length=64), nullable=True),
        sa.Column("cancellation_description", sa.Text(), nullable=True),
        sa.Column("abnormal_reason_code", sa.String(length=64), nullable=True),
        sa.Column("abnormal_description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sequence_no > 0", name="ck_interview_rounds_sequence_positive"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["job_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_version_id"], ["job_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            "sequence_no",
            name="uq_interview_rounds_application_sequence",
        ),
    )
    op.create_index(
        "ix_interview_rounds_application_id",
        "interview_rounds",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_interview_rounds_status", "interview_rounds", ["status"], unique=False
    )
    op.create_index(
        "ix_interview_rounds_owner_id", "interview_rounds", ["owner_id"], unique=False
    )

    op.create_table(
        "interview_round_interviewers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["interview_round_id"], ["interview_rounds.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["interviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id",
            "interviewer_id",
            name="uq_interview_round_interviewer",
        ),
    )
    op.create_index(
        "ix_interview_round_interviewers_interviewer_id",
        "interview_round_interviewers",
        ["interviewer_id"],
        unique=False,
    )
    op.create_index(
        "ix_interview_round_interviewers_round_id",
        "interview_round_interviewers",
        ["interview_round_id"],
        unique=False,
    )

    op.create_table(
        "interview_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("meeting_mode", sa.String(length=16), nullable=True),
        sa.Column("meeting_provider", sa.String(length=64), nullable=True),
        sa.Column("meeting_url", sa.String(length=512), nullable=True),
        sa.Column("meeting_no", sa.String(length=64), nullable=True),
        sa.Column("meeting_password_encrypted", sa.String(length=1024), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("contact_name", sa.String(length=128), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("reschedule_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "end_at_utc > start_at_utc",
            name="ck_interview_schedules_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["interview_round_id"], ["interview_rounds.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id",
            "schedule_version",
            name="uq_interview_schedules_round_version",
        ),
    )
    op.create_index(
        "ix_interview_schedules_round_id",
        "interview_schedules",
        ["interview_round_id"],
        unique=False,
    )
    op.create_index(
        "ix_interview_schedules_status",
        "interview_schedules",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_interview_schedules_start_end",
        "interview_schedules",
        ["start_at_utc", "end_at_utc"],
        unique=False,
    )
    op.create_index(
        "uq_interview_schedules_one_active",
        "interview_schedules",
        ["interview_round_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_foreign_key(
        "fk_interview_rounds_current_schedule",
        "interview_rounds",
        "interview_schedules",
        ["current_schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "interview_idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_round_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["result_round_id"], ["interview_rounds.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id",
            "action",
            "scope_id",
            "idempotency_key",
            name="uq_interview_idempotency_actor_action_scope_key",
        ),
    )
    op.create_index(
        "ix_interview_idempotency_scope",
        "interview_idempotency_keys",
        ["scope_id"],
        unique=False,
    )

    op.alter_column("interview_rounds", "version", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_interview_rounds_current_schedule",
        "interview_rounds",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_interview_idempotency_scope", table_name="interview_idempotency_keys"
    )
    op.drop_table("interview_idempotency_keys")
    op.drop_index(
        "uq_interview_schedules_one_active", table_name="interview_schedules"
    )
    op.drop_index("ix_interview_schedules_start_end", table_name="interview_schedules")
    op.drop_index("ix_interview_schedules_status", table_name="interview_schedules")
    op.drop_index("ix_interview_schedules_round_id", table_name="interview_schedules")
    op.drop_table("interview_schedules")
    op.drop_index(
        "ix_interview_round_interviewers_round_id",
        table_name="interview_round_interviewers",
    )
    op.drop_index(
        "ix_interview_round_interviewers_interviewer_id",
        table_name="interview_round_interviewers",
    )
    op.drop_table("interview_round_interviewers")
    op.drop_index("ix_interview_rounds_owner_id", table_name="interview_rounds")
    op.drop_index("ix_interview_rounds_status", table_name="interview_rounds")
    op.drop_index("ix_interview_rounds_application_id", table_name="interview_rounds")
    op.drop_table("interview_rounds")
