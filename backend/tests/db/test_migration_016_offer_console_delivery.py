"""Alembic 016 offer console delivery migration structure (Task 1)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"
REVISION = "016_offer_console_delivery"
DOWN_REVISION = "015_comprehensive_interview_analysis"
MIGRATION_FILE = VERSIONS / "016_offer_console_delivery.py"

REQUIRED_OFFER_COLUMNS = (
    "id",
    "application_id",
    "hiring_decision_id",
    "status",
    "current_version_id",
    "recipient_email_masked",
    "recipient_name",
    "lock_version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "voided_at",
    "void_reason_code",
)

REQUIRED_VERSION_COLUMNS = (
    "id",
    "offer_id",
    "version_no",
    "subject_encrypted",
    "body_html_encrypted",
    "body_text_encrypted",
    "content_hash",
    "template_code",
    "template_version",
    "frozen",
    "created_by",
    "created_at",
)

REQUIRED_ATTEMPT_COLUMNS = (
    "id",
    "offer_id",
    "offer_version_id",
    "provider",
    "status",
    "attempt_no",
    "idempotency_key",
    "error_code",
    "error_message_safe",
    "started_at",
    "finished_at",
    "next_retry_at",
    "created_by",
    "created_at",
)


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migration_016_revision_chain() -> None:
    script = _script()
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == DOWN_REVISION
    head = script.get_current_head()
    assert head in {REVISION, "017_integration_secrets"}
    if head == "017_integration_secrets":
        assert script.get_revision(head).down_revision == REVISION
    else:
        assert script.get_heads() == [REVISION]
    assert len(revision.revision) <= 64


def test_migration_016_upgrade_creates_three_tables_and_downgrade_drops() -> None:
    assert MIGRATION_FILE.is_file()
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert f'revision: str = "{REVISION}"' in source
    assert f'down_revision: str | None = "{DOWN_REVISION}"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source

    assert 'op.create_table(\n        "offers"' in source or (
        'op.create_table("offers"' in source
    )
    assert "offer_versions" in source
    assert "offer_send_attempts" in source
    assert "uq_offers_application_active" in source
    assert "NOT IN ('voided')" in source
    assert "uq_offer_versions_offer_version_no" in source
    assert "uq_offer_send_attempts_idempotency" in source
    assert "fk_offers_current_version" in source
    assert "hiring_decisions" in source
    assert "ix_offers_application_id" in source

    for col in REQUIRED_OFFER_COLUMNS:
        assert col in source
    for col in REQUIRED_VERSION_COLUMNS:
        assert col in source
    for col in REQUIRED_ATTEMPT_COLUMNS:
        assert col in source

    assert 'sa.Column("recipient_email"' not in source
    assert 'sa.Column("subject"' not in source
    assert 'sa.Column("body_html"' not in source
    assert 'sa.Column("body_text"' not in source
    assert "attachment" not in source.lower()
    assert "smtp" not in source.lower()
    assert "ck_ai_tasks_task_type" not in source
    assert 'op.create_table(\n        "hiring_decisions"' not in source

    # downgrade order: attempts → versions → offers (FK-safe)
    drop_attempts = source.index('op.drop_table("offer_send_attempts")')
    drop_versions = source.index('op.drop_table("offer_versions")')
    drop_offers = source.index('op.drop_table("offers")')
    assert drop_attempts < drop_versions < drop_offers
    assert "ALTER TABLE alembic_version" not in source
