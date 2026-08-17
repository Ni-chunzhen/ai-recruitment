"""Service-layer tests for interview transcript workflow."""

from __future__ import annotations

import inspect
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import (
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
)
from app.models.interview_transcript import (
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
    TranscriptCompletionMode,
    TranscriptSegmentSource,
    TranscriptVersionStatus,
    TranscriptVersionType,
)
from app.models.resume import PIPELINE_INTERVIEWING
from app.schemas.interview import InterviewRoundActionRequest
from app.schemas.interview_transcript import (
    CompleteWithoutTranscriptRequest,
    ConfirmRequest,
    DraftCreateRequest,
    DraftSaveRequest,
    TranscriptImportRequest,
)
from app.services.audit import RequestContext
from app.services.interviews import (
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
    complete_interview_round,
)
from app.services.interview_transcripts import (
    DRAFT_OPTIMISTIC_LOCK_MESSAGE,
    complete_without_transcript,
    confirm_transcript_draft,
    create_transcript_draft,
    get_transcript_version,
    import_transcript,
    list_transcript_reason_codes,
    list_transcript_versions,
    preview_transcript,
    save_transcript_draft,
)
from app.services.transcript_parser import decode_transcript, parse_transcript


def _actor(*, manage: bool = True, execute: bool = True, user_id=None):
    user = SimpleNamespace(
        id=user_id or uuid4(),
        username="hr",
        display_name="HR",
        roles=[],
    )
    codes = []
    if manage:
        codes.append("recruitment.manage")
    if execute:
        codes.append("interview.execute")
    user.permission_codes = codes
    return user


def _ctx() -> RequestContext:
    return RequestContext(request_id="req-1", ip_address="127.0.0.1")


def _application():
    return SimpleNamespace(
        id=uuid4(),
        candidate_id=uuid4(),
        candidate=SimpleNamespace(id=uuid4(), name="张三"),
        job_id=uuid4(),
        job_version_id=uuid4(),
        pipeline_status=PIPELINE_INTERVIEWING,
        status=APPLICATION_STATUS_IN_PROGRESS,
        lock_version=1,
    )


def _now() -> datetime:
    return datetime(2026, 8, 16, 2, 0, tzinfo=UTC)


def _make_round(*, status: str = INTERVIEW_STATUS_PENDING_TRANSCRIPT) -> InterviewRound:
    now = _now()
    round_ = InterviewRound(
        id=uuid4(),
        application_id=uuid4(),
        job_version_id=uuid4(),
        name="第一轮专业面",
        sequence_no=1,
        status=status,
        format="ONLINE",
        owner_id=uuid4(),
        version=1,
        created_at=now,
        updated_at=now,
    )
    round_.interviewers = [
        InterviewRoundInterviewer(
            interviewer_id=uuid4(),
            is_primary=True,
            created_by=round_.owner_id,
        )
    ]
    round_.schedules = []
    return round_


SAMPLE_TEXT = "面试官：请介绍自己\n候选人：我有五年经验\n"


def _sample_bytes() -> bytes:
    return SAMPLE_TEXT.encode("utf-8")


def _import_payload(*, text: str = SAMPLE_TEXT, corrected: bool = False, **overrides):
    decoded = decode_transcript(text.encode("utf-8"))
    parsed = parse_transcript(decoded.text)
    segments = []
    for seg in parsed.segments:
        item = {
            "speaker_key": seg.speaker_key,
            "speaker_name": seg.speaker_name,
            "speaker_role": seg.speaker_role,
            "text": ("修正后的" + seg.text) if corrected else seg.text,
            "start_time_ms": seg.start_time_ms,
            "end_time_ms": seg.end_time_ms,
            "is_unclear": False,
            "is_included_in_analysis": True,
            "source_segment_refs": [seg.segment_no],
        }
        segments.append(item)
    data = {
        "idempotency_key": "import-1",
        "source_method": "PASTE",
        "raw_text": text,
        "source_sha256": decoded.sha256,
        "segments": segments,
    }
    data.update(overrides)
    return TranscriptImportRequest.model_validate(data)


