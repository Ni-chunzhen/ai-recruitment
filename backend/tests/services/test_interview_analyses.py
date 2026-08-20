"""Service-layer tests for single-round interview analysis."""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    TASK_TYPE_INTERVIEW_ROUND_ANALYZE,
    AITask,
)
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import (
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_SCHEDULED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
)
from app.models.interview_ai import (
    InterviewRoundAnalysis,
    InterviewRoundAnalysisDimension,
    InterviewRoundAnalysisEvidence,
    InterviewRoundAnalysisVersion,
)
from app.models.interview_transcript import (
    TranscriptCompletionMode,
    TranscriptSegmentSource,
    TranscriptSpeakerRole,
    TranscriptVersionStatus,
    TranscriptVersionType,
    InterviewTranscript,
    InterviewTranscriptSegment,
    InterviewTranscriptVersion,
)
from app.models.resume import PIPELINE_INTERVIEWING
from app.services.audit import RequestContext, _scrub_value
from app.services.crypto import CIPHER_PREFIX, EncryptionError, encrypt_secret
from app.services.interview_ai_validation import AIOutputValidationError
from app.services.interviews import (
    InterviewConflictError,
    InterviewForbiddenError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewValidationError,
)

MODULE = "app.services.interview_analyses"
SECRET_ANALYSIS = "TOP_SECRET_ANALYSIS_24680"
SECRET_QUOTE = "TOP_SECRET_QUOTE_13579"
SECRET_CIPHER = "enc:v1:TOP_SECRET_CIPHER"
SNAPSHOT_WHITELIST = {
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
DIMENSION_WHITELIST = {
    "dimension_key",
    "display_order",
    "name",
    "weight",
    "description",
    "anchors",
}
SEGMENT_REF_WHITELIST = {"segment_id", "segment_no", "plaintext_sha256"}
INCLUDED_SEGMENT_TEXT_1 = "我当时先对齐目标。"
INCLUDED_SEGMENT_TEXT_2 = "我们完成了交付。"


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
    return RequestContext(request_id="req-a1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _anchors(prefix: str) -> list[str]:
    return [f"{prefix}{i}" for i in range(1, 6)]


def _dimensions(*, incomplete: bool = False, bad_weight: bool = False):
    anchors_a = ["弱"] if incomplete else _anchors("协")
    anchors_b = _anchors("专")
    weight_a, weight_b = (40, 50) if bad_weight else (40, 60)
    return [
        {
            "name": "协作",
            "weight": weight_a,
            "description": "跨团队协作",
            "anchors": anchors_a,
        },
        {
            "name": "专业",
            "weight": weight_b,
            "description": "专业深度",
            "anchors": anchors_b,
        },
    ]


def _application():
    candidate_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        candidate_id=candidate_id,
        candidate=SimpleNamespace(
            id=candidate_id,
            name="张三",
            email="zhang@example.com",
            phone="13800138000",
        ),
        job_id=uuid4(),
        job_version_id=uuid4(),
        resume_version_id=uuid4(),
        pipeline_status=PIPELINE_INTERVIEWING,
        status=APPLICATION_STATUS_IN_PROGRESS,
        lock_version=1,
    )


def _make_round(
    *,
    status: str = INTERVIEW_STATUS_COMPLETED,
    completion_mode: str | None = TranscriptCompletionMode.CONFIRMED_TRANSCRIPT.value,
    job_version_id=None,
) -> InterviewRound:
    now = _now()
    round_ = InterviewRound(
        id=uuid4(),
        application_id=uuid4(),
        job_version_id=job_version_id or uuid4(),
        name="第一轮专业面",
        sequence_no=1,
        status=status,
        format="ONLINE",
        owner_id=uuid4(),
        version=1,
        created_at=now,
        updated_at=now,
        transcript_completion_mode=completion_mode,
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


def _job_bundle(*, frozen_id, dimensions=None):
    frozen = SimpleNamespace(
        id=frozen_id,
        raw_jd_text="JD 正文不应进入 snapshot",
        structured_jd={"responsibilities": ["秘密职责"]},
        score_dimensions=dimensions if dimensions is not None else _dimensions(),
        version_label="V1.0",
        major=1,
        minor=0,
        status="published",
    )
    newer = SimpleNamespace(
        id=uuid4(),
        raw_jd_text="新版 JD",
        score_dimensions=_dimensions(),
        version_label="V2.0",
        major=2,
        minor=0,
        status="published",
    )
    job = SimpleNamespace(
        id=uuid4(),
        name="后端工程师",
        department="研发",
        current_version_id=newer.id,
        versions=[frozen, newer],
    )
    return job, frozen, newer


def _segment(
    *,
    version_id,
    segment_no: int,
    text: str,
    included: bool = True,
    speaker_name: str = "李面试官",
):
    cipher = encrypt_secret(text)
    assert cipher is not None
    return InterviewTranscriptSegment(
        id=uuid4(),
        transcript_version_id=version_id,
        segment_no=segment_no,
        speaker_key=f"s{segment_no}",
        speaker_name=speaker_name,
        speaker_role=TranscriptSpeakerRole.CANDIDATE.value,
        start_time_ms=0,
        end_time_ms=1000,
        text_encrypted=cipher,
        source_type=TranscriptSegmentSource.ORIGINAL.value,
        source_segment_refs=[segment_no],
        is_included_in_analysis=included,
        is_unclear=False,
        created_at=_now(),
    )


def _confirmed_version(transcript: InterviewTranscript, *, label: str = "C1"):
    version = InterviewTranscriptVersion(
        id=uuid4(),
        transcript_id=transcript.id,
        version_type=TranscriptVersionType.CONFIRMED.value,
        version_no=int(label[1:]),
        version_label=label,
        status=TranscriptVersionStatus.IMMUTABLE.value,
        raw_text_encrypted=encrypt_secret("raw") or "enc:v1:x",
        source_method="PASTE",
        source_sha256="abc",
        created_at=_now(),
        updated_at=_now(),
        version=1,
    )
    version.transcript = transcript
    version.segments = [
        _segment(
            version_id=version.id,
            segment_no=1,
            text=INCLUDED_SEGMENT_TEXT_1,
            speaker_name="候选人甲",
        ),
        _segment(
            version_id=version.id,
            segment_no=2,
            text=INCLUDED_SEGMENT_TEXT_2,
            speaker_name="候选人甲",
        ),
        _segment(
            version_id=version.id,
            segment_no=3,
            text="这段被排除。",
            included=False,
        ),
    ]
    return version


def _transcript(round_: InterviewRound, *, with_c1: bool = True):
    transcript = InterviewTranscript(
        id=uuid4(),
        interview_round_id=round_.id,
        version=1,
        created_at=_now(),
        updated_at=_now(),
    )
    versions = []
    if with_c1:
        c1 = _confirmed_version(transcript, label="C1")
        transcript.current_confirmed_version_id = c1.id
        transcript.versions = [c1]
        versions.append(c1)
    else:
        transcript.current_confirmed_version_id = None
        transcript.versions = []
    return transcript, versions


def _analysis_payload(*, segment, extra_dim=None, reverse=False, score_none=False):
    dim1 = {
        "dimension_key": "D001",
        "score": None if score_none else 4,
        "evidence": []
        if score_none
        else [
            {
                "segment_id": str(segment.id),
                "segment_no": segment.segment_no,
                "quote": "我当时先对齐目标。",
            }
        ],
        "analysis": "候选人能描述冲突处理路径。",
        "strengths": ["目标对齐"],
        "risks": ["细节偏少"],
        "insufficient_information": "信息不足需追问" if score_none else None,
        "suggested_follow_ups": ["请补充具体结果"],
    }
    dim2 = extra_dim or {
        "dimension_key": "D002",
        "score": 5,
        "evidence": [
            {
                "segment_id": str(segment.id),
                "segment_no": segment.segment_no,
                "quote": "我当时先对齐目标。",
            }
        ],
        "analysis": "专业深度充分。",
        "strengths": ["交付"],
        "risks": ["偏少量化"],
        "insufficient_information": None,
        "suggested_follow_ups": ["问指标"],
    }
    dims = [dim2, dim1] if reverse else [dim1, dim2]
    return {
        "dimensions": dims,
        "overall_summary": "整体表现稳定。",
        "model_reported_overall_score": "9.99",
    }


def _assert_safe_snapshot(snapshot: dict) -> None:
    assert set(snapshot) == SNAPSHOT_WHITELIST
    blob = json.dumps(snapshot, ensure_ascii=False, default=str)
    for marker in (
        "JD 正文",
        "我当时先对齐目标",
        "候选人甲",
        "李面试官",
        "张三",
        "zhang@example.com",
        "13800138000",
        CIPHER_PREFIX,
        SECRET_ANALYSIS,
        SECRET_QUOTE,
        "sk-live",
    ):
        assert marker not in blob
    for dim in snapshot["dimensions"]:
        assert set(dim) == DIMENSION_WHITELIST
        assert str(dim["dimension_key"]).startswith("D")
    for ref in snapshot["segment_refs"]:
        assert set(ref) == SEGMENT_REF_WHITELIST
        digest = ref["plaintext_sha256"]
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert all(char in "0123456789abcdef" for char in digest)
        assert CIPHER_PREFIX not in digest


def _assert_safe_audit(entry: dict) -> None:
    from app.models import sanitize_audit_changes

    changes = entry["changes"]
    blob = json.dumps(changes, ensure_ascii=False, default=str)
    assert SECRET_ANALYSIS not in blob
    assert SECRET_QUOTE not in blob
    assert CIPHER_PREFIX not in blob
    assert "我当时先对齐目标" not in blob
    sanitize_audit_changes(changes)
    scrubbed = _scrub_value(changes)
    if "task_type" in changes:
        assert scrubbed["task_type"] == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    if "dimension_count" in changes:
        assert scrubbed["dimension_count"] == changes["dimension_count"]
    if "evidence_count" in changes:
        assert scrubbed["evidence_count"] == changes["evidence_count"]


def _patch_base(
    monkeypatch: pytest.MonkeyPatch,
    round_: InterviewRound,
    *,
    application=None,
    job=None,
    transcript=None,
    transcript_versions=None,
    assigned: bool = True,
    analysis=None,
    versions=None,
    inflight=None,
):
    from app.repositories.jobs import get_version_by_id as real_job_version

    application = application or _application()
    application.id = round_.application_id
    if job is None:
        job, frozen, _newer = _job_bundle(frozen_id=round_.job_version_id)
    else:
        frozen = real_job_version(job, round_.job_version_id)
    if transcript is None:
        transcript, transcript_versions = _transcript(round_)
    transcript_versions = list(transcript_versions or [])
    versions = versions if versions is not None else list(
        getattr(analysis, "versions", None) or []
    )
    audits: list[dict] = []
    added_idempotency: list[InterviewIdempotencyKey] = []
    added_tasks: list[AITask] = []
    added_objects: list[object] = []
    state = {
        "idempotency": None,
        "inflight": inflight,
        "analysis": analysis,
        "round_status": round_.status,
        "completion_mode": round_.transcript_completion_mode,
    }

    async def fake_record_audit(_session, **kwargs):
        audits.append(kwargs)

    async def fake_add_idempotency(_session, key):
        added_idempotency.append(key)
        state["idempotency"] = key
        return key

    async def fake_find_idempotency(
        _session, *, actor_id, action, scope_id, idempotency_key
    ):
        for record in added_idempotency:
            if (
                record.actor_id == actor_id
                and record.action == action
                and record.scope_id == scope_id
                and record.idempotency_key == idempotency_key
            ):
                return record
        return None

    async def fake_add_ai_task(_session, task):
        if getattr(task, "id", None) is None:
            task.id = uuid4()
        added_tasks.append(task)
        added_objects.append(task)
        if task.status in {AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING}:
            state["inflight"] = task
        await _session.flush()
        return task

    async def fake_find_inflight(_session, **_kwargs):
        return state["inflight"]

    async def fake_find_by_hash(_session, **kwargs):
        needle = kwargs.get("input_snapshot_hash")
        for task in added_tasks:
            if (task.input_snapshot or {}).get("input_snapshot_hash") == needle:
                return task
        return None

    async def fake_get_ai_task(_session, task_id, **_kwargs):
        for task in added_tasks:
            if task.id == task_id:
                return task
        if state["inflight"] is not None and state["inflight"].id == task_id:
            return state["inflight"]
        return None

    async def fake_get_transcript(_session, round_id):
        if transcript.interview_round_id == round_id:
            return transcript
        return None

    async def fake_get_tv(_session, version_id):
        for item in transcript_versions:
            if item.id == version_id:
                return item
        return None

    async def fake_get_analysis(_session, *, round_id):
        current = state["analysis"]
        if current is not None and current.interview_round_id == round_id:
            return current
        return None

    async def fake_create_analysis(_session, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if getattr(obj, "versions", None) is None:
            obj.versions = []
        state["analysis"] = obj
        added_objects.append(obj)
        return obj

    async def fake_create_version(_session, version):
        if getattr(version, "id", None) is None:
            version.id = uuid4()
        if getattr(version, "dimensions", None) is None:
            version.dimensions = []
        versions.append(version)
        current = state["analysis"]
        if current is not None:
            current.versions = list(getattr(current, "versions", []) or [])
            current.versions.append(version)
            version.analysis = current
        added_objects.append(version)
        return version

    async def fake_create_dims(_session, rows):
        for row in rows:
            if getattr(row, "id", None) is None:
                row.id = uuid4()
            if getattr(row, "evidence", None) is None:
                row.evidence = []
            added_objects.append(row)
            for version in versions:
                if version.id == row.analysis_version_id:
                    version.dimensions = list(version.dimensions or [])
                    version.dimensions.append(row)
        return rows

    async def fake_create_evidence(_session, rows):
        for row in rows:
            if getattr(row, "id", None) is None:
                row.id = uuid4()
            added_objects.append(row)
            for version in versions:
                for dim in version.dimensions or []:
                    if dim.id == row.analysis_dimension_id:
                        dim.evidence = list(dim.evidence or [])
                        dim.evidence.append(row)
        return rows

    async def fake_next_no(_session, *, analysis_id):
        nos = [item.version_no for item in versions if item.analysis_id == analysis_id]
        return (max(nos) if nos else 0) + 1

    async def fake_get_version(_session, *, round_id, version_id):
        current = state["analysis"]
        if current is None or current.interview_round_id != round_id:
            return None
        for item in versions:
            if item.id == version_id and item.analysis_id == current.id:
                return item
        return None

    async def fake_get_by_task(_session, *, ai_task_id, round_id=None):
        current = state["analysis"]
        for item in versions:
            if item.ai_task_id != ai_task_id:
                continue
            if round_id is not None and (
                current is None or current.interview_round_id != round_id
            ):
                continue
            return item
        return None

    async def fake_list(_session, *, round_id):
        current = state["analysis"]
        if current is None or current.interview_round_id != round_id:
            return []
        return sorted(
            [item for item in versions if item.analysis_id == current.id],
            key=lambda item: item.version_no,
        )

    monkeypatch.setattr(f"{MODULE}.get_round_for_update", AsyncMock(return_value=round_))
    monkeypatch.setattr(f"{MODULE}.get_round_by_id", AsyncMock(return_value=round_))
    monkeypatch.setattr(
        f"{MODULE}.get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(f"{MODULE}.get_job_by_id", AsyncMock(return_value=job))
    monkeypatch.setattr(f"{MODULE}.get_job_version_by_id", real_job_version)
    monkeypatch.setattr(
        f"{MODULE}.actor_assigned_to_round", AsyncMock(return_value=assigned)
    )
    monkeypatch.setattr(f"{MODULE}.find_idempotency", fake_find_idempotency)
    monkeypatch.setattr(f"{MODULE}.add_idempotency", fake_add_idempotency)
    monkeypatch.setattr(f"{MODULE}.record_audit", fake_record_audit)
    monkeypatch.setattr(f"{MODULE}.add_ai_task", fake_add_ai_task)
    monkeypatch.setattr(f"{MODULE}.find_inflight_task", fake_find_inflight)
    monkeypatch.setattr(f"{MODULE}.find_task_by_input_snapshot_hash", fake_find_by_hash)
    monkeypatch.setattr(f"{MODULE}.get_ai_task_by_id", fake_get_ai_task)
    monkeypatch.setattr(f"{MODULE}.get_transcript_by_round_id", fake_get_transcript)
    monkeypatch.setattr(f"{MODULE}.get_transcript_version_by_id", fake_get_tv)
    monkeypatch.setattr(f"{MODULE}.get_analysis_by_round", fake_get_analysis)
    monkeypatch.setattr(f"{MODULE}.get_analysis_for_update", fake_get_analysis)
    monkeypatch.setattr(f"{MODULE}.create_analysis", fake_create_analysis)
    monkeypatch.setattr(f"{MODULE}.create_analysis_version", fake_create_version)
    monkeypatch.setattr(f"{MODULE}.create_analysis_dimensions", fake_create_dims)
    monkeypatch.setattr(f"{MODULE}.create_analysis_evidence", fake_create_evidence)
    monkeypatch.setattr(f"{MODULE}.next_analysis_version_no", fake_next_no)
    monkeypatch.setattr(f"{MODULE}.get_analysis_version_by_id", fake_get_version)
    monkeypatch.setattr(
        f"{MODULE}.get_analysis_version_by_task_id", fake_get_by_task
    )
    monkeypatch.setattr(f"{MODULE}.list_analysis_version_rows", fake_list)
    enqueue = MagicMock()
    monkeypatch.setattr(
        f"{MODULE}.enqueue_sensitive_interview_ai_task", enqueue, raising=False
    )
    session = AsyncMock()
    return SimpleNamespace(
        session=session,
        application=application,
        job=job,
        frozen=frozen,
        transcript=transcript,
        transcript_versions=transcript_versions,
        audits=audits,
        added_idempotency=added_idempotency,
        added_tasks=added_tasks,
        added_objects=added_objects,
        versions=versions,
        enqueue=enqueue,
        state=state,
    )


# ---------------------------------------------------------------------------
# A. Repository
# ---------------------------------------------------------------------------


def test_repository_scopes_lock_and_does_not_commit() -> None:
    import app.repositories.interview_analyses as repo

    source = inspect.getsource(repo)
    assert "with_for_update" in source
    assert "interview_round_id" in source
    assert "version_no" in source
    assert "session.commit" not in source
    assert "session.rollback" not in source
    assert "get_analysis_version_by_id" in source
    assert "round_id" in inspect.getsource(repo.get_analysis_version_by_id)


@pytest.mark.asyncio
async def test_repository_create_and_next_version_flush_only() -> None:
    from app.repositories.interview_analyses import (
        create_analysis,
        next_analysis_version_no,
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar = AsyncMock(return_value=2)
    analysis = InterviewRoundAnalysis(interview_round_id=uuid4())
    await create_analysis(session, analysis)
    session.add.assert_called()
    session.flush.assert_awaited()
    session.commit.assert_not_called()
    assert await next_analysis_version_no(session, analysis_id=uuid4()) == 3


# ---------------------------------------------------------------------------
# B. 门禁
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manage_can_request_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=False)
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="a-manage",
        actor=_actor(manage=True, execute=False),
        request_context=_ctx(),
    )
    assert task.task_type == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    assert task.business_type == BUSINESS_TYPE_INTERVIEW_ROUND
    assert task.business_id == round_.id
    assert task.status == AI_TASK_STATUS_PENDING
    assert task.result_payload is None
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_assigned_execute_cannot_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=True)
    with pytest.raises(InterviewForbiddenError, match="forbidden"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="a-exec",
            actor=_actor(manage=False, execute=True),
            request_context=_ctx(),
        )
    assert env.added_tasks == []
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_unassigned_execute_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=False)
    with pytest.raises(InterviewNotFoundError):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="a-404",
            actor=_actor(manage=False, execute=True),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_missing_round_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    monkeypatch.setattr(f"{MODULE}.get_round_for_update", AsyncMock(return_value=None))
    with pytest.raises(InterviewNotFoundError):
        await request_analysis_generation(
            env.session,
            round_id=uuid4(),
            idempotency_key="missing",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.parametrize(
    "status",
    [INTERVIEW_STATUS_SCHEDULED, INTERVIEW_STATUS_IN_PROGRESS],
)
@pytest.mark.asyncio
async def test_non_completed_round_rejected(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round(status=status)
    env = _patch_base(monkeypatch, round_)
    with pytest.raises(InterviewValidationError, match="完成"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="not-done",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_without_transcript_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round(
        completion_mode=TranscriptCompletionMode.WITHOUT_TRANSCRIPT.value
    )
    env = _patch_base(monkeypatch, round_)
    with pytest.raises(InterviewValidationError, match="无转写"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="no-tr",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_missing_confirmed_version_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    transcript, versions = _transcript(round_, with_c1=False)
    env = _patch_base(
        monkeypatch, round_, transcript=transcript, transcript_versions=versions
    )
    with pytest.raises(InterviewValidationError, match="确认转写"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="no-cn",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_analysis_generate_rejects_without_confirmed_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    transcript, versions = _transcript(round_, with_c1=False)
    env = _patch_base(
        monkeypatch, round_, transcript=transcript, transcript_versions=versions
    )
    with pytest.raises(InterviewValidationError, match="确认转写"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="no-cn-gate",
            actor=_actor(),
            request_context=_ctx(),
        )
    env.enqueue.assert_not_called()
    assert env.added_tasks == []


@pytest.mark.asyncio
async def test_confirmed_version_from_other_transcript_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    foreign = _confirmed_version(
        InterviewTranscript(id=uuid4(), interview_round_id=uuid4()), label="C1"
    )
    env.transcript.current_confirmed_version_id = foreign.id
    env.transcript_versions.append(foreign)
    with pytest.raises((InterviewValidationError, InterviewNotFoundError)):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="foreign-cn",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_uses_round_job_version_not_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    frozen_id = uuid4()
    round_ = _make_round(job_version_id=frozen_id)
    job, frozen, newer = _job_bundle(frozen_id=frozen_id)
    env = _patch_base(monkeypatch, round_, job=job)
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="freeze-job",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert task.input_snapshot["job_version_id"] == str(frozen.id)
    assert task.input_snapshot["job_version_id"] != str(newer.id)
    assert task.input_snapshot["dimensions"][0]["name"] == "协作"


@pytest.mark.asyncio
async def test_incomplete_anchors_and_bad_weights_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    job, _frozen, _newer = _job_bundle(
        frozen_id=round_.job_version_id, dimensions=_dimensions(incomplete=True)
    )
    env = _patch_base(monkeypatch, round_, job=job)
    with pytest.raises(InterviewValidationError):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="anchors",
            actor=_actor(),
            request_context=_ctx(),
        )
    job2, _f, _n = _job_bundle(
        frozen_id=round_.job_version_id, dimensions=_dimensions(bad_weight=True)
    )
    env2 = _patch_base(monkeypatch, round_, job=job2)
    with pytest.raises(InterviewValidationError):
        await request_analysis_generation(
            env2.session,
            round_id=round_.id,
            idempotency_key="weights",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_no_included_or_blank_segments_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    c1 = env.transcript_versions[0]
    for seg in c1.segments:
        seg.is_included_in_analysis = False
    with pytest.raises(InterviewValidationError, match="片段"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="no-seg",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_blank_included_segments_are_skipped_then_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    c1 = env.transcript_versions[0]
    for seg in c1.segments:
        if seg.is_included_in_analysis:
            seg.text_encrypted = encrypt_secret("   ")
    with pytest.raises(InterviewValidationError, match="片段"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="blank-seg",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_segment_decrypt_failure_is_safe_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    c1 = env.transcript_versions[0]
    c1.segments[0].text_encrypted = SECRET_CIPHER
    with pytest.raises(InterviewValidationError) as exc:
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="bad-dec",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert SECRET_CIPHER not in str(exc.value)
    assert CIPHER_PREFIX not in str(exc.value)


@pytest.mark.asyncio
async def test_over_char_limit_rejected_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        INTERVIEW_ANALYZE_MAX_CHARS,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    c1 = env.transcript_versions[0]
    huge = "字" * (INTERVIEW_ANALYZE_MAX_CHARS + 1)
    c1.segments[0].text_encrypted = encrypt_secret(huge)
    c1.segments[1].is_included_in_analysis = False
    with pytest.raises(InterviewValidationError, match="上限"):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="too-long",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert len(huge) > INTERVIEW_ANALYZE_MAX_CHARS


def test_service_does_not_enqueue_or_read_resume_ai() -> None:
    import app.services.interview_analyses as module
    from app.services.interview_analyses import (
        dispatch_persisted_analysis_generation_task,
        request_analysis_generation,
    )

    request_source = inspect.getsource(request_analysis_generation)
    dispatch_source = inspect.getsource(dispatch_persisted_analysis_generation_task)
    source = inspect.getsource(module)
    assert "enqueue_ai_task" not in request_source
    assert "session.commit" not in request_source
    assert "enqueue_sensitive_interview_ai_task" in dispatch_source
    assert "enqueue_ai_task" not in dispatch_source
    assert "AiResult" not in source
    assert "list_ai_results" not in source
    assert "QUESTION_SET_STATUS_READY" not in source


# ---------------------------------------------------------------------------
# C. Snapshot / hash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_whitelist_and_stable_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    first = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="snap-a",
        actor=_actor(),
        request_context=_ctx(),
    )
    _assert_safe_snapshot(first.input_snapshot)
    assert first.input_snapshot["task_type"] == TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    assert first.input_snapshot["dimensions"][0]["dimension_key"] == "D001"
    refs = first.input_snapshot["segment_refs"]
    assert [item["segment_no"] for item in refs] == [1, 2]
    assert [item["plaintext_sha256"] for item in refs] == [
        _sha256(INCLUDED_SEGMENT_TEXT_1),
        _sha256(INCLUDED_SEGMENT_TEXT_2),
    ]
    blob = json.dumps(first.input_snapshot, ensure_ascii=False)
    assert INCLUDED_SEGMENT_TEXT_1 not in blob
    assert "这段被排除" not in blob
    env.state["inflight"] = None
    second = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="snap-b",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert (
        first.input_snapshot["input_snapshot_hash"]
        == second.input_snapshot["input_snapshot_hash"]
    )
    assert second.input_snapshot["segment_refs"][0]["plaintext_sha256"] == _sha256(
        INCLUDED_SEGMENT_TEXT_1
    )


@pytest.mark.asyncio
async def test_hash_changes_with_transcript_or_segment_or_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    first = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="h1",
        actor=_actor(),
        request_context=_ctx(),
    )
    env.state["inflight"] = None
    c2 = _confirmed_version(env.transcript, label="C2")
    env.transcript.current_confirmed_version_id = c2.id
    env.transcript_versions.append(c2)
    second = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="h2",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert (
        first.input_snapshot["input_snapshot_hash"]
        != second.input_snapshot["input_snapshot_hash"]
    )


@pytest.mark.asyncio
async def test_excluded_segment_does_not_enter_refs_or_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    first = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="excl-a",
        actor=_actor(),
        request_context=_ctx(),
    )
    nos = [item["segment_no"] for item in first.input_snapshot["segment_refs"]]
    assert nos == [1, 2]
    assert 3 not in nos
    excluded = env.transcript_versions[0].segments[2]
    excluded.text_encrypted = encrypt_secret("排除片段被改也不应改 hash")
    env.state["inflight"] = None
    second = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="excl-b",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert (
        first.input_snapshot["input_snapshot_hash"]
        == second.input_snapshot["input_snapshot_hash"]
    )


@pytest.mark.asyncio
async def test_frozen_plaintext_hash_mismatch_rejects_load_and_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        load_analysis_provider_input,
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="tamper",
        actor=actor,
        request_context=_ctx(),
    )
    dto = await load_analysis_provider_input(env.session, task_id=task.id)
    assert dto.segments[0].text == INCLUDED_SEGMENT_TEXT_1
    frozen = env.transcript_versions[0].segments[0]
    tampered = "被篡改的转写正文 XYZ"
    frozen.text_encrypted = encrypt_secret(tampered)
    with pytest.raises(AIOutputValidationError) as load_exc:
        await load_analysis_provider_input(env.session, task_id=task.id)
    assert load_exc.value.code == "output_validation_failed"
    message = str(load_exc.value)
    assert tampered not in message
    assert INCLUDED_SEGMENT_TEXT_1 not in message
    assert CIPHER_PREFIX not in message
    assert task.input_snapshot["segment_refs"][0]["plaintext_sha256"] not in message
    with pytest.raises(AIOutputValidationError) as persist_exc:
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=_analysis_payload(segment=frozen),
            actor=actor,
            request_context=_ctx(),
        )
    assert persist_exc.value.code == "output_validation_failed"
    persist_message = str(persist_exc.value)
    assert tampered not in persist_message
    assert INCLUDED_SEGMENT_TEXT_1 not in persist_message
    assert env.state["analysis"] is None
    assert env.versions == []


@pytest.mark.asyncio
async def test_segment_ref_missing_or_invalid_fields_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        load_analysis_provider_input,
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="bad-ref",
        actor=actor,
        request_context=_ctx(),
    )
    original = dict(task.input_snapshot["segment_refs"][0])
    del task.input_snapshot["segment_refs"][0]["plaintext_sha256"]
    with pytest.raises((InterviewValidationError, AIOutputValidationError)):
        await load_analysis_provider_input(env.session, task_id=task.id)
    task.input_snapshot["segment_refs"][0] = dict(original)
    task.input_snapshot["segment_refs"][0]["plaintext_sha256"] = "not-a-sha256"
    with pytest.raises((InterviewValidationError, AIOutputValidationError)) as exc:
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=_analysis_payload(segment=env.transcript_versions[0].segments[0]),
            actor=actor,
            request_context=_ctx(),
        )
    assert env.state["analysis"] is None
    assert "not-a-sha256" not in str(exc.value)
    task.input_snapshot["segment_refs"][0] = dict(original)
    task.input_snapshot["segment_refs"][0]["speaker_name"] = "候选人甲"
    with pytest.raises((InterviewValidationError, AIOutputValidationError)):
        await load_analysis_provider_input(env.session, task_id=task.id)


@pytest.mark.asyncio
async def test_dispatch_requires_committed_pending_analyze_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        dispatch_persisted_analysis_generation_task,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    env.enqueue.assert_not_called()
    with pytest.raises(InterviewNotFoundError):
        await dispatch_persisted_analysis_generation_task(
            env.session, task_id=uuid4()
        )
    env.enqueue.assert_not_called()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="dispatch-a",
        actor=_actor(),
        request_context=_ctx(),
    )
    env.enqueue.assert_not_called()
    env.session.commit.assert_not_called()
    task.task_type = "RESUME_SCORE"
    with pytest.raises(InterviewNotFoundError):
        await dispatch_persisted_analysis_generation_task(
            env.session, task_id=task.id
        )
    env.enqueue.assert_not_called()
    task.task_type = TASK_TYPE_INTERVIEW_ROUND_ANALYZE
    task.status = AI_TASK_STATUS_SUCCEEDED
    with pytest.raises(InterviewValidationError):
        await dispatch_persisted_analysis_generation_task(
            env.session, task_id=task.id
        )
    env.enqueue.assert_not_called()
    task.status = AI_TASK_STATUS_PENDING
    await env.session.commit()
    await dispatch_persisted_analysis_generation_task(env.session, task_id=task.id)
    env.enqueue.assert_called_once_with(task.id)
    audit_count = len(env.audits)
    await dispatch_persisted_analysis_generation_task(env.session, task_id=task.id)
    assert env.enqueue.call_count == 2
    assert len(env.added_tasks) == 1
    assert len(env.audits) == audit_count == 1


