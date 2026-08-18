from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_task import TASK_TYPE_RESUME_SCORE
from app.models.invitation import (
    INVITATION_STATUS_DRAFT,
    INVITATION_STATUS_READY,
    INVITATION_STATUS_RECORDED_SENT,
    INVITATION_STATUS_VOIDED,
)
from app.repositories.candidate_center import (
    count_candidate_center_applications,
    get_candidate_center_application_row,
    list_candidate_center_application_rows,
    list_job_version_labels,
    list_other_applications_for_candidate,
    list_round_status_rows,
)
from app.repositories.candidates import CandidateNotFoundError
from app.repositories.interviews import list_rounds_for_application
from app.repositories.resumes import get_current_ai_result, get_resume_version_by_id
from app.schemas.candidate_center import (
    CandidateCenterDetailOut,
    CandidateCenterListItem,
    CandidateCenterListQuery,
    CandidateCenterListResponse,
    CandidateCenterRoundOut,
    OtherApplicationSummary,
    ResumeSummaryOut,
    ScoreDimensionSummary,
    ScoreSummaryOut,
)

_WITHOUT_TRANSCRIPT = "WITHOUT_TRANSCRIPT"


def derive_invitation_status(
    *,
    invitation_confirmed_at: datetime | None,
    message_statuses: Sequence[str],
) -> str:
    if invitation_confirmed_at is not None:
        return "confirmed"
    statuses = set(message_statuses)
    if INVITATION_STATUS_RECORDED_SENT in statuses:
        return "recorded_sent"
    if INVITATION_STATUS_READY in statuses:
        return "ready"
    if INVITATION_STATUS_DRAFT in statuses:
        return "draft"
    if statuses and statuses <= {INVITATION_STATUS_VOIDED}:
        return "voided"
    return "none"


def derive_transcript_status(
    *,
    completion_mode: str | None,
    confirmed_version_id: UUID | None,
    draft_version_id: UUID | None,
    original_version_id: UUID | None,
) -> str:
    if completion_mode == _WITHOUT_TRANSCRIPT:
        return "without_transcript"
    if confirmed_version_id is not None:
        return "confirmed"
    if draft_version_id is not None:
        return "draft"
    if original_version_id is not None:
        return "original"
    return "none"


def derive_question_status(question_set_status: str | None) -> str:
    if not question_set_status:
        return "none"
    return question_set_status


def derive_analysis_status(
    *,
    current_version_id: UUID | None,
    analysis_transcript_version_id: UUID | None,
    confirmed_transcript_version_id: UUID | None,
) -> str:
    if current_version_id is None:
        return "none"
    if confirmed_transcript_version_id is None:
        return "stale"
    if analysis_transcript_version_id != confirmed_transcript_version_id:
        return "stale"
    return "ready"


def build_score_summary(result: Any) -> ScoreSummaryOut:
    payload = result.normalized_result if isinstance(result.normalized_result, dict) else {}
    calculated = result.calculated_total_score
    if calculated is None:
        calculated = payload.get("total_score") or 0
    dimensions: list[ScoreDimensionSummary] = []
    for item in payload.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        dimensions.append(
            ScoreDimensionSummary(
                name=str(item.get("name") or ""),
                weight=float(item.get("weight") or 0),
                score=float(item.get("score") or 0),
            )
        )
    return ScoreSummaryOut(
        result_id=result.id,
        version_label=result.version_label,
        total_score=float(calculated),
        calculated_total_score=float(calculated),
        score_band=str(payload.get("score_band") or ""),
        recommendation=str(payload.get("recommendation") or ""),
        summary=str(payload.get("summary") or ""),
        information_insufficient=bool(payload.get("information_insufficient")),
        is_stale=bool(result.is_stale),
        is_current=bool(result.is_current),
        dimensions=dimensions,
    )


def _schedule_status(status_row: Any | None) -> str:
    if status_row is None or status_row.current_schedule_id is None:
        return "none"
    return status_row.current_schedule_status or "none"


def _derived_round_fields(status_row: Any | None) -> SimpleNamespace:
    if status_row is None:
        return SimpleNamespace(
            schedule_status="none",
            invitation_status="none",
            transcript_status="none",
            question_status="none",
            analysis_status="none",
            analysis_overall_score=None,
            has_meeting_password=False,
        )
    analysis_status = derive_analysis_status(
        current_version_id=status_row.analysis_current_version_id,
        analysis_transcript_version_id=status_row.analysis_transcript_version_id,
        confirmed_transcript_version_id=status_row.transcript_confirmed_version_id,
    )
    overall: Decimal | None = None
    if analysis_status != "none":
        overall = status_row.analysis_overall_score
    return SimpleNamespace(
        schedule_status=_schedule_status(status_row),
        invitation_status=derive_invitation_status(
            invitation_confirmed_at=status_row.invitation_confirmed_at,
            message_statuses=status_row.invitation_message_statuses,
        ),
        transcript_status=derive_transcript_status(
            completion_mode=status_row.transcript_completion_mode,
            confirmed_version_id=status_row.transcript_confirmed_version_id,
            draft_version_id=status_row.transcript_draft_version_id,
            original_version_id=status_row.transcript_original_version_id,
        ),
        question_status=derive_question_status(status_row.question_set_status),
        analysis_status=analysis_status,
        analysis_overall_score=overall,
        has_meeting_password=bool(status_row.has_meeting_password),
    )


