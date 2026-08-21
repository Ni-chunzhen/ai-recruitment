"""Stage 7 batch 3 reuses the existing interview state machine,
crypto service (encrypt_secret/decrypt_secret + EncryptionError),
recruitment.manage/interview.execute permissions,
idempotency storage (InterviewIdempotencyKey), audit writer (record_audit),
and optimistic locking conventions (version + InterviewConflictError).

Audit naming note: project uses SENSITIVE_VALUE_MARKERS (not SENSITIVE_AUDIT_KEYS).
Complete entry: POST /interview-rounds/{id}/complete via complete_interview_round.
State: finish IN_PROGRESS→PENDING_TRANSCRIPT; complete PENDING_TRANSCRIPT→COMPLETED.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
EXPECTED_TABLES = (
    "interview_transcripts",
    "interview_transcript_versions",
    "interview_transcript_segments",
)
ROUND_COMPLETION_FIELDS = (
    "transcript_completion_mode",
    "transcript_completion_reason_code",
    "transcript_completion_reason_description",
    "transcript_completed_by",
    "transcript_completed_at",
)


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_revision_012_is_head() -> None:
    script = _script()
    assert script.get_revision("012_transcript_workflow") is not None
    # 017 is the current head; 012 remains in the linear chain.
    assert script.get_current_head() == "017_integration_secrets"
    assert script.get_heads() == ["017_integration_secrets"]
    assert script.get_revision("014_hiring_decisions") is not None
    assert script.get_revision("015_comprehensive_interview_analysis") is not None
    assert script.get_revision("016_offer_console_delivery") is not None


def test_012_revises_011() -> None:
    revision = _script().get_revision("012_transcript_workflow")
    assert revision.down_revision == "011_stage7_invitation_confirmation_summary"
    assert len(revision.revision) <= 32


def test_012_declares_transcript_tables() -> None:
    source = (VERSIONS / "012_transcript_workflow.py").read_text(encoding="utf-8")
    for name in EXPECTED_TABLES:
        assert f'"{name}"' in source or f"'{name}'" in source or name in source
    for field in ROUND_COMPLETION_FIELDS:
        assert field in source
    assert "fk_interview_rounds_transcript_completed_by" in source
    assert "uq_interview_transcripts_round_id" in source or (
        "interview_round_id" in source and "unique=True" in source
    )
    assert "uq_transcript_version_label" in source
    assert "uq_transcript_one_original" in source
    assert "uq_transcript_one_editing_draft" in source
    assert "ck_transcript_segment_no_positive" in source
    assert "ck_transcript_segment_time_range" in source
    assert "fk_transcripts_original_version" in source
    assert "fk_transcripts_current_draft_version" in source
    assert "fk_transcripts_current_confirmed_version" in source
    assert "alembic_version" not in source or "ALTER TABLE alembic_version" not in source
    assert "008_" not in source.split("down_revision")[0]


def test_012_migration_file_defines_upgrade_and_downgrade() -> None:
    path = VERSIONS / "012_transcript_workflow.py"
    source = path.read_text(encoding="utf-8")
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    assert "011_stage7_invitation_confirmation_summary" in source