def _patch_base(
    monkeypatch: pytest.MonkeyPatch,
    round_: InterviewRound,
    *,
    application=None,
    existing_idempotency=None,
    transcript=None,
    versions=None,
    assigned: bool = True,
):
    application = application or _application()
    application.id = round_.application_id
    audits: list[dict] = []
    added_objects: list[object] = []
    added_idempotency: list[InterviewIdempotencyKey] = []
    versions = versions if versions is not None else []

    async def fake_record_audit(_session, **kwargs):
        audits.append(kwargs)

    async def fake_add_idempotency(_session, key):
        added_idempotency.append(key)
        return key

    async def fake_add_transcript(_session, transcript_obj):
        added_objects.append(transcript_obj)
        if getattr(transcript_obj, "versions", None) is None:
            transcript_obj.versions = []
        return transcript_obj

    async def fake_add_version(_session, version):
        added_objects.append(version)
        versions.append(version)
        if version.segments is None:
            version.segments = []
        return version

    async def fake_add_segment(_session, segment):
        added_objects.append(segment)
        return segment

    async def fake_replace_segments(_session, version, segments):
        version.segments = list(segments)

    async def fake_get_version_by_id(_session, version_id):
        for item in versions:
            if item.id == version_id:
                return item
        return None

    async def fake_get_version_for_update(_session, version_id):
        return await fake_get_version_by_id(_session, version_id)

    async def fake_list_versions(_session, transcript_id):
        return [item for item in versions if item.transcript_id == transcript_id]

    async def fake_get_editing_draft(_session, transcript_id):
        for item in versions:
            if (
                item.transcript_id == transcript_id
                and item.version_type == TranscriptVersionType.DRAFT.value
                and item.status == TranscriptVersionStatus.EDITING.value
            ):
                return item
        return None

    async def fake_next_version_no(_session, transcript_id, version_type):
        nos = [
            item.version_no
            for item in versions
            if item.transcript_id == transcript_id
            and item.version_type == version_type
        ]
        return (max(nos) if nos else 0) + 1

    module = "app.services.interview_transcripts"
    monkeypatch.setattr(f"{module}.get_round_for_update", AsyncMock(return_value=round_))
    monkeypatch.setattr(f"{module}.get_round_by_id", AsyncMock(return_value=round_))
    monkeypatch.setattr(
        f"{module}.get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(
        f"{module}.find_idempotency", AsyncMock(return_value=existing_idempotency)
    )
    monkeypatch.setattr(f"{module}.add_idempotency", fake_add_idempotency)
    monkeypatch.setattr(f"{module}.record_audit", fake_record_audit)
    monkeypatch.setattr(
        f"{module}.get_transcript_by_round_id",
        AsyncMock(return_value=transcript),
    )
    monkeypatch.setattr(
        f"{module}.get_transcript_for_update_by_round",
        AsyncMock(return_value=transcript),
    )
    monkeypatch.setattr(
        f"{module}.get_transcript_by_id",
        AsyncMock(
            side_effect=lambda _s, tid: transcript
            if transcript is not None and transcript.id == tid
            else None
        ),
    )
    monkeypatch.setattr(
        f"{module}.get_transcript_for_update",
        AsyncMock(
            side_effect=lambda _s, tid: transcript
            if transcript is not None and transcript.id == tid
            else None
        ),
    )
    monkeypatch.setattr(f"{module}.add_transcript", fake_add_transcript)
    monkeypatch.setattr(f"{module}.add_version", fake_add_version)
    monkeypatch.setattr(f"{module}.add_segment", fake_add_segment)
    monkeypatch.setattr(f"{module}.replace_segments", fake_replace_segments)
    monkeypatch.setattr(f"{module}.get_version_by_id", fake_get_version_by_id)
    monkeypatch.setattr(f"{module}.get_version_for_update", fake_get_version_for_update)
    monkeypatch.setattr(f"{module}.list_versions_for_transcript", fake_list_versions)
    monkeypatch.setattr(f"{module}.get_editing_draft", fake_get_editing_draft)
    monkeypatch.setattr(f"{module}.next_version_no", fake_next_version_no)
    monkeypatch.setattr(
        f"{module}.actor_assigned_to_round",
        AsyncMock(return_value=assigned),
    )
    session = AsyncMock()
    return session, application, audits, added_objects, added_idempotency, versions


def _attach_transcript(
    round_: InterviewRound,
    *,
    with_t1: bool = True,
    with_draft: bool = False,
    with_confirmed: bool = False,
):
    now = _now()
    transcript = InterviewTranscript(
        id=uuid4(),
        interview_round_id=round_.id,
        version=1,
        created_by=round_.owner_id,
        created_at=now,
        updated_by=round_.owner_id,
        updated_at=now,
    )
    versions: list[InterviewTranscriptVersion] = []
    if with_t1:
        t1 = InterviewTranscriptVersion(
            id=uuid4(),
            transcript_id=transcript.id,
            version_type=TranscriptVersionType.ORIGINAL.value,
            version_no=1,
            version_label="T1",
            status=TranscriptVersionStatus.IMMUTABLE.value,
            raw_text_encrypted="",  # filled below
            source_method="PASTE",
            source_filename=None,
            source_size=len(_sample_bytes()),
            source_mime="text/plain",
            source_encoding="utf-8",
            source_sha256=decode_transcript(_sample_bytes()).sha256,
            created_by=round_.owner_id,
            created_at=now,
            updated_by=round_.owner_id,
            updated_at=now,
            version=1,
        )
        from app.services.interview_transcripts import _encrypt_text

        parsed = parse_transcript(SAMPLE_TEXT)
        segs = []
        for seg in parsed.segments:
            segs.append(
                InterviewTranscriptSegment(
                    id=uuid4(),
                    transcript_version_id=t1.id,
                    segment_no=seg.segment_no,
                    speaker_key=seg.speaker_key,
                    speaker_name=seg.speaker_name,
                    speaker_role=seg.speaker_role,
                    start_time_ms=seg.start_time_ms,
                    end_time_ms=seg.end_time_ms,
                    text_encrypted=_encrypt_text(seg.text),
                    source_type=TranscriptSegmentSource.ORIGINAL.value,
                    source_segment_refs=[seg.segment_no],
                    is_included_in_analysis=True,
                    is_unclear=False,
                )
            )
        t1.raw_text_encrypted = _encrypt_text(
            "\n".join(seg.text for seg in parsed.segments)
        )
        t1.segments = segs
        t1.transcript = transcript
        transcript.original_version_id = t1.id
        versions.append(t1)

    if with_draft and versions:
        base = versions[0]
        from app.services.interview_transcripts import _encrypt_text

        draft = InterviewTranscriptVersion(
            id=uuid4(),
            transcript_id=transcript.id,
            version_type=TranscriptVersionType.DRAFT.value,
            version_no=1,
            version_label="D1",
            status=TranscriptVersionStatus.EDITING.value,
            raw_text_encrypted=base.raw_text_encrypted,
            source_method=base.source_method,
            source_filename=base.source_filename,
            source_size=base.source_size,
            source_mime=base.source_mime,
            source_encoding=base.source_encoding,
            source_sha256=base.source_sha256,
            based_on_version_id=base.id,
            created_by=round_.owner_id,
            created_at=now,
            updated_by=round_.owner_id,
            updated_at=now,
            version=1,
        )
        draft_segs = []
        for seg in base.segments:
            draft_segs.append(
                InterviewTranscriptSegment(
                    id=uuid4(),
                    transcript_version_id=draft.id,
                    segment_no=seg.segment_no,
                    speaker_key=seg.speaker_key,
                    speaker_name=seg.speaker_name,
                    speaker_role=seg.speaker_role,
                    start_time_ms=seg.start_time_ms,
                    end_time_ms=seg.end_time_ms,
                    text_encrypted=seg.text_encrypted,
                    source_type=seg.source_type,
                    source_segment_refs=list(seg.source_segment_refs or []),
                    is_included_in_analysis=seg.is_included_in_analysis,
                    is_unclear=seg.is_unclear,
                )
            )
        draft.segments = draft_segs
        draft.transcript = transcript
        transcript.current_draft_version_id = draft.id
        versions.append(draft)

    if with_confirmed and versions:
        base = versions[-1]
        from app.services.interview_transcripts import _encrypt_text

        confirmed = InterviewTranscriptVersion(
            id=uuid4(),
            transcript_id=transcript.id,
            version_type=TranscriptVersionType.CONFIRMED.value,
            version_no=1,
            version_label="C1",
            status=TranscriptVersionStatus.IMMUTABLE.value,
            raw_text_encrypted=base.raw_text_encrypted,
            source_method=base.source_method,
            source_filename=base.source_filename,
            source_size=base.source_size,
            source_mime=base.source_mime,
            source_encoding=base.source_encoding,
            source_sha256=base.source_sha256,
            based_on_version_id=base.id,
            confirmed_by=round_.owner_id,
            confirmed_at=now,
            created_by=round_.owner_id,
            created_at=now,
            updated_by=round_.owner_id,
            updated_at=now,
            version=1,
        )
        conf_segs = []
        for seg in base.segments:
            conf_segs.append(
                InterviewTranscriptSegment(
                    id=uuid4(),
                    transcript_version_id=confirmed.id,
                    segment_no=seg.segment_no,
                    speaker_key=seg.speaker_key,
                    speaker_name=seg.speaker_name,
                    speaker_role=seg.speaker_role,
                    start_time_ms=seg.start_time_ms,
                    end_time_ms=seg.end_time_ms,
                    text_encrypted=seg.text_encrypted,
                    source_type=seg.source_type,
                    source_segment_refs=list(seg.source_segment_refs or []),
                    is_included_in_analysis=seg.is_included_in_analysis,
                    is_unclear=seg.is_unclear,
                )
            )
        confirmed.segments = conf_segs
        confirmed.transcript = transcript
        transcript.current_confirmed_version_id = confirmed.id
        versions.append(confirmed)

    transcript.versions = versions
    return transcript, versions


_FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "raw_text",
        "text",
        "segment_text",
        "segments",
        "ciphertext",
        "text_encrypted",
        "raw_text_encrypted",
        "key",
        "encryption_key",
        "data_encryption_key",
        "fernet",
        "plaintext",
    }
)


def _assert_audit_changes_safe(changes: dict) -> None:
    keys = set(changes.keys())
    assert keys.isdisjoint(_FORBIDDEN_AUDIT_KEYS)
    blob = str(changes).lower()
    assert "enc:v1:" not in blob
    assert "请介绍自己" not in str(changes)
    assert "五年经验" not in str(changes)
    assert "ciphertext" not in blob
    assert "raw_text" not in keys


@pytest.mark.asyncio
async def test_preview_writes_no_transcript_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    session, _app, audits, added, _keys, _versions = _patch_base(monkeypatch, round_)
    result = await preview_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        data=_sample_bytes(),
        filename=None,
    )
    assert result.segment_count >= 2
    assert result.sha256
    assert "enc:v1:" not in result.segments[0].text
    assert audits[0]["action"] == "interview_transcript.preview"
    _assert_audit_changes_safe(audits[0]["changes"])
    assert not any(isinstance(obj, InterviewTranscript) for obj in added)
    assert not any(isinstance(obj, InterviewTranscriptVersion) for obj in added)