async def list_candidate_center_applications(
    session: AsyncSession, *, query: CandidateCenterListQuery
) -> CandidateCenterListResponse:
    rows = await list_candidate_center_application_rows(
        session,
        assigned=query.assigned,
        status=query.status,
        pipeline_status=query.pipeline_status,
        job_id=query.job_id,
        keyword=query.keyword,
        sort=query.sort,
        page=query.page,
        page_size=query.page_size,
    )
    total = await count_candidate_center_applications(
        session,
        assigned=query.assigned,
        status=query.status,
        pipeline_status=query.pipeline_status,
        job_id=query.job_id,
        keyword=query.keyword,
    )
    round_ids = [
        row.display_round_id for row in rows if row.display_round_id is not None
    ]
    status_rows = await list_round_status_rows(session, round_ids=round_ids)
    status_by_id = {row.round_id: row for row in status_rows}
    labels = await list_job_version_labels(
        session,
        version_ids=list({row.application.job_version_id for row in rows}),
    )
    items: list[CandidateCenterListItem] = []
    for row in rows:
        application = row.application
        derived = _derived_round_fields(
            status_by_id.get(row.display_round_id)
            if row.display_round_id is not None
            else None
        )
        items.append(
            CandidateCenterListItem(
                application_id=application.id,
                candidate_id=application.candidate_id,
                name=row.candidate_name,
                phone=row.candidate_phone,
                email=row.candidate_email,
                job_id=application.job_id,
                job_name=row.job_name,
                job_code=row.job_code,
                job_version_id=application.job_version_id,
                job_version_label=labels.get(application.job_version_id),
                status=application.status,
                pipeline_status=application.pipeline_status,
                round_id=row.display_round_id,
                round_name=row.display_round_name,
                sequence_no=row.display_round_sequence_no,
                round_status=row.display_round_status,
                schedule_status=derived.schedule_status,
                invitation_status=derived.invitation_status,
                transcript_status=derived.transcript_status,
                question_status=derived.question_status,
                analysis_status=derived.analysis_status,
                analysis_overall_score=derived.analysis_overall_score,
            )
        )
    return CandidateCenterListResponse(
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def get_candidate_center_application_detail(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    application_id: UUID,
) -> CandidateCenterDetailOut:
    row = await get_candidate_center_application_row(session, application_id)
    if row is None or row.application.candidate_id != candidate_id:
        raise CandidateNotFoundError("not found")
    application = row.application

    resume_summary = None
    if application.resume_version_id is not None:
        version = await get_resume_version_by_id(session, application.resume_version_id)
        if version is not None:
            resume_summary = ResumeSummaryOut(
                resume_id=version.resume_id,
                resume_version_id=version.id,
                version_label=version.version_label,
                kind=version.kind,
                status=version.status,
                original_filename=version.original_filename,
                confirmed_at=version.confirmed_at,
            )

    ai_result = await get_current_ai_result(
        session, application_id=application_id, result_type=TASK_TYPE_RESUME_SCORE
    )
    score_summary = build_score_summary(ai_result) if ai_result is not None else None

    rounds = await list_rounds_for_application(session, application_id)
    round_ids = [item.id for item in rounds]
    status_rows = await list_round_status_rows(session, round_ids=round_ids)
    status_by_id = {item.round_id: item for item in status_rows}
    round_outs: list[CandidateCenterRoundOut] = []
    for item in rounds:
        if item.application_id != application_id:
            continue
        derived = _derived_round_fields(status_by_id.get(item.id))
        round_outs.append(
            CandidateCenterRoundOut(
                application_id=application_id,
                round_id=item.id,
                name=item.name,
                sequence_no=item.sequence_no,
                status=item.status,
                schedule_status=derived.schedule_status,
                has_meeting_password=derived.has_meeting_password,
                invitation_status=derived.invitation_status,
                transcript_status=derived.transcript_status,
                question_status=derived.question_status,
                analysis_status=derived.analysis_status,
                analysis_overall_score=derived.analysis_overall_score,
            )
        )
    round_outs.sort(key=lambda item: item.sequence_no)

    others = await list_other_applications_for_candidate(
        session,
        candidate_id=candidate_id,
        exclude_application_id=application_id,
    )
    other_applications = [
        OtherApplicationSummary(
            application_id=item.application_id,
            job_id=item.job_id,
            job_name=item.job_name,
            job_code=item.job_code,
            status=item.status,
            pipeline_status=item.pipeline_status,
            created_at=item.created_at,
        )
        for item in others
    ]
    return CandidateCenterDetailOut(
        application_id=application.id,
        candidate_id=application.candidate_id,
        name=row.candidate_name,
        phone=row.candidate_phone,
        email=row.candidate_email,
        job_id=application.job_id,
        job_name=row.job_name,
        job_code=row.job_code,
        job_version_id=application.job_version_id,
        job_version_label=row.job_version_label,
        status=application.status,
        pipeline_status=application.pipeline_status,
        close_action=application.close_action,
        interview_started=application.interview_started,
        resume_summary=resume_summary,
        score_summary=score_summary,
        rounds=round_outs,
        other_applications=other_applications,
    )
