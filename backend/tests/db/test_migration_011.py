"""011 invitation confirmation summary migration tests."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"


def test_011_revises_010() -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("011_stage7_invitation_confirmation_summary")
    assert revision.down_revision == "010_stage7_manual_invitations"
    assert script.get_revision("011_stage7_invitation_confirmation_summary") is not None
    # 012 is the current head; 011 remains in the linear chain.
    assert "012_transcript_workflow" in script.get_heads()


def test_010_does_not_define_confirmation_summary() -> None:
    source = (VERSIONS / "010_stage7_manual_invitations.py").read_text(encoding="utf-8")
    assert "invitation_confirmation_summary" not in source
    assert "invitation_confirmed_at" in source
    assert "invitation_confirmed_by" in source
    assert "invitation_confirmed_schedule_version" in source


def test_011_migration_file_defines_idempotent_upgrade_and_downgrade() -> None:
    path = VERSIONS / "011_stage7_invitation_confirmation_summary.py"
    source = path.read_text(encoding="utf-8")
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    assert "invitation_confirmation_summary" in source
    assert "RuntimeError" in source
    assert "010_stage7_manual_invitations" in source
