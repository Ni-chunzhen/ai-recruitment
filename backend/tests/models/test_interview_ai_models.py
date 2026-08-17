"""RED/GREEN tests for stage 8 interview AI ORM, AI task constants, and audit keys."""

from __future__ import annotations

from decimal import Decimal

from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, Numeric, Text, UniqueConstraint, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_013 = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "013_stage8_interview_ai_foundation.py"
)
EXPECTED_TASK_TYPES = frozenset(
    {
        "JD_PARSE",
        "SCORE_DIMENSION_RECOMMEND",
        "RESUME_PARSE",
        "RESUME_SCORE",
        "INTERVIEW_QUESTION_GENERATE",
        "INTERVIEW_ROUND_ANALYZE",
    }
)

EXPECTED_TABLES = {
    "InterviewQuestionSet": "interview_question_sets",
    "InterviewQuestionVersion": "interview_question_versions",
    "InterviewQuestionItem": "interview_question_items",
    "InterviewRoundAnalysis": "interview_round_analyses",
    "InterviewRoundAnalysisVersion": "interview_round_analysis_versions",
    "InterviewRoundAnalysisDimension": "interview_round_analysis_dimensions",
    "InterviewRoundAnalysisEvidence": "interview_round_analysis_evidence",
}

EXPECTED_COLUMNS: dict[str, dict[str, bool]] = {
    "interview_question_sets": {
        "id": False,
        "interview_round_id": False,
        "current_version_id": True,
        "status": False,
        "confirmed_by": True,
        "confirmed_at": True,
        "created_by": True,
        "created_at": False,
        "updated_at": False,
    },
    "interview_question_versions": {
        "id": False,
        "question_set_id": False,
        "version_no": False,
        "version_label": False,
        "source_type": False,
        "ai_task_id": True,
        "job_version_id": False,
        "resume_version_id": False,
        "input_snapshot_hash": False,
        "created_by": True,
        "created_at": False,
    },
    "interview_question_items": {
        "id": False,
        "question_version_id": False,
        "dimension_key": False,
        "question_encrypted": False,
        "purpose_encrypted": False,
        "evidence_source": False,
        "resume_evidence_encrypted": True,
        "follow_up_prompts_encrypted": False,
        "risk_flags_encrypted": False,
        "display_order": False,
        "created_at": False,
    },
    "interview_round_analyses": {
        "id": False,
        "interview_round_id": False,
        "current_version_id": True,
        "created_at": False,
        "updated_at": False,
    },
    "interview_round_analysis_versions": {
        "id": False,
        "analysis_id": False,
        "version_no": False,
        "version_label": False,
        "transcript_version_id": False,
        "job_version_id": False,
        "ai_task_id": False,
        "dimensions_snapshot": False,
        "overall_score": True,
        "overall_summary_encrypted": False,
        "created_by": True,
        "created_at": False,
    },
    "interview_round_analysis_dimensions": {
        "id": False,
        "analysis_version_id": False,
        "dimension_key": False,
        "dimension_name": False,
        "weight": False,
        "score": True,
        "analysis_encrypted": False,
        "strengths_encrypted": False,
        "risks_encrypted": False,
        "insufficient_information_encrypted": True,
        "suggested_follow_ups_encrypted": False,
        "display_order": False,
        "created_at": False,
    },
    "interview_round_analysis_evidence": {
        "id": False,
        "analysis_dimension_id": False,
        "transcript_segment_id": False,
        "segment_no": False,
        "quote_encrypted": False,
        "created_at": False,
    },
}

