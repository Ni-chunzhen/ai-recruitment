"""add job version snapshot for field diffs

Revision ID: 003_job_version_snapshot
Revises: 002_job_management
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "003_job_version_snapshot"
down_revision: str | None = "002_job_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_versions",
        sa.Column(
            "job_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("job_versions", "job_snapshot")
