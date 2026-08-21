"""Alembic 017 integration_secrets migration structure (Task 1)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
REVISION = "017_integration_secrets"
DOWN_REVISION = "016_offer_console_delivery"
MIGRATION_FILE = VERSIONS / "017_integration_secrets.py"

REQUIRED_COLUMNS = (
    "id",
    "provider",
    "config_key",
    "value_encrypted",
    "is_secret",
    "enabled",
    "updated_by",
    "created_at",
    "updated_at",
)


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migration_017_revision_chain() -> None:
    script = _script()
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == DOWN_REVISION
    assert script.get_current_head() == REVISION
    assert script.get_heads() == [REVISION]
    assert script.get_revision(DOWN_REVISION) is not None
    assert len(revision.revision) <= 64


def test_migration_017_upgrade_creates_table_and_downgrade_drops() -> None:
    assert MIGRATION_FILE.is_file()
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | None = "{DOWN_REVISION}"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "integration_secrets" in source
    assert "uq_integration_secrets_provider_key" in source
    assert "value_encrypted" in source
    assert "is_secret" in source
    assert "enabled" in source
    for col in REQUIRED_COLUMNS:
        assert col in source

    assert "value_nonsecret" not in source
    assert "value_plain" not in source
    assert 'sa.Column("secret"' not in source
    assert 'sa.Column("api_key"' not in source
    assert 'sa.Column("password"' not in source
    assert "smtp" not in source.lower()
    assert "ck_ai_tasks_task_type" not in source
    assert 'op.create_table(\n        "offers"' not in source
    assert 'op.drop_table("offers")' not in source

    assert 'op.drop_table("integration_secrets")' in source
    assert "ALTER TABLE alembic_version" not in source
