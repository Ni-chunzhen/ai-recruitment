"""Provider contracts for INTERVIEW_COMPREHENSIVE_ANALYZE (Task 3)."""

from __future__ import annotations

import inspect

import pytest

from app.models.ai_task import TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE


def test_config_has_no_comprehensive_dify_live_settings() -> None:
    from app.core.config import Settings

    field_names = set(Settings.model_fields.keys())
    lowered = {name.lower() for name in field_names}
    for needle in (
        "dify_interview_comprehensive",
        "comprehensive_live",
        "interview_comprehensive_live",
    ):
        assert not any(needle in name for name in lowered), field_names
    source = inspect.getsource(Settings)
    assert "DIFY_INTERVIEW_COMPREHENSIVE" not in source
    assert "COMPREHENSIVE_LIVE" not in source


def test_mock_comprehensive_output_validates() -> None:
    from app.services.ai_providers.base import validate_ai_result
    from app.services.ai_providers.mock import mock_interview_comprehensive_analyze

    snap = {
        "application_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "round_refs": [
            {
                "round_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "sequence_no": 1,
                "analysis_version_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "analysis_version_no": 1,
                "overall_score": 4.0,
                "dimensions": [
                    {
                        "dimension_key": "collab",
                        "dimension_name": "协作",
                        "weight": 100.0,
                        "score": 4,
                        "insufficient_information": False,
                    }
                ],
                "evidence_refs": [],
            }
        ],
        "coverage_report": {
            "eligible_round_count": 1,
            "total_round_count": 1,
            "included_rounds": [],
            "gaps": [],
            "coverage_insufficient": False,
            "single_round_only": True,
            "missing_round_count": 0,
        },
    }
    raw = mock_interview_comprehensive_analyze(snap)
    validated = validate_ai_result(TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE, raw)
    assert "overall_summary" in validated
    assert "overall_score" in validated
    assert "dimension_notes" in validated
    blob = str(validated).lower()
    for banned in ("录用", "淘汰", "offer", "hire", "reject"):
        assert banned not in blob


@pytest.mark.asyncio
async def test_run_dify_comprehensive_never_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.ai_providers import dify
    from app.services.ai_providers.base import ProviderOutcome

    http_calls: list = []

    async def boom(*args, **kwargs):
        http_calls.append((args, kwargs))
        raise AssertionError("HTTP must not be called for comprehensive")

    monkeypatch.setattr(dify, "_post_workflow", boom)

    mock_calls: list = []

    async def fake_mock(*, task_type, input_snapshot, sleep_seconds=0.0):
        mock_calls.append(task_type)
        return ProviderOutcome(
            ok=True,
            result={"overall_summary": "辅助综合", "overall_score": "4.0", "dimension_notes": []},
            raw_request={"provider": "mock", "task_type": task_type},
            raw_response={"outputs": {}},
        )

    monkeypatch.setattr(
        "app.services.ai_providers.mock.run_mock", fake_mock
    )
    # Also patch local import path used inside run_dify
    import app.services.ai_providers.mock as mock_mod

    monkeypatch.setattr(mock_mod, "run_mock", fake_mock)

    outcome = await dify.run_dify(
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        input_snapshot={"round_refs": [], "coverage_report": {}},
    )
    assert outcome.ok is True
    assert http_calls == []
    assert mock_calls == [TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE]


def test_dispatch_uses_enqueue_sensitive_interview_ai_task() -> None:
    from app.services import comprehensive_analyses as svc

    source = inspect.getsource(svc.dispatch_persisted_comprehensive_analysis_task)
    assert "enqueue_sensitive_interview_ai_task" in source
    assert "enqueue_ai_task(" not in source
    assert "process_ai_task" not in source


def test_comprehensive_result_model_forbids_decision_fields() -> None:
    from app.schemas.interview_ai import InterviewComprehensiveAnalyzeResult

    fields = set(InterviewComprehensiveAnalyzeResult.model_fields.keys())
    for banned in ("decision", "hire", "offer", "reject", "pipeline_status"):
        assert banned not in fields
