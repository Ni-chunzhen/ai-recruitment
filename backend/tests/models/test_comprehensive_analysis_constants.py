"""Comprehensive analysis constants and immutable model shape (Task 1)."""

from __future__ import annotations

from typing import get_args

from app.models.ai_task import (
    SENSITIVE_AI_TASK_TYPES,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPES,
)
from app.models.comprehensive_analysis import (
    COMPREHENSIVE_GAP_CODES,
    COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION,
    COMPREHENSIVE_WORKFLOW_KEY,
    COMPREHENSIVE_WORKFLOW_VERSION,
    ApplicationComprehensiveAnalysis,
    ApplicationComprehensiveAnalysisVersion,
)
from app.schemas.ai_task import TaskType


def test_task_types_include_comprehensive() -> None:
    assert (
        TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE
        == "INTERVIEW_COMPREHENSIVE_ANALYZE"
    )
    assert TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE in TASK_TYPES
    assert len(TASK_TYPES) == 7


def test_sensitive_whitelist_is_exactly_three() -> None:
    assert SENSITIVE_AI_TASK_TYPES == {
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    }
    assert len(SENSITIVE_AI_TASK_TYPES) == 3


def test_task_type_literal_includes_comprehensive() -> None:
    literals = set(get_args(TaskType))
    assert "INTERVIEW_COMPREHENSIVE_ANALYZE" in literals
    assert literals == set(TASK_TYPES)
    assert len(literals) == 7


def test_comprehensive_workflow_and_gap_constants() -> None:
    assert COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION == "1.0"
    assert COMPREHENSIVE_WORKFLOW_KEY == "interview_comprehensive_analyze"
    assert COMPREHENSIVE_WORKFLOW_VERSION == "1.0"
    assert COMPREHENSIVE_GAP_CODES == frozenset(
        {
            "cancelled",
            "ended_abnormally",
            "not_completed",
            "without_transcript",
            "transcript_unconfirmed",
            "analysis_none",
            "analysis_stale",
            "excluded_other",
        }
    )


def test_comprehensive_models_have_no_plaintext_body_columns() -> None:
    set_cols = ApplicationComprehensiveAnalysis.__table__.c
    ver_cols = ApplicationComprehensiveAnalysisVersion.__table__.c

    assert "application_id" in set_cols
    assert "current_version_id" in set_cols
    assert "overall_summary" not in ver_cols
    assert "overall_summary_encrypted" in ver_cols
    assert "round_refs" in ver_cols
    assert "coverage_report" in ver_cols
    assert "input_snapshot_hash" in ver_cols
    assert "ai_task_id" in ver_cols
    assert ver_cols["round_refs"].nullable is False
    assert ver_cols["coverage_report"].nullable is False
    assert ver_cols["overall_summary_encrypted"].nullable is False

    forbidden_substrings = (
        "quote",
        "jd_text",
        "resume_text",
        "transcript_text",
        "hiring_decision",
    )
    for name in list(set_cols.keys()) + list(ver_cols.keys()):
        lower = name.lower()
        for needle in forbidden_substrings:
            assert needle not in lower, f"forbidden {needle!r} in {name}"
        if name == "overall_summary":
            raise AssertionError("plaintext overall_summary column forbidden")


def test_version_ai_task_id_unique_and_application_unique() -> None:
    set_table = ApplicationComprehensiveAnalysis.__table__
    ver_table = ApplicationComprehensiveAnalysisVersion.__table__

    set_uq = {
        uq.name
        for uq in set_table.constraints
        if uq.name and "application" in uq.name.lower()
    }
    assert "uq_comprehensive_analyses_application_id" in set_uq or any(
        list(uq.columns.keys()) == ["application_id"]
        for uq in set_table.constraints
        if getattr(uq, "unique", False) or uq.__class__.__name__ == "UniqueConstraint"
    )

    # Prefer named unique constraints from plan-aligned ORM
    ver_uq_names = {c.name for c in ver_table.constraints if c.name}
    assert "uq_comprehensive_versions_ai_task" in ver_uq_names
    assert "uq_comprehensive_versions_analysis_no" in ver_uq_names
    assert "uq_comprehensive_versions_analysis_label" in ver_uq_names

    app_unique = False
    for uq in set_table.constraints:
        cols = list(getattr(uq, "columns", {}).keys()) if hasattr(uq, "columns") else []
        if cols == ["application_id"]:
            app_unique = True
            break
    if not app_unique:
        for idx in set_table.indexes:
            if idx.unique and [c.name for c in idx.columns] == ["application_id"]:
                app_unique = True
                break
    assert app_unique, "application_id must be unique on comprehensive analyses"
