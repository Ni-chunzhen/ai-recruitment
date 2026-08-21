"""integration secrets ciphertext store (Dify / MinIO only)

Revision ID: 017_integration_secrets
Revises: 016_offer_console_delivery
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017_integration_secrets"
down_revision: str | None = "016_offer_console_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("config_key", sa.String(length=64), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider IN ('dify', 'minio')",
            name="ck_integration_secrets_provider",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "config_key",
            name="uq_integration_secrets_provider_key",
        ),
    )
    op.create_index(
        "ix_integration_secrets_provider",
        "integration_secrets",
        ["provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_secrets_provider",
        table_name="integration_secrets",
    )
    op.drop_table("integration_secrets")
