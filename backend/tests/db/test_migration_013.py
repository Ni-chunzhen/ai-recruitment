"""013 Stage 8 interview AI foundation — unit migration RED tests (corrected).

Locks revision metadata, seven-table DDL shape via Column-definition regexes,
circular FKs, attempt encrypted columns, task_type check introduction,
and strict downgrade ordering. Fails until migration file exists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
VERSIONS = BACKEND_ROOT / "alembic" / "versions"

REVISION = "013_stage8_interview_ai_foundation"
DOWN_REVISION = "012_transcript_workflow"
MIGRATION_FILE = VERSIONS / f"{REVISION}.py"

EXPECTED_TABLES = (
    "interview_question_sets",
    "interview_question_versions",
    "interview_question_items",
    "interview_round_analyses",
    "interview_round_analysis_versions",
    "interview_round_analysis_dimensions",
    "interview_round_analysis_evidence",
)

TRANSCRIPT_TABLES_012 = (
    "interview_transcripts",
    "interview_transcript_versions",
    "interview_transcript_segments",
)

CIRCULAR_FK_NAMES = (
    "fk_question_sets_current_version",
    "fk_round_analyses_current_version",
)

CONSTRAINT_NAMES = (
    "uq_interview_question_sets_round_id",
    "ck_interview_question_sets_status",
    "ck_interview_question_sets_confirmed_pair",
    "ck_interview_question_sets_ready_requires_confirm",
    "uq_question_versions_set_no",
    "uq_question_versions_set_label",
    "ck_question_versions_no_positive",
    "ck_question_versions_source_type",
    "ck_question_versions_source_ai_task",
    "uq_question_versions_ai_task",
    "uq_question_items_version_order",
    "ck_question_items_display_order_positive",
    "ck_question_items_evidence_source",
    "uq_round_analyses_round_id",
    "uq_analysis_versions_analysis_no",
    "uq_analysis_versions_analysis_label",
    "uq_analysis_versions_ai_task",
    "ck_analysis_versions_no_positive",
    "ck_analysis_versions_overall_score",
    "uq_analysis_dims_version_key",
    "uq_analysis_dims_version_order",
    "ck_analysis_dims_order_positive",
    "ck_analysis_dims_weight",
    "ck_analysis_dims_score",
    "ck_analysis_dims_score_info_mutex",
    "uq_analysis_evidence_dim_segment",
    "ck_analysis_evidence_segment_no_positive",
    "ck_ai_tasks_task_type",
)

# column_name -> (must_be_Text, nullable)
ENCRYPTED_COLUMN_SPECS: dict[str, tuple[bool, bool]] = {
    "question_encrypted": (True, False),
    "purpose_encrypted": (True, False),
    "resume_evidence_encrypted": (True, True),
    "follow_up_prompts_encrypted": (True, False),
    "risk_flags_encrypted": (True, False),
    "overall_summary_encrypted": (True, False),
    "analysis_encrypted": (True, False),
    "strengths_encrypted": (True, False),
    "risks_encrypted": (True, False),
    "insufficient_information_encrypted": (True, True),
    "suggested_follow_ups_encrypted": (True, False),
    "quote_encrypted": (True, False),
    "sensitive_request_encrypted": (True, True),
    "sensitive_response_encrypted": (True, True),
}


def _script() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _source() -> str:
    assert MIGRATION_FILE.exists(), f"missing migration file: {MIGRATION_FILE.name}"
    return MIGRATION_FILE.read_text(encoding="utf-8")


def _column_def_pattern(name: str, *, text: bool, nullable: bool) -> re.Pattern[str]:
    null_token = "True" if nullable else "False"
    type_part = r"sa\.Text\(\)" if text else r".+?"
    return re.compile(
        rf'Column\(\s*[\'"]{re.escape(name)}[\'"]\s*,\s*{type_part}\s*,\s*'
        rf"(?:[^)]*?)*nullable\s*=\s*{null_token}",
        re.MULTILINE | re.DOTALL,
    )


def test_013_migration_file_exists() -> None:
    assert MIGRATION_FILE.exists()


def test_revision_013_revises_012_and_is_parent_of_014() -> None:
    script = _script()
    assert script.get_current_head() == "016_offer_console_delivery"
    assert script.get_heads() == ["016_offer_console_delivery"]
    revision = script.get_revision(REVISION)
    assert revision.down_revision == DOWN_REVISION
    child = script.get_revision("014_hiring_decisions")
    assert child is not None
    assert child.down_revision == REVISION
    mid = script.get_revision("015_comprehensive_interview_analysis")
    assert mid is not None
    assert mid.down_revision == "014_hiring_decisions"
    head = script.get_revision("016_offer_console_delivery")
    assert head is not None
    assert head.down_revision == "015_comprehensive_interview_analysis"
    assert len(revision.revision) <= 64
    assert len(REVISION) <= 64


def test_013_declares_seven_tables_and_infrastructure() -> None:
    source = _source()
    for name in EXPECTED_TABLES:
        assert name in source
    assert "ck_ai_tasks_task_type" in source
    assert "sensitive_request_encrypted" in source
    assert "sensitive_response_encrypted" in source
    assert "INTERVIEW_QUESTION_GENERATE" in source
    assert "INTERVIEW_ROUND_ANALYZE" in source
    # inputs live on versions, not sets
    sets_block_match = re.search(
        r'create_table\(\s*[\'"]interview_question_sets[\'"].*?(?=create_table|create_foreign_key|def downgrade)',
        source,
        re.DOTALL,
    )
    assert sets_block_match is not None
    sets_block = sets_block_match.group(0)
    assert "job_version_id" not in sets_block
    assert "resume_version_id" not in sets_block
    assert "job_version_id" in source
    assert "resume_version_id" in source
    assert "ck_question_versions_source_ai_task" in source


def test_013_constraint_names_within_pg_limit() -> None:
    source = _source()
    for name in CONSTRAINT_NAMES + CIRCULAR_FK_NAMES:
        assert name in source
        assert len(name) <= 63


def test_013_encrypted_columns_have_text_and_nullable() -> None:
    source = _source()
    for col, (is_text, nullable) in ENCRYPTED_COLUMN_SPECS.items():
        pattern = _column_def_pattern(col, text=is_text, nullable=nullable)
        assert pattern.search(source), (
            f"column {col} must be sa.Text nullable={nullable} in a Column(...)"
        )


def test_013_dimensions_snapshot_jsonb_non_null() -> None:
    source = _source()
    pattern = re.compile(
        r'Column\(\s*[\'"]dimensions_snapshot[\'"]\s*,\s*.*JSONB.*nullable\s*=\s*False',
        re.DOTALL | re.IGNORECASE,
    )
    assert pattern.search(source)


def test_013_circular_fks_set_null_after_tables() -> None:
    source = _source()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    last_create_table = max(upgrade.rfind("create_table"), upgrade.rfind("op.create_table"))
    assert last_create_table >= 0
    for name in CIRCULAR_FK_NAMES:
        pos = upgrade.find(name)
        assert pos > last_create_table
        # ondelete SET NULL near the FK name window
        window = upgrade[pos : pos + 400]
        assert "SET NULL" in window or "SET NULL" in upgrade


def test_013_ai_task_fk_on_question_version_is_restrict() -> None:
    source = _source()
    # ai_task_id FK must RESTRICT (not SET NULL)
    assert re.search(
        r"ai_task_id[\s\S]{0,500}ondelete\s*=\s*[\'\"]RESTRICT[\'\"]",
        source,
        re.IGNORECASE,
    )


def test_013_no_pg_enum_no_alembic_version_alter() -> None:
    source = _source()
    assert "postgresql.ENUM" not in source
    assert "create_type" not in source.lower()
    assert "ALTER TABLE alembic_version" not in source
    assert "version_num TYPE" not in source


def test_013_upgrade_downgrade_defined() -> None:
    source = _source()
    assert "def upgrade()" in source
    assert "def downgrade()" in source
    assert DOWN_REVISION in source


def test_013_downgrade_order_is_strict() -> None:
    source = _source()
    downgrade = source.split("def downgrade()")[1]
    # Parse approximate op call order by line index of markers.
    markers = {
        "drop_fk_q": downgrade.find("fk_question_sets_current_version"),
        "drop_fk_a": downgrade.find("fk_round_analyses_current_version"),
        "drop_evidence": downgrade.find("interview_round_analysis_evidence"),
        "drop_dims": downgrade.find("interview_round_analysis_dimensions"),
        "drop_aver": downgrade.find("interview_round_analysis_versions"),
        "drop_analyses": downgrade.find(
            'drop_table(\n        "interview_round_analyses"'
        ),
        "drop_items": downgrade.find("interview_question_items"),
        "drop_qver": downgrade.find("interview_question_versions"),
        "drop_qsets": downgrade.find('drop_table(\n        "interview_question_sets"')
        if 'drop_table(\n        "interview_question_sets"' in downgrade
        else downgrade.find("interview_question_sets"),
        "delete_stage8_tasks": max(
            downgrade.find("INTERVIEW_QUESTION_GENERATE"),
            downgrade.find("INTERVIEW_ROUND_ANALYZE"),
        ),
        "drop_task_type_ck": downgrade.find("ck_ai_tasks_task_type"),
        "drop_sens_req": downgrade.find("sensitive_request_encrypted"),
        "drop_sens_resp": downgrade.find("sensitive_response_encrypted"),
    }
    for key, pos in markers.items():
        assert pos >= 0, f"downgrade missing marker for {key}"

    assert markers["drop_fk_q"] < markers["drop_qsets"]
    assert markers["drop_fk_a"] < markers["drop_analyses"]
    assert markers["drop_evidence"] < markers["drop_dims"] < markers["drop_aver"]
    assert markers["drop_aver"] < markers["drop_analyses"]
    assert markers["drop_items"] < markers["drop_qver"] < markers["drop_qsets"]
    # Business tables removed before stage8 task cleanup / check restore / encrypted cols.
    assert markers["drop_qsets"] < markers["delete_stage8_tasks"]
    assert markers["drop_analyses"] < markers["delete_stage8_tasks"]
    assert markers["delete_stage8_tasks"] < markers["drop_task_type_ck"]
    assert markers["drop_task_type_ck"] < markers["drop_sens_req"]
    assert markers["drop_task_type_ck"] < markers["drop_sens_resp"]

    for table in TRANSCRIPT_TABLES_012:
        assert not re.search(rf"drop_table\(\s*[\'\"]{table}[\'\"]", downgrade)
    for field in (
        "transcript_completion_mode",
        "transcript_completion_reason_code",
        "transcript_completed_by",
        "transcript_completed_at",
    ):
        assert not re.search(
            rf"drop_column\(\s*[\'\"]interview_rounds[\'\"]\s*,\s*[\'\"]{field}[\'\"]",
            downgrade,
        )


def test_013_does_not_add_ai_columns_to_interview_rounds() -> None:
    source = _source()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    assert not re.search(
        r"add_column\(\s*[\'\"]interview_rounds[\'\"]",
        upgrade,
    )


def test_013_module_parses_as_python_when_present() -> None:
    source = _source()
    tree = ast.parse(source)
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "upgrade" in names
    assert "downgrade" in names
