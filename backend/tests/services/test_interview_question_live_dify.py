from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import get_settings
from app.models.ai_task import (
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    TASK_TYPE_JD_PARSE,
    TASK_TYPE_RESUME_PARSE,
)
from app.services.ai_providers import dify
from app.services.ai_providers.base import ProviderOutcome
from app.services.ai_providers.dify import (
    _workflow_id_for,
    build_dify_inputs,
    normalize_dify_outputs,
)

FICTIONAL_INPUT = {
    "job_title": "FICTIONAL-LIVE-20260818 示例岗位-虚构仓储接口工程师",
    "jd_text": "FICTIONAL-LIVE-20260818 本岗位为完全虚构的演示说明。",
    "resume_text": "FICTIONAL-LIVE-20260818 候选人档案为完全虚构样本。",
    "dimensions": [
        {
            "dimension_key": "D001",
            "display_order": 1,
            "name": "接口实现",
            "weight": "100.00",
            "description": "考察接口实现",
            "anchors": ["1", "2", "3", "4", "5"],
        }
    ],
}

_FAKE_KEY = "test-interview-question-key"
_FAKE_WORKFLOW_ID = "test-interview-question-workflow-id"
_FAKE_BASE_URL = "https://dify.example.test"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bind_post_counter(monkeypatch):
    posted = {"n": 0}

    async def fail_post(**kwargs):
        posted["n"] += 1
        raise AssertionError("must not call real Dify for interview live gate tests")

    monkeypatch.setattr(dify, "_post_workflow", fail_post)
    return posted


def _set_question_live_env(
    monkeypatch,
    *,
    environment: str = "development",
    enabled: str = "true",
    api_key: str = _FAKE_KEY,
    workflow_id: str = _FAKE_WORKFLOW_ID,
    base_url: str = _FAKE_BASE_URL,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("DIFY_INTERVIEW_QUESTION_LIVE_ENABLED", enabled)
    monkeypatch.setenv("DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY", api_key)
    monkeypatch.setenv("DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID", workflow_id)
    monkeypatch.setenv("DIFY_API_BASE_URL", base_url)
    get_settings.cache_clear()


def test_dify_api_key_for_interview_does_not_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "generic-dify-key")
    monkeypatch.setenv("DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY", "")
    monkeypatch.setenv("DIFY_RESUME_PARSE_API_KEY", "")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.dify_api_key_for(TASK_TYPE_INTERVIEW_QUESTION_GENERATE) == ""
    assert settings.dify_api_key_for(TASK_TYPE_RESUME_PARSE) == "generic-dify-key"

    get_settings.cache_clear()