NAMED_CONSTRAINTS = (
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

NAMED_INDEXES = (
    "ix_question_versions_set_id",
    "ix_question_items_version_id",
    "ix_analysis_versions_analysis_id",
    "ix_analysis_dims_version_id",
    "ix_analysis_evidence_dim_id",
    "ix_analysis_evidence_segment_id",
)

PLAINTEXT_FORBIDDEN = frozenset(
    {
        "question",
        "purpose",
        "resume_evidence",
        "follow_up_prompts",
        "risk_flags",
        "overall_summary",
        "analysis",
        "strengths",
        "risks",
        "insufficient_information",
        "suggested_follow_ups",
        "quote",
        "body",
        "text",
    }
)

SENSITIVE_AUDIT_FIELD_NAMES = (
    "question",
    "purpose",
    "resume_evidence",
    "follow_up_prompts",
    "risk_flags",
    "overall_summary",
    "analysis",
    "strengths",
    "risks",
    "insufficient_information",
    "suggested_follow_ups",
    "quote",
    "sensitive_request",
    "sensitive_response",
    "raw_request",
    "raw_response",
    "result_payload",
    "transcript_text",
    "segment_text",
    "resume_text",
    "jd_content",
    "question_encrypted",
    "purpose_encrypted",
    "resume_evidence_encrypted",
    "follow_up_prompts_encrypted",
    "risk_flags_encrypted",
    "overall_summary_encrypted",
    "analysis_encrypted",
    "strengths_encrypted",
    "risks_encrypted",
    "insufficient_information_encrypted",
    "suggested_follow_ups_encrypted",
    "quote_encrypted",
    "sensitive_request_encrypted",
    "sensitive_response_encrypted",
)

SENSITIVE_VALUE_MARKERS_REQUIRED = (
    "password",
    "token",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "bearer",
    "enc:v1:",
)

SENSITIVE_VALUE_MARKERS_FORBIDDEN = (
    "question",
    "purpose",
    "quote",
    "overall_summary",
    "analysis",
    "strengths",
    "risks",
    "follow_up",
    "resume_text",
    "jd_content",
    "segment_text",
    "raw_output",
    "raw_response",
    "raw_request",
    "encrypted",
)


def _load_models():
    from app.models.interview_ai import (
        InterviewQuestionItem,
        InterviewQuestionSet,
        InterviewQuestionVersion,
        InterviewRoundAnalysis,
        InterviewRoundAnalysisDimension,
        InterviewRoundAnalysisEvidence,
        InterviewRoundAnalysisVersion,
    )

    return {
        "InterviewQuestionSet": InterviewQuestionSet,
        "InterviewQuestionVersion": InterviewQuestionVersion,
        "InterviewQuestionItem": InterviewQuestionItem,
        "InterviewRoundAnalysis": InterviewRoundAnalysis,
        "InterviewRoundAnalysisVersion": InterviewRoundAnalysisVersion,
        "InterviewRoundAnalysisDimension": InterviewRoundAnalysisDimension,
        "InterviewRoundAnalysisEvidence": InterviewRoundAnalysisEvidence,
    }


def _fk_map(table) -> dict[str, tuple[str, str | None]]:
    mapping: dict[str, tuple[str, str | None]] = {}
    for column in table.columns:
        for fk in column.foreign_keys:
            mapping[column.name] = (fk.column.table.name, fk.ondelete)
    return mapping


def _constraint_names(table) -> set[str]:
    names = {c.name for c in table.constraints if c.name}
    names.update({idx.name for idx in table.indexes if idx.name})
    return names


def test_seven_orm_classes_and_table_names() -> None:
    models = _load_models()
    for class_name, table_name in EXPECTED_TABLES.items():
        model = models[class_name]
        assert model.__tablename__ == table_name


def test_column_sets_nullable_and_no_plaintext_body() -> None:
    models = _load_models()
    tables = {model.__tablename__: model.__table__ for model in models.values()}
    for table_name, cols in EXPECTED_COLUMNS.items():
        table = tables[table_name]
        actual = {column.name: column.nullable for column in table.columns}
        for col, nullable in cols.items():
            assert col in actual, f"{table_name}.{col} missing"
            assert actual[col] is nullable, f"{table_name}.{col} nullable"
        for col in table.columns:
            assert col.name not in PLAINTEXT_FORBIDDEN
            if col.name.endswith("_encrypted"):
                assert isinstance(col.type, Text)


def test_question_version_input_fks_not_on_set() -> None:
    models = _load_models()
    set_cols = {c.name for c in models["InterviewQuestionSet"].__table__.columns}
    assert "job_version_id" not in set_cols
    assert "resume_version_id" not in set_cols

    version = models["InterviewQuestionVersion"].__table__
    fks = _fk_map(version)
    assert version.c.ai_task_id.nullable is True
    assert fks["ai_task_id"] == ("ai_tasks", "RESTRICT")
    assert fks["job_version_id"] == ("job_versions", "RESTRICT")
    assert fks["resume_version_id"] == ("resume_versions", "RESTRICT")
    names = _constraint_names(version)
    assert "ck_question_versions_source_ai_task" in names
    uniques = [
        c
        for c in version.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_question_versions_ai_task"
    ]
    assert len(uniques) == 1
    assert list(uniques[0].columns)[0].name == "ai_task_id"


def test_circular_current_version_fks() -> None:
    models = _load_models()
    qset = models["InterviewQuestionSet"].__table__
    analysis = models["InterviewRoundAnalysis"].__table__
    q_fks = list(qset.c.current_version_id.foreign_keys)
    a_fks = list(analysis.c.current_version_id.foreign_keys)
    assert len(q_fks) == 1
    assert q_fks[0].ondelete == "SET NULL"
    assert q_fks[0].name == "fk_question_sets_current_version"
    assert q_fks[0].column.table.name == "interview_question_versions"
    assert qset.c.current_version_id.nullable is True
    assert len(a_fks) == 1
    assert a_fks[0].ondelete == "SET NULL"
    assert a_fks[0].name == "fk_round_analyses_current_version"
    assert a_fks[0].column.table.name == "interview_round_analysis_versions"
    assert analysis.c.current_version_id.nullable is True


def test_analysis_version_types_and_restrict_fks() -> None:
    models = _load_models()
    table = models["InterviewRoundAnalysisVersion"].__table__
    assert isinstance(table.c.dimensions_snapshot.type, JSONB)
    assert table.c.dimensions_snapshot.nullable is False
    assert isinstance(table.c.overall_score.type, Numeric)
    assert table.c.overall_score.type.precision == 5
    assert table.c.overall_score.type.scale == 2
    fks = _fk_map(table)
    assert fks["transcript_version_id"] == ("interview_transcript_versions", "RESTRICT")
    assert fks["job_version_id"] == ("job_versions", "RESTRICT")
    assert fks["ai_task_id"] == ("ai_tasks", "RESTRICT")
    assert models["InterviewRoundAnalysisVersion"].__annotations__["overall_score"]


def test_named_constraints_and_indexes_match_013() -> None:
    from app.models.ai_task import AITask

    models = _load_models()
    names: set[str] = set()
    for model in models.values():
        names |= _constraint_names(model.__table__)
    names |= _constraint_names(AITask.__table__)
    for name in NAMED_CONSTRAINTS + NAMED_INDEXES:
        assert name in names, name
    business_cks = [
        c.name
        for c in AITask.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name and "business_type" in c.name
    ]
    assert business_cks == []


def test_fk_ondelete_for_child_and_user_columns() -> None:
    models = _load_models()
    assert _fk_map(models["InterviewQuestionSet"].__table__)["interview_round_id"] == (
        "interview_rounds",
        "CASCADE",
    )
    assert _fk_map(models["InterviewQuestionVersion"].__table__)["question_set_id"] == (
        "interview_question_sets",
        "CASCADE",
    )
    assert _fk_map(models["InterviewQuestionItem"].__table__)[
        "question_version_id"
    ] == ("interview_question_versions", "CASCADE")
    assert _fk_map(models["InterviewRoundAnalysis"].__table__)[
        "interview_round_id"
    ] == ("interview_rounds", "CASCADE")
    assert _fk_map(models["InterviewRoundAnalysisVersion"].__table__)["analysis_id"] == (
        "interview_round_analyses",
        "CASCADE",
    )
    assert _fk_map(models["InterviewRoundAnalysisDimension"].__table__)[
        "analysis_version_id"
    ] == ("interview_round_analysis_versions", "CASCADE")
    evidence_fks = _fk_map(models["InterviewRoundAnalysisEvidence"].__table__)
    assert evidence_fks["analysis_dimension_id"] == (
        "interview_round_analysis_dimensions",
        "CASCADE",
    )
    assert evidence_fks["transcript_segment_id"] == (
        "interview_transcript_segments",
        "RESTRICT",
    )
    set_fks = _fk_map(models["InterviewQuestionSet"].__table__)
    assert set_fks["created_by"] == ("users", "SET NULL")
    assert set_fks["confirmed_by"] == ("users", "SET NULL")


def test_task_types_include_stage8_and_legacy() -> None:
    from app.models.ai_task import (
        BUSINESS_TYPE_APPLICATION,
        BUSINESS_TYPE_INTERVIEW_ROUND,
        BUSINESS_TYPE_JOB,
        BUSINESS_TYPE_RESUME_VERSION,
        BUSINESS_TYPES,
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        TASK_TYPE_JD_PARSE,
        TASK_TYPE_RESUME_PARSE,
        TASK_TYPE_RESUME_SCORE,
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        TASK_TYPES,
    )

    assert TASK_TYPE_INTERVIEW_QUESTION_GENERATE == "INTERVIEW_QUESTION_GENERATE"
    assert TASK_TYPE_INTERVIEW_ROUND_ANALYZE == "INTERVIEW_ROUND_ANALYZE"
    assert TASK_TYPES == EXPECTED_TASK_TYPES
    migration_source = MIGRATION_013.read_text(encoding="utf-8")
    for task_type in EXPECTED_TASK_TYPES:
        assert task_type in migration_source
    for legacy in (
        TASK_TYPE_JD_PARSE,
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        TASK_TYPE_RESUME_PARSE,
        TASK_TYPE_RESUME_SCORE,
    ):
        assert legacy in TASK_TYPES
    assert BUSINESS_TYPE_INTERVIEW_ROUND == "interview_round"
    assert BUSINESS_TYPE_INTERVIEW_ROUND in BUSINESS_TYPES
    assert BUSINESS_TYPE_JOB in BUSINESS_TYPES
    assert BUSINESS_TYPE_RESUME_VERSION in BUSINESS_TYPES
    assert BUSINESS_TYPE_APPLICATION in BUSINESS_TYPES


def test_attempt_encrypted_columns_are_nullable_text() -> None:
    from app.models.ai_task import AITaskAttempt

    table = AITaskAttempt.__table__
    for name in ("sensitive_request_encrypted", "sensitive_response_encrypted"):
        column = table.c[name]
        assert isinstance(column.type, Text)
        assert column.nullable is True


def test_sensitive_audit_keys_and_markers() -> None:
    from app.models import SENSITIVE_AUDIT_KEYS
    from app.services.audit import SENSITIVE_VALUE_MARKERS

    lowered_keys = {key.lower() for key in SENSITIVE_AUDIT_KEYS}
    for name in SENSITIVE_AUDIT_FIELD_NAMES:
        assert name.lower() in lowered_keys
    markers = tuple(marker.lower() for marker in SENSITIVE_VALUE_MARKERS)
    for marker in SENSITIVE_VALUE_MARKERS_REQUIRED:
        assert marker.lower() in markers
    for marker in SENSITIVE_VALUE_MARKERS_FORBIDDEN:
        assert marker.lower() not in markers


def test_sanitize_audit_changes_rejects_top_level_sensitive_key() -> None:
    from app.models import sanitize_audit_changes

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"question": "面试官如何看冲突处理"})


