"""stage 7 invitation confirmation summary

Revision ID: 011_stage7_invitation_confirmation_summary
Revises: 010_stage7_manual_invitations
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "011_stage7_invitation_confirmation_summary"
down_revision: str | None = "010_stage7_manual_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_NAME = "invitation_confirmation_summary"
TABLE_NAME = "interview_rounds"


def _column_info(inspector: sa.Inspector) -> dict | None:
    for column in inspector.get_columns(TABLE_NAME):
        if column["name"] == COLUMN_NAME:
            return column
    return None


def _is_text_type(column_type: object) -> bool:
    return isinstance(column_type, (sa.Text, sa.UnicodeText))


def upgrade() -> None:
    # Default alembic_version.version_num is VARCHAR(32); this revision id is longer.
    op.execute(
        sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    )

    bind = op.get_bind()
    inspector = inspect(bind)
    existing = _column_info(inspector)
    if existing is None:
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.Text(), nullable=True),
        )
        return

    if not _is_text_type(existing["type"]):
        raise RuntimeError(
            "interview_rounds.invitation_confirmation_summary exists but is not TEXT; "
            f"found type={existing['type']!r}"
        )
    if existing.get("nullable") is False:
        raise RuntimeError(
            "interview_rounds.invitation_confirmation_summary exists but is NOT NULL; "
            "expected nullable TEXT"
        )
    # Compatible with databases where the column was added manually.


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _column_info(inspector) is None:
        return
    op.drop_column(TABLE_NAME, COLUMN_NAME)