# ---------------------------------------------------------------------------
# D. 幂等与并发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_same_key_reuses_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same",
        actor=actor,
        request_context=_ctx(),
    )
    second = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same",
        actor=actor,
        request_context=_ctx(),
    )
    assert first.id == second.id
    assert len(env.added_tasks) == 1
    assert len(env.audits) == 1
    assert env.audits[0]["action"] == "interview_analysis.generate_requested"
    assert isinstance(env.added_idempotency[0], InterviewIdempotencyKey)


@pytest.mark.asyncio
async def test_same_key_different_hash_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same",
        actor=actor,
        request_context=_ctx(),
    )
    env.added_idempotency[0].request_hash = "other"
    with pytest.raises(InterviewIdempotencyConflictError):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="same",
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_inflight_same_hash_reused_different_hash_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="in-a",
        actor=actor,
        request_context=_ctx(),
    )
    second = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="in-b",
        actor=actor,
        request_context=_ctx(),
    )
    assert second.id == first.id
    env.state["inflight"].input_snapshot["input_snapshot_hash"] = "different"
    with pytest.raises(InterviewConflictError):
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="in-c",
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_round_lock_taken_and_unique_conflict_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import interview_analyses as svc_mod
    from app.services.interview_analyses import request_analysis_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="lock",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert svc_mod.get_round_for_update.await_count >= 1

    async def boom(_session, _key):
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(f"{MODULE}.add_idempotency", boom)
    env.state["inflight"] = None
    with pytest.raises(InterviewIdempotencyConflictError) as exc:
        await request_analysis_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="lock-2",
            actor=_actor(),
            request_context=_ctx(),
        )
    assert "duplicate key" not in str(exc.value).lower()