def test_sanitize_audit_changes_rejects_nested_dict_sensitive_key() -> None:
    from app.models import sanitize_audit_changes

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes(
            {"metadata": {"analysis": "候选人的分析正文不得进入审计"}}
        )


def test_sanitize_audit_changes_rejects_sensitive_key_inside_list() -> None:
    from app.models import sanitize_audit_changes

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes(
            {"events": [{"status": "failed"}, {"quote": "候选人原话"}]}
        )


@pytest.mark.parametrize(
    "sensitive_key",
    (
        "overall_summary_encrypted",
        "quote_encrypted",
        "meeting_password",
    ),
)
def test_sanitize_audit_changes_keeps_existing_exact_key_protection(
    sensitive_key: str,
) -> None:
    from app.models import sanitize_audit_changes

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({sensitive_key: "must-not-enter-audit"})


def test_sanitize_audit_changes_keeps_exact_safe_metadata_and_serializes_tuple() -> None:
    from app.models import sanitize_audit_changes

    changes = {
        "question_count": 8,
        "evidence_quote_count": 3,
        "analysis_version_id": "11111111-1111-1111-1111-111111111111",
        "dimension_count": 4,
        "status": "succeeded",
        "task_type": "INTERVIEW_QUESTION_GENERATE",
        "workflow_version": "2026-08-17",
        "transitions": ("queued", "succeeded"),
    }

    assert sanitize_audit_changes(changes) == {
        **changes,
        "transitions": ["queued", "succeeded"],
    }


