"""stage 7 manual invitations: messages, versions, send records

Revision ID: 010_stage7_manual_invitations
Revises: 009_stage7_interview_foundation
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_stage7_manual_invitations"
down_revision: str | None = "009_stage7_interview_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.add_column(
        "interview_rounds",
        sa.Column("invitation_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview_rounds",
        sa.Column("invitation_confirmed_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "interview_rounds",
        sa.Column("invitation_confirmed_schedule_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_rounds_invitation_confirmed_by",
        "interview_rounds",
        "users",
        ["invitation_confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "interview_invitation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("audience_type", sa.String(length=32), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_key", sa.String(length=128), nullable=False),
        sa.Column("recipient_name", sa.String(length=128), nullable=False),
        sa.Column("recipient_email_masked", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "("
            "audience_type = 'CANDIDATE' AND recipient_user_id IS NULL"
            ") OR ("
            "audience_type = 'INTERVIEWER' AND recipient_user_id IS NOT NULL"
            ")",
            name="ck_invitation_messages_audience_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["interview_round_id"],
            ["interview_rounds.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["interview_schedules.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id",
            "event_type",
            "audience_type",
            "recipient_key",
            name="uq_invitation_msg_schedule_event_audience_recipient",
        ),
    )
    op.create_index(
        "ix_invitation_messages_round_id",
        "interview_invitation_messages",
        ["interview_round_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_messages_schedule_id",
        "interview_invitation_messages",
        ["schedule_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_messages_schedule_version",
        "interview_invitation_messages",
        ["schedule_version"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_messages_event_type",
        "interview_invitation_messages",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_messages_audience_type",
        "interview_invitation_messages",
        ["audience_type"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_messages_status",
        "interview_invitation_messages",
        ["status"],
        unique=False,
    )

    op.create_table(
        "interview_invitation_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("subject_encrypted", sa.Text(), nullable=False),
        sa.Column("body_html_encrypted", sa.Text(), nullable=False),
        sa.Column("body_text_encrypted", sa.Text(), nullable=False),
        sa.Column("template_code", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["interview_invitation_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "version_no",
            name="uq_invitation_version_message_version_no",
        ),
    )
    op.create_index(
        "ix_invitation_versions_message_id",
        "interview_invitation_versions",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_versions_created_at",
        "interview_invitation_versions",
        ["created_at"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_invitation_messages_current_version",
        "interview_invitation_messages",
        "interview_invitation_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "interview_invitation_send_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("message_version_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("channel_note", sa.Text(), nullable=True),
        sa.Column("recipient_email_masked", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["interview_invitation_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_version_id"],
            ["interview_invitation_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_key_id"],
            ["interview_idempotency_keys.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invitation_send_records_message_id",
        "interview_invitation_send_records",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_invitation_send_records_created_at",
        "interview_invitation_send_records",
        ["created_at"],
        unique=False,
    )

    op.alter_column(
        "interview_invitation_messages", "version", server_default=None
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_interview_rounds_invitation_confirmed_by",
        "interview_rounds",
        type_="foreignkey",
    )
    op.drop_column("interview_rounds", "invitation_confirmed_schedule_version")
    op.drop_column("interview_rounds", "invitation_confirmed_by")
    op.drop_column("interview_rounds", "invitation_confirmed_at")

    op.drop_index(
        "ix_invitation_send_records_created_at",
        table_name="interview_invitation_send_records",
    )
    op.drop_index(
        "ix_invitation_send_records_message_id",
        table_name="interview_invitation_send_records",
    )
    op.drop_table("interview_invitation_send_records")

    op.drop_constraint(
        "fk_invitation_messages_current_version",
        "interview_invitation_messages",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_invitation_versions_created_at",
        table_name="interview_invitation_versions",
    )
    op.drop_index(
        "ix_invitation_versions_message_id",
        table_name="interview_invitation_versions",
    )
    op.drop_table("interview_invitation_versions")

    op.drop_index(
        "ix_invitation_messages_status",
        table_name="interview_invitation_messages",
    )
    op.drop_index(
        "ix_invitation_messages_audience_type",
        table_name="interview_invitation_messages",
    )
    op.drop_index(
        "ix_invitation_messages_event_type",
        table_name="interview_invitation_messages",
    )
    op.drop_index(
        "ix_invitation_messages_schedule_version",
        table_name="interview_invitation_messages",
    )
    op.drop_index(
        "ix_invitation_messages_schedule_id",
        table_name="interview_invitation_messages",
    )
    op.drop_index(
        "ix_invitation_messages_round_id",
        table_name="interview_invitation_messages",
    )
    op.drop_table("interview_invitation_messages")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_column("users", "email")