# ---------------------------------------------------------------------------
# E. Provider loader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_uses_frozen_c1_not_current_c2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        load_analysis_provider_input,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="load-c1",
        actor=_actor(),
        request_context=_ctx(),
    )
    c1_id = env.transcript.current_confirmed_version_id
    extra = _segment(
        version_id=c1_id,
        segment_no=9,
        text="后来新增的片段不应进入旧任务。",
    )
    env.transcript_versions[0].segments.append(extra)
    c2 = _confirmed_version(env.transcript, label="C2")
    env.transcript.current_confirmed_version_id = c2.id
    env.transcript_versions.append(c2)
    dto = await load_analysis_provider_input(env.session, task_id=task.id)
    assert dto.transcript_version_id == c1_id
    assert dto.transcript_version_id != c2.id
    assert [seg.segment_no for seg in dto.segments] == [1, 2]
    assert all("后来新增" not in seg.text for seg in dto.segments)
    assert "我当时先对齐目标。" in dto.segments[0].text
    blob = json.dumps(task.input_snapshot, ensure_ascii=False)
    assert "我当时先对齐目标" not in blob
    assert "候选人甲" not in blob


@pytest.mark.asyncio
async def test_loader_rejects_cross_version_or_excluded_or_bad_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        load_analysis_provider_input,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="load-bad",
        actor=_actor(),
        request_context=_ctx(),
    )
    task.input_snapshot["segment_refs"][0]["segment_no"] = 99
    with pytest.raises((InterviewValidationError, AIOutputValidationError)):
        await load_analysis_provider_input(env.session, task_id=task.id)
    task.input_snapshot["segment_refs"][0]["segment_no"] = 1
    task.input_snapshot["task_type"] = "RESUME_SCORE"
    with pytest.raises((InterviewNotFoundError, InterviewValidationError)):
        await load_analysis_provider_input(env.session, task_id=task.id)


