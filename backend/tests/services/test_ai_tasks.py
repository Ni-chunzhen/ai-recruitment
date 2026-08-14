import pytest
from pydantic import ValidationError

from app.models.ai_task import (
    AI_TASK_MAX_ATTEMPTS,
    ERROR_CATEGORY_NON_RETRYABLE,
    ERROR_CATEGORY_RETRYABLE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
)
from app.schemas.ai_task import JdParseResult, ScoreDimensionRecommendResult
from app.services.ai_providers.base import (
    classify_http_error,
    retry_countdown_seconds,
    should_auto_retry,
    validate_ai_result,
)
from app.services.ai_providers.mock import parse_raw_jd_text, recommend_dimensions


def test_jd_parse_schema_accepts_lists() -> None:
    payload = {
        "responsibilities": ["做需求"],
        "requirements": ["3 年"],
        "must_have": ["本科"],
        "nice_to_have": ["英语"],
        "skills": ["Python"],
    }
    result = JdParseResult.model_validate(payload).model_dump()
    assert result["skills"] == ["Python"]


def test_jd_parse_schema_rejects_non_string_items() -> None:
    with pytest.raises(ValidationError):
        JdParseResult.model_validate(
            {
                "responsibilities": [1],
                "requirements": [],
                "must_have": [],
                "nice_to_have": [],
                "skills": [],
            }
        )