def test_audit_value_scrub_redacts_ciphertext_keeps_nonsensitive() -> None:
    from app.services.audit import _scrub_value

    scrubbed = _scrub_value(
        {
            "version_id": "11111111-1111-1111-1111-111111111111",
            "count": 3,
            "status": "READY",
            "debug": "enc:v1:ciphertext-should-not-leak",
            "note": "contains a quote from the candidate",
        }
    )
    rendered = str(scrubbed)
    assert "enc:v1:" not in rendered
    assert "ciphertext-should-not-leak" not in rendered
    assert scrubbed["note"] == "contains a quote from the candidate"
    assert scrubbed["version_id"] == "11111111-1111-1111-1111-111111111111"
    assert scrubbed["count"] == 3
    assert scrubbed["status"] == "READY"


def test_audit_value_scrub_redacts_nested_ciphertext() -> None:
    from app.services.audit import _scrub_value

    changes = {
        "events": [
            {"status": "succeeded"},
            {"details": ("safe", {"debug": "prefix enc:v1:ciphertext"})},
        ]
    }

    assert _scrub_value(changes) == {
        "events": [
            {"status": "succeeded"},
            {"details": ["safe", {"debug": "[redacted]"}]},
        ]
    }


@pytest.mark.parametrize(
    "credential",
    (
        "password=hunter2",
        "refresh_token=abc",
        "Bearer abc.def",
    ),
)
def test_audit_value_scrub_still_redacts_credentials(credential: str) -> None:
    from app.services.audit import _scrub_value

    assert _scrub_value(credential) == "[redacted]"