# ---------------------------------------------------------------------------
# F. 持久化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_creates_a1_in_snapshot_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.crypto import decrypt_secret
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="p1",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    payload = _analysis_payload(segment=c1.segments[0], reverse=True)
    version = await persist_analysis_generation_result(
        env.session,
        task_id=task.id,
        payload=payload,
        actor=actor,
        request_context=_ctx(),
    )
    assert version.version_label == "A1"
    assert version.version_no == 1
    assert version.ai_task_id == task.id
    assert str(version.job_version_id) == task.input_snapshot["job_version_id"]
    assert version.overall_score == Decimal("4.60")
    assert env.state["analysis"].current_version_id == version.id
    assert [dim.dimension_key for dim in version.dimensions] == ["D001", "D002"]
    for dim in version.dimensions:
        assert dim.analysis_encrypted.startswith(CIPHER_PREFIX)
        assert SECRET_ANALYSIS not in (dim.analysis_encrypted or "")
        assert decrypt_secret(dim.analysis_encrypted)
    generated = [
        item for item in env.audits if item["action"] == "interview_analysis.generated"
    ]
    assert generated
    _assert_safe_audit(generated[0])
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_persist_null_actor_with_context_writes_generated_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="null-actor",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    version = await persist_analysis_generation_result(
        env.session,
        task_id=task.id,
        payload=_analysis_payload(segment=c1.segments[0]),
        actor=None,
        request_context=_ctx(),
    )
    assert version.version_no == 1
    generated = [
        item for item in env.audits if item["action"] == "interview_analysis.generated"
    ]
    assert len(generated) == 1
    assert generated[0]["actor_user_id"] is None
    assert generated[0]["resource_id"] == str(round_.id)
    _assert_safe_audit(generated[0])
    blob = json.dumps(generated, ensure_ascii=False, default=str)
    assert CIPHER_PREFIX not in blob
    changes = generated[0]["changes"]
    for forbidden in (
        "quote",
        "analysis",
        "overall_summary",
        "raw_request",
        "raw_response",
        "result_payload",
    ):
        assert forbidden not in changes
    assert changes["round_id"] == str(round_.id)
    assert changes["task_id"] == str(task.id)
    assert "dimension_count" in changes
    assert "evidence_count" in changes
    assert "overall_score" in changes