@pytest.mark.asyncio
async def test_preview_creates_no_temp_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview/decode must stay in-memory; no tempfile or disk open in the path."""
    from app.services import transcript_parser as parser_mod

    preview_src = inspect.getsource(preview_transcript)
    decode_src = inspect.getsource(parser_mod.decode_transcript)
    combined = preview_src + "\n" + decode_src
    assert "NamedTemporaryFile" not in combined
    assert "TemporaryFile" not in combined
    assert "mkstemp" not in combined
    assert "tempfile." not in combined

    round_ = _make_round()
    session, *_ = _patch_base(monkeypatch, round_)

    def _forbid_open(*_a, **_k):
        raise AssertionError("open() must not be used in preview path")

    def _forbid_named_temp(*_a, **_k):
        raise AssertionError("NamedTemporaryFile must not be used in preview path")

    monkeypatch.setattr("builtins.open", _forbid_open)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _forbid_named_temp)
    temp_root = Path(tempfile.gettempdir())
    before_names = {p.name for p in temp_root.iterdir()} if temp_root.exists() else set()
    result = await preview_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        data=_sample_bytes(),
        filename=None,
    )
    assert result.segment_count >= 2
    after_names = {p.name for p in temp_root.iterdir()} if temp_root.exists() else set()
    created = after_names - before_names
    assert not any("transcript" in name.lower() for name in created)


@pytest.mark.asyncio
async def test_preview_rejects_non_pending_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    session, *_ = _patch_base(monkeypatch, round_)
    with pytest.raises(InterviewValidationError, match="PENDING_TRANSCRIPT"):
        await preview_transcript(
            session,
            round_id=round_.id,
            actor=_actor(),
            request_context=_ctx(),
            data=_sample_bytes(),
            filename=None,
        )


@pytest.mark.asyncio
async def test_import_encrypts_and_creates_immutable_t1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    session, _app, audits, added, keys, versions = _patch_base(monkeypatch, round_)
    # After import, get_transcript_by_round_id still returns None until we wire it;
    # import creates objects via add_* fakes. Update get_version after add.
    result = await import_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=_import_payload(),
    )
    assert result.version_label == "T1"
    assert result.status == TranscriptVersionStatus.IMMUTABLE.value
    assert result.version_type == TranscriptVersionType.ORIGINAL.value
    assert all(not seg.text.startswith("enc:v1:") for seg in result.segments)
    assert "enc:v1:" not in result.raw_text
    assert any(isinstance(obj, InterviewTranscript) for obj in added)
    t1 = next(
        obj
        for obj in added
        if isinstance(obj, InterviewTranscriptVersion)
        and obj.version_label == "T1"
    )
    assert t1.raw_text_encrypted.startswith("enc:v1:")
    assert all(
        isinstance(obj, InterviewTranscriptSegment)
        and obj.text_encrypted.startswith("enc:v1:")
        for obj in added
        if isinstance(obj, InterviewTranscriptSegment)
    )
    assert keys[0].action == "transcript.import"
    assert audits[0]["action"] == "interview_transcript.import"
    assert "请介绍自己" not in str(audits[0]["changes"])


@pytest.mark.asyncio
async def test_import_corrected_segment_not_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    session, *_rest = _patch_base(monkeypatch, round_)
    result = await import_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=_import_payload(corrected=True),
    )
    assert all(
        seg.source_type == TranscriptSegmentSource.CORRECTED.value
        for seg in result.segments
    )


@pytest.mark.asyncio
async def test_import_manual_addition_without_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    session, *_rest = _patch_base(monkeypatch, round_)
    payload = _import_payload()
    payload.segments.append(
        payload.segments[0].model_copy(
            update={
                "text": "补充遗漏内容",
                "source_segment_refs": [],
                "speaker_key": "other",
                "speaker_name": "其他",
                "speaker_role": "OTHER",
            }
        )
    )
    result = await import_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=payload,
    )
    assert result.segments[-1].source_type == TranscriptSegmentSource.MANUAL_ADDITION.value


@pytest.mark.asyncio
async def test_import_idempotent_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_)
    existing = InterviewIdempotencyKey(
        actor_id=uuid4(),
        action="transcript.import",
        scope_id=round_.id,
        idempotency_key="import-1",
        request_hash="",  # filled after payload
        result_round_id=round_.id,
    )
    payload = _import_payload()
    from app.services.interview_transcripts import _canonical_hash

    existing.request_hash = _canonical_hash(payload.model_dump(mode="json"))
    actor = _actor(user_id=existing.actor_id)
    session, *_rest = _patch_base(
        monkeypatch,
        round_,
        transcript=transcript,
        versions=versions,
        existing_idempotency=existing,
    )
    result = await import_transcript(
        session,
        round_id=round_.id,
        actor=actor,
        request_context=_ctx(),
        payload=payload,
    )
    assert result.version_label == "T1"
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_same_key_different_body_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    existing = InterviewIdempotencyKey(
        actor_id=uuid4(),
        action="transcript.import",
        scope_id=round_.id,
        idempotency_key="import-1",
        request_hash="different-hash",
        result_round_id=round_.id,
    )
    session, *_rest = _patch_base(
        monkeypatch, round_, existing_idempotency=existing
    )
    with pytest.raises(InterviewIdempotencyConflictError):
        await import_transcript(
            session,
            round_id=round_.id,
            actor=_actor(user_id=existing.actor_id),
            request_context=_ctx(),
            payload=_import_payload(),
        )


@pytest.mark.asyncio
async def test_duplicate_import_does_not_create_second_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_)
    session, _app, _audits, added, _keys, _versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await import_transcript(
        session,
        round_id=round_.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=_import_payload(idempotency_key="import-dup"),
    )
    assert result.version_label == "T1"
    assert not any(isinstance(obj, InterviewTranscript) for obj in added)


@pytest.mark.asyncio
async def test_t1_cannot_be_saved_as_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_)
    t1 = versions[0]
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    with pytest.raises(InterviewValidationError, match="EDITING drafts"):
        await save_transcript_draft(
            session,
            draft_id=t1.id,
            actor=_actor(),
            request_context=_ctx(),
            payload=DraftSaveRequest.model_validate(
                {
                    "draft_version_id": str(t1.id),
                    "version": 1,
                    "idempotency_key": "save-t1",
                    "segments": [
                        {
                            "speaker_key": "interviewer",
                            "speaker_name": "面试官",
                            "speaker_role": "INTERVIEWER",
                            "text": "x",
                            "source_segment_refs": [1],
                        }
                    ],
                }
            ),
        )


@pytest.mark.asyncio
async def test_t1_and_confirmed_immutable_cannot_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(round_, with_confirmed=True)
    t1 = next(item for item in versions if item.version_label == "T1")
    c1 = next(item for item in versions if item.version_label == "C1")
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    for version_id, key in ((t1.id, "save-imm-t1"), (c1.id, "save-imm-c1")):
        with pytest.raises(InterviewValidationError, match="EDITING drafts"):
            await save_transcript_draft(
                session,
                draft_id=version_id,
                actor=_actor(),
                request_context=_ctx(),
                payload=DraftSaveRequest.model_validate(
                    {
                        "draft_version_id": str(version_id),
                        "version": 1,
                        "idempotency_key": key,
                        "segments": [
                            {
                                "speaker_key": "interviewer",
                                "speaker_name": "面试官",
                                "speaker_role": "INTERVIEWER",
                                "text": "x",
                                "source_segment_refs": [1],
                            }
                        ],
                    }
                ),
            )
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_draft_d1_from_t1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_)
    session, _app, audits, added, _keys, all_versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await create_transcript_draft(
        session,
        transcript_id=transcript.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=DraftCreateRequest.model_validate({"idempotency_key": "draft-1"}),
    )
    assert result.version_label == "D1"
    assert result.status == TranscriptVersionStatus.EDITING.value
    assert transcript.current_draft_version_id == result.id
    assert audits[0]["action"] == "interview_transcript.draft_create"


@pytest.mark.asyncio
async def test_duplicate_draft_create_returns_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_, with_draft=True)
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await create_transcript_draft(
        session,
        transcript_id=transcript.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=DraftCreateRequest.model_validate({"idempotency_key": "draft-2"}),
    )
    assert result.version_label == "D1"
    assert result.id == transcript.current_draft_version_id


@pytest.mark.asyncio
async def test_draft_from_confirmed_creates_next_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(
        round_, with_draft=False, with_confirmed=True
    )
    # Freeze any leftover draft markers
    transcript.current_draft_version_id = None
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await create_transcript_draft(
        session,
        transcript_id=transcript.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=DraftCreateRequest.model_validate({"idempotency_key": "draft-c"}),
    )
    assert result.version_label == "D1"
    assert result.based_on_version_id == transcript.current_confirmed_version_id


@pytest.mark.asyncio
async def test_save_draft_merge_split_delete_manual_and_source_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(
        item for item in versions if item.version_label == "D1"
    )
    session, _app, audits, _added, _keys, _versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    # Merge refs [1,2], one manual, drop original segment 2 as standalone → delete
    payload = DraftSaveRequest.model_validate(
        {
            "draft_version_id": str(draft.id),
            "version": 1,
            "idempotency_key": "save-1",
            "segments": [
                {
                    "speaker_key": "interviewer",
                    "speaker_name": "面试官",
                    "speaker_role": "INTERVIEWER",
                    "text": "合并后的内容",
                    "source_segment_refs": [1, 2],
                    "is_included_in_analysis": True,
                },
                {
                    "speaker_key": "candidate",
                    "speaker_name": "候选人",
                    "speaker_role": "CANDIDATE",
                    "text": "拆分出的新内容",
                    "source_segment_refs": [2],
                    "is_included_in_analysis": False,
                },
                {
                    "speaker_key": "other",
                    "speaker_name": "补充",
                    "speaker_role": "OTHER",
                    "text": "人工补充段落",
                    "source_segment_refs": [],
                    "is_included_in_analysis": True,
                },
            ],
        }
    )
    result = await save_transcript_draft(
        session,
        draft_id=draft.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=payload,
    )
    assert result.version.version == 2
    assert result.version.segments[0].source_type == TranscriptSegmentSource.CORRECTED.value
    assert result.version.segments[2].source_type == (
        TranscriptSegmentSource.MANUAL_ADDITION.value
    )
    assert result.change_counts.manual_addition_count == 1
    assert result.change_counts.merge_split_count >= 1
    assert audits[0]["action"] == "interview_transcript.draft_save"
    assert "人工补充段落" not in str(audits[0]["changes"])
    assert "enc:v1:" not in result.version.raw_text


@pytest.mark.asyncio
async def test_save_draft_optimistic_lock_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(item for item in versions if item.version_label == "D1")
    from app.services.interview_transcripts import _decrypt_text

    before_version = draft.version
    before_texts = [_decrypt_text(seg.text_encrypted) for seg in draft.segments]
    before_seg_count = len(draft.segments)
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    with pytest.raises(InterviewOptimisticLockError, match=DRAFT_OPTIMISTIC_LOCK_MESSAGE):
        await save_transcript_draft(
            session,
            draft_id=draft.id,
            actor=_actor(),
            request_context=_ctx(),
            payload=DraftSaveRequest.model_validate(
                {
                    "draft_version_id": str(draft.id),
                    "version": 99,
                    "idempotency_key": "save-stale",
                    "segments": [
                        {
                            "speaker_key": "interviewer",
                            "speaker_name": "面试官",
                            "speaker_role": "INTERVIEWER",
                            "text": "should-not-persist",
                            "source_segment_refs": [1],
                        }
                    ],
                }
            ),
        )
    assert draft.version == before_version
    assert len(draft.segments) == before_seg_count
    assert [_decrypt_text(seg.text_encrypted) for seg in draft.segments] == before_texts
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_atomic_completes_pending_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_PENDING_TRANSCRIPT)
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(item for item in versions if item.version_label == "D1")
    actor = _actor()
    session, application, audits, _added, _keys, all_versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await confirm_transcript_draft(
        session,
        draft_id=draft.id,
        actor=actor,
        request_context=_ctx(),
        payload=ConfirmRequest.model_validate(
            {"idempotency_key": "confirm-1", "version": 1}
        ),
    )
    assert result.version_label == "C1"
    assert result.status == TranscriptVersionStatus.IMMUTABLE.value
    assert draft.status == TranscriptVersionStatus.IMMUTABLE.value
    assert transcript.current_confirmed_version_id == result.id
    assert transcript.current_draft_version_id is None
    assert round_.status == INTERVIEW_STATUS_COMPLETED
    assert round_.transcript_completion_mode == (
        TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value
    )
    assert round_.transcript_completion_reason_code is None
    assert round_.transcript_completion_reason_description is None
    assert round_.transcript_completed_at is not None
    assert round_.transcript_completed_by == actor.id
    assert application.pipeline_status == PIPELINE_INTERVIEWING
    assert audits[0]["action"] == "interview_transcript.confirm"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_rollback_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer failing before record_audit so commit never succeeds."""
    round_ = _make_round(status=INTERVIEW_STATUS_PENDING_TRANSCRIPT)
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(item for item in versions if item.version_label == "D1")
    session, _app, audits, added, added_keys, all_versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )

    before = {
        "draft_status": draft.status,
        "draft_version": draft.version,
        "current_confirmed_version_id": transcript.current_confirmed_version_id,
        "current_draft_version_id": transcript.current_draft_version_id,
        "round_status": round_.status,
        "completion_mode": round_.transcript_completion_mode,
        "completion_reason": round_.transcript_completion_reason_code,
        "completion_desc": round_.transcript_completion_reason_description,
        "completed_by": round_.transcript_completed_by,
        "completed_at": round_.transcript_completed_at,
        "version_count": len(all_versions),
    }
    assert before["draft_status"] == TranscriptVersionStatus.EDITING.value
    assert before["round_status"] == INTERVIEW_STATUS_PENDING_TRANSCRIPT
    assert before["completion_mode"] is None
    assert before["completion_reason"] is None
    assert before["completion_desc"] is None
    assert before["completed_by"] is None
    assert before["completed_at"] is None

    async def boom_add_version(_session, version):
        raise RuntimeError("boom-before-audit")

    module = "app.services.interview_transcripts"
    monkeypatch.setattr(f"{module}.add_version", boom_add_version)

    with pytest.raises(RuntimeError, match="boom-before-audit"):
        await confirm_transcript_draft(
            session,
            draft_id=draft.id,
            actor=_actor(),
            request_context=_ctx(),
            payload=ConfirmRequest.model_validate(
                {"idempotency_key": "confirm-fail", "version": 1}
            ),
        )
    session.rollback.assert_awaited()
    # Commit was never reached (failure before record_audit / commit).
    session.commit.assert_not_awaited()
    assert audits == []
    assert added_keys == []
    confirmed_added = [
        obj
        for obj in added
        if isinstance(obj, InterviewTranscriptVersion)
        and obj.version_type == TranscriptVersionType.CONFIRMED.value
    ]
    assert confirmed_added == []

    # In-memory objects may stay mutated under AsyncMock (SQLAlchemy would expire).
    # Restore editable draft and retry confirm successfully with a fresh path.
    draft.status = TranscriptVersionStatus.EDITING.value
    transcript.current_confirmed_version_id = before["current_confirmed_version_id"]
    transcript.current_draft_version_id = before["current_draft_version_id"]
    round_.status = before["round_status"]
    round_.transcript_completion_mode = before["completion_mode"]
    round_.transcript_completion_reason_code = before["completion_reason"]
    round_.transcript_completion_reason_description = before["completion_desc"]
    round_.transcript_completed_by = before["completed_by"]
    round_.transcript_completed_at = before["completed_at"]
    session.commit.reset_mock()
    session.rollback.reset_mock()

    async def ok_add_version(_session, version):
        added.append(version)
        all_versions.append(version)
        if version.segments is None:
            version.segments = []
        return version

    monkeypatch.setattr(f"{module}.add_version", ok_add_version)
    result = await confirm_transcript_draft(
        session,
        draft_id=draft.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=ConfirmRequest.model_validate(
            {"idempotency_key": "confirm-retry", "version": 1}
        ),
    )
    assert result.version_label == "C1"
    session.commit.assert_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_idempotent_same_key_does_not_create_second_cn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_PENDING_TRANSCRIPT)
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(item for item in versions if item.version_label == "D1")
    actor = _actor()
    session, _app, _audits, added, keys, all_versions = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    payload = ConfirmRequest.model_validate(
        {"idempotency_key": "confirm-idem", "version": 1}
    )
    first = await confirm_transcript_draft(
        session,
        draft_id=draft.id,
        actor=actor,
        request_context=_ctx(),
        payload=payload,
    )
    assert first.version_label == "C1"
    c1_id = first.id
    confirmed_before = [
        item
        for item in all_versions
        if item.version_type == TranscriptVersionType.CONFIRMED.value
    ]
    assert len(confirmed_before) == 1

    from app.services.interview_transcripts import _canonical_hash

    existing = keys[0]
    assert existing.idempotency_key == "confirm-idem"
    module = "app.services.interview_transcripts"
    monkeypatch.setattr(
        f"{module}.find_idempotency",
        AsyncMock(return_value=existing),
    )
    added_before = len(
        [
            obj
            for obj in added
            if isinstance(obj, InterviewTranscriptVersion)
            and obj.version_type == TranscriptVersionType.CONFIRMED.value
        ]
    )
    second = await confirm_transcript_draft(
        session,
        draft_id=draft.id,
        actor=actor,
        request_context=_ctx(),
        payload=payload,
    )
    assert second.id == c1_id
    assert second.version_label == "C1"
    confirmed_after = [
        item
        for item in all_versions
        if item.version_type == TranscriptVersionType.CONFIRMED.value
    ]
    assert len(confirmed_after) == 1
    added_after = [
        obj
        for obj in added
        if isinstance(obj, InterviewTranscriptVersion)
        and obj.version_type == TranscriptVersionType.CONFIRMED.value
    ]
    assert len(added_after) == added_before
    assert existing.request_hash == _canonical_hash(payload.model_dump(mode="json"))

