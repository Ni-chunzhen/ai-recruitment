from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.models.ai_task import (
    AI_TASK_MAX_ATTEMPTS,
    AI_TASK_RETRY_COUNTDOWNS,
    ERROR_CATEGORY_NON_RETRYABLE,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
)
from app.schemas.ai_task import JdParseResult, ScoreDimensionRecommendResult
from app.schemas.interview_ai import (
    InterviewQuestionGenerateResult,
    InterviewRoundAnalyzeResult,
)
from app.schemas.resume import ResumeParseResult, ResumeScoreResult
from app.services.interview_ai_validation import raise_safe_validation_error


@dataclass
class ProviderOutcome:
    ok: bool
    result: dict[str, Any] | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    provider_run_id: str | None = None
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def validate_ai_result(task_type: str, payload: object) -> dict[str, Any]:
    """Validate provider output; raise ValidationError / ValueError on failure."""
    if not isinstance(payload, dict):
        raise ValueError("AI result must be an object")
    if task_type == TASK_TYPE_JD_PARSE:
        return JdParseResult.model_validate(payload).model_dump(exclude_none=True)
    if task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
        return ScoreDimensionRecommendResult.model_validate(payload).model_dump()
    if task_type == TASK_TYPE_RESUME_PARSE:
        return ResumeParseResult.model_validate(payload).model_dump()
    if task_type == TASK_TYPE_RESUME_SCORE:
        return ResumeScoreResult.model_validate(payload).model_dump()
    try:
        if task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
            return InterviewQuestionGenerateResult.model_validate(payload).model_dump(
                mode="json"
            )
        if task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
            return InterviewRoundAnalyzeResult.model_validate(payload).model_dump(
                mode="json"
            )
    except ValidationError as exc:
        raise_safe_validation_error(exc)
    raise ValueError(f"unsupported task_type: {task_type}")


def classify_http_error(status_code: int | None) -> tuple[str, str]:
    """Return (error_code, error_category) for HTTP failures."""
    if status_code is None:
        return "network_error", ERROR_CATEGORY_RETRYABLE
    if status_code in {401, 403}:
        return "auth_failed", ERROR_CATEGORY_NON_RETRYABLE
    if 400 <= status_code < 500:
        return "invalid_params", ERROR_CATEGORY_NON_RETRYABLE
    if status_code >= 500:
        return "provider_5xx", ERROR_CATEGORY_RETRYABLE
    return "provider_error", ERROR_CATEGORY_NON_RETRYABLE


def should_auto_retry(*, error_category: str | None, cycle_attempt_count: int) -> bool:
    """True when retryable failure still has budget in this manual cycle."""
    return (
        error_category == ERROR_CATEGORY_RETRYABLE
        and cycle_attempt_count < AI_TASK_MAX_ATTEMPTS
    )


def retry_countdown_seconds(cycle_attempt_count: int) -> int | None:
    """Countdown after a failed cycle attempt (keys are cycle_attempt_no 1 or 2)."""
    return AI_TASK_RETRY_COUNTDOWNS.get(cycle_attempt_count)
