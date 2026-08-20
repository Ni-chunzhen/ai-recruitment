"""Single-round interview analysis request, persist, list and detail services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    AITask,
)
from app.models.interview import INTERVIEW_STATUS_COMPLETED, InterviewIdempotencyKey, InterviewRound
from app.models.interview_ai import (
    InterviewRoundAnalysis,
    InterviewRoundAnalysisDimension,
    InterviewRoundAnalysisEvidence,
    InterviewRoundAnalysisVersion,
)
from app.models.interview_transcript import (
    TranscriptCompletionMode,
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
)
from app.repositories.ai_tasks import (
    add_ai_task,
    find_inflight_task,
    find_task_by_input_snapshot_hash,
    get_ai_task_by_id,
)
from app.repositories.interview_analyses import (
    create_analysis,
    create_analysis_dimensions,
    create_analysis_evidence,
    create_analysis_version,
    get_analysis_by_round,
    get_analysis_for_update,
    get_analysis_version_by_id,
    get_analysis_version_by_task_id,
    next_analysis_version_no,
)
from app.repositories.interview_analyses import (
    list_analysis_versions as list_analysis_version_rows,
)
from app.repositories.interview_transcripts import (
    get_transcript_by_round_id,
)
from app.repositories.interview_transcripts import (
    get_version_by_id as get_transcript_version_by_id,
)
from app.repositories.interviews import (
    InterviewNotFoundError,
    actor_assigned_to_round,
    add_idempotency,
    find_idempotency,
    get_round_by_id,
    get_round_for_update,
)
from app.repositories.jobs import get_job_by_id
from app.repositories.jobs import get_version_by_id as get_job_version_by_id
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id
from app.schemas.interview_ai import (
    InterviewDimensionSnapshot,
    InterviewEvidenceSegment,
    InterviewRoundAnalyzeResult,
)
from app.services.ai_providers.base import validate_ai_result
from app.services.ai_tasks import enqueue_ai_task
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, decrypt_secret, encrypt_secret
from app.services.interview_ai_validation import (
    AIOutputValidationError,
    build_dimension_snapshot,
    require_complete_analysis_anchors,
    validate_analysis_result_against_snapshot,
    validate_dimension_weights,
)
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewValidationError,
)

ANALYSIS_SNAPSHOT_SCHEMA_VERSION = "1.0"
ANALYSIS_WORKFLOW_KEY = "interview_round_analyze"
ANALYSIS_WORKFLOW_VERSION = "1.0"
ANALYSIS_DETAIL_CACHE_CONTROL = "no-store"
INTERVIEW_ANALYZE_MAX_CHARS = 120_000
IDEMPOTENCY_ACTION_GENERATE = "analysis.generate"
_INFLIGHT_STATUSES = {AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING}
SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "task_type",
        "round_id",
        "job_version_id",
        "transcript_id",
        "transcript_version_id",
        "workflow_key",
        "workflow_version",
        "requested_by",
        "requested_at",
        "idempotency_key",
        "request_hash",
        "input_snapshot_hash",
        "dimensions",
        "segment_refs",
    }
)
SEGMENT_REF_KEYS = frozenset({"segment_id", "segment_no", "plaintext_sha256"})
_SHA256_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class AnalysisProviderSegment:
    id: UUID
    segment_no: int
    speaker_role: str | None
    speaker_name: str | None
    start_time_ms: int | None
    end_time_ms: int | None
    text: str


@dataclass(frozen=True)
class AnalysisProviderInput:
    round_id: UUID
    job_version_id: UUID
    transcript_id: UUID
    transcript_version_id: UUID
    dimensions: tuple[InterviewDimensionSnapshot, ...]
    segments: tuple[AnalysisProviderSegment, ...]


@dataclass
class AnalysisVersionSummary:
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    transcript_version_id: UUID
    job_version_id: UUID
    ai_task_id: UUID
    overall_score: Decimal | None
    dimension_count: int
    evidence_count: int
    created_by: UUID | None
    created_at: datetime
    is_current: bool
    is_stale: bool


@dataclass
class AnalysisSetSummary:
    analysis_id: UUID | None
    round_id: UUID
    current_version_id: UUID | None
    versions: list[AnalysisVersionSummary]
    cache_control: str = ANALYSIS_DETAIL_CACHE_CONTROL


@dataclass
class AnalysisEvidenceDetail:
    id: UUID
    transcript_segment_id: UUID
    segment_no: int
    quote: str


@dataclass
class AnalysisDimensionDetail:
    id: UUID
    dimension_key: str
    dimension_name: str
    weight: Decimal
    score: int | None
    analysis: str
    strengths: list[str]
    risks: list[str]
    insufficient_information: str | None
    suggested_follow_ups: list[str]
    display_order: int
    evidence: list[AnalysisEvidenceDetail]


@dataclass
class AnalysisVersionDetail:
    analysis_id: UUID
    version_id: UUID
    version_no: int
    version_label: str
    transcript_version_id: UUID
    job_version_id: UUID
    ai_task_id: UUID
    overall_score: Decimal | None
    overall_summary: str
    dimension_count: int
    evidence_count: int
    created_by: UUID | None
    created_at: datetime
    is_current: bool
    is_stale: bool
    dimensions: list[AnalysisDimensionDetail]
    cache_control: str = ANALYSIS_DETAIL_CACHE_CONTROL


def _now() -> datetime:
    return datetime.now(UTC)


async def _has_permission(actor: User, code: str) -> bool:
    codes = getattr(actor, "permission_codes", None)
    if codes is not None:
        return code in codes
    return await user_has_permission(actor, code)


async def _assert_can_access_round(
    session: AsyncSession, *, round_: InterviewRound, actor: User
) -> None:
    if await _has_permission(actor, "recruitment.manage"):
        return
    assigned = await actor_assigned_to_round(
        session, round_id=round_.id, user_id=actor.id
    )
    if await _has_permission(actor, "interview.execute") and assigned:
        return
    raise InterviewNotFoundError("interview round not found")


async def _assert_can_mutate_round(
    session: AsyncSession, *, round_: InterviewRound, actor: User
) -> None:
    await _assert_can_access_round(session, round_=round_, actor=actor)
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
    round_id: UUID,
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
                result_round_id=round_id,
            ),
        )
    except IntegrityError as exc:
        raise InterviewIdempotencyConflictError("idempotency conflict") from exc


async def _load_application(session: AsyncSession, application_id: UUID):
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    return application


async def _load_round_for_mutation(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> InterviewRound:
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _assert_can_mutate_round(session, round_=round_, actor=actor)
    await _load_application(session, round_.application_id)
    return round_


async def _load_round_for_read(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> InterviewRound:
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _assert_can_access_round(session, round_=round_, actor=actor)
    await _load_application(session, round_.application_id)
    return round_


def _encrypt_text(plain: str) -> str:
    token = encrypt_secret(plain)
    if token is None:
        raise EncryptionError("encryption produced empty ciphertext")
    return token


def _encrypt_json(value: list[str]) -> str:
    return _encrypt_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def _decrypt_text(cipher: str | None) -> str | None:
    if cipher is None:
        return None
    try:
        return decrypt_secret(cipher)
    except EncryptionError as exc:
        raise InterviewValidationError("analysis decryption failed") from exc


def _decrypt_required_text(cipher: str) -> str:
    plain = _decrypt_text(cipher)
    if plain is None:
        raise InterviewValidationError("analysis decryption failed")
    return plain


def _decrypt_json_list(cipher: str) -> list[str]:
    raw = _decrypt_required_text(cipher)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InterviewValidationError("analysis decryption failed") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise InterviewValidationError("analysis decryption failed")
    return parsed


def _dimension_dicts(
    snapshots: list[InterviewDimensionSnapshot],
) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in snapshots]


def _plaintext_sha256(plain_text: str) -> str:
    return hashlib.sha256(plain_text.encode("utf-8")).hexdigest()


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in _SHA256_HEX for char in value)
    )


def _hash_input_snapshot(
    *,
    round_id: UUID,
    job_version_id: UUID,
    transcript_id: UUID,
    transcript_version_id: UUID,
    dimensions: list[dict[str, Any]],
    segment_refs: list[dict[str, Any]],
) -> str:
    return _canonical_hash(
        {
            "schema_version": ANALYSIS_SNAPSHOT_SCHEMA_VERSION,
            "task_type": TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
            "round_id": str(round_id),
            "job_version_id": str(job_version_id),
            "transcript_id": str(transcript_id),
            "transcript_version_id": str(transcript_version_id),
            "workflow_key": ANALYSIS_WORKFLOW_KEY,
            "workflow_version": ANALYSIS_WORKFLOW_VERSION,
            "dimensions": dimensions,
            "segment_refs": segment_refs,
        }
    )


def _is_stale(
    version: InterviewRoundAnalysisVersion,
    transcript: InterviewTranscript | None,
) -> bool:
    if transcript is None or transcript.current_confirmed_version_id is None:
        return True
    return version.transcript_version_id != transcript.current_confirmed_version_id


def _evidence_count(version: InterviewRoundAnalysisVersion) -> int:
    return sum(len(dim.evidence or []) for dim in (version.dimensions or []))


def _decrypt_segment_text(segment: InterviewTranscriptSegment) -> str:
    try:
        plain = decrypt_secret(segment.text_encrypted)
    except EncryptionError as exc:
        raise InterviewValidationError("转写解密失败") from exc
    return (plain or "").strip()


def _included_plaintext_segments(
    version: InterviewTranscriptVersion,
) -> list[tuple[InterviewTranscriptSegment, str]]:
    selected: list[tuple[InterviewTranscriptSegment, str]] = []
    ordered = sorted(version.segments or [], key=lambda item: item.segment_no)
    for segment in ordered:
        if not segment.is_included_in_analysis:
            continue
        text = _decrypt_segment_text(segment)
        if not text:
            continue
        selected.append((segment, text))
    return selected


async def _load_confirmed_version(
    session: AsyncSession,
    *,
    transcript: InterviewTranscript,
    round_id: UUID,
) -> InterviewTranscriptVersion:
    confirmed_id = transcript.current_confirmed_version_id
    if confirmed_id is None:
        raise InterviewValidationError("请先确认转写版本后再生成单轮分析")
    version = await get_transcript_version_by_id(session, confirmed_id)
    if (
        version is None
        or version.transcript_id != transcript.id
        or transcript.interview_round_id != round_id
    ):
        raise InterviewValidationError("请先确认转写版本后再生成单轮分析")
    return version


def _snapshot_dimensions(
    snapshot: dict[str, Any],
) -> list[InterviewDimensionSnapshot]:
    raw = snapshot.get("dimensions") or []
    if not isinstance(raw, list) or not raw:
        raise AIOutputValidationError(
            "task snapshot dimensions are missing",
            code="output_validation_failed",
        )
    try:
        return [InterviewDimensionSnapshot.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise AIOutputValidationError(
            "task snapshot dimensions are invalid",
            code="output_validation_failed",
        ) from exc


def _require_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    missing = SNAPSHOT_KEYS - set(snapshot)
    extra = set(snapshot) - SNAPSHOT_KEYS
    if missing or extra:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        )
    try:
        round_id = UUID(str(snapshot["round_id"]))
        job_version_id = UUID(str(snapshot["job_version_id"]))
        transcript_id = UUID(str(snapshot["transcript_id"]))
        transcript_version_id = UUID(str(snapshot["transcript_version_id"]))
    except (TypeError, ValueError) as exc:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        ) from exc
    if snapshot.get("task_type") != TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        )
    refs = snapshot.get("segment_refs") or []
    if not isinstance(refs, list) or not refs:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        )
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != SEGMENT_REF_KEYS:
            raise AIOutputValidationError(
                "task snapshot is incomplete",
                code="output_validation_failed",
            )
        try:
            UUID(str(ref["segment_id"]))
            int(ref["segment_no"])
        except (TypeError, ValueError) as exc:
            raise AIOutputValidationError(
                "task snapshot is incomplete",
                code="output_validation_failed",
            ) from exc
        if not _is_sha256_hex(ref.get("plaintext_sha256")):
            raise AIOutputValidationError(
                "task snapshot is incomplete",
                code="output_validation_failed",
            )
    recomputed = _hash_input_snapshot(
        round_id=round_id,
        job_version_id=job_version_id,
        transcript_id=transcript_id,
        transcript_version_id=transcript_version_id,
        dimensions=list(snapshot["dimensions"]),
        segment_refs=list(refs),
    )
    if recomputed != str(snapshot.get("input_snapshot_hash") or ""):
        raise AIOutputValidationError(
            "task snapshot hash mismatch",
            code="output_validation_failed",
        )
    return snapshot


async def _segments_from_refs(
    session: AsyncSession,
    *,
    snapshot: dict[str, Any],
) -> tuple[InterviewTranscriptVersion, list[tuple[InterviewTranscriptSegment, str]]]:
    transcript_version_id = UUID(str(snapshot["transcript_version_id"]))
    transcript_id = UUID(str(snapshot["transcript_id"]))
    version = await get_transcript_version_by_id(session, transcript_version_id)
    if version is None or version.transcript_id != transcript_id:
        raise AIOutputValidationError(
            "frozen transcript version is no longer available",
            code="output_validation_failed",
        )
    by_id = {item.id: item for item in (version.segments or [])}
    loaded: list[tuple[InterviewTranscriptSegment, str]] = []
    for ref in snapshot["segment_refs"]:
        segment_id = UUID(str(ref["segment_id"]))
        segment_no = int(ref["segment_no"])
        segment = by_id.get(segment_id)
        if segment is None:
            raise AIOutputValidationError(
                "snapshot segment ref is invalid",
                code="output_validation_failed",
            )
        if segment.transcript_version_id != version.id:
            raise AIOutputValidationError(
                "snapshot segment ref is invalid",
                code="output_validation_failed",
            )
        if not segment.is_included_in_analysis or segment.segment_no != segment_no:
            raise AIOutputValidationError(
                "snapshot segment ref is invalid",
                code="output_validation_failed",
            )
        text = _decrypt_segment_text(segment)
        if not text:
            raise AIOutputValidationError(
                "snapshot segment ref is invalid",
                code="output_validation_failed",
            )
        if _plaintext_sha256(text) != str(ref.get("plaintext_sha256") or ""):
            raise AIOutputValidationError(
                "frozen segment content is no longer valid",
                code="output_validation_failed",
            )
        loaded.append((segment, text))
    total = sum(len(text) for _segment, text in loaded)
    if total > INTERVIEW_ANALYZE_MAX_CHARS:
        raise InterviewValidationError("转写内容超出单轮分析上限")
    return version, loaded


def _to_evidence_segments(
    version: InterviewTranscriptVersion,
    loaded: list[tuple[InterviewTranscriptSegment, str]],
) -> list[InterviewEvidenceSegment]:
    return [
        InterviewEvidenceSegment(
            id=segment.id,
            transcript_version_id=version.id,
            segment_no=segment.segment_no,
            is_included_in_analysis=True,
            text=text,
        )
        for segment, text in loaded
    ]


async def _audit(
    session: AsyncSession,
    *,
    action: str,
    actor: User | None,
    request_context: RequestContext,
    round_id: UUID,
    changes: dict[str, Any],
) -> None:
    await record_audit(
        session,
        action=action,
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=None if actor is None else actor.id,
        resource_id=str(round_id),
        changes=changes,
    )


async def request_analysis_generation(
    session: AsyncSession,
    *,
    round_id: UUID,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> AITask:
    """Create and flush a PENDING round-analysis task.

    Does not enqueue. The API layer must commit, then call
    dispatch_persisted_analysis_generation_task(task_id=task.id).
    """
    round_ = await _load_round_for_mutation(
        session, round_id=round_id, actor=actor
    )
    if round_.status != INTERVIEW_STATUS_COMPLETED:
        raise InterviewValidationError("仅已完成的面试轮次可以生成单轮分析")
    if (
        round_.transcript_completion_mode
        == TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value
    ):
        raise InterviewValidationError("该轮无转写，不能生成单轮分析")
    if (
        round_.transcript_completion_mode
        != TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value
    ):
        raise InterviewValidationError("请先确认转写版本后再生成单轮分析")

    transcript = await get_transcript_by_round_id(session, round_.id)
    if transcript is None:
        raise InterviewValidationError("请先确认转写版本后再生成单轮分析")
    confirmed = await _load_confirmed_version(
        session, transcript=transcript, round_id=round_.id
    )
    application = await _load_application(session, round_.application_id)
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_job_version_by_id(job, round_.job_version_id)
    if job_version is None:
        raise InterviewValidationError("frozen job version is missing")
    try:
        dimensions = build_dimension_snapshot(job_version.score_dimensions or [])
        require_complete_analysis_anchors(dimensions)
        validate_dimension_weights(dimensions)
    except AIOutputValidationError as exc:
        raise InterviewValidationError(str(exc)) from exc

    included = _included_plaintext_segments(confirmed)
    if not included:
        raise InterviewValidationError("没有可纳入分析的转写片段")
    total_chars = sum(len(text) for _segment, text in included)
    if total_chars > INTERVIEW_ANALYZE_MAX_CHARS:
        raise InterviewValidationError("转写内容超出单轮分析上限")

    dimension_dicts = _dimension_dicts(dimensions)
    segment_refs = [
        {
            "segment_id": str(segment.id),
            "segment_no": segment.segment_no,
            "plaintext_sha256": _plaintext_sha256(text),
        }
        for segment, text in included
    ]
    input_snapshot_hash = _hash_input_snapshot(
        round_id=round_.id,
        job_version_id=job_version.id,
        transcript_id=transcript.id,
        transcript_version_id=confirmed.id,
        dimensions=dimension_dicts,
        segment_refs=segment_refs,
    )
    request_payload = {
        "round_id": str(round_.id),
        "input_snapshot_hash": input_snapshot_hash,
        "transcript_version_id": str(confirmed.id),
        "job_version_id": str(job_version.id),
    }
    existing_key = await _consume_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_GENERATE,
        scope_id=round_.id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if existing_key is not None:
        existing_task = await find_task_by_input_snapshot_hash(
            session,
            business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
            business_id=round_.id,
            task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
            input_snapshot_hash=input_snapshot_hash,
        )
        if existing_task is None:
            existing_task = await find_inflight_task(
                session,
                business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
                business_id=round_.id,
                task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
                inflight_statuses=_INFLIGHT_STATUSES,
            )
        if existing_task is None:
            raise InterviewIdempotencyConflictError("idempotency conflict")
        return existing_task

    inflight = await find_inflight_task(
        session,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_.id,
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        inflight_statuses=_INFLIGHT_STATUSES,
    )
    if inflight is not None:
        existing_hash = (inflight.input_snapshot or {}).get("input_snapshot_hash")
        if existing_hash == input_snapshot_hash:
            await _store_idempotency(
                session,
                actor=actor,
                action=IDEMPOTENCY_ACTION_GENERATE,
                scope_id=round_.id,
                key=idempotency_key,
                request_payload=request_payload,
                round_id=round_.id,
            )
            return inflight
        raise InterviewConflictError(
            "an analysis task is already pending or running"
        )

    now = _now()
    snapshot = {
        "schema_version": ANALYSIS_SNAPSHOT_SCHEMA_VERSION,
        "task_type": TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        "round_id": str(round_.id),
        "job_version_id": str(job_version.id),
        "transcript_id": str(transcript.id),
        "transcript_version_id": str(confirmed.id),
        "workflow_key": ANALYSIS_WORKFLOW_KEY,
        "workflow_version": ANALYSIS_WORKFLOW_VERSION,
        "requested_by": str(actor.id),
        "requested_at": now.isoformat(),
        "idempotency_key": idempotency_key,
        "request_hash": _canonical_hash(request_payload),
        "input_snapshot_hash": input_snapshot_hash,
        "dimensions": dimension_dicts,
        "segment_refs": segment_refs,
    }
    task = AITask(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_.id,
        version_id=confirmed.id,
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
        scope_id=round_.id,
        key=idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await _audit(
        session,
        action="interview_analysis.generate_requested",
        actor=actor,
        request_context=request_context,
        round_id=round_.id,
        changes={
            "round_id": str(round_.id),
            "task_id": str(task.id),
            "task_type": TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
            "transcript_version_id": str(confirmed.id),
            "job_version_id": str(job_version.id),
            "dimension_count": len(dimension_dicts),
            "status": task.status,
            "workflow_version": ANALYSIS_WORKFLOW_VERSION,
        },
    )
    return task


async def dispatch_persisted_analysis_generation_task(
    session: AsyncSession, *, task_id: UUID
) -> None:
    """Enqueue a committed PENDING INTERVIEW_ROUND_ANALYZE task.

    API call order:
    1. request_analysis_generation(...)  # flush PENDING task, do not enqueue
    2. session.commit()
    3. dispatch_persisted_analysis_generation_task(session, task_id=task.id)

    Flush is not a dispatch signal. This helper re-reads the task and assumes
    the outer transaction has already committed. It does not create tasks,
    write analysis versions, mutate snapshots, or emit audit events. Celery
    publish failure must not roll back the committed PENDING row; keep the
    task_id for a later retry.
    """
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    if task is None or task.task_type != TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        raise InterviewNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_PENDING:
        raise InterviewValidationError("analysis generation task is not pending")
    enqueue_ai_task(task.id)


async def load_analysis_provider_input(
    session: AsyncSession, *, task_id: UUID
) -> AnalysisProviderInput:
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    if task is None:
        raise InterviewNotFoundError("ai task not found")
    if task.task_type != TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        raise InterviewNotFoundError("ai task not found")
    try:
        snapshot = _require_snapshot(dict(task.input_snapshot or {}))
    except AIOutputValidationError as exc:
        raise InterviewValidationError(str(exc)) from exc
    round_id = UUID(str(snapshot["round_id"]))
    if task.business_type != BUSINESS_TYPE_INTERVIEW_ROUND or task.business_id != round_id:
        raise InterviewNotFoundError("ai task not found")
    job_version_id = UUID(str(snapshot["job_version_id"]))
    transcript_id = UUID(str(snapshot["transcript_id"]))
    transcript_version_id = UUID(str(snapshot["transcript_version_id"]))
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    application = await _load_application(session, round_.application_id)
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_job_version_by_id(job, job_version_id)
    if job_version is None:
        raise AIOutputValidationError(
            "frozen input is no longer available",
            code="output_validation_failed",
        )
    version, loaded = await _segments_from_refs(session, snapshot=snapshot)
    dimensions = tuple(_snapshot_dimensions(snapshot))
    return AnalysisProviderInput(
        round_id=round_id,
        job_version_id=job_version_id,
        transcript_id=transcript_id,
        transcript_version_id=transcript_version_id,
        dimensions=dimensions,
        segments=tuple(
            AnalysisProviderSegment(
                id=segment.id,
                segment_no=segment.segment_no,
                speaker_role=segment.speaker_role,
                speaker_name=segment.speaker_name,
                start_time_ms=segment.start_time_ms,
                end_time_ms=segment.end_time_ms,
                text=text,
            )
            for segment, text in loaded
        ),
    )


async def persist_analysis_generation_result(
    session: AsyncSession,
    *,
    task_id: UUID,
    payload: dict[str, Any] | InterviewRoundAnalyzeResult,
    actor: User | None = None,
    request_context: RequestContext | None = None,
) -> InterviewRoundAnalysisVersion:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise InterviewNotFoundError("ai task not found")
    if task.task_type != TASK_TYPE_INTERVIEW_ROUND_ANALYZE:
        raise InterviewValidationError("unsupported task_type")
    snapshot = _require_snapshot(dict(task.input_snapshot or {}))
    round_id = UUID(str(snapshot["round_id"]))
    existing = await get_analysis_version_by_task_id(
        session, ai_task_id=task.id, round_id=round_id
    )
    if existing is not None:
        return existing

    if actor is not None:
        round_ = await get_round_for_update(session, round_id)
        if round_ is None:
            raise InterviewNotFoundError("interview round not found")
        await _assert_can_access_round(session, round_=round_, actor=actor)
    else:
        round_ = await get_round_for_update(session, round_id)
        if round_ is None:
            raise InterviewNotFoundError("interview round not found")

    dimensions = _snapshot_dimensions(snapshot)
    version, loaded = await _segments_from_refs(session, snapshot=snapshot)
    evidence_segments = _to_evidence_segments(version, loaded)
    raw_payload = (
        payload.model_dump(mode="json")
        if isinstance(payload, InterviewRoundAnalyzeResult)
        else payload
    )
    validated = validate_ai_result(TASK_TYPE_INTERVIEW_ROUND_ANALYZE, raw_payload)
    result = InterviewRoundAnalyzeResult.model_validate(validated)
    overall_score = validate_analysis_result_against_snapshot(
        result,
        dimensions,
        transcript_version_id=version.id,
        segments=evidence_segments,
    )

    by_key = {item.dimension_key: item for item in result.dimensions}
    encrypted_dims: list[dict[str, Any]] = []
    for snapshot_dim in dimensions:
        item = by_key[snapshot_dim.dimension_key]
        encrypted_dims.append(
            {
                "snapshot": snapshot_dim,
                "item": item,
                "analysis": _encrypt_text(item.analysis),
                "strengths": _encrypt_json(list(item.strengths)),
                "risks": _encrypt_json(list(item.risks)),
                "follow_ups": _encrypt_json(list(item.suggested_follow_ups)),
                "insufficient": (
                    _encrypt_text(item.insufficient_information)
                    if item.score is None
                    else None
                ),
                "quotes": [_encrypt_text(ref.quote) for ref in item.evidence],
            }
        )
    summary_encrypted = _encrypt_text(result.overall_summary)

    analysis = await get_analysis_for_update(session, round_id=round_id)
    actor_id = actor.id if actor is not None else task.created_by
    if analysis is None:
        analysis = InterviewRoundAnalysis(
            id=uuid4(),
            interview_round_id=round_id,
            current_version_id=None,
        )
        await create_analysis(session, analysis)

    try:
        version_no = await next_analysis_version_no(
            session, analysis_id=analysis.id
        )
        analysis_version = InterviewRoundAnalysisVersion(
            id=uuid4(),
            analysis_id=analysis.id,
            version_no=version_no,
            version_label=f"A{version_no}",
            transcript_version_id=UUID(str(snapshot["transcript_version_id"])),
            job_version_id=UUID(str(snapshot["job_version_id"])),
            ai_task_id=task.id,
            dimensions_snapshot=list(snapshot["dimensions"]),
            overall_score=overall_score,
            overall_summary_encrypted=summary_encrypted,
            created_by=actor_id,
        )
        await create_analysis_version(session, analysis_version)
        dim_rows: list[InterviewRoundAnalysisDimension] = []
        for packed in encrypted_dims:
            snapshot_dim = packed["snapshot"]
            item = packed["item"]
            dim_rows.append(
                InterviewRoundAnalysisDimension(
                    id=uuid4(),
                    analysis_version_id=analysis_version.id,
                    dimension_key=snapshot_dim.dimension_key,
                    dimension_name=snapshot_dim.name,
                    weight=snapshot_dim.weight,
                    score=item.score,
                    analysis_encrypted=packed["analysis"],
                    strengths_encrypted=packed["strengths"],
                    risks_encrypted=packed["risks"],
                    insufficient_information_encrypted=packed["insufficient"],
                    suggested_follow_ups_encrypted=packed["follow_ups"],
                    display_order=snapshot_dim.display_order,
                )
            )
        await create_analysis_dimensions(session, dim_rows)
        evidence_rows: list[InterviewRoundAnalysisEvidence] = []
        for packed, dim_row in zip(encrypted_dims, dim_rows, strict=True):
            item = packed["item"]
            for ref, quote_cipher in zip(item.evidence, packed["quotes"], strict=True):
                evidence_rows.append(
                    InterviewRoundAnalysisEvidence(
                        id=uuid4(),
                        analysis_dimension_id=dim_row.id,
                        transcript_segment_id=ref.segment_id,
                        segment_no=ref.segment_no,
                        quote_encrypted=quote_cipher,
                    )
                )
        if evidence_rows:
            await create_analysis_evidence(session, evidence_rows)
        for dim_row, packed in zip(dim_rows, encrypted_dims, strict=True):
            evidence_for_dim = [
                row
                for row in evidence_rows
                if row.analysis_dimension_id == dim_row.id
            ]
            set_committed_value(dim_row, "evidence", evidence_for_dim)
        set_committed_value(analysis_version, "dimensions", dim_rows)
        analysis.current_version_id = analysis_version.id
        analysis.updated_at = _now()
        await session.flush()
    except IntegrityError as exc:
        raise InterviewConflictError("analysis version conflict") from exc

    if request_context is not None:
        await _audit(
            session,
            action="interview_analysis.generated",
            actor=actor,
            request_context=request_context,
            round_id=round_id,
            changes={
                "round_id": str(round_id),
                "analysis_id": str(analysis.id),
                "analysis_version_id": str(analysis_version.id),
                "version_no": analysis_version.version_no,
                "version_label": analysis_version.version_label,
                "task_id": str(task.id),
                "task_type": TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
                "transcript_version_id": str(analysis_version.transcript_version_id),
                "job_version_id": str(analysis_version.job_version_id),
                "dimension_count": len(dim_rows),
                "evidence_count": len(evidence_rows),
                "status": "succeeded",
                "workflow_version": ANALYSIS_WORKFLOW_VERSION,
                "overall_score": (
                    str(overall_score) if overall_score is not None else None
                ),
            },
        )
    return analysis_version


def _to_version_summary(
    version: InterviewRoundAnalysisVersion,
    *,
    transcript: InterviewTranscript | None,
    current_version_id: UUID | None,
) -> AnalysisVersionSummary:
    return AnalysisVersionSummary(
        analysis_id=version.analysis_id,
        version_id=version.id,
        version_no=version.version_no,
        version_label=version.version_label,
        transcript_version_id=version.transcript_version_id,
        job_version_id=version.job_version_id,
        ai_task_id=version.ai_task_id,
        overall_score=version.overall_score,
        dimension_count=len(version.dimensions or []),
        evidence_count=_evidence_count(version),
        created_by=version.created_by,
        created_at=version.created_at,
        is_current=version.id == current_version_id,
        is_stale=_is_stale(version, transcript),
    )


def _to_version_detail(
    version: InterviewRoundAnalysisVersion,
    *,
    transcript: InterviewTranscript | None,
    current_version_id: UUID | None,
) -> AnalysisVersionDetail:
    dimensions: list[AnalysisDimensionDetail] = []
    for dim in sorted(version.dimensions or [], key=lambda item: item.display_order):
        evidence = [
            AnalysisEvidenceDetail(
                id=row.id,
                transcript_segment_id=row.transcript_segment_id,
                segment_no=row.segment_no,
                quote=_decrypt_required_text(row.quote_encrypted),
            )
            for row in sorted(
                dim.evidence or [],
                key=lambda item: (item.segment_no, str(item.id)),
            )
        ]
        dimensions.append(
            AnalysisDimensionDetail(
                id=dim.id,
                dimension_key=dim.dimension_key,
                dimension_name=dim.dimension_name,
                weight=dim.weight,
                score=dim.score,
                analysis=_decrypt_required_text(dim.analysis_encrypted),
                strengths=_decrypt_json_list(dim.strengths_encrypted),
                risks=_decrypt_json_list(dim.risks_encrypted),
                insufficient_information=_decrypt_text(
                    dim.insufficient_information_encrypted
                ),
                suggested_follow_ups=_decrypt_json_list(
                    dim.suggested_follow_ups_encrypted
                ),
                display_order=dim.display_order,
                evidence=evidence,
            )
        )
    return AnalysisVersionDetail(
        analysis_id=version.analysis_id,
        version_id=version.id,
        version_no=version.version_no,
        version_label=version.version_label,
        transcript_version_id=version.transcript_version_id,
        job_version_id=version.job_version_id,
        ai_task_id=version.ai_task_id,
        overall_score=version.overall_score,
        overall_summary=_decrypt_required_text(version.overall_summary_encrypted),
        dimension_count=len(dimensions),
        evidence_count=sum(len(item.evidence) for item in dimensions),
        created_by=version.created_by,
        created_at=version.created_at,
        is_current=version.id == current_version_id,
        is_stale=_is_stale(version, transcript),
        dimensions=dimensions,
        cache_control=ANALYSIS_DETAIL_CACHE_CONTROL,
    )


async def list_analysis_versions(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> AnalysisSetSummary:
    await _load_round_for_read(session, round_id=round_id, actor=actor)
    analysis = await get_analysis_by_round(session, round_id=round_id)
    transcript = await get_transcript_by_round_id(session, round_id)
    versions = await list_analysis_version_rows(session, round_id=round_id)
    current_id = analysis.current_version_id if analysis else None
    return AnalysisSetSummary(
        analysis_id=analysis.id if analysis else None,
        round_id=round_id,
        current_version_id=current_id,
        versions=[
            _to_version_summary(
                item, transcript=transcript, current_version_id=current_id
            )
            for item in versions
        ],
        cache_control=ANALYSIS_DETAIL_CACHE_CONTROL,
    )


async def get_analysis_version_detail(
    session: AsyncSession,
    *,
    round_id: UUID,
    version_id: UUID,
    actor: User,
) -> AnalysisVersionDetail:
    await _load_round_for_read(session, round_id=round_id, actor=actor)
    analysis = await get_analysis_by_round(session, round_id=round_id)
    version = await get_analysis_version_by_id(
        session, round_id=round_id, version_id=version_id
    )
    if analysis is None or version is None:
        raise InterviewNotFoundError("analysis version not found")
    transcript = await get_transcript_by_round_id(session, round_id)
    return _to_version_detail(
        version,
        transcript=transcript,
        current_version_id=analysis.current_version_id,
    )
