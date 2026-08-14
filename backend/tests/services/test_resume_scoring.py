import pytest

from app.models.resume import (
    SCREENING_REASON_CODES,
    SCREENING_REASON_OTHER,
    SCREENING_REASON_REQUIRED_DECISIONS,
    SCREENING_REJECT,
    SCREENING_TALENT_POOL,
)
from app.services.ai_providers.base import validate_ai_result
from app.services.ai_providers.mock import mock_resume_parse, mock_resume_score
from app.services.resumes import recompute_weighted_total
from app.services.score_validation import (
    ScoreOutputInvalidError,
    compute_score_totals,
    validate_score_against_snapshot,
    validate_screening_payload,
)


def test_resume_parse_mock_validates() -> None:
    payload = mock_resume_parse(
        {
            "resume_text": (
                "张三\n13800138000\nzhang@example.com\n熟悉 Vue 与 TypeScript\n"
            )
        }
    )
    validated = validate_ai_result("RESUME_PARSE", payload)
    assert validated["standardized_text"]
    assert "Vue" in validated["skills"] or "TypeScript" in validated["skills"]


def test_resume_score_recomputes_weights() -> None:
    dims = [
        {"name": "专业能力", "weight": 40, "score": 80, "evidence": "a"},
        {"name": "沟通协作", "weight": 60, "score": 50, "evidence": "b"},
    ]
    weight_map = {"专业能力": 40.0, "沟通协作": 60.0}
    recomputed, total = recompute_weighted_total(dimensions=dims, weight_map=weight_map)
    assert recomputed[0]["weighted_score"] == 32.0
    assert recomputed[1]["weighted_score"] == 30.0
    assert total == 62.0


def test_resume_score_mock_validates() -> None:
    payload = mock_resume_score(
        {
            "resume_text": "候选人具备专业能力与沟通协作经验",
            "dimensions_json": [
                {"name": "专业能力", "description": "d1", "weight": 50},
                {"name": "沟通协作", "description": "d2", "weight": 50},
            ],
        }
    )
    validated = validate_ai_result("RESUME_SCORE", payload)
    assert len(validated["dimensions"]) == 2


def test_validate_score_rejects_unknown_and_missing_dimensions() -> None:
    snapshot = {
        "dimensions": [
            {"name": "专业能力", "weight": 50},
            {"name": "沟通协作", "weight": 50},
        ]
    }
    with pytest.raises(ScoreOutputInvalidError, match="dimension name mismatch"):
        validate_score_against_snapshot(
            normalized={"dimensions": [{"name": "专业能力", "score": 80}]},
            snapshot=snapshot,
        )
    with pytest.raises(ScoreOutputInvalidError, match="unknown"):
        validate_score_against_snapshot(
            normalized={
                "dimensions": [
                    {"name": "专业能力", "score": 80},
                    {"name": "沟通协作", "score": 70},
                    {"name": "未知维度", "score": 10},
                ]
            },
            snapshot=snapshot,
        )


def test_validate_score_rejects_duplicates() -> None:
    snapshot = {
        "dimensions": [
            {"name": "专业能力", "weight": 50},
            {"name": "沟通协作", "weight": 50},
        ]
    }
    with pytest.raises(ScoreOutputInvalidError, match="duplicate"):
        validate_score_against_snapshot(
            normalized={
                "dimensions": [
                    {"name": "专业能力", "score": 80},
                    {"name": "专业能力", "score": 70},
                ]
            },
            snapshot=snapshot,
        )


def test_score_difference_warning_when_model_total_diverges() -> None:
    dims = [
        {"name": "专业能力", "score": 80},
        {"name": "沟通协作", "score": 50},
    ]
    recomputed, calculated, difference, warnings = compute_score_totals(
        dimensions=dims,
        weight_map={"专业能力": 40.0, "沟通协作": 60.0},
        model_total=90.0,
    )
    assert calculated == 62.0
    assert difference is not None and abs(difference) > 0.01
    assert warnings
    assert recomputed[0]["weighted_score"] == 32.0


def test_score_difference_none_when_model_total_missing() -> None:
    _recomputed, calculated, difference, warnings = compute_score_totals(
        dimensions=[{"name": "专业能力", "score": 100}],
        weight_map={"专业能力": 100.0},
        model_total=None,
    )
    assert calculated == 100.0
    assert difference is None
    assert warnings == []


def test_screening_requires_reason_code_for_reject_and_talent_pool() -> None:
    with pytest.raises(ValueError, match="reason_code"):
        validate_screening_payload(
            decision=SCREENING_REJECT,
            reason_code=None,
            reason=None,
            required_decisions=set(SCREENING_REASON_REQUIRED_DECISIONS),
            allowed_codes=set(SCREENING_REASON_CODES),
            other_code=SCREENING_REASON_OTHER,
        )
    with pytest.raises(ValueError, match="reason_code"):
        validate_screening_payload(
            decision=SCREENING_TALENT_POOL,
            reason_code=None,
            reason="ok",
            required_decisions=set(SCREENING_REASON_REQUIRED_DECISIONS),
            allowed_codes=set(SCREENING_REASON_CODES),
            other_code=SCREENING_REASON_OTHER,
        )
    with pytest.raises(ValueError, match="reason is required"):
        validate_screening_payload(
            decision=SCREENING_REJECT,
            reason_code=SCREENING_REASON_OTHER,
            reason="  ",
            required_decisions=set(SCREENING_REASON_REQUIRED_DECISIONS),
            allowed_codes=set(SCREENING_REASON_CODES),
            other_code=SCREENING_REASON_OTHER,
        )
    validate_screening_payload(
        decision="enter_interview",
        reason_code=None,
        reason=None,
        required_decisions=set(SCREENING_REASON_REQUIRED_DECISIONS),
        allowed_codes=set(SCREENING_REASON_CODES),
        other_code=SCREENING_REASON_OTHER,
    )
