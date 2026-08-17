"""stage 7 transcript workflow: master/version/segment + completion source

Revision ID: 012_transcript_workflow
Revises: 011_stage7_invitation_confirmation_summary
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "012_transcript_workflow"
down_revision: str | None = "011_stage7_invitation_confirmation_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_rounds",
        sa.Column("transcript_completion_mode", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "interview_rounds",
        sa.Column(
            "transcript_completion_reason_code", sa.String(length=64), nullable=True
        ),
    )
    op.add_column(
        "interview_rounds",
        sa.Column("transcript_completion_reason_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "interview_rounds",
        sa.Column("transcript_completed_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "interview_rounds",
        sa.Column("transcript_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_interview_rounds_transcript_completed_by",
        "interview_rounds",
        "users",
        ["transcript_completed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_interview_rounds_transcript_completion_mode",
        "interview_rounds",
        "transcript_completion_mode IS NULL OR transcript_completion_mode IN "
        "('CONFIRMED_TRANSCRIPT', 'WITHOUT_TRANSCRIPT')",
    )

    op.create_table(
        "interview_transcripts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("original_version_id", sa.Uuid(), nullable=True),
        sa.Column("current_draft_version_id", sa.Uuid(), nullable=True),
        sa.Column("current_confirmed_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["interview_round_id"],
            ["interview_rounds.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id", name="uq_interview_transcripts_round_id"
        ),
    )
    op.create_index(
        "ix_interview_transcripts_round_id",
        "interview_transcripts",
        ["interview_round_id"],
        unique=False,
    )

    op.create_table(
        "interview_transcript_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transcript_id", sa.Uuid(), nullable=False),
        sa.Column("version_type", sa.String(length=16), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("raw_text_encrypted", sa.Text(), nullable=False),
        sa.Column("source_method", sa.String(length=16), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_size", sa.Integer(), nullable=True),
        sa.Column("source_mime", sa.String(length=128), nullable=True),
        sa.Column("source_encoding", sa.String(length=32), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("based_on_version_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "version_type IN ('ORIGINAL', 'DRAFT', 'CONFIRMED')",
            name="ck_transcript_version_type",
        ),
        sa.CheckConstraint(
            "status IN ('EDITING', 'IMMUTABLE')",
            name="ck_transcript_version_status",
        ),
        sa.CheckConstraint(
            "source_method IN ('PASTE', 'TXT', 'MD')",
            name="ck_transcript_source_method",
        ),
        sa.CheckConstraint(
            "("
            "version_type IN ('ORIGINAL', 'CONFIRMED') AND status = 'IMMUTABLE'"
            ") OR ("
            "version_type = 'DRAFT'"
            ")",
            name="ck_transcript_version_type_status",
        ),
        sa.CheckConstraint("version_no > 0", name="ck_transcript_version_no_positive"),
        sa.ForeignKeyConstraint(
            ["transcript_id"],
            ["interview_transcripts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["based_on_version_id"],
            ["interview_transcript_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_id",
            "version_label",
            name="uq_transcript_version_label",
        ),
    )
    op.create_index(
        "ix_transcript_versions_transcript_id",
        "interview_transcript_versions",
        ["transcript_id"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_versions_version_type",
        "interview_transcript_versions",
        ["version_type"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_versions_status",
        "interview_transcript_versions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_versions_version_label",
        "interview_transcript_versions",
        ["version_label"],
        unique=False,
    )
    op.create_index(
        "uq_transcript_one_original",
        "interview_transcript_versions",
        ["transcript_id"],
        unique=True,
        postgresql_where=sa.text("version_type = 'ORIGINAL'"),
    )
    op.create_index(
        "uq_transcript_one_editing_draft",
        "interview_transcript_versions",
        ["transcript_id"],
        unique=True,
        postgresql_where=sa.text("version_type = 'DRAFT' AND status = 'EDITING'"),
    )

    op.create_foreign_key(
        "fk_transcripts_original_version",
        "interview_transcripts",
        "interview_transcript_versions",
        ["original_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transcripts_current_draft_version",
        "interview_transcripts",
        "interview_transcript_versions",
        ["current_draft_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transcripts_current_confirmed_version",
        "interview_transcripts",
        "interview_transcript_versions",
        ["current_confirmed_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "interview_transcript_segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transcript_version_id", sa.Uuid(), nullable=False),
        sa.Column("segment_no", sa.Integer(), nullable=False),
        sa.Column("speaker_key", sa.String(length=64), nullable=False),
        sa.Column("speaker_name", sa.String(length=128), nullable=False),
        sa.Column("speaker_role", sa.String(length=32), nullable=False),
        sa.Column("start_time_ms", sa.Integer(), nullable=True),
        sa.Column("end_time_ms", sa.Integer(), nullable=True),
        sa.Column("text_encrypted", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column(
            "source_segment_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_included_in_analysis",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_unclear",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "segment_no > 0", name="ck_transcript_segment_no_positive"
        ),
        sa.CheckConstraint(
            "("
            "start_time_ms IS NULL AND end_time_ms IS NULL"
            ") OR ("
            "start_time_ms IS NOT NULL AND end_time_ms IS NOT NULL "
            "AND start_time_ms >= 0 AND start_time_ms < end_time_ms"
            ")",
            name="ck_transcript_segment_time_range",
        ),
        sa.CheckConstraint(
            "speaker_role IN ('CANDIDATE', 'INTERVIEWER', 'OTHER', 'UNKNOWN')",
            name="ck_transcript_segment_speaker_role",
        ),
        sa.CheckConstraint(
            "source_type IN ('ORIGINAL', 'CORRECTED', 'MANUAL_ADDITION')",
            name="ck_transcript_segment_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["transcript_version_id"],
            ["interview_transcript_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_version_id",
            "segment_no",
            name="uq_transcript_segment_no",
        ),
    )
    op.create_index(
        "ix_transcript_segments_version_id",
        "interview_transcript_segments",
        ["transcript_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_segment_no",
        "interview_transcript_segments",
        ["segment_no"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_speaker_role",
        "interview_transcript_segments",
        ["speaker_role"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_included",
        "interview_transcript_segments",
        ["is_included_in_analysis"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("interview_transcript_segments")
    op.drop_constraint(
        "fk_transcripts_current_confirmed_version",
        "interview_transcripts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_transcripts_current_draft_version",
        "interview_transcripts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_transcripts_original_version",
        "interview_transcripts",
        type_="foreignkey",
    )
    op.drop_table("interview_transcript_versions")
    op.drop_table("interview_transcripts")
    op.drop_constraint(
        "ck_interview_rounds_transcript_completion_mode",
        "interview_rounds",
        type_="check",
    )
    op.drop_constraint(
        "fk_interview_rounds_transcript_completed_by",
        "interview_rounds",
        type_="foreignkey",
    )
    op.drop_column("interview_rounds", "transcript_completed_at")
    op.drop_column("interview_rounds", "transcript_completed_by")
    op.drop_column("interview_rounds", "transcript_completion_reason_description")
    op.drop_column("interview_rounds", "transcript_completion_reason_code")
    op.drop_column("interview_rounds", "transcript_completion_mode")