def test_workflow_id_for_interview_returns_dedicated_attr(monkeypatch) -> None:
    monkeypatch.setenv(
        "DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID",
        "test-interview-question-workflow-id",
    )
    monkeypatch.setenv("DIFY_JD_PARSE_WORKFLOW_ID", "test-jd-parse-workflow-id")
    get_settings.cache_clear()

    assert (
        _workflow_id_for(TASK_TYPE_INTERVIEW_QUESTION_GENERATE)
        == "test-interview-question-workflow-id"
    )
    assert _workflow_id_for(TASK_TYPE_JD_PARSE) == "test-jd-parse-workflow-id"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_default_switch_off_mocks_and_posts_zero(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="false")
    posted = _bind_post_counter(monkeypatch)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )

    assert out.ok is True
    assert out.result is not None
    assert out.result["questions"]
    assert (out.raw_request or {}).get("provider") == "mock"
    assert posted["n"] == 0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_production_ignores_switch_posts_zero(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, environment="production", enabled="true")
    posted = _bind_post_counter(monkeypatch)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )

    assert posted["n"] == 0
    assert out.ok is True
    assert out.result is not None
    assert out.result["questions"]
    assert (out.raw_request or {}).get("provider") == "mock"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_switch_on_missing_pair_is_not_configured(monkeypatch) -> None:
    posted = _bind_post_counter(monkeypatch)
    cases = (
        {"api_key": "", "workflow_id": _FAKE_WORKFLOW_ID},
        {"api_key": _FAKE_KEY, "workflow_id": ""},
        {"api_key": _FAKE_KEY, "workflow_id": _FAKE_WORKFLOW_ID, "base_url": ""},
    )
    for kwargs in cases:
        _set_question_live_env(monkeypatch, enabled="true", **kwargs)
        posted["n"] = 0
        out = await dify.run_dify(
            task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            input_snapshot=FICTIONAL_INPUT,
        )
        assert out.ok is False
        assert out.result is None
        assert out.error_code == "interview_question_live_not_configured"
        assert out.error_category == "non_retryable"
        assert posted["n"] == 0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_unauthorized_prefix_posts_zero(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="true")
    posted = _bind_post_counter(monkeypatch)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot={
            **FICTIONAL_INPUT,
            "job_title": "未授权岗位",
            "jd_text": "未授权 JD 正文",
            "resume_text": "未授权简历正文",
        },
    )

    assert out.ok is False
    assert out.result is None
    assert out.error_code == "interview_question_live_unauthorized"
    assert out.error_category == "non_retryable"
    assert posted["n"] == 0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_mixed_prefixes_unauthorized(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="true")
    posted = _bind_post_counter(monkeypatch)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot={
            **FICTIONAL_INPUT,
            "job_title": "FICTIONAL-LIVE-20260818 混用岗位",
            "jd_text": "FICTIONAL-LIVE-20260818 混用 JD。",
            "resume_text": "UAT-CC-20260818 混用简历。",
        },
    )

    assert out.ok is False
    assert out.error_code == "interview_question_live_unauthorized"
    assert out.error_category == "non_retryable"
    assert posted["n"] == 0
    get_settings.cache_clear()


def test_build_dify_inputs_question_keys() -> None:
    mapped = build_dify_inputs(TASK_TYPE_INTERVIEW_QUESTION_GENERATE, FICTIONAL_INPUT)
    assert set(mapped) == {
        "job_title",
        "jd_text",
        "resume_text",
        "dimensions_json",
    }
    assert "candidate_id" not in mapped
    assert "segments_json" not in mapped
    assert "password" not in mapped


@pytest.mark.asyncio
async def test_live_allowed_reuses_post_workflow_with_workflow_id(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="true")
    captured: list[dict] = []

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        captured.append({"url": url, "json": json})
        return httpx.Response(
            200,
            json={"data": {"id": "wf-run-test", "outputs": {"result": {}}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )

    assert len(captured) == 1
    body = captured[0]["json"]
    assert str(captured[0]["url"]).endswith("/v1/workflows/run")
    assert body["workflow_id"] == _FAKE_WORKFLOW_ID
    assert set(body["inputs"]) == {
        "job_title",
        "jd_text",
        "resume_text",
        "dimensions_json",
    }
    assert body["response_mode"] == "blocking"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_analyze_stays_mocked_when_question_live_enabled(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="true")
    posted = _bind_post_counter(monkeypatch)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        input_snapshot={
            "dimensions": [{"dimension_key": "D001"}],
            "segments": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "segment_no": 1,
                    "text": "对齐目标后推进。",
                }
            ],
        },
    )

    assert out.ok is True
    assert out.result is not None
    assert out.result["dimensions"]
    assert (out.raw_request or {}).get("provider") == "mock"
    assert posted["n"] == 0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_run_dify_analyze_still_unconditional_mock(monkeypatch) -> None:
    """ANALYZE must call run_mock once and never _post_workflow (even if live env on)."""
    from unittest.mock import AsyncMock

    from app.services.ai_providers import mock as mock_provider

    _set_question_live_env(monkeypatch, enabled="true")
    posted = _bind_post_counter(monkeypatch)
    mock_outcome = ProviderOutcome(
        ok=True,
        result={"dimensions": [], "overall_summary": "x"},
        raw_request={"provider": "mock"},
    )
    run_mock = AsyncMock(return_value=mock_outcome)
    monkeypatch.setattr(mock_provider, "run_mock", run_mock)

    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        input_snapshot={"dimensions": [{"dimension_key": "D001"}], "segments": []},
    )

    assert out is mock_outcome
    assert run_mock.await_count == 1
    assert run_mock.await_args.kwargs["task_type"] == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    assert posted["n"] == 0
    get_settings.cache_clear()


