"""Alembic 015 comprehensive interview analysis migration structure (Task 1)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
REVISION = "015_comprehensive_interview_analysis"
DOWN_REVISION = "014_hiring_decisions"
MIGRATION_FILE = VERSIONS / "015_comprehensive_interview_analysis.py"

REQUIRED_SET_COLUMNS = (
    "id",
    "application_id",
    "current_version_id",
    "created_at",
    "updated_at",
)

REQUIRED_VERSION_COLUMNS = (
    "id",
    "analysis_id",
    "version_no",
    "version_label",
    "ai_task_id",
    "input_snapshot_hash",
    "round_refs",
    "coverage_report",
    "overall_score",
    "overall_summary_encrypted",
    "created_by",
    "created_at",
)


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_revision_015_is_head() -> None:
    script = _script()
    assert script.get_revision(REVISION) is not None
    assert script.get_current_head() == REVISION
    assert script.get_heads() == [REVISION]


def test_015_revises_014() -> None:
    revision = _script().get_revision(REVISION)
    assert revision.down_revision == DOWN_REVISION
    assert len(revision.revision) <= 64


def test_migration_015_upgrade_creates_tables_and_ck_accepts_comprehensive() -> None:
    assert MIGRATION_FILE.is_file()
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | None = "{DOWN_REVISION}"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "application_comprehensive_analyses" in source
    assert "application_comprehensive_analysis_versions" in source
    assert "INTERVIEW_COMPREHENSIVE_ANALYZE" in source
    assert "ck_ai_tasks_task_type" in source
    assert "round_refs" in source
    assert "coverage_report" in source
    assert "overall_summary_encrypted" in source
    assert "input_snapshot_hash" in source
    assert "uq_comprehensive_versions_ai_task" in source
    assert "uq_comprehensive_analyses_application_id" in source
    assert "fk_comprehensive_analyses_current_version" in source
    assert 'op.create_table(\n        "hiring_decisions"' not in source
    assert 'op.drop_table("hiring_decisions")' not in source
    assert "interview_round_analyses" not in source
    for col in REQUIRED_SET_COLUMNS:
        assert col in source
    for col in REQUIRED_VERSION_COLUMNS:
        assert col in source
    # plaintext body columns must not be created
    assert 'sa.Column("overall_summary"' not in source
    assert 'sa.Column("quote"' not in source
    assert 'sa.Column("jd_text"' not in source
    assert 'sa.Column("resume_text"' not in source


def test_migration_015_downgrade_restores_six_type_ck_and_drops_tables() -> None:
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert 'op.drop_table("application_comprehensive_analysis_versions")' in source
    assert 'op.drop_table("application_comprehensive_analyses")' in source
    # six-type restore on downgrade (no comprehensive literal in restore SQL block)
    assert "INTERVIEW_ROUND_ANALYZE" in source
    assert "drop_constraint" in source.lower() or "drop_constraint" in source
    # restore list must include six legacy types without relying on comprehensive-only
    for legacy in (
        "JD_PARSE",
        "SCORE_DIMENSION_RECOMMEND",
        "RESUME_PARSE",
        "RESUME_SCORE",
        "INTERVIEW_QUESTION_GENERATE",
        "INTERVIEW_ROUND_ANALYZE",
    ):
        assert legacy in source
