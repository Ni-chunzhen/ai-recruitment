"""Interview transcript preview, import, proofreading and completion workflow."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.interview import (
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    InterviewIdempotencyKey,
    InterviewRound,
)
from app.models.interview_transcript import (
    TRANSCRIPT_REASON_CATALOG,
    TRANSCRIPT_REASON_OTHER,
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
    TranscriptCompletionMode,
    TranscriptSegmentSource,
    TranscriptVersionStatus,
    TranscriptVersionType,
    list_transcript_reason_catalog,
)
from app.repositories.interview_transcripts import (
    add_segment,
    add_transcript,
    add_version,
    get_editing_draft,
    get_transcript_by_id,
    get_transcript_by_round_id,
    get_transcript_for_update,
    get_transcript_for_update_by_round,
    get_version_by_id,
    get_version_for_update,
    list_versions_for_transcript,
    next_version_no,
    replace_segments,
)
from app.repositories.interviews import (
    actor_assigned_to_round,
    add_idempotency,
    find_idempotency,
    get_round_by_id,
    get_round_for_update,
)
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id
from app.schemas.interview_transcript import (
    ChangeCountsOut,
    CompleteWithoutTranscriptOut,
    CompleteWithoutTranscriptRequest,
    ConfirmRequest,
    DraftCreateRequest,
    DraftSaveRequest,
    DraftSaveResponse,
    DraftSegmentIn,
    TranscriptImportRequest,
    TranscriptImportSegmentIn,
    TranscriptListOut,
    TranscriptPreviewOut,
    TranscriptPreviewSegmentOut,
    TranscriptReasonCodeItem,
    TranscriptReasonCodeListResponse,
    TranscriptSegmentOut,
    TranscriptSummaryOut,
    TranscriptVersionDetailOut,
    TranscriptVersionSummaryOut,
)
from app.services.audit import RequestContext, record_audit
from app.services.crypto import CIPHER_PREFIX, EncryptionError, _load_fernet
from app.services.interview_state import next_status
from app.services.interviews import (
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)
from app.services.transcript_parser import (
    ParsedSegment,
    TranscriptParseError,
    decode_transcript,
    parse_transcript,
)

DRAFT_OPTIMISTIC_LOCK_MESSAGE = "转写草稿已被其他人员更新，请刷新后重新检查修改"
_REASON_CODES = {code for code, _ in TRANSCRIPT_REASON_CATALOG}


async def _has_permission(actor: User, code: str) -> bool:
    codes = getattr(actor, "permission_codes", None)
    if codes is not None:
        return code in codes
    return await user_has_permission(actor, code)


async def _require_manage(actor: User) -> None:
    if not await _has_permission(actor, "recruitment.manage"):
        raise InterviewForbiddenError("forbidden")


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_hash(payload: dict[str, Any]) -> str:
    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _strip(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_strip(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    canonical = json.dumps(_strip(payload), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _consume_idempotency(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    scope_id: UUID,
    key: str | None,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
    if not key:
        return None
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
    key: str | None,
    request_payload: dict[str, Any],
    round_id: UUID,
) -> None:
    if not key:
        return
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


async def _load_application(session: AsyncSession, application_id: UUID):
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise InterviewNotFoundError("application not found")
    return application


async def _require_manage_round(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> InterviewRound:
    await _require_manage(actor)
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _load_application(session, round_.application_id)
    return round_


def _encrypt_text(plain: str) -> str:
    try:
        token = _load_fernet().encrypt(plain.encode("utf-8"))
        return CIPHER_PREFIX + token.decode("ascii")
    except EncryptionError as exc:
        raise InterviewValidationError("transcript encryption failed") from exc
    except Exception as exc:
        raise InterviewValidationError("transcript encryption failed") from exc


def _decrypt_text(cipher: str) -> str:
    try:
        if not cipher or not cipher.startswith(CIPHER_PREFIX):
            raise EncryptionError("ciphertext is invalid")
        raw = cipher[len(CIPHER_PREFIX) :].encode("ascii")
        return _load_fernet().decrypt(raw).decode("utf-8")
    except EncryptionError as exc:
        raise InterviewValidationError("transcript decryption failed") from exc
    except Exception as exc:
        raise InterviewValidationError("transcript decryption failed") from exc


def _rebuild_aggregate(texts: list[str]) -> str:
    return "\n".join(texts)


def _segment_content_matches(
    *,
    speaker_key: str,
    speaker_name: str,
    speaker_role: str,
    text: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    source: ParsedSegment | InterviewTranscriptSegment | dict[str, Any],
) -> bool:
    if isinstance(source, ParsedSegment):
        return (
            speaker_key == source.speaker_key
            and speaker_name == source.speaker_name
            and speaker_role == source.speaker_role
            and text == source.text
            and start_time_ms == source.start_time_ms
            and end_time_ms == source.end_time_ms
        )
    if isinstance(source, InterviewTranscriptSegment):
        return (
            speaker_key == source.speaker_key
            and speaker_name == source.speaker_name
            and speaker_role == source.speaker_role
            and start_time_ms == source.start_time_ms
            and end_time_ms == source.end_time_ms
        )
    return (
        speaker_key == source.get("speaker_key")
        and speaker_name == source.get("speaker_name")
        and speaker_role == source.get("speaker_role")
        and text == source.get("text")
        and start_time_ms == source.get("start_time_ms")
        and end_time_ms == source.get("end_time_ms")
    )


def _derive_import_source_type(
    segment: TranscriptImportSegmentIn,
    parsed_by_no: dict[int, ParsedSegment],
) -> str:
    refs = list(segment.source_segment_refs or [])
    if not refs:
        return TranscriptSegmentSource.MANUAL_ADDITION.value
    if len(refs) != 1:
        return TranscriptSegmentSource.CORRECTED.value
    source = parsed_by_no.get(refs[0])
    if source is None:
        return TranscriptSegmentSource.MANUAL_ADDITION.value
    if _segment_content_matches(
        speaker_key=segment.speaker_key,
        speaker_name=segment.speaker_name,
        speaker_role=segment.speaker_role,
        text=segment.text,
        start_time_ms=segment.start_time_ms,
        end_time_ms=segment.end_time_ms,
        source=source,
    ):
        return TranscriptSegmentSource.ORIGINAL.value
    return TranscriptSegmentSource.CORRECTED.value


def _derive_draft_source_type(
    segment: DraftSegmentIn,
    base_by_no: dict[int, dict[str, Any]],
) -> str:
    refs = list(segment.source_segment_refs or [])
    if not refs:
        return TranscriptSegmentSource.MANUAL_ADDITION.value
    if len(refs) != 1:
        return TranscriptSegmentSource.CORRECTED.value
    base = base_by_no.get(refs[0])
    if base is None:
        return TranscriptSegmentSource.MANUAL_ADDITION.value
    if (
        segment.speaker_key == base["speaker_key"]
        and segment.speaker_name == base["speaker_name"]
        and segment.speaker_role == base["speaker_role"]
        and segment.text == base["text"]
        and segment.start_time_ms == base["start_time_ms"]
        and segment.end_time_ms == base["end_time_ms"]
    ):
        return TranscriptSegmentSource.ORIGINAL.value
    return TranscriptSegmentSource.CORRECTED.value


def _compute_change_counts(
    *,
    previous: list[dict[str, Any]],
    incoming: list[DraftSegmentIn],
    derived_types: list[str],
) -> ChangeCountsOut:
    prev_by_no = {item["segment_no"]: item for item in previous}
    prev_refs_order = [
        tuple(item.get("source_segment_refs") or [item["segment_no"]])
        for item in previous
    ]
    new_refs_order = [
        tuple(seg.source_segment_refs or []) for seg in incoming
    ]

    speaker_changes = 0
    text_corrections = 0
    merge_split_count = 0
    excluded = 0
    for seg, source_type in zip(incoming, derived_types, strict=True):
        refs = list(seg.source_segment_refs or [])
        if source_type == TranscriptSegmentSource.MANUAL_ADDITION.value:
            continue
        if len(refs) != 1:
            merge_split_count += 1
            continue
        prev = prev_by_no.get(refs[0])
        if prev is None:
            # Compare against previous draft by matching source refs string
            matched_prev = next(
                (
                    item
                    for item in previous
                    if list(item.get("source_segment_refs") or []) == refs
                ),
                None,
            )
            prev = matched_prev
        if prev is None:
            merge_split_count += 1
            continue
        if (
            seg.speaker_key != prev["speaker_key"]
            or seg.speaker_name != prev["speaker_name"]
            or seg.speaker_role != prev["speaker_role"]
        ):
            speaker_changes += 1
        if seg.text != prev["text"]:
            text_corrections += 1
        if not seg.is_included_in_analysis and prev.get(
            "is_included_in_analysis", True
        ):
            excluded += 1
        prev_ref_len = len(prev.get("source_segment_refs") or [])
        if prev_ref_len != len(refs) and prev_ref_len > 0:
            merge_split_count += 1

    kept_prev_nos: set[int] = set()
    for seg in incoming:
        refs = list(seg.source_segment_refs or [])
        if len(refs) == 1 and refs[0] in prev_by_no:
            kept_prev_nos.add(refs[0])
        else:
            for ref in refs:
                if ref in prev_by_no:
                    kept_prev_nos.add(ref)
    deleted_count = max(0, len(previous) - len(kept_prev_nos))

    manual_addition_count = sum(
        1
        for st in derived_types
        if st == TranscriptSegmentSource.MANUAL_ADDITION.value
    )
    reorder_count = 1 if prev_refs_order != new_refs_order and previous else 0

    return ChangeCountsOut(
        speaker_changes=speaker_changes,
        text_corrections=text_corrections,
        merge_split_count=merge_split_count,
        deleted_count=deleted_count,
        manual_addition_count=manual_addition_count,
        excluded_from_analysis_count=excluded,
        reorder_count=reorder_count,
    )


def _validate_confirm_segments(segments: list[dict[str, Any]]) -> None:
    if not segments:
        raise InterviewValidationError("draft must contain at least one segment")
    included = 0
    for item in segments:
        text = (item.get("text") or "").strip()
        if not text:
            raise InterviewValidationError("segment text cannot be empty")
        start = item.get("start_time_ms")
        end = item.get("end_time_ms")
        if (start is None) ^ (end is None):
            raise InterviewValidationError("invalid segment time range")
        if start is not None and end is not None and (start < 0 or start >= end):
            raise InterviewValidationError("invalid segment time range")
        if item.get("is_included_in_analysis") and text:
            included += 1
    if included < 1:
        raise InterviewValidationError(
            "at least one included_in_analysis segment is required"
        )


def _version_label(version_type: str, version_no: int) -> str:
    if version_type == TranscriptVersionType.ORIGINAL.value:
        return f"T{version_no}"
    if version_type == TranscriptVersionType.DRAFT.value:
        return f"D{version_no}"
    return f"C{version_no}"


def _segment_out(segment: InterviewTranscriptSegment, text: str) -> TranscriptSegmentOut:
    refs = segment.source_segment_refs or []
    return TranscriptSegmentOut(
        id=segment.id,
        segment_no=segment.segment_no,
        speaker_key=segment.speaker_key,
        speaker_name=segment.speaker_name,
        speaker_role=segment.speaker_role,
        start_time_ms=segment.start_time_ms,
        end_time_ms=segment.end_time_ms,
        text=text,
        source_type=segment.source_type,
        source_segment_refs=[int(item) for item in refs],
        is_included_in_analysis=segment.is_included_in_analysis,
        is_unclear=segment.is_unclear,
    )


def _summary_out(transcript: InterviewTranscript) -> TranscriptSummaryOut:
    return TranscriptSummaryOut(
        id=transcript.id,
        interview_round_id=transcript.interview_round_id,
        original_version_id=transcript.original_version_id,
        current_draft_version_id=transcript.current_draft_version_id,
        current_confirmed_version_id=transcript.current_confirmed_version_id,
        version=transcript.version,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
    )


def _version_summary_out(
    version: InterviewTranscriptVersion,
) -> TranscriptVersionSummaryOut:
    return TranscriptVersionSummaryOut(
        id=version.id,
        transcript_id=version.transcript_id,
        version_type=version.version_type,
        version_no=version.version_no,
        version_label=version.version_label,
        status=version.status,
        source_method=version.source_method,
        source_filename=version.source_filename,
        source_size=version.source_size,
        source_mime=version.source_mime,
        source_encoding=version.source_encoding,
        based_on_version_id=version.based_on_version_id,
        segment_count=len(version.segments or []),
        confirmed_by=version.confirmed_by,
        confirmed_at=version.confirmed_at,
        version=version.version,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


async def _version_detail_out(
    version: InterviewTranscriptVersion,
    *,
    round_id: UUID,
) -> TranscriptVersionDetailOut:
    decrypted_segments: list[TranscriptSegmentOut] = []
    texts: list[str] = []
    for segment in sorted(version.segments or [], key=lambda item: item.segment_no):
        text = _decrypt_text(segment.text_encrypted)
        texts.append(text)
        decrypted_segments.append(_segment_out(segment, text))
    raw_text = _decrypt_text(version.raw_text_encrypted)
    # Prefer stored aggregate; fall back to rebuild if needed for consistency checks.
    if not raw_text and texts:
        raw_text = _rebuild_aggregate(texts)
    return TranscriptVersionDetailOut(
        id=version.id,
        transcript_id=version.transcript_id,
        interview_round_id=round_id,
        version_type=version.version_type,
        version_no=version.version_no,
        version_label=version.version_label,
        status=version.status,
        source_method=version.source_method,
        source_filename=version.source_filename,
        source_size=version.source_size,
        source_mime=version.source_mime,
        source_encoding=version.source_encoding,
        source_sha256=version.source_sha256,
        based_on_version_id=version.based_on_version_id,
        confirmed_by=version.confirmed_by,
        confirmed_at=version.confirmed_at,
        version=version.version,
        created_at=version.created_at,
        updated_at=version.updated_at,
        segments=decrypted_segments,
        raw_text=raw_text,
    )


def _decrypted_segment_dicts(
    version: InterviewTranscriptVersion,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in sorted(version.segments or [], key=lambda item: item.segment_no):
        rows.append(
            {
                "segment_no": segment.segment_no,
                "speaker_key": segment.speaker_key,
                "speaker_name": segment.speaker_name,
                "speaker_role": segment.speaker_role,
                "start_time_ms": segment.start_time_ms,
                "end_time_ms": segment.end_time_ms,
                "text": _decrypt_text(segment.text_encrypted),
                "source_type": segment.source_type,
                "source_segment_refs": list(segment.source_segment_refs or []),
                "is_included_in_analysis": segment.is_included_in_analysis,
                "is_unclear": segment.is_unclear,
            }
        )
    return rows


def _build_segments_from_import(
    *,
    version_id: UUID,
    segments: list[TranscriptImportSegmentIn],
    parsed_by_no: dict[int, ParsedSegment],
) -> list[InterviewTranscriptSegment]:
    built: list[InterviewTranscriptSegment] = []
    for index, item in enumerate(segments, start=1):
        source_type = _derive_import_source_type(item, parsed_by_no)
        built.append(
            InterviewTranscriptSegment(
                id=uuid4(),
                transcript_version_id=version_id,
                segment_no=index,
                speaker_key=item.speaker_key,
                speaker_name=item.speaker_name,
                speaker_role=item.speaker_role,
                start_time_ms=item.start_time_ms,
                end_time_ms=item.end_time_ms,
                text_encrypted=_encrypt_text(item.text),
                source_type=source_type,
                source_segment_refs=list(item.source_segment_refs or []),
                is_included_in_analysis=item.is_included_in_analysis,
                is_unclear=item.is_unclear,
            )
        )
    return built


def _copy_segments_to_version(
    *,
    target_version_id: UUID,
    source_rows: list[dict[str, Any]],
) -> list[InterviewTranscriptSegment]:
    built: list[InterviewTranscriptSegment] = []
    for index, item in enumerate(source_rows, start=1):
        built.append(
            InterviewTranscriptSegment(
                id=uuid4(),
                transcript_version_id=target_version_id,
                segment_no=index,
                speaker_key=item["speaker_key"],
                speaker_name=item["speaker_name"],
                speaker_role=item["speaker_role"],
                start_time_ms=item["start_time_ms"],
                end_time_ms=item["end_time_ms"],
                text_encrypted=_encrypt_text(item["text"]),
                source_type=item.get(
                    "source_type", TranscriptSegmentSource.ORIGINAL.value
                ),
                source_segment_refs=list(item.get("source_segment_refs") or []),
                is_included_in_analysis=bool(
                    item.get("is_included_in_analysis", True)
                ),
                is_unclear=bool(item.get("is_unclear", False)),
            )
        )
    return built


def list_transcript_reason_codes() -> TranscriptReasonCodeListResponse:
    return TranscriptReasonCodeListResponse(
        items=[
            TranscriptReasonCodeItem.model_validate(item)
            for item in list_transcript_reason_catalog()
        ]
    )


async def preview_transcript(
    session: AsyncSession,
    *,
    round_id: UUID,
    actor: User,
    request_context: RequestContext,
    data: bytes,
    filename: str | None,
) -> TranscriptPreviewOut:
    await _require_manage(actor)
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _load_application(session, round_.application_id)
    if round_.status != INTERVIEW_STATUS_PENDING_TRANSCRIPT:
        raise InterviewValidationError(
            "transcript preview requires PENDING_TRANSCRIPT status"
        )
    try:
        decoded = decode_transcript(data, filename)
        parsed = parse_transcript(decoded.text)
    except TranscriptParseError as exc:
        raise InterviewValidationError(str(exc)) from exc

    await record_audit(
        session,
        action="interview_transcript.preview",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_id),
        changes={
            "application_id": str(round_.application_id),
            "char_count": decoded.char_count,
            "segment_count": parsed.segment_count,
            "encoding": decoded.encoding,
            "sha256": decoded.sha256,
            "source_method": decoded.source_method,
        },
    )
    # Preview must not persist transcript rows; audit may flush but no commit of
    # transcript entities. Caller/API commits audit only.
    await session.commit()

    return TranscriptPreviewOut(
        encoding=decoded.encoding,
        sha256=decoded.sha256,
        char_count=decoded.char_count,
        segment_count=parsed.segment_count,
        matched_rules=list(parsed.matched_rules),
        source_method=decoded.source_method,
        filename=decoded.filename,
        size=decoded.size,
        mime=decoded.mime,
        segments=[
            TranscriptPreviewSegmentOut(
                segment_no=seg.segment_no,
                speaker_key=seg.speaker_key,
                speaker_name=seg.speaker_name,
                speaker_role=seg.speaker_role,
                start_time_ms=seg.start_time_ms,
                end_time_ms=seg.end_time_ms,
                text=seg.text,
                matched_rule=seg.matched_rule,
            )
            for seg in parsed.segments
        ],
    )


async def import_transcript(
    session: AsyncSession,
    *,
    round_id: UUID,
    actor: User,
    request_context: RequestContext,
    payload: TranscriptImportRequest,
    data: bytes | None = None,
) -> TranscriptVersionDetailOut:
    round_ = await _require_manage_round(session, round_id=round_id, actor=actor)
    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="transcript.import",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        existing = await get_transcript_by_round_id(session, round_id)
        if existing is None or existing.original_version_id is None:
            raise InterviewIdempotencyConflictError("idempotency conflict")
        version = await get_version_by_id(session, existing.original_version_id)
        if version is None:
            raise InterviewIdempotencyConflictError("idempotency conflict")
        return await _version_detail_out(version, round_id=round_id)

    if round_.status != INTERVIEW_STATUS_PENDING_TRANSCRIPT:
        raise InterviewValidationError(
            "transcript import requires PENDING_TRANSCRIPT status"
        )

    existing = await get_transcript_for_update_by_round(session, round_id)
    if existing is not None:
        # Duplicate import without matching idempotency must not create a second master.
        if existing.original_version_id is None:
            raise InterviewValidationError("transcript master already exists")
        version = await get_version_by_id(session, existing.original_version_id)
        if version is None:
            raise InterviewValidationError("original transcript version missing")
        await _store_idempotency(
            session,
            actor=actor,
            action="transcript.import",
            scope_id=round_id,
            key=payload.idempotency_key,
            request_payload=request_payload,
            round_id=round_id,
        )
        await session.commit()
        return await _version_detail_out(version, round_id=round_id)

    raw_bytes = data
    if raw_bytes is None:
        if not payload.raw_text:
            raise InterviewValidationError("raw_text or file bytes are required")
        raw_bytes = payload.raw_text.encode("utf-8")

    try:
        decoded = decode_transcript(raw_bytes, payload.filename)
        parsed = parse_transcript(decoded.text)
    except TranscriptParseError as exc:
        raise InterviewValidationError(str(exc)) from exc

    if decoded.sha256 != payload.source_sha256:
        raise InterviewValidationError("source_sha256 mismatch")

    parsed_by_no = {seg.segment_no: seg for seg in parsed.segments}
    transcript_id = uuid4()
    version_id = uuid4()
    now = _now()
    aggregate = _rebuild_aggregate([item.text for item in payload.segments])

    transcript = InterviewTranscript(
        id=transcript_id,
        interview_round_id=round_id,
        original_version_id=None,
        current_draft_version_id=None,
        current_confirmed_version_id=None,
        version=1,
        created_by=actor.id,
        created_at=now,
        updated_by=actor.id,
        updated_at=now,
    )
    version = InterviewTranscriptVersion(
        id=version_id,
        transcript_id=transcript_id,
        version_type=TranscriptVersionType.ORIGINAL.value,
        version_no=1,
        version_label="T1",
        status=TranscriptVersionStatus.IMMUTABLE.value,
        raw_text_encrypted=_encrypt_text(aggregate),
        source_method=decoded.source_method,
        source_filename=decoded.filename,
        source_size=decoded.size,
        source_mime=decoded.mime,
        source_encoding=decoded.encoding,
        source_sha256=decoded.sha256,
        based_on_version_id=None,
        created_by=actor.id,
        created_at=now,
        updated_by=actor.id,
        updated_at=now,
        version=1,
    )
    segments = _build_segments_from_import(
        version_id=version_id,
        segments=payload.segments,
        parsed_by_no=parsed_by_no,
    )
    version.segments = segments
    await add_transcript(session, transcript)
    await add_version(session, version)
    for segment in segments:
        await add_segment(session, segment)
    transcript.original_version_id = version_id
    transcript.updated_at = now
    transcript.updated_by = actor.id

    await _store_idempotency(
        session,
        actor=actor,
        action="transcript.import",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_id,
    )
    await record_audit(
        session,
        action="interview_transcript.import",
        result="success",
        resource_type="interview_transcript",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(transcript_id),
        changes={
            "application_id": str(round_.application_id),
            "round_id": str(round_id),
            "version_id": str(version_id),
            "version_label": "T1",
            "segment_count": len(segments),
            "char_count": decoded.char_count,
            "encoding": decoded.encoding,
            "sha256": decoded.sha256,
            "source_method": decoded.source_method,
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_version_by_id(session, version_id)
    assert loaded is not None
    return await _version_detail_out(loaded, round_id=round_id)


async def list_transcript_versions(
    session: AsyncSession,
    *,
    round_id: UUID,
    actor: User,
) -> TranscriptListOut:
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")

    can_manage = await _has_permission(actor, "recruitment.manage")
    assigned = await actor_assigned_to_round(
        session, round_id=round_id, user_id=actor.id
    )
    can_execute = await _has_permission(actor, "interview.execute")

    if can_manage:
        await _load_application(session, round_.application_id)
    elif can_execute and assigned:
        pass
    else:
        raise InterviewNotFoundError("interview round not found")

    transcript = await get_transcript_by_round_id(session, round_id)
    if transcript is None:
        return TranscriptListOut(transcript=None, versions=[])

    versions = await list_versions_for_transcript(session, transcript.id)
    if can_manage:
        return TranscriptListOut(
            transcript=_summary_out(transcript),
            versions=[_version_summary_out(item) for item in versions],
        )

    # Assigned interviewer: only current confirmed summary.
    confirmed_id = transcript.current_confirmed_version_id
    if confirmed_id is None:
        return TranscriptListOut(transcript=_summary_out(transcript), versions=[])
    confirmed = next((item for item in versions if item.id == confirmed_id), None)
    if confirmed is None:
        return TranscriptListOut(transcript=_summary_out(transcript), versions=[])
    return TranscriptListOut(
        transcript=_summary_out(transcript),
        versions=[_version_summary_out(confirmed)],
    )


async def get_transcript_version(
    session: AsyncSession,
    *,
    version_id: UUID,
    actor: User,
    request_context: RequestContext | None = None,
) -> TranscriptVersionDetailOut:
    version = await get_version_by_id(session, version_id)
    if version is None:
        raise InterviewNotFoundError("transcript version not found")
    transcript = version.transcript or await get_transcript_by_id(
        session, version.transcript_id
    )
    if transcript is None:
        raise InterviewNotFoundError("transcript version not found")
    round_ = await get_round_by_id(session, transcript.interview_round_id)
    if round_ is None:
        raise InterviewNotFoundError("transcript version not found")

    can_manage = await _has_permission(actor, "recruitment.manage")
    assigned = await actor_assigned_to_round(
        session, round_id=round_.id, user_id=actor.id
    )
    can_execute = await _has_permission(actor, "interview.execute")

    if can_manage:
        await _load_application(session, round_.application_id)
    elif can_execute and assigned:
        if transcript.current_confirmed_version_id != version.id:
            raise InterviewForbiddenError("forbidden")
    else:
        raise InterviewNotFoundError("transcript version not found")

    detail = await _version_detail_out(version, round_id=round_.id)
    if request_context is not None:
        await record_audit(
            session,
            action="interview_transcript.view",
            result="success",
            resource_type="interview_transcript_version",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(version.id),
            changes={
                "application_id": str(round_.application_id),
                "round_id": str(round_.id),
                "version_label": version.version_label,
                "version_type": version.version_type,
            },
        )
        await session.commit()
    return detail


async def create_transcript_draft(
    session: AsyncSession,
    *,
    transcript_id: UUID,
    actor: User,
    request_context: RequestContext,
    payload: DraftCreateRequest,
) -> TranscriptVersionDetailOut:
    await _require_manage(actor)
    transcript = await get_transcript_for_update(session, transcript_id)
    if transcript is None:
        raise InterviewNotFoundError("transcript not found")
    round_ = await get_round_for_update(session, transcript.interview_round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _load_application(session, round_.application_id)

    if round_.status not in {
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
        INTERVIEW_STATUS_COMPLETED,
    }:
        raise InterviewValidationError(
            "draft creation requires PENDING_TRANSCRIPT or COMPLETED status"
        )

    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="transcript.draft_create",
        scope_id=transcript_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )

    existing_draft = await get_editing_draft(session, transcript_id)
    if existing_draft is not None:
        if reused is None:
            await _store_idempotency(
                session,
                actor=actor,
                action="transcript.draft_create",
                scope_id=transcript_id,
                key=payload.idempotency_key,
                request_payload=request_payload,
                round_id=round_.id,
            )
            await session.commit()
        return await _version_detail_out(
            existing_draft, round_id=round_.id
        )

    if reused is not None:
        # Idempotent replay but draft missing — conflict.
        raise InterviewIdempotencyConflictError("idempotency conflict")

    if transcript.current_confirmed_version_id is not None:
        base_id = transcript.current_confirmed_version_id
    elif transcript.original_version_id is not None:
        base_id = transcript.original_version_id
    else:
        raise InterviewValidationError("no base version available for draft")

    base = await get_version_by_id(session, base_id)
    if base is None:
        raise InterviewValidationError("base version not found")
    base_rows = _decrypted_segment_dicts(base)
    draft_no = await next_version_no(
        session, transcript_id, TranscriptVersionType.DRAFT.value
    )
    draft_id = uuid4()
    now = _now()
    aggregate = _rebuild_aggregate([row["text"] for row in base_rows])
    draft = InterviewTranscriptVersion(
        id=draft_id,
        transcript_id=transcript_id,
        version_type=TranscriptVersionType.DRAFT.value,
        version_no=draft_no,
        version_label=_version_label(TranscriptVersionType.DRAFT.value, draft_no),
        status=TranscriptVersionStatus.EDITING.value,
        raw_text_encrypted=_encrypt_text(aggregate),
        source_method=base.source_method,
        source_filename=base.source_filename,
        source_size=base.source_size,
        source_mime=base.source_mime,
        source_encoding=base.source_encoding,
        source_sha256=base.source_sha256,
        based_on_version_id=base.id,
        created_by=actor.id,
        created_at=now,
        updated_by=actor.id,
        updated_at=now,
        version=1,
    )
    # Preserve source refs to base segment numbers for first draft from T1/Cn.
    for row in base_rows:
        if not row.get("source_segment_refs"):
            row["source_segment_refs"] = [row["segment_no"]]
            row["source_type"] = TranscriptSegmentSource.ORIGINAL.value
    segments = _copy_segments_to_version(
        target_version_id=draft_id, source_rows=base_rows
    )
    draft.segments = segments
    await add_version(session, draft)
    for segment in segments:
        await add_segment(session, segment)
    transcript.current_draft_version_id = draft_id
    transcript.version += 1
    transcript.updated_by = actor.id
    transcript.updated_at = now

    await _store_idempotency(
        session,
        actor=actor,
        action="transcript.draft_create",
        scope_id=transcript_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_transcript.draft_create",
        result="success",
        resource_type="interview_transcript_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(draft_id),
        changes={
            "application_id": str(round_.application_id),
            "round_id": str(round_.id),
            "transcript_id": str(transcript_id),
            "version_label": draft.version_label,
            "based_on_version_id": str(base.id),
            "segment_count": len(segments),
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_version_by_id(session, draft_id)
    assert loaded is not None
    return await _version_detail_out(loaded, round_id=round_.id)


async def save_transcript_draft(
    session: AsyncSession,
    *,
    draft_id: UUID,
    actor: User,
    request_context: RequestContext,
    payload: DraftSaveRequest,
) -> DraftSaveResponse:
    await _require_manage(actor)
    if payload.draft_version_id != draft_id:
        raise InterviewValidationError("draft_version_id mismatch")

    draft = await get_version_for_update(session, draft_id)
    if draft is None:
        raise InterviewNotFoundError("transcript draft not found")
    if (
        draft.version_type != TranscriptVersionType.DRAFT.value
        or draft.status != TranscriptVersionStatus.EDITING.value
    ):
        raise InterviewValidationError("only EDITING drafts can be saved")

    transcript = await get_transcript_for_update(session, draft.transcript_id)
    if transcript is None:
        raise InterviewNotFoundError("transcript not found")
    round_ = await get_round_for_update(session, transcript.interview_round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    await _load_application(session, round_.application_id)

    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="transcript.draft_save",
        scope_id=draft_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        loaded = await get_version_by_id(session, draft_id)
        assert loaded is not None
        detail = await _version_detail_out(loaded, round_id=round_.id)
        return DraftSaveResponse(
            version=detail, change_counts=ChangeCountsOut()
        )

    if draft.version != payload.version:
        raise InterviewOptimisticLockError(DRAFT_OPTIMISTIC_LOCK_MESSAGE)

    previous_rows = _decrypted_segment_dicts(draft)
    base_rows: list[dict[str, Any]] = []
    if draft.based_on_version_id is not None:
        base = await get_version_by_id(session, draft.based_on_version_id)
        if base is not None:
            base_rows = _decrypted_segment_dicts(base)
    base_by_no = {row["segment_no"]: row for row in base_rows}

    derived_types: list[str] = []
    new_segments: list[InterviewTranscriptSegment] = []
    texts: list[str] = []
    for index, item in enumerate(payload.segments, start=1):
        source_type = _derive_draft_source_type(item, base_by_no)
        derived_types.append(source_type)
        texts.append(item.text)
        new_segments.append(
            InterviewTranscriptSegment(
                id=uuid4(),
                transcript_version_id=draft_id,
                segment_no=index,
                speaker_key=item.speaker_key,
                speaker_name=item.speaker_name,
                speaker_role=item.speaker_role,
                start_time_ms=item.start_time_ms,
                end_time_ms=item.end_time_ms,
                text_encrypted=_encrypt_text(item.text),
                source_type=source_type,
                source_segment_refs=list(item.source_segment_refs or []),
                is_included_in_analysis=item.is_included_in_analysis,
                is_unclear=item.is_unclear,
            )
        )

    change_counts = _compute_change_counts(
        previous=previous_rows,
        incoming=payload.segments,
        derived_types=derived_types,
    )
    await replace_segments(session, draft, new_segments)
    draft.raw_text_encrypted = _encrypt_text(_rebuild_aggregate(texts))
    draft.version += 1
    draft.updated_by = actor.id
    draft.updated_at = _now()
    transcript.version += 1
    transcript.updated_by = actor.id
    transcript.updated_at = draft.updated_at

    await _store_idempotency(
        session,
        actor=actor,
        action="transcript.draft_save",
        scope_id=draft_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_.id,
    )
    await record_audit(
        session,
        action="interview_transcript.draft_save",
        result="success",
        resource_type="interview_transcript_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(draft_id),
        changes={
            "application_id": str(round_.application_id),
            "round_id": str(round_.id),
            "transcript_id": str(transcript.id),
            "version_label": draft.version_label,
            "version": draft.version,
            "segment_count": len(new_segments),
            "change_counts": change_counts.model_dump(),
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    loaded = await get_version_by_id(session, draft_id)
    assert loaded is not None
    detail = await _version_detail_out(loaded, round_id=round_.id)
    return DraftSaveResponse(version=detail, change_counts=change_counts)


async def confirm_transcript_draft(
    session: AsyncSession,
    *,
    draft_id: UUID,
    actor: User,
    request_context: RequestContext,
    payload: ConfirmRequest,
) -> TranscriptVersionDetailOut:
    await _require_manage(actor)
    application_status_before = None
    try:
        draft = await get_version_for_update(session, draft_id)
        if draft is None:
            raise InterviewNotFoundError("transcript draft not found")
        if (
            draft.version_type != TranscriptVersionType.DRAFT.value
            or draft.status != TranscriptVersionStatus.EDITING.value
        ):
            # Idempotent: if already confirmed, return current confirmed when key matches.
            transcript_probe = await get_transcript_by_id(session, draft.transcript_id)
            if (
                transcript_probe is not None
                and transcript_probe.current_confirmed_version_id is not None
            ):
                request_payload = payload.model_dump(mode="json")
                reused = await _consume_idempotency(
                    session,
                    actor=actor,
                    action="transcript.confirm",
                    scope_id=draft_id,
                    key=payload.idempotency_key,
                    request_payload=request_payload,
                )
                if reused is not None:
                    confirmed = await get_version_by_id(
                        session, transcript_probe.current_confirmed_version_id
                    )
                    if confirmed is not None:
                        round_probe = await get_round_by_id(
                            session, transcript_probe.interview_round_id
                        )
                        assert round_probe is not None
                        return await _version_detail_out(
                            confirmed, round_id=round_probe.id
                        )
            raise InterviewValidationError("only EDITING drafts can be confirmed")

        transcript = await get_transcript_for_update(session, draft.transcript_id)
        if transcript is None:
            raise InterviewNotFoundError("transcript not found")
        round_ = await get_round_for_update(session, transcript.interview_round_id)
        if round_ is None:
            raise InterviewNotFoundError("interview round not found")
        application = await _load_application(session, round_.application_id)
        application_status_before = (
            application.pipeline_status,
            application.status,
        )

        request_payload = payload.model_dump(mode="json")
        reused = await _consume_idempotency(
            session,
            actor=actor,
            action="transcript.confirm",
            scope_id=draft_id,
            key=payload.idempotency_key,
            request_payload=request_payload,
        )
        if reused is not None and transcript.current_confirmed_version_id:
            confirmed = await get_version_by_id(
                session, transcript.current_confirmed_version_id
            )
            if confirmed is not None:
                return await _version_detail_out(confirmed, round_id=round_.id)

        if draft.version != payload.version:
            raise InterviewOptimisticLockError(DRAFT_OPTIMISTIC_LOCK_MESSAGE)

        rows = _decrypted_segment_dicts(draft)
        _validate_confirm_segments(rows)

        now = _now()
        confirmed_no = await next_version_no(
            session, transcript.id, TranscriptVersionType.CONFIRMED.value
        )
        confirmed_id = uuid4()
        aggregate = _rebuild_aggregate([row["text"] for row in rows])
        # Freeze draft
        draft.status = TranscriptVersionStatus.IMMUTABLE.value
        draft.updated_by = actor.id
        draft.updated_at = now

        confirmed = InterviewTranscriptVersion(
            id=confirmed_id,
            transcript_id=transcript.id,
            version_type=TranscriptVersionType.CONFIRMED.value,
            version_no=confirmed_no,
            version_label=_version_label(
                TranscriptVersionType.CONFIRMED.value, confirmed_no
            ),
            status=TranscriptVersionStatus.IMMUTABLE.value,
            raw_text_encrypted=_encrypt_text(aggregate),
            source_method=draft.source_method,
            source_filename=draft.source_filename,
            source_size=draft.source_size,
            source_mime=draft.source_mime,
            source_encoding=draft.source_encoding,
            source_sha256=draft.source_sha256,
            based_on_version_id=draft.id,
            confirmed_by=actor.id,
            confirmed_at=now,
            created_by=actor.id,
            created_at=now,
            updated_by=actor.id,
            updated_at=now,
            version=1,
        )
        confirmed_segments = _copy_segments_to_version(
            target_version_id=confirmed_id, source_rows=rows
        )
        confirmed.segments = confirmed_segments
        await add_version(session, confirmed)
        for segment in confirmed_segments:
            await add_segment(session, segment)

        transcript.current_confirmed_version_id = confirmed_id
        transcript.current_draft_version_id = None
        transcript.version += 1
        transcript.updated_by = actor.id
        transcript.updated_at = now

        status_before = round_.status
        if round_.status == INTERVIEW_STATUS_PENDING_TRANSCRIPT:
            round_.status = next_status(round_.status, "complete")
            round_.version += 1
            round_.updated_by = actor.id
            round_.updated_at = now
        round_.transcript_completion_mode = (
            TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value
        )
        round_.transcript_completion_reason_code = None
        round_.transcript_completion_reason_description = None
        round_.transcript_completed_by = actor.id
        round_.transcript_completed_at = now

        application_after = await _load_application(session, round_.application_id)
        if (
            application_after.pipeline_status,
            application_after.status,
        ) != application_status_before:
            raise InterviewValidationError(
                "confirm must not change application decision"
            )

        await _store_idempotency(
            session,
            actor=actor,
            action="transcript.confirm",
            scope_id=draft_id,
            key=payload.idempotency_key,
            request_payload=request_payload,
            round_id=round_.id,
        )
        await record_audit(
            session,
            action="interview_transcript.confirm",
            result="success",
            resource_type="interview_transcript_version",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(confirmed_id),
            changes={
                "application_id": str(round_.application_id),
                "round_id": str(round_.id),
                "draft_id": str(draft_id),
                "version_label": confirmed.version_label,
                "segment_count": len(confirmed_segments),
                "round_status_before": status_before,
                "round_status_after": round_.status,
                "idempotency_key": payload.idempotency_key,
            },
        )
        await session.commit()
        loaded = await get_version_by_id(session, confirmed_id)
        assert loaded is not None
        return await _version_detail_out(loaded, round_id=round_.id)
    except Exception:
        await session.rollback()
        raise


async def complete_without_transcript(
    session: AsyncSession,
    *,
    round_id: UUID,
    actor: User,
    request_context: RequestContext,
    payload: CompleteWithoutTranscriptRequest,
) -> CompleteWithoutTranscriptOut:
    round_ = await _require_manage_round(session, round_id=round_id, actor=actor)
    if payload.reason_code not in _REASON_CODES:
        raise InterviewValidationError("invalid reason_code")
    if payload.reason_code == TRANSCRIPT_REASON_OTHER and not (
        payload.description or ""
    ).strip():
        raise InterviewValidationError("description is required when reason_code is OTHER")

    request_payload = payload.model_dump(mode="json")
    reused = await _consume_idempotency(
        session,
        actor=actor,
        action="transcript.complete_without",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if reused is not None:
        loaded = await get_round_by_id(session, round_id)
        assert loaded is not None
        return CompleteWithoutTranscriptOut(
            round_id=loaded.id,
            status=loaded.status,
            version=loaded.version,
            transcript_completion_mode=loaded.transcript_completion_mode
            or TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value,
            transcript_completion_reason_code=loaded.transcript_completion_reason_code
            or payload.reason_code,
            transcript_completion_reason_description=(
                loaded.transcript_completion_reason_description
            ),
            transcript_completed_by=loaded.transcript_completed_by or actor.id,
            transcript_completed_at=loaded.transcript_completed_at or _now(),
        )

    if round_.status != INTERVIEW_STATUS_PENDING_TRANSCRIPT:
        raise InterviewValidationError(
            "complete-without-transcript requires PENDING_TRANSCRIPT status"
        )
    if round_.version != payload.version:
        raise InterviewOptimisticLockError(
            "面试信息已被其他人员更新，请刷新后重试"
        )

    existing = await get_transcript_for_update_by_round(session, round_id)
    if existing is not None:
        raise InterviewValidationError(
            "cannot complete without transcript when transcript master exists"
        )

    application = await _load_application(session, round_.application_id)
    application_status_before = (application.pipeline_status, application.status)

    now = _now()
    round_.status = next_status(round_.status, "complete")
    round_.transcript_completion_mode = (
        TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value
    )
    round_.transcript_completion_reason_code = payload.reason_code
    round_.transcript_completion_reason_description = payload.description
    round_.transcript_completed_by = actor.id
    round_.transcript_completed_at = now
    round_.version += 1
    round_.updated_by = actor.id
    round_.updated_at = now

    application_after = await _load_application(session, round_.application_id)
    if (
        application_after.pipeline_status,
        application_after.status,
    ) != application_status_before:
        raise InterviewValidationError(
            "complete must not change application decision"
        )

    await _store_idempotency(
        session,
        actor=actor,
        action="transcript.complete_without",
        scope_id=round_id,
        key=payload.idempotency_key,
        request_payload=request_payload,
        round_id=round_id,
    )
    await record_audit(
        session,
        action="interview_transcript.complete_without_transcript",
        result="success",
        resource_type="interview_round",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(round_id),
        changes={
            "application_id": str(round_.application_id),
            "reason_code": payload.reason_code,
            "has_description": bool((payload.description or "").strip()),
            "idempotency_key": payload.idempotency_key,
            "after_status": round_.status,
        },
    )
    await session.commit()
    return CompleteWithoutTranscriptOut(
        round_id=round_.id,
        status=round_.status,
        version=round_.version,
        transcript_completion_mode=round_.transcript_completion_mode,
        transcript_completion_reason_code=round_.transcript_completion_reason_code,
        transcript_completion_reason_description=(
            round_.transcript_completion_reason_description
        ),
        transcript_completed_by=round_.transcript_completed_by,
        transcript_completed_at=round_.transcript_completed_at,
    )


__all__ = [
    "DRAFT_OPTIMISTIC_LOCK_MESSAGE",
    "complete_without_transcript",
    "confirm_transcript_draft",
    "create_transcript_draft",
    "get_transcript_version",
    "import_transcript",
    "list_transcript_reason_codes",
    "list_transcript_versions",
    "preview_transcript",
    "save_transcript_draft",
]