@pytest.mark.asyncio
async def test_persist_a2_and_idempotent_task_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    t1 = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="r1",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    a1 = await persist_analysis_generation_result(
        env.session,
        task_id=t1.id,
        payload=_analysis_payload(segment=c1.segments[0]),
        actor=actor,
        request_context=_ctx(),
    )
    replay = await persist_analysis_generation_result(
        env.session,
        task_id=t1.id,
        payload=_analysis_payload(segment=c1.segments[0]),
        actor=actor,
        request_context=_ctx(),
    )
    assert replay.id == a1.id
    generated = [
        item for item in env.audits if item["action"] == "interview_analysis.generated"
    ]
    assert len(generated) == 1
    t1.status = AI_TASK_STATUS_SUCCEEDED
    env.state["inflight"] = None
    t2 = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="r2",
        actor=actor,
        request_context=_ctx(),
    )
    a2 = await persist_analysis_generation_result(
        env.session,
        task_id=t2.id,
        payload=_analysis_payload(segment=c1.segments[0]),
        actor=actor,
        request_context=_ctx(),
    )
    assert a2.version_label == "A2"
    assert a1.version_label == "A1"
    assert env.state["analysis"].current_version_id == a2.id
    assert a1.id != a2.id


@pytest.mark.asyncio
async def test_overall_score_none_when_any_dimension_unscored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.crypto import decrypt_secret
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="score-none",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    version = await persist_analysis_generation_result(
        env.session,
        task_id=task.id,
        payload=_analysis_payload(segment=c1.segments[0], score_none=True),
        actor=actor,
        request_context=_ctx(),
    )
    assert version.overall_score is None
    d001 = next(dim for dim in version.dimensions if dim.dimension_key == "D001")
    assert d001.score is None
    assert d001.insufficient_information_encrypted
    assert decrypt_secret(d001.insufficient_information_encrypted)
    d002 = next(dim for dim in version.dimensions if dim.dimension_key == "D002")
    assert d002.score == 5
    assert d002.insufficient_information_encrypted is None