@pytest.mark.asyncio
async def test_confirm_rejects_empty_and_no_included(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_, with_draft=True)
    draft = next(item for item in versions if item.version_label == "D1")
    from app.services.interview_transcripts import _encrypt_text

    draft.segments = [
        InterviewTranscriptSegment(
            id=uuid4(),
            transcript_version_id=draft.id,
            segment_no=1,
            speaker_key="interviewer",
            speaker_name="面试官",
            speaker_role="INTERVIEWER",
            start_time_ms=None,
            end_time_ms=None,
            text_encrypted=_encrypt_text("   "),
            source_type=TranscriptSegmentSource.ORIGINAL.value,
            source_segment_refs=[1],
            is_included_in_analysis=True,
            is_unclear=False,
        )
    ]
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    with pytest.raises(InterviewValidationError, match="empty"):
        await confirm_transcript_draft(
            session,
            draft_id=draft.id,
            actor=_actor(),
            request_context=_ctx(),
            payload=ConfirmRequest.model_validate(
                {"idempotency_key": "confirm-empty", "version": 1}
            ),
        )


@pytest.mark.asyncio
async def test_confirm_on_completed_no_double_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    round_.transcript_completion_mode = (
        TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value
    )
    transcript, versions = _attach_transcript(
        round_, with_draft=True, with_confirmed=True
    )
    # Create a fresh D2-like editing draft based on C1 for re-proof
    from app.services.interview_transcripts import _encrypt_text

    c1 = next(item for item in versions if item.version_label == "C1")
    # Mark previous draft immutable if present and add new editing draft
    for item in versions:
        if item.version_type == TranscriptVersionType.DRAFT.value:
            item.status = TranscriptVersionStatus.IMMUTABLE.value
    d2 = InterviewTranscriptVersion(
        id=uuid4(),
        transcript_id=transcript.id,
        version_type=TranscriptVersionType.DRAFT.value,
        version_no=2,
        version_label="D2",
        status=TranscriptVersionStatus.EDITING.value,
        raw_text_encrypted=c1.raw_text_encrypted,
        source_method=c1.source_method,
        source_sha256=c1.source_sha256,
        based_on_version_id=c1.id,
        created_at=_now(),
        updated_at=_now(),
        version=1,
    )
    d2.segments = [
        InterviewTranscriptSegment(
            id=uuid4(),
            transcript_version_id=d2.id,
            segment_no=seg.segment_no,
            speaker_key=seg.speaker_key,
            speaker_name=seg.speaker_name,
            speaker_role=seg.speaker_role,
            start_time_ms=seg.start_time_ms,
            end_time_ms=seg.end_time_ms,
            text_encrypted=seg.text_encrypted,
            source_type=seg.source_type,
            source_segment_refs=list(seg.source_segment_refs or []),
            is_included_in_analysis=True,
            is_unclear=False,
        )
        for seg in c1.segments
    ]
    d2.transcript = transcript
    transcript.current_draft_version_id = d2.id
    versions.append(d2)
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    result = await confirm_transcript_draft(
        session,
        draft_id=d2.id,
        actor=_actor(),
        request_context=_ctx(),
        payload=ConfirmRequest.model_validate(
            {"idempotency_key": "confirm-c2", "version": 1}
        ),
    )
    assert result.version_label == "C2"
    assert round_.status == INTERVIEW_STATUS_COMPLETED
    assert transcript.current_confirmed_version_id == result.id