def _valid_question_item(
    *,
    display_order: int = 1,
    evidence_source: str = "JOB_REQUIREMENT",
    dimension_key: str = "D001",
    question: str = "请结合该维度描述一次具体实践。",
) -> dict:
    return {
        "dimension_key": dimension_key,
        "question": question,
        "purpose": f"考察{dimension_key}",
        "evidence_source": evidence_source,
        "resume_evidence": None,
        "follow_up_prompts": ["请补充可量化结果。"],
        "risk_flags": ["可能缺少细节"],
        "display_order": display_order,
    }


def test_normalize_question_result_object() -> None:
    item = _valid_question_item()
    normalized = normalize_dify_outputs(
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        {"questions": [item], "debug_trace": "must-not-keep", "error": False},
    )
    assert set(normalized) == {"questions"}
    assert normalized["questions"] == [item]


def test_normalize_question_error_object_raises() -> None:
    with pytest.raises(ValueError):
        normalize_dify_outputs(
            TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            {"error": True, "error_code": "output_validation_failed"},
        )


def _bind_question_live_post_result(monkeypatch, result: dict) -> None:
    _set_question_live_env(monkeypatch, enabled="true")

    async def fake_post(*, task_type, input_snapshot):
        return ProviderOutcome(
            ok=True,
            result=result,
            raw_request={
                "provider": "dify",
                "workflow_id": _FAKE_WORKFLOW_ID,
                "task_type": task_type,
                "inputs": dict(input_snapshot),
                "api_key_suffix": _FAKE_KEY[-6:],
            },
            raw_response={"data": {"outputs": {"result": result}}},
            http_status=200,
        )

    monkeypatch.setattr(dify, "_post_workflow", fake_post)


@pytest.mark.asyncio
async def test_run_dify_invalid_enum_is_output_validation_failed(monkeypatch) -> None:
    _bind_question_live_post_result(
        monkeypatch,
        {"questions": [_valid_question_item(evidence_source="OTHER")]},
    )
    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )
    assert out.ok is False
    assert out.result is None
    assert out.error_code == "output_validation_failed"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_run_dify_skipped_display_order_is_output_validation_failed(
    monkeypatch,
) -> None:
    _bind_question_live_post_result(
        monkeypatch,
        {
            "questions": [
                _valid_question_item(display_order=1, dimension_key="D001"),
                _valid_question_item(
                    display_order=3,
                    dimension_key="D002",
                    question="请描述另一项实践。",
                ),
            ]
        },
    )
    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )
    assert out.ok is False
    assert out.result is None
    assert out.error_code == "output_validation_failed"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_live_outcome_raw_request_has_no_body_or_key_suffix(monkeypatch) -> None:
    _set_question_live_env(monkeypatch, enabled="true")
    item = _valid_question_item()

    async def fake_post(self, url, headers=None, json=None, **kwargs):
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "wf-run-test",
                    "outputs": {"result": {"questions": [item]}},
                }
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        input_snapshot=FICTIONAL_INPUT,
    )
    assert out.ok is True
    raw = out.raw_request or {}
    blob = json.dumps(raw, ensure_ascii=False)
    assert FICTIONAL_INPUT["jd_text"] not in blob
    assert FICTIONAL_INPUT["resume_text"] not in blob
    assert "inputs" not in raw
    assert "api_key_suffix" not in raw
    assert _FAKE_KEY not in blob
    assert raw.get("workflow_id") == _FAKE_WORKFLOW_ID
    names = raw.get("input_field_names")
    has_names = names == [
        "dimensions_json",
        "jd_text",
        "job_title",
        "resume_text",
    ]
    has_hashes = isinstance(raw.get("job_title_sha256"), str) and isinstance(
        raw.get("jd_text_sha256"), str
    )
    assert has_names or has_hashes
    get_settings.cache_clear()