@pytest.mark.asyncio
async def test_invalid_output_does_not_create_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="bad-out",
        actor=actor,
        request_context=_ctx(),
    )
    payload = {
        "dimensions": [
            {
                "dimension_key": "D999",
                "score": 4,
                "evidence": [],
                "analysis": SECRET_ANALYSIS,
                "strengths": [],
                "risks": [],
                "insufficient_information": None,
                "suggested_follow_ups": [],
            }
        ],
        "overall_summary": SECRET_ANALYSIS,
    }
    with pytest.raises(AIOutputValidationError) as exc:
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=payload,
            actor=actor,
            request_context=_ctx(),
        )
    assert env.versions == []
    assert env.state["analysis"] is None
    assert SECRET_ANALYSIS not in str(exc.value)


@pytest.mark.asyncio
async def test_bad_evidence_and_encryption_failure_do_not_move_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.crypto import encrypt_secret as real_encrypt
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="ev-bad",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    excluded = next(seg for seg in c1.segments if not seg.is_included_in_analysis)
    payload = _analysis_payload(segment=c1.segments[0])
    payload["dimensions"][0]["evidence"][0]["segment_id"] = str(excluded.id)
    payload["dimensions"][0]["evidence"][0]["segment_no"] = excluded.segment_no
    payload["dimensions"][0]["evidence"][0]["quote"] = SECRET_QUOTE
    with pytest.raises(AIOutputValidationError) as exc:
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=payload,
            actor=actor,
            request_context=_ctx(),
        )
    assert SECRET_QUOTE not in str(exc.value)
    assert env.state["analysis"] is None

    def flaky(plain):
        if plain and "专业深度充分" in str(plain):
            raise EncryptionError("encryption failed")
        return real_encrypt(plain)

    monkeypatch.setattr(f"{MODULE}.encrypt_secret", flaky)
    with pytest.raises(EncryptionError):
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=_analysis_payload(segment=c1.segments[0]),
            actor=actor,
            request_context=_ctx(),
        )
    assert env.state["analysis"] is None or env.state["analysis"].current_version_id is None
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_evidence_write_failure_does_not_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="ev-write",
        actor=actor,
        request_context=_ctx(),
    )

    async def boom(*_a, **_k):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(f"{MODULE}.create_analysis_evidence", boom)
    c1 = env.transcript_versions[0]
    with pytest.raises(RuntimeError, match="db write failed"):
        await persist_analysis_generation_result(
            env.session,
            task_id=task.id,
            payload=_analysis_payload(segment=c1.segments[0]),
            actor=actor,
            request_context=_ctx(),
        )
    assert env.state["analysis"] is None or env.state["analysis"].current_version_id is None
    env.session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# G. 列表/详情/STALE
