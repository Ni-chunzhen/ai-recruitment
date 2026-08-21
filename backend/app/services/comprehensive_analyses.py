"""Application-level comprehensive interview analysis service (Task 2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    BUSINESS_TYPE_APPLICATION,
    TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
    AITask,
)
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.comprehensive_analysis import (
    COMPREHENSIVE_GAP_ANALYSIS_NONE,
    COMPREHENSIVE_GAP_ANALYSIS_STALE,
    COMPREHENSIVE_GAP_CANCELLED,
    COMPREHENSIVE_GAP_ENDED_ABNORMALLY,
    COMPREHENSIVE_GAP_NOT_COMPLETED,
    COMPREHENSIVE_GAP_TRANSCRIPT_UNCONFIRMED,
    COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT,
    COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION,
    COMPREHENSIVE_WORKFLOW_KEY,
    COMPREHENSIVE_WORKFLOW_VERSION,
    ApplicationComprehensiveAnalysis,
    ApplicationComprehensiveAnalysisVersion,
)
from app.models.interview import (
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_ENDED_ABNORMALLY,
    InterviewIdempotencyKey,
    InterviewRound,
)
from app.models.interview_ai import InterviewRoundAnalysis, InterviewRoundAnalysisVersion
from app.models.interview_transcript import TranscriptCompletionMode, InterviewTranscript
from app.models.resume import PIPELINE_INTERVIEWING
from app.repositories.ai_tasks import (
    add_ai_task,
    find_inflight_task,
    find_task_by_input_snapshot_hash,
    get_ai_task_by_id,
)
from app.repositories.comprehensive_analyses import (
    create_comprehensive_analysis,
    create_comprehensive_version,
    get_comprehensive_analysis_by_application,
    get_comprehensive_analysis_for_update,
    get_comprehensive_version_by_id,
    get_comprehensive_version_by_task_id,
    list_comprehensive_version_rows,
    next_comprehensive_version_no,
)
from app.repositories.hiring_decisions import list_hiring_by_application
from app.repositories.interview_analyses import (
    get_analysis_by_round,
    get_analysis_version_by_pk,
)
from app.repositories.interview_transcripts import get_transcript_by_round_id
from app.repositories.interviews import (
    add_idempotency,
    find_idempotency,
    list_rounds_for_application,
)
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id, get_application_by_id_for_update
from app.schemas.comprehensive_analysis import (
    ComprehensiveSetSummary,
    ComprehensiveVersionDetail,
    ComprehensiveVersionSummary,
    CoverageGap,
    CoverageReport,
)
from app.services.ai_tasks import enqueue_sensitive_interview_ai_task
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, decrypt_secret, encrypt_secret
from app.services.interview_analyses import is_analysis_version_stale
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewValidationError,
)

IDEMPOTENCY_ACTION_GENERATE = "comprehensive_analysis.generate"
_INFLIGHT_STATUSES = {AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING}

FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    {
        "text",
        "quote",
        "overall_summary",
        "analysis",
        "strengths",
        "risks",
        "suggested_follow_ups",
        "jd_text",
        "jd_content",
        "resume_text",
        "segment_text",
        "transcript_text",
    }
)

ROUND_REF_KEYS = frozenset(
    {
        "round_id",
        "sequence_no",
        "analysis_version_id",
        "analysis_version_no",
        "overall_score",
        "dimensions",
        "evidence_refs",
    }
)
DIMENSION_REF_KEYS = frozenset(
    {
        "dimension_key",
        "dimension_name",
        "weight",
        "score",
        "insufficient_information",
    }
)
EVIDENCE_REF_KEYS = frozenset(
    {"dimension_key", "segment_no", "transcript_segment_id"}
)


def _now() -> datetime:
    return datetime.now(UTC)


async def _has_permission(actor: User, code: str) -> bool:
    codes = getattr(actor, "permission_codes", None)
    if codes is not None:
        return code in codes
    return await user_has_permission(actor, code)


async def _assert_manage(actor: User) -> None:
    if not await _has_permission(actor, "recruitment.manage"):
        raise InterviewForbiddenError("forbidden")


def _canonical_hash(payload: dict[str, Any]) -> str:
    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): _strip(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_strip(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        return value

    canonical = json.dumps(
        _strip(payload),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_no_forbidden_snapshot_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_str = str(key)
            lower = key_str.lower()
            if lower in FORBIDDEN_SNAPSHOT_KEYS or lower.endswith("_encrypted"):
                raise InterviewValidationError(
                    f"forbidden sensitive snapshot key: {key_str}"
                )
            assert_no_forbidden_snapshot_keys(item, path=f"{path}.{key_str}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_forbidden_snapshot_keys(item, path=f"{path}[{index}]")


def build_round_ref(round_: Any, version: Any) -> dict[str, Any]:
    dimensions: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    for dim in version.dimensions or []:
        dimensions.append(
            {
                "dimension_key": dim.dimension_key,
                "dimension_name": dim.dimension_name,
                "weight": float(dim.weight) if dim.weight is not None else 0.0,
                "score": dim.score,
                "insufficient_information": dim.score is None,
            }
        )
        for evidence in dim.evidence or []:
            evidence_refs.append(
                {
                    "dimension_key": dim.dimension_key,
                    "segment_no": evidence.segment_no,
                    "transcript_segment_id": str(evidence.transcript_segment_id),
                }
            )
    overall = version.overall_score
    if overall is not None:
        overall = float(overall)
    ref = {
        "round_id": str(round_.id),
        "sequence_no": int(round_.sequence_no),
        "analysis_version_id": str(version.id),
        "analysis_version_no": int(version.version_no),
        "overall_score": overall,
        "dimensions": dimensions,
        "evidence_refs": evidence_refs,
    }
    assert_no_forbidden_snapshot_keys(ref)
    extra = set(ref.keys()) - ROUND_REF_KEYS
    if extra:
        raise InterviewValidationError(f"unexpected round_ref keys: {sorted(extra)}")
    return ref


def _gap_for_round(
    round_: InterviewRound | Any,
    *,
    analysis: InterviewRoundAnalysis | Any | None,
    version: InterviewRoundAnalysisVersion | Any | None,
    transcript: InterviewTranscript | Any | None,
) -> CoverageGap | None:
    status = getattr(round_, "status", None)
    if status == INTERVIEW_STATUS_CANCELLED:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_CANCELLED,
            status=status,
        )
    if status == INTERVIEW_STATUS_ENDED_ABNORMALLY:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_ENDED_ABNORMALLY,
            status=status,
        )
    if status != INTERVIEW_STATUS_COMPLETED:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_NOT_COMPLETED,
            status=status,
        )

    mode = getattr(round_, "transcript_completion_mode", None)
    if mode == TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_WITHOUT_TRANSCRIPT,
            status=status,
        )
    if mode != TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_TRANSCRIPT_UNCONFIRMED,
            status=status,
        )
    if transcript is None or getattr(transcript, "current_confirmed_version_id", None) is None:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_TRANSCRIPT_UNCONFIRMED,
            status=status,
        )
    if analysis is None or getattr(analysis, "current_version_id", None) is None:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_ANALYSIS_NONE,
            status=status,
        )
    if version is None:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_ANALYSIS_NONE,
            status=status,
        )
    if getattr(analysis, "current_version_id", None) != version.id:
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_ANALYSIS_STALE,
            status=status,
        )
    if is_analysis_version_stale(version, transcript):
        return CoverageGap(
            round_id=round_.id,
            sequence_no=getattr(round_, "sequence_no", None),
            reason_code=COMPREHENSIVE_GAP_ANALYSIS_STALE,
            status=status,
        )
    return None


def build_coverage_report(
    *,
    rounds: list[Any],
    analyses_by_round: dict[UUID, Any | None],
    versions_by_id: dict[UUID, Any],
    transcripts_by_round: dict[UUID, Any | None],
) -> CoverageReport:
    gaps: list[CoverageGap] = []
    included: list[dict[str, Any]] = []
    for round_ in rounds:
        analysis = analyses_by_round.get(round_.id)
        current_id = (
            getattr(analysis, "current_version_id", None) if analysis is not None else None
        )
        version = versions_by_id.get(current_id) if current_id is not None else None
        transcript = transcripts_by_round.get(round_.id)
        gap = _gap_for_round(
            round_, analysis=analysis, version=version, transcript=transcript
        )
        if gap is not None:
            gaps.append(gap)
            continue
        assert version is not None
        overall = version.overall_score
        if overall is not None:
            overall = float(overall)
        included.append(
            {
                "round_id": str(round_.id),
                "sequence_no": int(round_.sequence_no),
                "analysis_version_id": str(version.id),
                "overall_score": overall,
            }
        )

    eligible = len(included)
    total = len(rounds)
    single_round_only = eligible == 1
    coverage_insufficient = (
        eligible < total or bool(gaps) or (eligible == 1 and total >= 2)
    )
    return CoverageReport(
        eligible_round_count=eligible,
        total_round_count=total,
        included_rounds=included,
        gaps=gaps,
        coverage_insufficient=coverage_insufficient,
        single_round_only=single_round_only,
        missing_round_count=max(total - eligible, 0),
    )


def is_comprehensive_version_stale(
    version: Any,
    *,
    rounds_by_id: dict[UUID, Any],
    analyses_by_round: dict[UUID, Any | None],
    transcripts_by_round: dict[UUID, Any | None],
    versions_by_id: dict[UUID, Any],
) -> bool:
    refs = getattr(version, "round_refs", None) or []
    if not refs:
        return True
    for ref in refs:
        if not isinstance(ref, dict):
            return True
        try:
            round_id = UUID(str(ref["round_id"]))
            analysis_version_id = UUID(str(ref["analysis_version_id"]))
        except (KeyError, ValueError, TypeError):
            return True
        round_ = rounds_by_id.get(round_id)
        if round_ is None:
            return True
        analysis = analyses_by_round.get(round_id)
        if analysis is None or analysis.current_version_id != analysis_version_id:
            return True
        single = versions_by_id.get(analysis_version_id)
        if single is None:
            return True
        transcript = transcripts_by_round.get(round_id)
        if is_analysis_version_stale(single, transcript):
            return True
    return False


async def _consume_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
    request_hash = _canonical_hash(request_payload)
    existing = await find_idempotency(
        session,
        actor_id=actor.id,
        action=action,
        scope_id=scope_id,
        idempotency_key=key,
    )
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise InterviewIdempotencyConflictError("idempotency conflict")
    return existing


async def _store_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str,
    request_payload: dict[str, Any],
) -> None:
    try:
        await add_idempotency(
            session,
            InterviewIdempotencyKey(
                actor_id=actor.id,
                action=action,
                scope_id=scope_id,
                idempotency_key=key,
                request_hash=_canonical_hash(request_payload),
                result_round_id=None,
            ),
        )
    except IntegrityError as exc:
        raise InterviewIdempotencyConflictError("idempotency conflict") from exc


async def _collect_application_context(
    session: AsyncSession, *, application_id: UUID
) -> tuple[
    list[InterviewRound],
    dict[UUID, InterviewRoundAnalysis | None],
    dict[UUID, InterviewRoundAnalysisVersion],
    dict[UUID, InterviewTranscript | None],
    CoverageReport,
    list[dict[str, Any]],
]:
    rounds = await list_rounds_for_application(session, application_id)
    analyses_by_round: dict[UUID, InterviewRoundAnalysis | None] = {}
    versions_by_id: dict[UUID, InterviewRoundAnalysisVersion] = {}
    transcripts_by_round: dict[UUID, InterviewTranscript | None] = {}
    for round_ in rounds:
        analysis = await get_analysis_by_round(session, round_id=round_.id)
        analyses_by_round[round_.id] = analysis
        transcripts_by_round[round_.id] = await get_transcript_by_round_id(
            session, round_.id
        )
        if analysis is None:
            continue
        for item in getattr(analysis, "versions", None) or []:
            versions_by_id[item.id] = item
        current_id = getattr(analysis, "current_version_id", None)
        if current_id and current_id not in versions_by_id:
            version = await get_analysis_version_by_pk(
                session, version_id=current_id
            )
            if version is not None:
                versions_by_id[version.id] = version

    report = build_coverage_report(
        rounds=rounds,
        analyses_by_round=analyses_by_round,
        versions_by_id=versions_by_id,
        transcripts_by_round=transcripts_by_round,
    )
    round_refs: list[dict[str, Any]] = []
    for item in report.included_rounds:
        round_id = UUID(str(item["round_id"]))
        version_id = UUID(str(item["analysis_version_id"]))
        round_ = next(r for r in rounds if r.id == round_id)
        version = versions_by_id[version_id]
        round_refs.append(build_round_ref(round_, version))
    return (
        rounds,
        analyses_by_round,
        versions_by_id,
        transcripts_by_round,
        report,
        round_refs,
    )


async def request_comprehensive_analysis_generation(
    session: AsyncSession,
    *,
    application_id: UUID,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> AITask:
    """Create PENDING comprehensive task; does not enqueue."""
    await _assert_manage(actor)
    application = await get_application_by_id_for_update(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise InterviewValidationError(
            "only in_progress interviewing applications can generate comprehensive analysis"
        )
    if application.pipeline_status != PIPELINE_INTERVIEWING:
        raise InterviewValidationError(
            "only interviewing applications can generate comprehensive analysis"
        )

    (
        _rounds,
        _analyses,
        _versions,
        _transcripts,
        report,
        round_refs,
    ) = await _collect_application_context(session, application_id=application.id)

    if report.eligible_round_count < 1:
        raise InterviewValidationError(
            "no eligible current non-stale round analysis for coverage"
        )

    coverage_dict = report.to_dict()
    input_snapshot_hash = _canonical_hash(
        {
            "schema_version": COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION,
            "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
            "application_id": str(application.id),
            "workflow_key": COMPREHENSIVE_WORKFLOW_KEY,
            "workflow_version": COMPREHENSIVE_WORKFLOW_VERSION,
            "round_refs": round_refs,
            "coverage_report": coverage_dict,
        }
    )
    request_payload = {
        "application_id": str(application.id),
        "input_snapshot_hash": input_snapshot_hash,
    }
    assert_no_forbidden_snapshot_keys({"round_refs": round_refs, "coverage_report": coverage_dict})

    existing_key = await _consume_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_GENERATE,
        scope_id=application.id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if existing_key is not None:
        existing_task = await find_task_by_input_snapshot_hash(
            session,
            business_type=BUSINESS_TYPE_APPLICATION,
            business_id=application.id,
            task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
            input_snapshot_hash=input_snapshot_hash,
        )
        if existing_task is None:
            existing_task = await find_inflight_task(
                session,
                business_type=BUSINESS_TYPE_APPLICATION,
                business_id=application.id,
                task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
                inflight_statuses=_INFLIGHT_STATUSES,
            )
        if existing_task is None:
            raise InterviewIdempotencyConflictError("idempotency conflict")
        return existing_task

    inflight = await find_inflight_task(
        session,
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application.id,
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        inflight_statuses=_INFLIGHT_STATUSES,
    )
    if inflight is not None:
        existing_hash = (inflight.input_snapshot or {}).get("input_snapshot_hash")
        if existing_hash == input_snapshot_hash:
            await _store_idempotency(
                session,
                actor=actor,
                action=IDEMPOTENCY_ACTION_GENERATE,
                scope_id=application.id,
                key=idempotency_key,
                request_payload=request_payload,
            )
            return inflight
        raise InterviewConflictError(
            "a comprehensive analysis task is already pending or running"
        )

    now = _now()
    snapshot = {
        "schema_version": COMPREHENSIVE_SNAPSHOT_SCHEMA_VERSION,
        "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        "application_id": str(application.id),
        "workflow_key": COMPREHENSIVE_WORKFLOW_KEY,
        "workflow_version": COMPREHENSIVE_WORKFLOW_VERSION,
        "requested_by": str(actor.id),
        "requested_at": now.isoformat(),
        "idempotency_key": idempotency_key,
        "request_hash": _canonical_hash(request_payload),
        "input_snapshot_hash": input_snapshot_hash,
        "round_refs": round_refs,
        "coverage_report": coverage_dict,
    }
    assert_no_forbidden_snapshot_keys(snapshot)
    task = AITask(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application.id,
        version_id=None,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        input_snapshot=snapshot,
        result_payload=None,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    await add_ai_task(session, task)
    await _store_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_GENERATE,
        scope_id=application.id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    await record_audit(
        session,
        action="comprehensive_analysis.generate_requested",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "application_id": str(application.id),
            "task_id": str(task.id),
            "task_type": TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE,
            "input_snapshot_hash": input_snapshot_hash,
            "eligible_round_count": report.eligible_round_count,
            "gap_count": len(report.gaps),
            "status": task.status,
        },
    )
    return task


async def dispatch_persisted_comprehensive_analysis_task(
    session: AsyncSession, *, task_id: UUID
) -> None:
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    if task is None or task.task_type != TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE:
        raise InterviewNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_PENDING:
        raise InterviewValidationError("comprehensive analysis task is not pending")
    enqueue_sensitive_interview_ai_task(task.id)


async def persist_comprehensive_analysis_result(
    session: AsyncSession,
    *,
    task_id: UUID,
    payload: dict[str, Any],
    actor: User | None = None,
    request_context: RequestContext | None = None,
) -> ApplicationComprehensiveAnalysisVersion:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise InterviewNotFoundError("ai task not found")
    if task.task_type != TASK_TYPE_INTERVIEW_COMPREHENSIVE_ANALYZE:
        raise InterviewValidationError("unsupported task_type")

    existing = await get_comprehensive_version_by_task_id(session, ai_task_id=task.id)
    if existing is not None:
        return existing

    snapshot = dict(task.input_snapshot or {})
    application_id = UUID(str(snapshot.get("application_id") or task.business_id))
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")

    # Side-effect ban: never mutate pipeline/status/lock here.
    before_pipeline = application.pipeline_status
    before_status = application.status
    before_lock = application.lock_version

    coverage_report = snapshot.get("coverage_report")
    if not isinstance(coverage_report, dict):
        raise InterviewValidationError("coverage_report missing from snapshot")
    round_refs = snapshot.get("round_refs")
    if not isinstance(round_refs, list):
        raise InterviewValidationError("round_refs missing from snapshot")
    assert_no_forbidden_snapshot_keys({"round_refs": round_refs, "coverage_report": coverage_report})

    summary = str(payload.get("overall_summary") or "").strip()
    if not summary:
        raise InterviewValidationError("overall_summary is required")
    overall_score = payload.get("overall_score")
    if overall_score is not None:
        overall_score = Decimal(str(overall_score))

    analysis = await get_comprehensive_analysis_for_update(
        session, application_id=application_id
    )
    if analysis is None:
        analysis = ApplicationComprehensiveAnalysis(
            id=uuid4(),
            application_id=application_id,
            current_version_id=None,
        )
        await create_comprehensive_analysis(session, analysis)

    version_no = await next_comprehensive_version_no(session, analysis_id=analysis.id)
    cipher = encrypt_secret(summary)
    if not cipher:
        raise InterviewValidationError("encryption produced empty ciphertext")

    version = ApplicationComprehensiveAnalysisVersion(
        id=uuid4(),
        analysis_id=analysis.id,
        version_no=version_no,
        version_label=f"C{version_no}",
        ai_task_id=task.id,
        input_snapshot_hash=str(snapshot.get("input_snapshot_hash") or ""),
        round_refs=round_refs,
        coverage_report=coverage_report,
        overall_score=overall_score,
        overall_summary_encrypted=cipher,
        created_by=actor.id if actor is not None else task.created_by,
    )
    await create_comprehensive_version(session, version)
    analysis.current_version_id = version.id
    analysis.updated_at = _now()
    await session.flush()

    assert application.pipeline_status == before_pipeline
    assert application.status == before_status
    assert application.lock_version == before_lock

    if request_context is not None:
        await record_audit(
            session,
            action="comprehensive_analysis.generated",
            result="success",
            resource_type="job_application",
            request_context=request_context,
            actor_user_id=None if actor is None else actor.id,
            resource_id=str(application_id),
            changes={
                "application_id": str(application_id),
                "analysis_id": str(analysis.id),
                "analysis_version_id": str(version.id),
                "version_no": version.version_no,
                "task_id": str(task.id),
                "overall_score": (
                    float(overall_score) if overall_score is not None else None
                ),
                "eligible_round_count": coverage_report.get("eligible_round_count"),
                "coverage_insufficient": coverage_report.get("coverage_insufficient"),
                "single_round_only": coverage_report.get("single_round_only"),
            },
        )
    return version


async def count_hiring_decisions(session: AsyncSession, *, application_id: UUID) -> int:
    rows = await list_hiring_by_application(session, application_id=application_id)
    return len(rows)


async def _stale_context_for_application(
    session: AsyncSession, *, application_id: UUID
) -> tuple[
    dict[UUID, Any],
    dict[UUID, Any | None],
    dict[UUID, Any | None],
    dict[UUID, Any],
]:
    rounds = await list_rounds_for_application(session, application_id)
    rounds_by_id = {item.id: item for item in rounds}
    analyses_by_round: dict[UUID, Any | None] = {}
    transcripts_by_round: dict[UUID, Any | None] = {}
    versions_by_id: dict[UUID, Any] = {}
    for round_ in rounds:
        analysis = await get_analysis_by_round(session, round_id=round_.id)
        analyses_by_round[round_.id] = analysis
        transcripts_by_round[round_.id] = await get_transcript_by_round_id(
            session, round_.id
        )
        if analysis is not None:
            for item in analysis.versions or []:
                versions_by_id[item.id] = item
            if analysis.current_version_id and analysis.current_version_id not in versions_by_id:
                version = await get_analysis_version_by_pk(
                    session, version_id=analysis.current_version_id
                )
                if version is not None:
                    versions_by_id[version.id] = version
    return rounds_by_id, analyses_by_round, transcripts_by_round, versions_by_id


async def list_comprehensive_analysis(
    session: AsyncSession, *, application_id: UUID, actor: User
) -> ComprehensiveSetSummary:
    await _assert_manage(actor)
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    analysis = await get_comprehensive_analysis_by_application(
        session, application_id=application_id
    )
    versions = await list_comprehensive_version_rows(
        session, application_id=application_id
    )
    rounds_by_id, analyses_by_round, transcripts_by_round, versions_by_id = (
        await _stale_context_for_application(session, application_id=application_id)
    )
    current_id = analysis.current_version_id if analysis else None
    summaries: list[ComprehensiveVersionSummary] = []
    for item in versions:
        summaries.append(
            ComprehensiveVersionSummary(
                analysis_id=item.analysis_id,
                version_id=item.id,
                version_no=item.version_no,
                version_label=item.version_label,
                ai_task_id=item.ai_task_id,
                overall_score=item.overall_score,
                coverage_report=dict(item.coverage_report or {}),
                created_by=item.created_by,
                created_at=item.created_at,
                is_current=item.id == current_id,
                is_stale=is_comprehensive_version_stale(
                    item,
                    rounds_by_id=rounds_by_id,
                    analyses_by_round=analyses_by_round,
                    transcripts_by_round=transcripts_by_round,
                    versions_by_id=versions_by_id,
                ),
            )
        )
    return ComprehensiveSetSummary(
        analysis_id=analysis.id if analysis else None,
        application_id=application_id,
        current_version_id=current_id,
        versions=summaries,
    )


async def get_comprehensive_analysis_version_detail(
    session: AsyncSession,
    *,
    application_id: UUID,
    version_id: UUID,
    actor: User,
) -> ComprehensiveVersionDetail:
    await _assert_manage(actor)
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    analysis = await get_comprehensive_analysis_by_application(
        session, application_id=application_id
    )
    version = await get_comprehensive_version_by_id(
        session, application_id=application_id, version_id=version_id
    )
    if analysis is None or version is None:
        raise InterviewNotFoundError("comprehensive analysis version not found")
    rounds_by_id, analyses_by_round, transcripts_by_round, versions_by_id = (
        await _stale_context_for_application(session, application_id=application_id)
    )
    try:
        summary = decrypt_secret(version.overall_summary_encrypted) or ""
    except EncryptionError as exc:
        raise InterviewValidationError("analysis decryption failed") from exc
    return ComprehensiveVersionDetail(
        analysis_id=version.analysis_id,
        version_id=version.id,
        version_no=version.version_no,
        version_label=version.version_label,
        ai_task_id=version.ai_task_id,
        overall_score=version.overall_score,
        overall_summary=summary,
        round_refs=list(version.round_refs or []),
        coverage_report=dict(version.coverage_report or {}),
        created_by=version.created_by,
        created_at=version.created_at,
        is_current=version.id == analysis.current_version_id,
        is_stale=is_comprehensive_version_stale(
            version,
            rounds_by_id=rounds_by_id,
            analyses_by_round=analyses_by_round,
            transcripts_by_round=transcripts_by_round,
            versions_by_id=versions_by_id,
        ),
    )
