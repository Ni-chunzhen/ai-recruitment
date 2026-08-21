"""offer console delivery: offers, versions, send attempts

Revision ID: 016_offer_console_delivery
Revises: 015_comprehensive_interview_analysis
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016_offer_console_delivery"
down_revision: str | None = "015_comprehensive_interview_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("hiring_decision_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_email_masked", sa.String(length=255), nullable=True),
        sa.Column("recipient_name", sa.String(length=128), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ("
            "'draft', 'ready', 'sending', 'sent', 'failed', 'voided'"
            ")",
            name="ck_offers_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["job_applications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["hiring_decision_id"],
            ["hiring_decisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_offers_application_id",
        "offers",
        ["application_id"],
    )
    op.create_index(
        "uq_offers_application_active",
        "offers",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('voided')"),
    )

    op.create_table(
        "offer_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("subject_encrypted", sa.Text(), nullable=False),
        sa.Column("body_html_encrypted", sa.Text(), nullable=False),
        sa.Column("body_text_encrypted", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("template_code", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=32), nullable=False),
        sa.Column(
            "frozen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="ck_offer_versions_version_no"),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "offer_id",
            "version_no",
            name="uq_offer_versions_offer_version_no",
        ),
    )
    op.create_index(
        "ix_offer_versions_offer_id",
        "offer_versions",
        ["offer_id"],
    )

    op.create_foreign_key(
        "fk_offers_current_version",
        "offers",
        "offer_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "offer_send_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("offer_version_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message_safe", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_no >= 1",
            name="ck_offer_send_attempts_attempt_no",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'running', 'succeeded', 'failed', 'dead'"
            ")",
            name="ck_offer_send_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            ["offer_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "offer_id",
            "idempotency_key",
            name="uq_offer_send_attempts_idempotency",
        ),
    )
    op.create_index(
        "ix_offer_send_attempts_offer_id",
        "offer_send_attempts",
        ["offer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_offer_send_attempts_offer_id",
        table_name="offer_send_attempts",
    )
    op.drop_table("offer_send_attempts")
    op.drop_constraint(
        "fk_offers_current_version",
        "offers",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_offer_versions_offer_id",
        table_name="offer_versions",
    )
    op.drop_table("offer_versions")
    op.drop_index(
        "uq_offers_application_active",
        table_name="offers",
    )
    op.drop_index(
        "ix_offers_application_id",
        table_name="offers",
    )
    op.drop_table("offers")