# ---------------------------------------------------------------------------


def _seed_version(env, *, transcript_version_id, version_no=1, actor_id=None):
    analysis = env.state["analysis"] or InterviewRoundAnalysis(
        id=uuid4(),
        interview_round_id=env.transcript.interview_round_id,
        current_version_id=None,
    )
    env.state["analysis"] = analysis
    version = InterviewRoundAnalysisVersion(
        id=uuid4(),
        analysis_id=analysis.id,
        version_no=version_no,
        version_label=f"A{version_no}",
        transcript_version_id=transcript_version_id,
        job_version_id=env.frozen.id,
        ai_task_id=uuid4(),
        dimensions_snapshot=[],
        overall_score=Decimal("4.60"),
        overall_summary_encrypted=encrypt_secret("整体表现稳定。"),
        created_by=actor_id,
        created_at=_now(),
    )
    dim = InterviewRoundAnalysisDimension(
        id=uuid4(),
        analysis_version_id=version.id,
        dimension_key="D001",
        dimension_name="协作",
        weight=Decimal("40.00"),
        score=4,
        analysis_encrypted=encrypt_secret("分析正文"),
        strengths_encrypted=encrypt_secret(json.dumps(["目标对齐"], ensure_ascii=False)),
        risks_encrypted=encrypt_secret(json.dumps(["细节偏少"], ensure_ascii=False)),
        insufficient_information_encrypted=None,
        suggested_follow_ups_encrypted=encrypt_secret(
            json.dumps(["追问"], ensure_ascii=False)
        ),
        display_order=1,
        created_at=_now(),
    )
    ev = InterviewRoundAnalysisEvidence(
        id=uuid4(),
        analysis_dimension_id=dim.id,
        transcript_segment_id=env.transcript_versions[0].segments[0].id,
        segment_no=1,
        quote_encrypted=encrypt_secret("我当时先对齐目标。"),
        created_at=_now(),
    )
    dim.evidence = [ev]
    version.dimensions = [dim]
    version.analysis = analysis
    analysis.versions = list(getattr(analysis, "versions", []) or [])
    analysis.versions.append(version)
    analysis.current_version_id = version.id
    env.versions.append(version)
    return version