def test_audit_value_scrub_keeps_ordinary_chinese_description() -> None:
    from app.services.audit import _scrub_value

    note = "题纲生成成功，共八道题；本次分析已完成，候选人说明清晰。"

    assert _scrub_value(note) == note


def test_audit_value_scrub_keeps_stage8_business_metadata_verbatim() -> None:
    from app.services.audit import _scrub_value

    changes = {
        "task_types": [
            "INTERVIEW_QUESTION_GENERATE",
            "INTERVIEW_ROUND_ANALYZE",
        ],
        "events": [
            "interview_question.generate",
            "interview_analysis.view",
        ],
        "question_count": 8,
        "evidence_quote_count": 3,
        "analysis_version_id": "11111111-1111-1111-1111-111111111111",
        "dimension_count": 4,
        "status": "succeeded",
        "task_type": "INTERVIEW_QUESTION_GENERATE",
        "workflow_version": "2026-08-17",
    }

    assert _scrub_value(changes) == changes


def test_configure_mappers_resolves_circular_relationships() -> None:
    models = _load_models()
    configure_mappers()
    qset = inspect(models["InterviewQuestionSet"])
    versions = qset.relationships["versions"]
    current = qset.relationships["current_version"]
    assert {col.name for col in current.local_columns} == {"current_version_id"}
    version_remote = {col.name for col in versions.remote_side}
    assert "question_set_id" in version_remote
    assert "current_version_id" not in version_remote

    analysis = inspect(models["InterviewRoundAnalysis"])
    a_versions = analysis.relationships["versions"]
    a_current = analysis.relationships["current_version"]
    assert {col.name for col in a_current.local_columns} == {"current_version_id"}
    assert "analysis_id" in {col.name for col in a_versions.remote_side}


def test_child_relationships_use_delete_orphan_not_task_cascade() -> None:
    models = _load_models()
    qver = inspect(models["InterviewQuestionVersion"])
    items = qver.relationships["items"]
    assert "delete-orphan" in (items.cascade or set())
    ai_task = qver.relationships["ai_task"]
    assert "delete-orphan" not in (ai_task.cascade or set())
    assert "all" not in (ai_task.cascade or set()) or "delete" not in str(
        ai_task.cascade
    )

    aver = inspect(models["InterviewRoundAnalysisVersion"])
    dims = aver.relationships["dimensions"]
    assert "delete-orphan" in (dims.cascade or set())
    for name in ("transcript_version", "job_version", "ai_task"):
        rel = aver.relationships[name]
        assert "delete-orphan" not in (rel.cascade or set())

    evidence = inspect(models["InterviewRoundAnalysisDimension"]).relationships[
        "evidence"
    ]
    assert "delete-orphan" in (evidence.cascade or set())
    segment = inspect(models["InterviewRoundAnalysisEvidence"]).relationships[
        "transcript_segment"
    ]
    assert "delete-orphan" not in (segment.cascade or set())


def test_models_exported_from_package() -> None:
    from app import models

    for class_name in EXPECTED_TABLES:
        assert hasattr(models, class_name)


def test_overall_score_python_type_is_decimal() -> None:
    from app.models.interview_ai import InterviewRoundAnalysisVersion

    column = InterviewRoundAnalysisVersion.__table__.c.overall_score
    assert column.type.python_type is Decimal
