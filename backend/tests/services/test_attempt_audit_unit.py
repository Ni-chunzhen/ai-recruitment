"""Unit tests for 008 attempt audit (TDD)."""

from __future__ import annotations

from app.models.ai_task import (
    AI_TASK_MAX_ATTEMPTS,
    ERROR_CATEGORY_NON_RETRYABLE,
    ERROR_CATEGORY_RETRYABLE,
)
from app.services.ai_providers.base import (
    retry_countdown_seconds,
    should_auto_retry,
)


def test_should_auto_retry_uses_cycle_attempt_count_not_global() -> None:
    """Budget is per manual retry cycle (max 3), independent of global attempt_count."""
    assert should_auto_retry(
        error_category=ERROR_CATEGORY_RETRYABLE, cycle_attempt_count=1
    )
    assert should_auto_retry(
        error_category=ERROR_CATEGORY_RETRYABLE, cycle_attempt_count=2
    )
    assert not should_auto_retry(
        error_category=ERROR_CATEGORY_RETRYABLE,
        cycle_attempt_count=AI_TASK_MAX_ATTEMPTS,
    )
    assert not should_auto_retry(
        error_category=ERROR_CATEGORY_NON_RETRYABLE, cycle_attempt_count=1
    )
    # Global high count must NOT block auto-retry if cycle budget remains
    assert should_auto_retry(
        error_category=ERROR_CATEGORY_RETRYABLE, cycle_attempt_count=1
    )


def test_retry_countdown_uses_cycle_attempt_no() -> None:
    assert retry_countdown_seconds(1) == 10
    assert retry_countdown_seconds(2) == 30
    assert retry_countdown_seconds(3) is None


def test_extract_dify_run_ids_from_single_workflow_response() -> None:
    from app.services.ai_providers.dify import extract_dify_run_ids

    provider_run_id, request_id = extract_dify_run_ids(
        {
            "workflow_run_id": "run-aaa",
            "task_id": "req-bbb",
            "data": {"id": "run-aaa", "status": "succeeded"},
        }
    )
    assert provider_run_id == "run-aaa"
    assert request_id == "req-bbb"


def test_extract_dify_run_ids_from_steps_takes_last() -> None:
    from app.services.ai_providers.dify import extract_dify_run_ids

    provider_run_id, request_id = extract_dify_run_ids(
        {
            "steps": [
                {
                    "workflow_run_id": "run-step1",
                    "task_id": "req-step1",
                    "data": {"id": "run-step1"},
                },
                {
                    "workflow_run_id": "run-step2",
                    "task_id": "req-step2",
                    "data": {"id": "run-step2"},
                },
            ]
        }
    )
    assert provider_run_id == "run-step2"
    assert request_id == "req-step2"


def test_extract_dify_run_ids_none_when_no_raw() -> None:
    from app.services.ai_providers.dify import extract_dify_run_ids

    assert extract_dify_run_ids(None) == (None, None)
    assert extract_dify_run_ids({}) == (None, None)
    assert extract_dify_run_ids({"body": "not-json-structure"}) == (None, None)


def test_manual_retry_numbering_semantics_documented() -> None:
    """Pure semantic check for dual counters after one manual retry cycle."""
    # After first cycle exhausted 3 auto attempts:
    attempt_count = 3
    retry_cycle_no = 0
    cycle_attempt_count = 3
    # Manual retry:
    retry_cycle_no += 1
    cycle_attempt_count = 0
    assert attempt_count == 3  # never reset
    assert retry_cycle_no == 1
    assert cycle_attempt_count == 0
    # Next provider call:
    attempt_count += 1
    cycle_attempt_count += 1
    global_attempt_no = attempt_count
    cycle_attempt_no = cycle_attempt_count
    assert global_attempt_no == 4
    assert cycle_attempt_no == 1
    # Auto retries in new cycle:
    attempt_count += 1
    cycle_attempt_count += 1
    assert attempt_count == 5 and cycle_attempt_count == 2
    attempt_count += 1
    cycle_attempt_count += 1
    assert attempt_count == 6 and cycle_attempt_count == 3
    assert not should_auto_retry(
        error_category=ERROR_CATEGORY_RETRYABLE,
        cycle_attempt_count=cycle_attempt_count,
    )
