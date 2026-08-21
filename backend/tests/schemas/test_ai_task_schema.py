"""TaskType Literal ↔ ORM TASK_TYPES alignment (Task 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.ai_task import TASK_TYPES
from app.schemas.ai_task import (
    AITaskAdminDetailOut,
    AITaskAdminListItemOut,
    AITaskSummaryOut,
    MarkStaleFailedAITaskOut,
    TaskType,
)

EXPECTED_TASK_TYPE_LITERALS = frozenset(
    {
        "JD_PARSE",
        "SCORE_DIMENSION_RECOMMEND",
        "RESUME_PARSE",
        "RESUME_SCORE",
        "INTERVIEW_QUESTION_GENERATE",
        "INTERVIEW_ROUND_ANALYZE",
        "INTERVIEW_COMPREHENSIVE_ANALYZE",
    }
)


def test_task_type_literal_seven_exact_values() -> None:
    assert set(get_args(TaskType)) == EXPECTED_TASK_TYPE_LITERALS
    assert len(get_args(TaskType)) == 7


def test_task_type_literal_matches_orm_task_types() -> None:
    assert set(get_args(TaskType)) == set(TASK_TYPES)


def _summary_payload(*, task_type: str) -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "task_type": task_type,
        "status": "pending",
        "business_type": "interview_round",
        "business_id": uuid4(),
        "error_code": None,
        "error_message": None,
        "attempt_count": 0,
        "started_at": None,
        "finished_at": None,
        "created_at": now,
        "updated_at": now,
        "attempts": [],
    }


def _admin_item_payload(*, task_type: str) -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "task_type": task_type,
        "business_type": "interview_round",
        "business_id": uuid4(),
        "status": "pending",
        "attempt_count": 0,
        "created_at": now,
    }


@pytest.mark.parametrize(
    "task_type",
    ("INTERVIEW_QUESTION_GENERATE", "INTERVIEW_ROUND_ANALYZE"),
)
def test_ai_task_summary_out_accepts_question_and_analyze(task_type: str) -> None:
    out = AITaskSummaryOut.model_validate(_summary_payload(task_type=task_type))
    assert out.task_type == task_type


@pytest.mark.parametrize(
    "task_type",
    ("INTERVIEW_QUESTION_GENERATE", "INTERVIEW_ROUND_ANALYZE"),
)
def test_ai_task_admin_detail_out_accepts_question_and_analyze(task_type: str) -> None:
    item = AITaskAdminListItemOut.model_validate(
        _admin_item_payload(task_type=task_type)
    )
    assert item.task_type == task_type
    detail = AITaskAdminDetailOut.model_validate(
        {**_admin_item_payload(task_type=task_type), "attempts": []}
    )
    assert detail.task_type == task_type


def test_mark_stale_out_still_omits_task_type() -> None:
    assert "task_type" not in MarkStaleFailedAITaskOut.model_fields


@pytest.mark.parametrize(
    "task_type",
    (
        "JD_PARSE",
        "SCORE_DIMENSION_RECOMMEND",
        "RESUME_PARSE",
        "RESUME_SCORE",
    ),
)
def test_ai_task_summary_out_still_accepts_legacy_task_types(task_type: str) -> None:
    payload = _summary_payload(task_type=task_type)
    payload["business_type"] = "job" if task_type.startswith("JD") else "application"
    out = AITaskSummaryOut.model_validate(payload)
    assert out.task_type == task_type


def test_ai_task_summary_out_rejects_unknown_task_type() -> None:
    with pytest.raises(ValidationError):
        AITaskSummaryOut.model_validate(
            _summary_payload(task_type="NOT_A_REAL_TASK_TYPE")
        )