@pytest.mark.asyncio
async def test_list_hides_body_and_marks_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import list_analysis_versions

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    a1 = _seed_version(env, transcript_version_id=env.transcript.current_confirmed_version_id)
    listed = await list_analysis_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    dumped = json.dumps(listed.__dict__, default=str)
    assert CIPHER_PREFIX not in dumped
    assert "整体表现稳定" not in dumped
    assert listed.versions[0].is_stale is False
    assert listed.versions[0].is_current is True
    assert listed.cache_control == "no-store"
    c2 = _confirmed_version(env.transcript, label="C2")
    env.transcript.current_confirmed_version_id = c2.id
    env.transcript_versions.append(c2)
    listed2 = await list_analysis_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    assert listed2.versions[0].version_id == a1.id
    assert listed2.versions[0].is_stale is True
    a2 = _seed_version(
        env, transcript_version_id=c2.id, version_no=2
    )
    listed3 = await list_analysis_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    by_id = {item.version_id: item for item in listed3.versions}
    assert by_id[a1.id].is_stale is True
    assert by_id[a2.id].is_stale is False


@pytest.mark.asyncio
async def test_analysis_stale_flag_still_dynamic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import list_analysis_versions

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    a1 = _seed_version(
        env, transcript_version_id=env.transcript.current_confirmed_version_id
    )
    listed = await list_analysis_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    assert listed.versions[0].is_stale is False
    c2 = _confirmed_version(env.transcript, label="C2")
    env.transcript.current_confirmed_version_id = c2.id
    env.transcript_versions.append(c2)
    listed2 = await list_analysis_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    assert listed2.versions[0].version_id == a1.id
    assert listed2.versions[0].is_stale is True


@pytest.mark.asyncio
async def test_detail_decrypts_and_cross_round_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import get_analysis_version_detail

    round_ = _make_round(status=INTERVIEW_STATUS_SCHEDULED)
    env = _patch_base(monkeypatch, round_)
    version = _seed_version(
        env, transcript_version_id=env.transcript.current_confirmed_version_id
    )
    detail = await get_analysis_version_detail(
        env.session,
        round_id=round_.id,
        version_id=version.id,
        actor=_actor(),
    )
    dumped = json.dumps(detail.__dict__, default=str)
    assert CIPHER_PREFIX not in dumped
    assert detail.overall_summary == "整体表现稳定。"
    assert detail.cache_control == "no-store"
    other = _make_round()
    with pytest.raises(InterviewNotFoundError):
        await get_analysis_version_detail(
            env.session,
            round_id=other.id,
            version_id=version.id,
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_detail_decrypt_failure_safe_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import get_analysis_version_detail

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    version = _seed_version(
        env, transcript_version_id=env.transcript.current_confirmed_version_id
    )
    version.overall_summary_encrypted = SECRET_CIPHER
    with pytest.raises(InterviewValidationError) as exc:
        await get_analysis_version_detail(
            env.session,
            round_id=round_.id,
            version_id=version.id,
            actor=_actor(),
        )
    assert SECRET_CIPHER not in str(exc.value)


@pytest.mark.asyncio
async def test_execute_cannot_read_unassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_analyses import list_analysis_versions

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=False)
    with pytest.raises(InterviewNotFoundError):
        await list_analysis_versions(
            env.session,
            round_id=round_.id,
            actor=_actor(manage=False, execute=True),
        )


# ---------------------------------------------------------------------------
# H. 审计
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_events_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import sanitize_audit_changes
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="aud",
        actor=actor,
        request_context=_ctx(),
    )
    c1 = env.transcript_versions[0]
    await persist_analysis_generation_result(
        env.session,
        task_id=task.id,
        payload=_analysis_payload(segment=c1.segments[0]),
        actor=actor,
        request_context=_ctx(),
    )
    actions = {item["action"] for item in env.audits}
    assert actions == {
        "interview_analysis.generate_requested",
        "interview_analysis.generated",
    }
    for entry in env.audits:
        _assert_safe_audit(entry)
        changes = sanitize_audit_changes(entry["changes"])
        assert changes["task_type"] == TASK_TYPE_INTERVIEW_ROUND_ANALYZE


@pytest.mark.asyncio
async def test_analysis_audit_changes_pass_sanitize_audit_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models import sanitize_audit_changes
    from app.services.interview_analyses import (
        persist_analysis_generation_result,
        request_analysis_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_analysis_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="aud-sanitize",
        actor=actor,
        request_context=_ctx(),
    )
    await persist_analysis_generation_result(
        env.session,
        task_id=task.id,
        payload=_analysis_payload(segment=env.transcript_versions[0].segments[0]),
        actor=actor,
        request_context=_ctx(),
    )
    assert env.audits
    for entry in env.audits:
        sanitized = sanitize_audit_changes(entry["changes"])
        assert sanitized == entry["changes"]
        blob = json.dumps(sanitized, ensure_ascii=False, default=str)
        assert SECRET_ANALYSIS not in blob
        assert SECRET_QUOTE not in blob
        assert CIPHER_PREFIX not in blob
