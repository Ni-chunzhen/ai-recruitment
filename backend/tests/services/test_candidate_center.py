"""Service/schema tests for candidate center list and detail aggregates."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.ai_task import TASK_TYPE_RESUME_SCORE
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import INTERVIEW_STATUS_CANCELLED, INTERVIEW_STATUS_ENDED_ABNORMALLY
from app.models.interview_ai import QUESTION_SET_STATUS_READY
from app.models.invitation import (
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
)
from app.models.resume import PIPELINE_INTERVIEWING
from app.repositories.candidates import CandidateNotFoundError
from app.schemas.candidate_center import CandidateCenterListQuery
from app.services.candidate_center import (
    build_score_summary,
    derive_analysis_status,
    derive_invitation_status,
    derive_question_status,
    derive_transcript_status,
    get_candidate_center_application_detail,
    list_candidate_center_applications,
)

SENSITIVE_KEYS = frozenset(
    {
        "extracted_text",
        "standardized_text",
        "confirmed_content",
        "ai_structured",
        "question",
        "quote",
        "raw_output",
        "raw_request",
        "raw_response",
        "result_payload",
        "meeting_password",
        "meeting_password_encrypted",
        "overall_summary",
        "evidence",
        "gap",
        "risk",
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            found.update(_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_keys(item))
    return found


def _assert_no_sensitive(payload: object) -> None:
    keys = _keys(payload)
    assert SENSITIVE_KEYS.isdisjoint(keys)
    assert not any(key.endswith("_encrypted") for key in keys)


def _application(**overrides) -> SimpleNamespace:
    candidate_id = overrides.pop("candidate_id", uuid4())
    data = dict(
        id=uuid4(),
        candidate_id=candidate_id,
        job_id=uuid4(),
        job_version_id=uuid4(),
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_INTERVIEWING,
        resume_version_id=None,
        close_action=None,
        interview_started=True,
        lock_version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _list_row(*, application, display_round_id=None, **overrides) -> SimpleNamespace:
    data = dict(
        application=application,
        candidate_name="张三",
        candidate_phone="13800000000",
        candidate_email="zhang@example.com",
        job_name="后端工程师",
        job_code="JOB-202608-0001",
        display_round_id=display_round_id,
        display_round_name=None if display_round_id is None else "一轮",
        display_round_sequence_no=None if display_round_id is None else 1,
        display_round_status=None if display_round_id is None else "CANCELLED",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _status_row(*, round_id, **overrides) -> SimpleNamespace:
    data = dict(
        round_id=round_id,
        current_schedule_id=None,
        current_schedule_status=None,
        has_meeting_password=False,
        invitation_confirmed_at=None,
        invitation_message_statuses=(),
        transcript_completion_mode=None,
        transcript_confirmed_version_id=None,
        transcript_draft_version_id=None,
        transcript_original_version_id=None,
        question_set_status=None,
        analysis_current_version_id=None,
        analysis_transcript_version_id=None,
        analysis_overall_score=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _round(*, application_id, sequence_no, status="DRAFT", **overrides) -> SimpleNamespace:
    data = dict(
        id=uuid4(),
        application_id=application_id,
        name=f"轮次{sequence_no}",
        sequence_no=sequence_no,
        status=status,
        current_schedule_id=None,
        invitation_confirmed_at=None,
        transcript_completion_mode=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _patch_list(monkeypatch: pytest.MonkeyPatch, *, rows, total=1, status_rows=None, labels=None):
    list_rows = AsyncMock(return_value=rows)
    count_rows = AsyncMock(return_value=total)
    status = AsyncMock(return_value=status_rows or [])
    version_labels = AsyncMock(return_value=labels or {})
    monkeypatch.setattr(
        "app.services.candidate_center.list_candidate_center_application_rows",
        list_rows,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.count_candidate_center_applications",
        count_rows,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.list_round_status_rows",
        status,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.list_job_version_labels",
        version_labels,
    )
    return SimpleNamespace(
        list_rows=list_rows,
        count_rows=count_rows,
        status=status,
        version_labels=version_labels,
    )


def _patch_detail(
    monkeypatch: pytest.MonkeyPatch,
    *,
    detail_row,
    rounds,
    status_rows,
    other_applications=None,
    resume_version=None,
    ai_result=None,
):
    get_row = AsyncMock(return_value=detail_row)
    list_rounds = AsyncMock(return_value=rounds)
    status = AsyncMock(return_value=status_rows)
    others = AsyncMock(return_value=other_applications or [])
    resume = AsyncMock(return_value=resume_version)
    score = AsyncMock(return_value=ai_result)
    monkeypatch.setattr(
        "app.services.candidate_center.get_candidate_center_application_row",
        get_row,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.list_rounds_for_application",
        list_rounds,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.list_round_status_rows",
        status,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.list_other_applications_for_candidate",
        others,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.get_resume_version_by_id",
        resume,
    )
    monkeypatch.setattr(
        "app.services.candidate_center.get_current_ai_result",
        score,
    )
    return SimpleNamespace(
        get_row=get_row,
        list_rounds=list_rounds,
        status=status,
        others=others,
        resume=resume,
        score=score,
    )


@pytest.mark.asyncio
async def test_list_item_has_split_status_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    application = _application(
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_INTERVIEWING,
    )
    _patch_list(monkeypatch, rows=[_list_row(application=application)])
    result = await list_candidate_center_applications(
        AsyncMock(),
        query=CandidateCenterListQuery(),
    )
    item = result.items[0]
    dumped = item.model_dump()
    assert item.status == APPLICATION_STATUS_IN_PROGRESS
    assert item.pipeline_status == PIPELINE_INTERVIEWING
    assert "application_status" not in dumped
    assert "combined_status" not in dumped


@pytest.mark.asyncio
async def test_list_display_round_follows_assigned_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    cancelled_assigned_id = uuid4()
    patches = _patch_list(
        monkeypatch,
        rows=[
            _list_row(
                application=application,
                display_round_id=cancelled_assigned_id,
                display_round_name="已分配取消轮",
                display_round_sequence_no=1,
                display_round_status=INTERVIEW_STATUS_CANCELLED,
            )
        ],
        status_rows=[_status_row(round_id=cancelled_assigned_id)],
    )
    result = await list_candidate_center_applications(
        AsyncMock(),
        query=CandidateCenterListQuery(assigned=True),
    )
    assert patches.list_rows.await_args.kwargs["assigned"] is True
    item = result.items[0]
    assert item.round_id == cancelled_assigned_id
    assert item.round_status == INTERVIEW_STATUS_CANCELLED
    assert item.sequence_no == 1


@pytest.mark.asyncio
async def test_list_display_round_follows_assigned_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    max_any_round_id = uuid4()
    patches = _patch_list(
        monkeypatch,
        rows=[
            _list_row(
                application=application,
                display_round_id=max_any_round_id,
                display_round_name="无面试官最高轮",
                display_round_sequence_no=2,
                display_round_status="DRAFT",
            )
        ],
        status_rows=[_status_row(round_id=max_any_round_id)],
    )
    result = await list_candidate_center_applications(
        AsyncMock(),
        query=CandidateCenterListQuery(assigned=False),
    )
    assert patches.list_rows.await_args.kwargs["assigned"] is False
    assert result.items[0].round_id == max_any_round_id
    assert result.items[0].sequence_no == 2


@pytest.mark.asyncio
async def test_list_empty_display_round_statuses_are_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    _patch_list(monkeypatch, rows=[_list_row(application=application)])
    result = await list_candidate_center_applications(
        AsyncMock(),
        query=CandidateCenterListQuery(),
    )
    item = result.items[0]
    assert item.round_id is None
    assert item.schedule_status == "none"
    assert item.invitation_status == "none"
    assert item.transcript_status == "none"
    assert item.question_status == "none"
    assert item.analysis_status == "none"


def test_invitation_status_priority() -> None:
    assert (
        derive_invitation_status(
            invitation_confirmed_at=_now(),
            message_statuses=(INVITATION_STATUS_VOIDED,),
        )
        == "confirmed"
    )
    assert (
        derive_invitation_status(
            invitation_confirmed_at=None,
            message_statuses=(INVITATION_STATUS_VOIDED, INVITATION_STATUS_RECORDED_SENT),
        )
        == "recorded_sent"
    )
    assert (
        derive_invitation_status(
            invitation_confirmed_at=None,
            message_statuses=(INVITATION_STATUS_DRAFT, INVITATION_STATUS_READY),
        )
        == "ready"
    )
    assert (
        derive_invitation_status(
            invitation_confirmed_at=None,
            message_statuses=(INVITATION_STATUS_DRAFT,),
        )
        == "draft"
    )
    assert (
        derive_invitation_status(
            invitation_confirmed_at=None,
            message_statuses=(INVITATION_STATUS_VOIDED, INVITATION_STATUS_VOIDED),
        )
        == "voided"
    )
    assert (
        derive_invitation_status(invitation_confirmed_at=None, message_statuses=())
        == "none"
    )


def test_transcript_status_priority() -> None:
    confirmed = uuid4()
    assert (
        derive_transcript_status(
            completion_mode="WITHOUT_TRANSCRIPT",
            confirmed_version_id=confirmed,
            draft_version_id=uuid4(),
            original_version_id=uuid4(),
        )
        == "without_transcript"
    )
    assert (
        derive_transcript_status(
            completion_mode=None,
            confirmed_version_id=confirmed,
            draft_version_id=uuid4(),
            original_version_id=uuid4(),
        )
        == "confirmed"
    )
    assert (
        derive_transcript_status(
            completion_mode=None,
            confirmed_version_id=None,
            draft_version_id=uuid4(),
            original_version_id=uuid4(),
        )
        == "draft"
    )
    assert (
        derive_transcript_status(
            completion_mode=None,
            confirmed_version_id=None,
            draft_version_id=None,
            original_version_id=uuid4(),
        )
        == "original"
    )
    assert (
        derive_transcript_status(
            completion_mode=None,
            confirmed_version_id=None,
            draft_version_id=None,
            original_version_id=None,
        )
        == "none"
    )


def test_question_status_is_set_status_or_none() -> None:
    assert derive_question_status(None) == "none"
    assert derive_question_status("DRAFT") == "DRAFT"
    assert derive_question_status("READY") == "READY"
    assert derive_question_status("ARCHIVED") == "ARCHIVED"


def test_analysis_status_ready_and_stale() -> None:
    current = uuid4()
    confirmed = uuid4()
    assert (
        derive_analysis_status(
            current_version_id=None,
            analysis_transcript_version_id=None,
            confirmed_transcript_version_id=confirmed,
        )
        == "none"
    )
    assert (
        derive_analysis_status(
            current_version_id=current,
            analysis_transcript_version_id=uuid4(),
            confirmed_transcript_version_id=None,
        )
        == "stale"
    )
    assert (
        derive_analysis_status(
            current_version_id=current,
            analysis_transcript_version_id=uuid4(),
            confirmed_transcript_version_id=confirmed,
        )
        == "stale"
    )
    assert (
        derive_analysis_status(
            current_version_id=current,
            analysis_transcript_version_id=confirmed,
            confirmed_transcript_version_id=confirmed,
        )
        == "ready"
    )
    dumped = build_score_summary(
        SimpleNamespace(
            id=uuid4(),
            version_label="M1",
            calculated_total_score=88.0,
            is_stale=False,
            is_current=True,
            normalized_result={
                "total_score": 88,
                "score_band": "A",
                "recommendation": "建议面试",
                "summary": "匹配",
                "information_insufficient": False,
                "overall_summary": "should not leak",
                "dimensions": [{"name": "专业", "weight": 40, "score": 90}],
            },
        )
    ).model_dump()
    assert "overall_summary" not in dumped
    assert "overall_summary" not in _keys(dumped)


@pytest.mark.asyncio
async def test_list_payload_strips_sensitive_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    application = _application()
    round_id = uuid4()
    _patch_list(
        monkeypatch,
        rows=[_list_row(application=application, display_round_id=round_id)],
        status_rows=[
            _status_row(
                round_id=round_id,
                analysis_current_version_id=uuid4(),
                analysis_transcript_version_id=uuid4(),
                confirmed_transcript_version_id=uuid4(),
                analysis_overall_score=Decimal("4.5"),
            )
        ],
    )
    result = await list_candidate_center_applications(
        AsyncMock(),
        query=CandidateCenterListQuery(),
    )
    payload = result.model_dump()
    _assert_no_sensitive(payload)
    assert "overall_summary" not in _keys(payload)


@pytest.mark.asyncio
async def test_score_summary_omits_evidence_and_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application(resume_version_id=uuid4())
    detail_row = SimpleNamespace(
        application=application,
        candidate_name="张三",
        candidate_phone="13800000000",
        candidate_email=None,
        job_name="后端工程师",
        job_code="JOB-1",
        job_version_label="V1.0",
    )
    result_id = uuid4()
    patches = _patch_detail(
        monkeypatch,
        detail_row=detail_row,
        rounds=[],
        status_rows=[],
        ai_result=SimpleNamespace(
            id=result_id,
            application_id=application.id,
            version_label="M2",
            raw_output={"secret": True},
            calculated_total_score=81.5,
            is_stale=False,
            is_current=True,
            normalized_result={
                "total_score": 80,
                "score_band": "B",
                "recommendation": "考虑面试",
                "summary": "整体匹配",
                "information_insufficient": False,
                "dimensions": [
                    {
                        "name": "专业能力",
                        "weight": 40,
                        "score": 80,
                        "evidence": "项目经历",
                        "gap": "深度不足",
                        "risk": "稳定性",
                    }
                ],
            },
        ),
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert patches.score.await_args.kwargs["application_id"] == application.id
    assert patches.score.await_args.kwargs["result_type"] == TASK_TYPE_RESUME_SCORE
    summary = detail.score_summary
    assert summary is not None
    dumped = summary.model_dump()
    assert dumped["total_score"] == 81.5
    assert dumped["score_band"] == "B"
    assert dumped["summary"] == "整体匹配"
    assert dumped["dimensions"] == [
        {"name": "专业能力", "weight": 40.0, "score": 80.0}
    ]
    assert "raw_output" not in dumped
    assert "evidence" not in _keys(dumped)
    assert "gap" not in _keys(dumped)
    assert "risk" not in _keys(dumped)
    _assert_no_sensitive(detail.model_dump())


@pytest.mark.asyncio
async def test_detail_mismatch_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    application = _application(candidate_id=uuid4())
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=[],
        status_rows=[],
    )
    with pytest.raises(CandidateNotFoundError):
        await get_candidate_center_application_detail(
            AsyncMock(),
            candidate_id=uuid4(),
            application_id=application.id,
        )


@pytest.mark.asyncio
async def test_other_applications_are_summaries_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    other_id = uuid4()
    other_round_id = uuid4()
    other_result_id = uuid4()
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=[],
        status_rows=[],
        other_applications=[
            SimpleNamespace(
                application_id=other_id,
                job_id=uuid4(),
                job_name="前端",
                job_code="JOB-2",
                status="rejected",
                pipeline_status="rejected",
                created_at=_now(),
                round_id=other_round_id,
                result_id=other_result_id,
                resume_version_id=uuid4(),
            )
        ],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert len(detail.other_applications) == 1
    other = detail.other_applications[0].model_dump()
    assert set(other) == {
        "application_id",
        "job_id",
        "job_name",
        "job_code",
        "status",
        "pipeline_status",
        "created_at",
    }
    assert other_round_id not in {item.round_id for item in detail.rounds}
    assert detail.score_summary is None or detail.score_summary.result_id != other_result_id


@pytest.mark.asyncio
async def test_detail_rounds_stay_on_current_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    rounds = [
        _round(application_id=application.id, sequence_no=1),
        _round(application_id=application.id, sequence_no=2),
    ]
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=rounds,
        status_rows=[_status_row(round_id=item.id) for item in rounds],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert {item.application_id for item in detail.rounds} == {application.id}


@pytest.mark.asyncio
async def test_detail_returns_all_rounds_ordered_by_sequence_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    rounds = [
        _round(application_id=application.id, sequence_no=1, name="一"),
        _round(application_id=application.id, sequence_no=2, name="二"),
        _round(application_id=application.id, sequence_no=3, name="三"),
    ]
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=rounds,
        status_rows=[_status_row(round_id=item.id) for item in rounds],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert len(detail.rounds) == 3
    assert [item.sequence_no for item in detail.rounds] == [1, 2, 3]


@pytest.mark.asyncio
async def test_detail_keeps_cancelled_and_ended_abnormally_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    rounds = [
        _round(
            application_id=application.id,
            sequence_no=1,
            status=INTERVIEW_STATUS_CANCELLED,
        ),
        _round(
            application_id=application.id,
            sequence_no=2,
            status=INTERVIEW_STATUS_ENDED_ABNORMALLY,
        ),
    ]
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=rounds,
        status_rows=[_status_row(round_id=item.id) for item in rounds],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert [item.status for item in detail.rounds] == [
        INTERVIEW_STATUS_CANCELLED,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
    ]


@pytest.mark.asyncio
async def test_detail_per_round_statuses_from_that_round_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    round_one = _round(application_id=application.id, sequence_no=1)
    round_two = _round(application_id=application.id, sequence_no=2)
    confirmed_version = uuid4()
    _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=[round_one, round_two],
        status_rows=[
            _status_row(
                round_id=round_one.id,
                invitation_confirmed_at=_now(),
                invitation_message_statuses=(INVITATION_STATUS_VOIDED,),
                transcript_confirmed_version_id=confirmed_version,
                question_set_status=QUESTION_SET_STATUS_READY,
                analysis_current_version_id=uuid4(),
                analysis_transcript_version_id=confirmed_version,
                analysis_overall_score=Decimal("4.0"),
            ),
            _status_row(round_id=round_two.id),
        ],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    first, second = detail.rounds
    assert first.round_id == round_one.id
    assert first.invitation_status == "confirmed"
    assert first.transcript_status == "confirmed"
    assert first.question_status == "READY"
    assert first.analysis_status == "ready"
    assert second.round_id == round_two.id
    assert second.invitation_status == "none"
    assert second.transcript_status == "none"
    assert second.question_status == "none"
    assert second.analysis_status == "none"


@pytest.mark.asyncio
async def test_detail_excludes_other_application_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    current_round = _round(application_id=application.id, sequence_no=1)
    other_round_id = uuid4()
    patches = _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=[current_round],
        status_rows=[
            _status_row(round_id=current_round.id),
            _status_row(round_id=other_round_id, invitation_confirmed_at=_now()),
        ],
    )
    detail = await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert patches.list_rounds.await_args.args[1] == application.id
    assert {item.round_id for item in detail.rounds} == {current_round.id}
    assert other_round_id not in {item.round_id for item in detail.rounds}


@pytest.mark.asyncio
async def test_detail_loads_rounds_without_n_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    rounds = [
        _round(application_id=application.id, sequence_no=1),
        _round(application_id=application.id, sequence_no=2),
    ]
    patches = _patch_detail(
        monkeypatch,
        detail_row=SimpleNamespace(
            application=application,
            candidate_name="张三",
            candidate_phone=None,
            candidate_email=None,
            job_name="后端",
            job_code="JOB-1",
            job_version_label="V1.0",
        ),
        rounds=rounds,
        status_rows=[_status_row(round_id=item.id) for item in rounds],
    )
    await get_candidate_center_application_detail(
        AsyncMock(),
        candidate_id=application.candidate_id,
        application_id=application.id,
    )
    assert patches.list_rounds.await_count == 1
    assert patches.status.await_count == 1
    passed_ids = patches.status.await_args.kwargs["round_ids"]
    assert set(passed_ids) == {item.id for item in rounds}
    source = inspect.getsource(get_candidate_center_application_detail)
    assert "get_question_set_by_round" not in source
    assert "get_analysis_by_round" not in source
    assert "list_messages_for_round" not in source
    assert "get_transcript_by_round_id" not in source


def test_list_query_rejects_unknown_sort() -> None:
    with pytest.raises(ValidationError):
        CandidateCenterListQuery.model_validate({"sort": "score_desc"})
    with pytest.raises(ValidationError):
        CandidateCenterListQuery.model_validate({"assigned": True, "foo": 1})
    query = CandidateCenterListQuery()
    assert query.assigned is True
    assert query.page == 1
    assert query.page_size == 20
    assert query.sort == "updated_at_desc"


def test_does_not_use_interview_task_state_as_filter() -> None:
    from app.services import candidate_center as service_mod

    source = inspect.getsource(service_mod)
    assert "interview_task_state" not in source