def test_score_dimension_requires_name_and_positive_weight() -> None:
    with pytest.raises(ValidationError):
        ScoreDimensionRecommendResult.model_validate(
            {
                "dimensions": [
                    {
                        "name": "  ",
                        "weight": 10,
                        "description": "",
                        "anchors": ["1", "2", "3", "4", "5"],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        ScoreDimensionRecommendResult.model_validate(
            {
                "dimensions": [
                    {
                        "name": "沟通",
                        "weight": 0,
                        "description": "",
                        "anchors": [],
                    }
                ]
            }
        )


def test_score_dimension_weight_need_not_sum_100() -> None:
    result = ScoreDimensionRecommendResult.model_validate(
        {
            "dimensions": [
                {"name": "A", "weight": 30, "description": "", "anchors": []},
                {"name": "B", "weight": 40, "description": "", "anchors": []},
            ]
        }
    )
    assert sum(item.weight for item in result.dimensions) == 70


def test_validate_ai_result_jd_parse() -> None:
    validated = validate_ai_result(
        TASK_TYPE_JD_PARSE,
        {
            "responsibilities": ["a"],
            "requirements": [],
            "must_have": [],
            "nice_to_have": [],
            "skills": [],
        },
    )
    assert validated["responsibilities"] == ["a"]


def test_validate_ai_result_rejects_bad_payload() -> None:
    with pytest.raises((ValidationError, ValueError)):
        validate_ai_result(TASK_TYPE_JD_PARSE, ["not", "object"])


def test_mock_parse_raw_jd_by_sections() -> None:
    text = """
岗位职责
- 负责招聘需求分析
- 推动面试流程

任职要求
- 3 年以上 HR 经验
- 熟悉劳动法

技能
- 沟通
- Excel
"""
    parsed = parse_raw_jd_text(text)
    assert "负责招聘需求分析" in parsed["responsibilities"]
    assert "3 年以上 HR 经验" in parsed["requirements"]
    assert "沟通" in parsed["skills"]
    validated = validate_ai_result(TASK_TYPE_JD_PARSE, parsed)
    assert set(validated.keys()) == {
        "responsibilities",
        "requirements",
        "must_have",
        "nice_to_have",
        "skills",
    }


def test_mock_recommend_dimensions_weights_near_100() -> None:
    result = recommend_dimensions(
        {
            "skills": ["Python", "SQL", "沟通", "协作"],
            "requirements": ["本科", "3 年经验"],
        }
    )
    dims = result["dimensions"]
    assert 3 <= len(dims) <= 6
    assert abs(sum(item["weight"] for item in dims) - 100) < 0.01
    validated = validate_ai_result(TASK_TYPE_SCORE_DIMENSION_RECOMMEND, result)
    assert validated["dimensions"][0]["name"]


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_category"),
    [
        (None, "network_error", ERROR_CATEGORY_RETRYABLE),
        (500, "provider_5xx", ERROR_CATEGORY_RETRYABLE),
        (502, "provider_5xx", ERROR_CATEGORY_RETRYABLE),
        (401, "auth_failed", ERROR_CATEGORY_NON_RETRYABLE),
        (403, "auth_failed", ERROR_CATEGORY_NON_RETRYABLE),
        (400, "invalid_params", ERROR_CATEGORY_NON_RETRYABLE),
        (422, "invalid_params", ERROR_CATEGORY_NON_RETRYABLE),
    ],
)
def test_classify_http_error(
    status_code: int | None,
    expected_code: str,
    expected_category: str,
) -> None:
    code, category = classify_http_error(status_code)
    assert code == expected_code
    assert category == expected_category


def test_should_auto_retry_budget() -> None:
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
    assert not should_auto_retry(error_category=None, cycle_attempt_count=1)


def test_retry_countdown_mapping() -> None:
    assert retry_countdown_seconds(1) == 10
    assert retry_countdown_seconds(2) == 30
    assert retry_countdown_seconds(3) is None


@pytest.mark.asyncio
async def test_mock_provider_jd_parse_no_sleep() -> None:
    from app.services.ai_providers.mock import run_mock

    outcome = await run_mock(
        task_type=TASK_TYPE_JD_PARSE,
        input_snapshot={"raw_jd_text": "岗位职责\n- 写代码\n任职要求\n- 本科"},
        sleep_seconds=0,
    )
    assert outcome.ok
    assert outcome.result is not None
    assert "responsibilities" in outcome.result


def test_enqueue_is_patchable(monkeypatch) -> None:
    from uuid import uuid4

    from app.services import ai_tasks as ai_tasks_service

    called: list[tuple] = []

    def fake_enqueue(task_id, *, countdown=0):
        called.append((task_id, countdown))

    monkeypatch.setattr(ai_tasks_service, "enqueue_ai_task", fake_enqueue)
    task_id = uuid4()
    ai_tasks_service.enqueue_ai_task(task_id, countdown=10)
    assert called == [(task_id, 10)]


def test_build_dify_inputs_jd_parse_maps_hr_manual_override() -> None:
    from app.services.ai_providers.dify import build_dify_inputs

    inputs = build_dify_inputs(
        TASK_TYPE_JD_PARSE,
        {
            "raw_jd_text": "岗位职责：负责需求",
            "job_title": "产品经理",
            "department": "产品部",
        },
    )
    assert inputs == {
        "job_title": "产品经理",
        "department": "产品部",
        "hr_manual_override": "岗位职责：负责需求",
    }


def test_build_dify_inputs_score_maps_structured_jd_json() -> None:
    import json

    from app.services.ai_providers.dify import build_dify_inputs

    inputs = build_dify_inputs(
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        {
            "job_title": "算法工程师",
            "department": "研发中心",
            "jd_content": "原始JD",
            "structured_jd": {
                "responsibilities": ["建模"],
                "requirements": ["3年"],
                "must_have": ["硕士"],
                "nice_to_have": ["论文"],
                "skills": ["Python", "PyTorch"],
            },
        },
    )
    assert inputs["job_title"] == "算法工程师"
    assert inputs["jd_content"] == "原始JD"
    parsed = json.loads(inputs["structured_jd"])
    assert parsed["skill_keywords"] == ["Python", "PyTorch"]
    assert parsed["responsibilities"] == ["建模"]


def test_normalize_dify_outputs_jd_parse() -> None:
    from app.services.ai_providers.dify import normalize_dify_outputs

    result = normalize_dify_outputs(
        TASK_TYPE_JD_PARSE,
        {
            "structured_jd": (
                '{"responsibilities":["职责1"],"requirements":["要求1"],'
                '"must_have":["本科"],"nice_to_have":["英语"],'
                '"plus_items_for_diff":"有大模型经验","skill_keywords":["AI","产品"]}'
            )
        },
    )
    assert result["skills"] == ["AI", "产品"]
    assert "有大模型经验" in result["nice_to_have"]
    validated = validate_ai_result(TASK_TYPE_JD_PARSE, result)
    assert validated["responsibilities"] == ["职责1"]


def test_normalize_dify_outputs_jd_structured_result_blob() -> None:
    import json

    from app.services.ai_providers.dify import (
        extract_jd_content_from_outputs,
        normalize_dify_outputs,
    )

    blob = {
        "job_title": "产品经理",
        "department": "产品部",
        "jd_content": "手录JD正文",
        "structured_jd": {
            "responsibilities": ["a", "b", "c", "d"],
            "requirements": ["r1", "r2", "r3"],
            "must_have": ["本科"],
            "nice_to_have": ["英语"],
            "plus_items_for_diff": "大厂背景",
            "skill_keywords": ["PRD", "调研", "分析", "沟通", "协作"],
        },
    }
    result = normalize_dify_outputs(
        TASK_TYPE_JD_PARSE,
        {"result": json.dumps(blob, ensure_ascii=False)},
    )
    assert result["skills"][:2] == ["PRD", "调研"]
    assert "大厂背景" in result["nice_to_have"]
    assert extract_jd_content_from_outputs(
        {"result": json.dumps(blob, ensure_ascii=False)}
    ) == "手录JD正文"
    validated = validate_ai_result(TASK_TYPE_JD_PARSE, result)
    assert len(validated["responsibilities"]) == 4


def test_normalize_dify_outputs_score_dimensions() -> None:
    from app.services.ai_providers.dify import normalize_dify_outputs

    result = normalize_dify_outputs(
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        {
            "final_dimensions": (
                '{"dimensions":[{"name":"专业能力","description":"业务判断",'
                '"weight":40},{"name":"协作","description":"跨团队",'
                '"weight":60}]}'
            )
        },
    )
    assert len(result["dimensions"]) == 2
    assert result["dimensions"][0]["anchors"] == ["", "", "", "", ""]
    validated = validate_ai_result(TASK_TYPE_SCORE_DIMENSION_RECOMMEND, result)
    assert validated["dimensions"][0]["name"] == "专业能力"


def test_normalize_dify_outputs_score_anchors_object() -> None:
    import json

    from app.services.ai_providers.dify import normalize_dify_outputs

    payload = {
        "dimensions": [
            {
                "name": "前沿技术学习与转化",
                "description": "主动跟踪多模态大模型并快速应用于产品团队",
                "weight": 15,
                "score_anchors": {
                    "1": "很少关注新技术，学习停留在听闻层面",
                    "2": "会跟进资讯，但难以落到产品实践",
                    "3": "能跟踪关键技术并在项目中小范围试用",
                    "4": "能推动技术选型并形成可复用方案",
                    "5": "持续引领团队转化前沿技术并显著提升产品竞争力",
                },
            },
            {
                "name": "协作",
                "description": "跨团队",
                "weight": 85,
                "score_anchors": {
                    "1": "a",
                    "2": "b",
                    "3": "c",
                    "4": "d",
                    "5": "e",
                },
            },
        ]
    }
    result = normalize_dify_outputs(
        TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        {"final_dimensions": json.dumps(payload, ensure_ascii=False)},
    )
    assert result["dimensions"][0]["anchors"][0].startswith("很少关注")
    assert result["dimensions"][0]["anchors"][4].startswith("持续引领")
    assert result["dimensions"][1]["anchors"] == ["a", "b", "c", "d", "e"]
    validated = validate_ai_result(TASK_TYPE_SCORE_DIMENSION_RECOMMEND, result)
    assert validated["dimensions"][0]["name"] == "前沿技术学习与转化"


def test_normalize_dify_outputs_score_error_payload() -> None:
    from app.services.ai_providers.dify import normalize_dify_outputs

    with pytest.raises(ValueError, match="维度数量为0"):
        normalize_dify_outputs(
            TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
            {"final_dimensions": '{"error": "维度数量为0，不是6"}'},
        )


def test_looks_like_score_outputs() -> None:
    import json

    from app.services.ai_providers.dify import looks_like_score_outputs

    assert looks_like_score_outputs(
        {"final_dimensions": '{"dimensions":[{"name":"A","weight":100}]}'}
    )
    assert not looks_like_score_outputs(
        {
            "result": json.dumps(
                {
                    "structured_jd": {
                        "responsibilities": ["x"],
                        "requirements": [],
                        "must_have": [],
                        "nice_to_have": [],
                        "skill_keywords": [],
                    }
                }
            )
        }
    )
