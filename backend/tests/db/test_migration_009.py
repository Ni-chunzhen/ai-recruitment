"""009 interview foundation migration tests."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def test_009_revises_008() -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("009_stage7_interview_foundation")
    assert revision.down_revision == "008_stage6_attempt_audit"
    assert script.get_revision("009_stage7_interview_foundation") is not None


def test_009_migration_file_defines_upgrade_and_downgrade() -> None:
    path = VERSIONS / "009_stage7_interview_foundation.py"
    source = path.read_text(encoding="utf-8")
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    assert "interview_rounds" in source
    assert "interview_round_interviewers" in source
    assert "interview_schedules" in source
    assert "uq_interview_rounds_application_sequence" in source
    assert "sequence_no" in source
    assert "008_stage6_attempt_audit" in source
    assert "notification_tasks" not in source
    assert "ai_interview" not in source.lower()


def test_009_migration_file_exists() -> None:
    path = VERSIONS / "009_stage7_interview_foundation.py"
    assert path.exists()
    _ = os.environ.get("TEST_DATABASE_URL", "")