@pytest.mark.asyncio
async def test_complete_without_transcript_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codes = list_transcript_reason_codes()
    assert {item.code for item in codes.items} >= {
        "EXTERNAL_TOOL_UNAVAILABLE",
        "RECORDING_NOT_PERMITTED",
        "TRANSCRIPT_FILE_LOST",
        "CONTENT_UNUSABLE",
        "OTHER",
    }
    other = next(item for item in codes.items if item.code == "OTHER")
    assert other.requires_description

    round_ = _make_round()
    actor = _actor()
    session, _app, audits, added, _keys, _versions = _patch_base(monkeypatch, round_)
    with pytest.raises(Exception):
        CompleteWithoutTranscriptRequest.model_validate(
            {
                "reason_code": "OTHER",
                "version": 1,
                "idempotency_key": "wo-1",
            }
        )
    result = await complete_without_transcript(
        session,
        round_id=round_.id,
        actor=actor,
        request_context=_ctx(),
        payload=CompleteWithoutTranscriptRequest.model_validate(
            {
                "reason_code": "EXTERNAL_TOOL_UNAVAILABLE",
                "version": 1,
                "idempotency_key": "wo-1",
            }
        ),
    )
    assert result.status == INTERVIEW_STATUS_COMPLETED
    assert result.transcript_completion_mode == (
        TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value
    )
    assert result.transcript_completion_reason_code == "EXTERNAL_TOOL_UNAVAILABLE"
    assert result.transcript_completion_reason_description is None
    assert result.transcript_completed_by == actor.id
    assert result.transcript_completed_at is not None
    assert round_.transcript_completion_reason_description is None
    assert round_.transcript_completed_by == actor.id
    assert round_.transcript_completed_at is not None
    assert not any(isinstance(obj, InterviewTranscript) for obj in added)
    assert not any(isinstance(obj, InterviewTranscriptVersion) for obj in added)
    assert not any(isinstance(obj, InterviewTranscriptSegment) for obj in added)
    assert audits[0]["action"] == "interview_transcript.complete_without_transcript"

