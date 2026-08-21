"""Alembic 014 hiring_decisions migration structure (Task 1)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
REVISION = "014_hiring_decisions"
DOWN_REVISION = "013_stage8_interview_ai_foundation"
MIGRATION_FILE = VERSIONS / "014_hiring_decisions.py"

REQUIRED_COLUMNS = (
    "id",
    "application_id",
    "decision",
    "reason_code",
    "round_id",
    "analysis_version_id",
    "overall_score",
    "analysis_version_no",
    "transcript_version_id",
    "job_version_id",
    "from_pipeline_status",
    "to_pipeline_status",
    "decided_by",
    "idempotency_key",
    "created_at",
)


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_revision_014_in_chain_before_015() -> None:
    script = _script()
    assert script.get_revision(REVISION) is not None
    assert script.get_revision(REVISION).down_revision == DOWN_REVISION
    head = script.get_current_head()
    assert head in {
        REVISION,
        "015_comprehensive_interview_analysis",
        "016_offer_console_delivery",
    }
    if head == "016_offer_console_delivery":
        assert (
            script.get_revision(head).down_revision
            == "015_comprehensive_interview_analysis"
        )
        assert (
            script.get_revision("015_comprehensive_interview_analysis").down_revision
            == REVISION
        )
    elif head == "015_comprehensive_interview_analysis":
        assert script.get_revision(head).down_revision == REVISION
    else:
        assert script.get_heads() == [REVISION]


def test_014_revises_013() -> None:
    revision = _script().get_revision(REVISION)
    assert revision.down_revision == DOWN_REVISION
    assert len(revision.revision) <= 32


def test_migration_014_upgrade_creates_hiring_decisions_and_downgrade_drops() -> None:
    assert MIGRATION_FILE.is_file()
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert 'revision: str = "014_hiring_decisions"' in source
    assert f'down_revision: str | None = "{DOWN_REVISION}"' in source
    assert "hiring_decisions" in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert 'op.create_table(\n        "hiring_decisions"' in source or (
        'op.create_table("hiring_decisions"' in source
    )
    for col in REQUIRED_COLUMNS:
        assert col in source
    assert "uq_hiring_decisions_idempotency" in source
    assert "ix_hiring_decisions_application_id" in source
    assert "idempotency_key IS NOT NULL" in source
    assert "interview_round_analysis_versions" in source
    assert "interview_rounds" in source
    assert "sa.Float()" in source
    assert 'sa.Column("reason"' not in source
    assert "create_table(\"offers\"" not in source
    assert "create_table('offers'" not in source
    assert "screening_decisions" not in source
    assert 'op.drop_table("hiring_decisions")' in source
    assert "drop_index" in source
    assert "ALTER TABLE alembic_version" not in source
