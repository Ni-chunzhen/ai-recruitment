"""010 manual invitation migration tests."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def test_010_revises_009() -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("010_stage7_manual_invitations")
    assert revision.down_revision == "009_stage7_interview_foundation"
    assert script.get_revision("010_stage7_manual_invitations") is not None


def test_010_migration_file_defines_upgrade_and_downgrade() -> None:
    path = VERSIONS / "010_stage7_manual_invitations.py"
    source = path.read_text(encoding="utf-8")
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    assert "interview_invitation_messages" in source
    assert "interview_invitation_versions" in source
    assert "interview_invitation_send_records" in source
    assert "uq_invitation_msg_schedule_event_audience_recipient" in source
    assert "uq_invitation_version_message_version_no" in source
    assert "fk_invitation_messages_current_version" in source
    assert "009_stage7_interview_foundation" in source
    assert "invitation_confirmation_summary" not in source
    assert "notification_tasks" not in source
    assert "smtp" not in source.lower()
    assert "sendgrid" not in source.lower()


def test_010_migration_file_exists() -> None:
    path = VERSIONS / "010_stage7_manual_invitations.py"
    assert path.exists()
    _ = os.environ.get("TEST_DATABASE_URL", "")