@pytest.mark.asyncio
async def test_complete_without_rejects_when_master_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round()
    transcript, versions = _attach_transcript(round_)
    session, *_rest = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    with pytest.raises(InterviewValidationError, match="transcript master"):
        await complete_without_transcript(
            session,
            round_id=round_.id,
            actor=_actor(),
            request_context=_ctx(),
            payload=CompleteWithoutTranscriptRequest.model_validate(
                {
                    "reason_code": "CONTENT_UNUSABLE",
                    "version": 1,
                    "idempotency_key": "wo-2",
                }
            ),
        )


@pytest.mark.asyncio
async def test_generic_complete_rejected() -> None:
    session = AsyncMock()
    with pytest.raises(
        InterviewValidationError,
        match="confirm transcript or complete-without-transcript",
    ):
        await complete_interview_round(
            session,
            round_id=uuid4(),
            payload=InterviewRoundActionRequest.model_validate(
                {"version": 1, "idempotency_key": "x"}
            ),
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_rbac_interviewer_confirmed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(
        round_, with_draft=True, with_confirmed=True
    )
    t1 = next(item for item in versions if item.version_label == "T1")
    draft = next(item for item in versions if item.version_label == "D1")
    c1 = next(item for item in versions if item.version_label == "C1")
    interviewer = _actor(manage=False, execute=True)
    session, *_rest = _patch_base(
        monkeypatch,
        round_,
        transcript=transcript,
        versions=versions,
        assigned=True,
    )
    ok = await get_transcript_version(
        session, version_id=c1.id, actor=interviewer
    )
    assert ok.version_label == "C1"
    assert "enc:v1:" not in ok.raw_text

    with pytest.raises(InterviewForbiddenError):
        await get_transcript_version(session, version_id=t1.id, actor=interviewer)
    with pytest.raises(InterviewForbiddenError):
        await get_transcript_version(session, version_id=draft.id, actor=interviewer)

    listed = await list_transcript_versions(
        session, round_id=round_.id, actor=interviewer
    )
    assert [item.version_label for item in listed.versions] == ["C1"]

    # Unassigned → 404 semantics
    session2, *_ = _patch_base(
        monkeypatch,
        round_,
        transcript=transcript,
        versions=versions,
        assigned=False,
    )
    with pytest.raises(InterviewNotFoundError):
        await get_transcript_version(
            session2, version_id=c1.id, actor=interviewer
        )


@pytest.mark.asyncio
async def test_rbac_manage_can_read_t1_draft_and_history_cn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(
        round_, with_draft=True, with_confirmed=True
    )
    t1 = next(item for item in versions if item.version_label == "T1")
    draft = next(item for item in versions if item.version_label == "D1")
    c1 = next(item for item in versions if item.version_label == "C1")
    manager = _actor(manage=True, execute=False)
    session, *_ = _patch_base(
        monkeypatch,
        round_,
        transcript=transcript,
        versions=versions,
        assigned=False,
    )
    for version in (t1, draft, c1):
        detail = await get_transcript_version(
            session, version_id=version.id, actor=manager
        )
        assert detail.version_label == version.version_label
        assert "enc:v1:" not in detail.raw_text
    listed = await list_transcript_versions(
        session, round_id=round_.id, actor=manager
    )
    labels = {item.version_label for item in listed.versions}
    assert labels >= {"T1", "D1", "C1"}


@pytest.mark.asyncio
async def test_rbac_interviewer_cannot_read_old_confirmed_when_c2_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(
        round_, with_draft=False, with_confirmed=True
    )
    c1 = next(item for item in versions if item.version_label == "C1")

    c2 = InterviewTranscriptVersion(
        id=uuid4(),
        transcript_id=transcript.id,
        version_type=TranscriptVersionType.CONFIRMED.value,
        version_no=2,
        version_label="C2",
        status=TranscriptVersionStatus.IMMUTABLE.value,
        raw_text_encrypted=c1.raw_text_encrypted,
        source_method=c1.source_method,
        source_sha256=c1.source_sha256,
        based_on_version_id=c1.id,
        confirmed_by=round_.owner_id,
        confirmed_at=_now(),
        created_by=round_.owner_id,
        created_at=_now(),
        updated_by=round_.owner_id,
        updated_at=_now(),
        version=1,
    )
    c2.segments = [
        InterviewTranscriptSegment(
            id=uuid4(),
            transcript_version_id=c2.id,
            segment_no=seg.segment_no,
            speaker_key=seg.speaker_key,
            speaker_name=seg.speaker_name,
            speaker_role=seg.speaker_role,
            start_time_ms=seg.start_time_ms,
            end_time_ms=seg.end_time_ms,
            text_encrypted=seg.text_encrypted,
            source_type=seg.source_type,
            source_segment_refs=list(seg.source_segment_refs or []),
            is_included_in_analysis=True,
            is_unclear=False,
        )
        for seg in c1.segments
    ]
    c2.transcript = transcript
    transcript.current_confirmed_version_id = c2.id
    versions.append(c2)

    interviewer = _actor(manage=False, execute=True)
    session, *_ = _patch_base(
        monkeypatch,
        round_,
        transcript=transcript,
        versions=versions,
        assigned=True,
    )
    ok = await get_transcript_version(session, version_id=c2.id, actor=interviewer)
    assert ok.version_label == "C2"
    with pytest.raises(InterviewForbiddenError):
        await get_transcript_version(session, version_id=c1.id, actor=interviewer)
    listed = await list_transcript_versions(
        session, round_id=round_.id, actor=interviewer
    )
    assert [item.version_label for item in listed.versions] == ["C2"]


@pytest.mark.asyncio
async def test_tampered_segment_ciphertext_raises_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transcript decrypt path must fail closed without leaking partial plaintext.

    Crypto primitives themselves are covered by tests/services/test_crypto.py:
    test_wrong_key_cannot_decrypt, test_missing_key_fails_closed,
    test_tampered_ciphertext_fails.
    """
    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    transcript, versions = _attach_transcript(round_, with_confirmed=True)
    c1 = next(item for item in versions if item.version_label == "C1")
    original = c1.segments[0].text_encrypted
    assert original.startswith("enc:v1:")
    c1.segments[0].text_encrypted = original[:-2] + (
        "A" if original[-1] != "A" else "B"
    )
    session, *_ = _patch_base(
        monkeypatch, round_, transcript=transcript, versions=versions
    )
    with pytest.raises(InterviewValidationError, match="decryption failed") as exc_info:
        await get_transcript_version(session, version_id=c1.id, actor=_actor())
    message = str(exc_info.value)
    assert "请介绍自己" not in message
    assert "五年经验" not in message
    assert "enc:v1:" not in message
    assert original not in message