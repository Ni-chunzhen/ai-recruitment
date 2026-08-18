"""PostgreSQL compile tests for candidate center list/count queries."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.candidate_center import (
    assigned_interview_exists,
    build_count_candidate_center_applications_select,
    build_list_candidate_center_application_rows_select,
    display_round_id_subquery,
)

DIALECT = postgresql.dialect()


def _compile(statement) -> str:
    return str(
        statement.compile(
            dialect=DIALECT,
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def _outer_from_clause(sql: str) -> str:
    """Return SQL up to the first correlated/scalar subselect for outer-query checks."""
    markers = ("(select", " exists")
    indices = [sql.find(marker) for marker in markers if marker in sql]
    if not indices:
        return sql
    return sql[: min(indices)]


def test_assigned_interview_exists_sql_is_nested_exists() -> None:
    sql = _compile(assigned_interview_exists())
    assert "exists" in sql
    assert "interview_rounds" in sql
    assert "interview_round_interviewers" in sql
    assert "application_id" in sql
    assert "interview_round_id" in sql
    assert sql.count("exists") >= 2


def test_assigned_interview_exists_not_used_in_outer_join() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    outer = _outer_from_clause(sql)
    assert "from job_applications" in outer
    assert "join interview_round_interviewers" not in outer


def _where_clause(sql: str) -> str:
    if " where " not in sql:
        return ""
    return sql.split(" where ", 1)[1].split(" order by ")[0]


def test_assigned_sql_does_not_exclude_cancelled_or_abnormal() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    where = _where_clause(sql)
    assert "cancelled" not in where
    assert "ended_abnormally" not in where
    assert "interview_task_state" not in where


def test_assigned_false_omits_exists_filter() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=False,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "interview_round_interviewers" not in sql


def test_display_round_assigned_true_requires_interviewer() -> None:
    sql = _compile(display_round_id_subquery(assigned=True))
    assert "max" in sql or "order by" in sql
    assert "sequence_no" in sql
    assert "interview_round_interviewers" in sql
    assert "exists" in sql


def test_display_round_assigned_false_is_max_sequence_any_round() -> None:
    sql = _compile(display_round_id_subquery(assigned=False))
    assert "sequence_no" in sql
    assert "interview_round_interviewers" not in sql


def test_keyword_sql_only_hits_allowed_columns() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword="alice",
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "candidates.name" in sql
    assert "candidates.phone" in sql
    assert "candidates.email" in sql
    assert "jobs.code" in sql
    assert "jobs.name" in sql
    assert "extracted_text" not in sql
    assert "standardized_text" not in sql
    assert "raw_jd_text" not in sql
    assert "question" not in sql
    assert "quote" not in sql


def test_status_and_pipeline_filters_are_equality() -> None:
    job_id = uuid4()
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status="in_progress",
            pipeline_status="interviewing",
            job_id=job_id,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "job_applications.status = " in sql
    assert "job_applications.pipeline_status = " in sql
    assert "job_applications.job_id = " in sql
    assert "in_progress" in sql
    assert "interviewing" in sql


def test_sort_whitelist_updated_at_and_created_at_desc() -> None:
    updated_sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "job_applications.updated_at desc" in updated_sql
    assert "job_applications.id desc" in updated_sql
    updated_idx = updated_sql.index("job_applications.updated_at desc")
    id_idx = updated_sql.index("job_applications.id desc")
    assert id_idx > updated_idx

    created_sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="created_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "job_applications.created_at desc" in created_sql
    assert "job_applications.id desc" in created_sql
    created_idx = created_sql.index("job_applications.created_at desc")
    id_idx = created_sql.index("job_applications.id desc")
    assert id_idx > created_idx


def test_pagination_uses_offset_limit() -> None:
    list_sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=2,
            page_size=20,
        )
    )
    assert "offset 20" in list_sql
    assert "limit 20" in list_sql

    count_sql = _compile(
        build_count_candidate_center_applications_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
        )
    )
    assert "offset" not in count_sql
    assert "limit" not in count_sql


def test_list_outer_query_from_job_applications() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    outer = _outer_from_clause(sql)
    assert "from job_applications" in outer
    assert "from interview_round_interviewers" not in outer
    assert "from interview_rounds" not in outer.split("order by")[0].split("where")[0]


def test_display_round_at_most_one_row_per_application() -> None:
    list_sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    round_sql = _compile(display_round_id_subquery(assigned=True))
    outer = _outer_from_clause(list_sql)
    assert "join interview_round_interviewers" not in outer
    assert "distinct" not in outer
    assert "group by" not in outer
    has_single_row_shape = (
        "partition by" in round_sql
        or "limit 1" in round_sql
        or "lateral" in round_sql
    )
    assert has_single_row_shape


def test_list_selects_application_id_once() -> None:
    sql = _compile(
        build_list_candidate_center_application_rows_select(
            assigned=True,
            status=None,
            pipeline_status=None,
            job_id=None,
            keyword=None,
            sort="updated_at_desc",
            page=1,
            page_size=20,
        )
    )
    assert "job_applications.id" in sql
    outer = _outer_from_clause(sql)
    assert "join interview_round_interviewers" not in outer
