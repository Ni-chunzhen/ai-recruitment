"""RED/GREEN tests for stage 8 interview AI Pydantic contracts and provider wiring."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.ai_task import (
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
)
from app.services.ai_providers.base import validate_ai_result

SECRET = "TOP_SECRET_TRANSCRIPT_98765"
SEGMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _question_item(**overrides: object) -> dict:
    item = {
        "dimension_key": "D001",
        "question": "请描述一次跨团队冲突处理。",
        "purpose": "考察协作与冲突处理。",
        "evidence_source": "JOB_REQUIREMENT",
        "resume_evidence": None,
        "follow_up_prompts": ["对方立场是什么？"],
        "risk_flags": ["可能回避责任"],
        "display_order": 1,
    }
    item.update(overrides)
    return item


def _valid_questions_payload() -> dict:
    return {"questions": [_question_item()]}


def _evidence(**overrides: object) -> dict:
    item = {
        "segment_id": str(SEGMENT_ID),
        "segment_no": 1,
        "quote": "我当时先对齐目标。",
    }
    item.update(overrides)
    return item


def _dimension_analysis(**overrides: object) -> dict:
    item = {
        "dimension_key": "D001",
        "score": 4,
        "evidence": [_evidence()],
        "analysis": "候选人能描述冲突处理路径。",
        "strengths": ["目标对齐"],
        "risks": ["细节偏少"],
        "insufficient_information": None,
        "suggested_follow_ups": ["请补充具体结果"],
    }
    item.update(overrides)
    return item


def _valid_analyze_payload() -> dict:
    return {
        "dimensions": [_dimension_analysis()],
        "overall_summary": "整体表现稳定，建议深入追问结果指标。",
        "model_reported_overall_score": "4.00",
    }


def test_question_generate_schema_accepts_valid_payload() -> None:
    from app.schemas.interview_ai import InterviewQuestionGenerateResult

    result = InterviewQuestionGenerateResult.model_validate(_valid_questions_payload())
    assert len(result.questions) == 1
    assert result.questions[0].dimension_key == "D001"
    assert result.questions[0].evidence_source == "JOB_REQUIREMENT"


def test_question_generate_trims_required_text_and_rejects_blank() -> None:
    from app.schemas.interview_ai import InterviewQuestionGenerateResult

    payload = _valid_questions_payload()
    payload["questions"][0]["question"] = "  请举例说明推进项目的方法。  "
    payload["questions"][0]["purpose"] = "  考察推进能力  "
    result = InterviewQuestionGenerateResult.model_validate(payload)
    assert result.questions[0].question == "请举例说明推进项目的方法。"
    assert result.questions[0].purpose == "考察推进能力"

    blank = _valid_questions_payload()
    blank["questions"][0]["question"] = "   "
    with pytest.raises(Exception) as exc_info:
        InterviewQuestionGenerateResult.model_validate(blank)
    assert SECRET not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("questions", []),
        ("questions", [_question_item() for _ in range(31)]),
        ("question", ""),
        ("question", "x" * 2001),
        ("purpose", "x" * 2001),
        ("resume_evidence", "x" * 2001),
        ("follow_up_prompts", ["ok"] * 11),
        ("follow_up_prompts", ["x" * 1001]),
        ("risk_flags", ["ok"] * 11),
        ("risk_flags", ["x" * 501]),
        ("display_order", 0),
        ("display_order", 31),
        ("evidence_source", "OTHER"),
    ),
)
def test_question_generate_size_and_enum_limits(field: str, value: object) -> None:
    from app.schemas.interview_ai import InterviewQuestionGenerateResult

    payload = _valid_questions_payload()
    if field == "questions":
        payload["questions"] = value  # type: ignore[assignment]
    else:
        payload["questions"][0][field] = value
    with pytest.raises(Exception):
        InterviewQuestionGenerateResult.model_validate(payload)


def test_question_generate_forbids_decision_extra_fields() -> None:
    from app.schemas.interview_ai import InterviewQuestionGenerateResult

    payload = _valid_questions_payload()
    payload["hire"] = True
    with pytest.raises(Exception) as exc_info:
        InterviewQuestionGenerateResult.model_validate(payload)
    assert "hire" in str(exc_info.value).lower() or "extra" in str(exc_info.value).lower()
    assert SECRET not in str(exc_info.value)

    nested = _valid_questions_payload()
    nested["questions"][0]["offer"] = "录用"
    with pytest.raises(Exception):
        InterviewQuestionGenerateResult.model_validate(nested)


def test_analyze_schema_accepts_optional_model_score() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    result = InterviewRoundAnalyzeResult.model_validate(_valid_analyze_payload())
    assert result.overall_summary.startswith("整体表现")
    assert result.model_reported_overall_score == Decimal("4.00")
    assert result.dimensions[0].score == 4


def test_analyze_schema_allows_null_score_and_insufficient() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    payload = _valid_analyze_payload()
    payload["dimensions"][0]["score"] = None
    payload["dimensions"][0]["insufficient_information"] = "转写未覆盖该维度"
    payload["dimensions"][0]["evidence"] = []
    result = InterviewRoundAnalyzeResult.model_validate(payload)
    assert result.dimensions[0].score is None
    assert result.dimensions[0].insufficient_information == "转写未覆盖该维度"


def _mutate_analyze(kind: str) -> dict:
    payload = _valid_analyze_payload()
    if kind == "empty_dims":
        payload["dimensions"] = []
    elif kind == "too_many_dims":
        payload["dimensions"] = [
            _dimension_analysis(dimension_key=f"D{i:03d}") for i in range(1, 52)
        ]
    elif kind == "too_much_evidence":
        payload["dimensions"][0]["evidence"] = [
            _evidence(segment_id=str(uuid4()), segment_no=i) for i in range(1, 7)
        ]
    elif kind == "long_quote":
        payload["dimensions"][0]["evidence"][0]["quote"] = "x" * 2001
    elif kind == "long_analysis":
        payload["dimensions"][0]["analysis"] = "x" * 10001
    elif kind == "too_many_strengths":
        payload["dimensions"][0]["strengths"] = ["ok"] * 21
    elif kind == "long_risk":
        payload["dimensions"][0]["risks"] = ["x" * 1001]
    elif kind == "long_insufficient":
        payload["dimensions"][0]["insufficient_information"] = "x" * 5001
    elif kind == "too_many_follow_ups":
        payload["dimensions"][0]["suggested_follow_ups"] = ["ok"] * 21
    elif kind == "long_summary":
        payload["overall_summary"] = "x" * 20001
    elif kind == "segment_no_zero":
        payload["dimensions"][0]["evidence"][0]["segment_no"] = 0
    return payload


@pytest.mark.parametrize(
    "kind",
    (
        "empty_dims",
        "too_many_dims",
        "too_much_evidence",
        "long_quote",
        "long_analysis",
        "too_many_strengths",
        "long_risk",
        "long_insufficient",
        "too_many_follow_ups",
        "long_summary",
        "segment_no_zero",
    ),
)
def test_analyze_schema_size_limits(kind: str) -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    with pytest.raises(Exception):
        InterviewRoundAnalyzeResult.model_validate(_mutate_analyze(kind))


def test_analyze_schema_rejects_extra_and_blank_summary() -> None:
    from app.schemas.interview_ai import InterviewRoundAnalyzeResult

    extra = _valid_analyze_payload()
    extra["reject"] = True
    with pytest.raises(Exception):
        InterviewRoundAnalyzeResult.model_validate(extra)

    blank = _valid_analyze_payload()
    blank["overall_summary"] = "   "
    with pytest.raises(Exception):
        InterviewRoundAnalyzeResult.model_validate(blank)


def test_evidence_segment_is_in_memory_only_model() -> None:
    from app.schemas.interview_ai import InterviewEvidenceSegment

    segment = InterviewEvidenceSegment(
        id=SEGMENT_ID,
        transcript_version_id=uuid4(),
        segment_no=1,
        is_included_in_analysis=True,
        text="仅内存正文",
    )
    assert segment.text == "仅内存正文"


def test_validate_ai_result_accepts_stage8_task_types() -> None:
    questions = validate_ai_result(
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE, _valid_questions_payload()
    )
    assert questions["questions"][0]["display_order"] == 1
    analyzed = validate_ai_result(
        TASK_TYPE_INTERVIEW_ROUND_ANALYZE, _valid_analyze_payload()
    )
    assert analyzed["dimensions"][0]["dimension_key"] == "D001"


def test_validate_ai_result_stage8_does_not_require_snapshot() -> None:
    payload = _valid_questions_payload()
    payload["questions"][0]["dimension_key"] = "D999"
    validated = validate_ai_result(TASK_TYPE_INTERVIEW_QUESTION_GENERATE, payload)
    assert validated["questions"][0]["dimension_key"] == "D999"


def test_validate_ai_result_unknown_task_type_still_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported task_type"):
        validate_ai_result("NOT_A_REAL_TYPE", {"questions": []})


def test_validate_ai_result_legacy_types_unchanged() -> None:
    jd = validate_ai_result(
        TASK_TYPE_JD_PARSE,
        {
            "responsibilities": ["a"],
            "requirements": [],
            "must_have": [],
            "nice_to_have": [],
            "skills": [],
        },
    )
    assert jd["responsibilities"] == ["a"]
    dims = validate_ai_result(
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        {
            "dimensions": [
                {"name": "沟通", "weight": 40, "description": "", "anchors": []},
                {"name": "专业", "weight": 60, "description": "", "anchors": []},
            ]
        },
    )
    assert len(dims["dimensions"]) == 2
    parsed = validate_ai_result(
        TASK_TYPE_RESUME_PARSE,
        {
            "name": "张三",
            "phone": "",
            "email": "",
            "years_of_experience": None,
            "education": [],
            "work_experience": [],
            "projects": [],
            "skills": [],
            "standardized_text": "简历正文",
        },
    )
    assert parsed["standardized_text"] == "简历正文"
    scored = validate_ai_result(
        TASK_TYPE_RESUME_SCORE,
        {
            "dimensions": [
                {
                    "name": "专业能力",
                    "description": "",
                    "weight": 100,
                    "score": 80,
                    "evidence": "x",
                }
            ]
        },
    )
    assert scored["dimensions"][0]["score"] == 80


def test_stage8_validation_errors_omit_secret_on_too_long(caplog: pytest.LogCaptureFixture) -> None:
    from app.services.interview_ai_validation import AIOutputValidationError

    payload = _valid_questions_payload()
    payload["questions"][0]["question"] = SECRET + ("x" * 2000)
    with pytest.raises(AIOutputValidationError) as exc_info:
        validate_ai_result(TASK_TYPE_INTERVIEW_QUESTION_GENERATE, payload)
    rendered = str(exc_info.value)
    assert SECRET not in rendered
    assert "input_value" not in rendered
    assert SECRET not in caplog.text
    assert getattr(exc_info.value, "code", None)


def test_stage8_validation_errors_omit_secret_on_extra_field(caplog: pytest.LogCaptureFixture) -> None:
    from app.services.interview_ai_validation import AIOutputValidationError

    payload = _valid_analyze_payload()
    payload["offer"] = SECRET
    with pytest.raises(AIOutputValidationError) as exc_info:
        validate_ai_result(TASK_TYPE_INTERVIEW_ROUND_ANALYZE, payload)
    rendered = str(exc_info.value)
    assert SECRET not in rendered
    assert "input_value" not in rendered
    assert SECRET not in caplog.text


def test_stage8_validation_errors_omit_secret_on_type_error(caplog: pytest.LogCaptureFixture) -> None:
    from app.services.interview_ai_validation import AIOutputValidationError

    payload = _valid_analyze_payload()
    payload["dimensions"][0]["score"] = SECRET
    with pytest.raises(AIOutputValidationError) as exc_info:
        validate_ai_result(TASK_TYPE_INTERVIEW_ROUND_ANALYZE, payload)
    rendered = str(exc_info.value)
    assert SECRET not in rendered
    assert "input_value" not in rendered
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_mock_provider_returns_valid_stage8_payloads() -> None:
    from app.services.ai_providers.mock import run_mock

    question_out = await run_mock(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot={"dimensions": [{"dimension_key": "D001"}]},
    )
    assert question_out.ok is True
    assert question_out.result is not None
    assert question_out.result["questions"]

    analyze_out = await run_mock(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        input_snapshot={"dimensions": [{"dimension_key": "D001"}]},
    )
    assert analyze_out.ok is True
    assert analyze_out.result is not None
    assert analyze_out.result["overall_summary"]
