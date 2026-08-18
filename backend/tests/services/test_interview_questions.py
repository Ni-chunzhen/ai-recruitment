"""Service-layer tests for interview question outline generate/edit/confirm."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    AI_TASK_STATUS_SUCCEEDED,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    AITask,
)
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.interview import (
    INTERVIEW_STATUS_CANCELLED,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_DRAFT,
    INTERVIEW_STATUS_ENDED_ABNORMALLY,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_PENDING_TRANSCRIPT,
    INTERVIEW_STATUS_SCHEDULED,
    InterviewIdempotencyKey,
    InterviewRound,
    InterviewRoundInterviewer,
)
from app.models.interview_ai import (
    QUESTION_SET_STATUS_DRAFT,
    QUESTION_SET_STATUS_READY,
    QUESTION_SOURCE_AI_GENERATED,
    QUESTION_SOURCE_MANUAL_EDIT,
    InterviewQuestionItem,
    InterviewQuestionSet,
    InterviewQuestionVersion,
)
from app.models.resume import PIPELINE_INTERVIEWING, RESUME_STATUS_CONFIRMED
from app.services.audit import RequestContext, _scrub_value
from app.services.crypto import CIPHER_PREFIX, decrypt_secret
from app.services.interview_ai_validation import AIOutputValidationError
from app.services.interviews import (
    InterviewConflictError,
    InterviewIdempotencyConflictError,
    InterviewNotFoundError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)

MODULE = "app.services.interview_questions"

FORBIDDEN_SNAPSHOT_MARKERS = (
    "JD 正文",
    "简历正文",
    "张三",
    "李面试官",
    "zhang@example.com",
    "13800138000",
    "enc:v1:",
    "sk-live",
    "请描述一次跨团队冲突处理",
    "secret-token",
)
FORBIDDEN_SNAPSHOT_KEYS = {
    "jd_content",
    "raw_jd_text",
    "structured_jd",
    "resume_text",
    "standardized_text",
    "candidate_name",
    "interviewer_name",
    "email",
    "phone",
    "question",
    "purpose",
    "resume_evidence",
}


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
    return RequestContext(request_id="req-q1", ip_address="127.0.0.1")


def _now() -> datetime:
    return datetime(2026, 8, 17, 7, 0, tzinfo=UTC)


def _dimensions():
    return [
        {
            "name": "协作",
            "weight": 40,
            "description": "跨团队协作",
            "anchors": ["弱", "一般"],
        },
        {
            "name": "专业",
            "weight": 60,
            "description": "专业深度",
            "anchors": [],
        },
    ]


def _application(*, resume_version_id=..., candidate_id=None):
    candidate_id = candidate_id or uuid4()
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
        resume_version_id=uuid4() if resume_version_id is ... else resume_version_id,
        pipeline_status=PIPELINE_INTERVIEWING,
        status=APPLICATION_STATUS_IN_PROGRESS,
        lock_version=1,
    )


def _make_round(
    *,
    status: str = INTERVIEW_STATUS_SCHEDULED,
    application_id=None,
    job_version_id=None,
) -> InterviewRound:
    now = _now()
    round_ = InterviewRound(
        id=uuid4(),
        application_id=application_id or uuid4(),
        job_version_id=job_version_id or uuid4(),
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


def _job_bundle(*, frozen_id, current_id=None):
    frozen = SimpleNamespace(
        id=frozen_id,
        raw_jd_text="JD 正文不应进入 snapshot",
        structured_jd={"responsibilities": ["秘密职责"]},
        score_dimensions=_dimensions(),
        version_label="V1.0",
        major=1,
        minor=0,
        status="published",
    )
    newer = SimpleNamespace(
        id=current_id or uuid4(),
        raw_jd_text="新版 JD 正文",
        structured_jd={"responsibilities": ["新职责"]},
        score_dimensions=[
            {
                "name": "新维度",
                "weight": 100,
                "description": "不应被题纲使用",
                "anchors": ["1", "2", "3", "4", "5"],
            }
        ],
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


def _resume_version(*, version_id, candidate_id, status=RESUME_STATUS_CONFIRMED):
    parent = SimpleNamespace(id=uuid4(), candidate_id=candidate_id)
    return SimpleNamespace(
        id=version_id,
        resume_id=parent.id,
        resume=parent,
        status=status,
        version_label="C1",
        standardized_text="简历正文不应进入 snapshot",
        confirmed_content={
            "standardized_text": "简历正文不应进入 snapshot",
            "name": "张三",
            "phone": "13800138000",
            "email": "zhang@example.com",
        },
        extracted_text="提取正文",
    )


def _question_item(*, dimension_key="D001", display_order=1, **overrides):
    item = {
        "dimension_key": dimension_key,
        "question": f"请说明维度 {dimension_key} 的协作案例。",
        "purpose": "考察协作与冲突处理。",
        "evidence_source": "JOB_REQUIREMENT",
        "resume_evidence": None,
        "follow_up_prompts": ["对方立场是什么？"],
        "risk_flags": ["可能回避责任"],
        "display_order": display_order,
    }
    item.update(overrides)
    return item


def _questions_payload(keys=("D001", "D002")) -> dict:
    return {
        "questions": [
            _question_item(dimension_key=key, display_order=index)
            for index, key in enumerate(keys, start=1)
        ]
    }


def _encrypt_fields(version: InterviewQuestionVersion) -> None:
    from app.services.crypto import encrypt_secret

    for item in version.items:
        if not str(item.question_encrypted).startswith(CIPHER_PREFIX):
            item.question_encrypted = encrypt_secret(item.question_encrypted)
        if not str(item.purpose_encrypted).startswith(CIPHER_PREFIX):
            item.purpose_encrypted = encrypt_secret(item.purpose_encrypted)
        if item.resume_evidence_encrypted and not str(
            item.resume_evidence_encrypted
        ).startswith(CIPHER_PREFIX):
            item.resume_evidence_encrypted = encrypt_secret(
                item.resume_evidence_encrypted
            )
        if not str(item.follow_up_prompts_encrypted).startswith(CIPHER_PREFIX):
            item.follow_up_prompts_encrypted = encrypt_secret(
                item.follow_up_prompts_encrypted
            )
        if not str(item.risk_flags_encrypted).startswith(CIPHER_PREFIX):
            item.risk_flags_encrypted = encrypt_secret(item.risk_flags_encrypted)


def _make_set_with_version(
    round_: InterviewRound,
    *,
    status: str = QUESTION_SET_STATUS_DRAFT,
    version_no: int = 1,
    source_type: str = QUESTION_SOURCE_AI_GENERATED,
    ai_task_id=None,
    confirmed: bool = False,
    actor_id=None,
    job_version_id=None,
    resume_version_id=None,
    input_snapshot_hash: str = "abc123",
) -> tuple[InterviewQuestionSet, InterviewQuestionVersion]:
    now = _now()
    qset = InterviewQuestionSet(
        id=uuid4(),
        interview_round_id=round_.id,
        current_version_id=None,
        status=status,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )
    if confirmed:
        qset.confirmed_by = actor_id or uuid4()
        qset.confirmed_at = now
    version = InterviewQuestionVersion(
        id=uuid4(),
        question_set_id=qset.id,
        version_no=version_no,
        version_label=f"Q{version_no}",
        source_type=source_type,
        ai_task_id=ai_task_id or (uuid4() if source_type == QUESTION_SOURCE_AI_GENERATED else None),
        job_version_id=job_version_id or round_.job_version_id,
        resume_version_id=resume_version_id or uuid4(),
        input_snapshot_hash=input_snapshot_hash,
        created_by=actor_id,
        created_at=now,
    )
    version.items = [
        InterviewQuestionItem(
            id=uuid4(),
            question_version_id=version.id,
            dimension_key="D001",
            question_encrypted="请说明协作案例。",
            purpose_encrypted="考察协作。",
            evidence_source="JOB_REQUIREMENT",
            resume_evidence_encrypted=None,
            follow_up_prompts_encrypted=json.dumps(["追问1"], ensure_ascii=False),
            risk_flags_encrypted=json.dumps(["风险1"], ensure_ascii=False),
            display_order=1,
            created_at=now,
        )
    ]
    _encrypt_fields(version)
    version.question_set = qset
    qset.current_version_id = version.id
    qset.current_version = version
    qset.versions = [version]
    return qset, version


def _patch_base(
    monkeypatch: pytest.MonkeyPatch,
    round_: InterviewRound,
    *,
    application=None,
    job=None,
    resume_version=None,
    existing_idempotency=None,
    inflight=None,
    question_set=None,
    versions=None,
    assigned: bool = True,
    tasks=None,
):
    from app.repositories.jobs import get_version_by_id

    application = application or _application()
    application.id = round_.application_id
    if job is None:
        job, frozen, _newer = _job_bundle(frozen_id=round_.job_version_id)
    else:
        frozen = get_version_by_id(job, round_.job_version_id)
    if resume_version is None:
        resume_version = _resume_version(
            version_id=application.resume_version_id,
            candidate_id=application.candidate_id,
        )

    audits: list[dict] = []
    added_objects: list[object] = []
    added_idempotency: list[InterviewIdempotencyKey] = []
    added_tasks: list[AITask] = list(tasks or [])
    versions = versions if versions is not None else list(
        getattr(question_set, "versions", None) or []
    )
    state = {
        "idempotency": existing_idempotency,
        "inflight": inflight,
        "question_set": question_set,
        "ai_results_called": 0,
    }

    async def fake_record_audit(_session, **kwargs):
        audits.append(kwargs)

    async def fake_add_idempotency(_session, key):
        added_idempotency.append(key)
        state["idempotency"] = key
        return key

    async def fake_find_idempotency(
        _session,
        *,
        actor_id,
        action,
        scope_id,
        idempotency_key,
    ):
        for record in added_idempotency:
            if (
                record.actor_id == actor_id
                and record.action == action
                and record.scope_id == scope_id
                and record.idempotency_key == idempotency_key
            ):
                return record
        current = state["idempotency"]
        if current is None:
            return None
        if (
            current.actor_id == actor_id
            and current.action == action
            and current.scope_id == scope_id
            and current.idempotency_key == idempotency_key
        ):
            return current
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
        for task in reversed(added_tasks):
            snapshot = task.input_snapshot or {}
            if snapshot.get("input_snapshot_hash") == needle:
                return task
        if state["inflight"] is not None:
            snapshot = state["inflight"].input_snapshot or {}
            if snapshot.get("input_snapshot_hash") == needle:
                return state["inflight"]
        return None

    async def fake_get_ai_task(_session, task_id, **_kwargs):
        for task in added_tasks:
            if task.id == task_id:
                return task
        if state["inflight"] is not None and state["inflight"].id == task_id:
            return state["inflight"]
        return None

    async def fake_get_set(_session, round_id):
        current = state["question_set"]
        if current is not None and current.interview_round_id == round_id:
            return current
        return None

    async def fake_get_set_for_update(_session, round_id):
        return await fake_get_set(_session, round_id)

    async def fake_create_set(_session, qset):
        if getattr(qset, "id", None) is None:
            qset.id = uuid4()
        if getattr(qset, "versions", None) is None:
            qset.versions = []
        state["question_set"] = qset
        added_objects.append(qset)
        return qset

    async def fake_create_version(_session, version):
        if getattr(version, "id", None) is None:
            version.id = uuid4()
        if getattr(version, "items", None) is None:
            version.items = []
        versions.append(version)
        current = state["question_set"]
        if current is not None:
            current.versions = list(getattr(current, "versions", []) or [])
            current.versions.append(version)
            version.question_set = current
        added_objects.append(version)
        return version

    async def fake_create_items(_session, items):
        for item in items:
            if getattr(item, "id", None) is None:
                item.id = uuid4()
            added_objects.append(item)
            for version in versions:
                if version.id == item.question_version_id:
                    version.items = list(version.items or [])
                    version.items.append(item)
        return items

    async def fake_next_no(_session, question_set_id):
        nos = [
            item.version_no
            for item in versions
            if item.question_set_id == question_set_id
        ]
        return (max(nos) if nos else 0) + 1

    async def fake_get_version(_session, *, round_id, version_id):
        current = state["question_set"]
        if current is None or current.interview_round_id != round_id:
            return None
        for item in versions:
            if item.id == version_id and item.question_set_id == current.id:
                return item
        return None

    async def fake_get_version_by_task(_session, ai_task_id, **kwargs):
        round_id = kwargs.get("round_id")
        current = state["question_set"]
        for item in versions:
            if item.ai_task_id != ai_task_id:
                continue
            if round_id is not None and (
                current is None or current.interview_round_id != round_id
            ):
                continue
            return item
        return None

    async def fake_list_versions(_session, round_id):
        current = state["question_set"]
        if current is None or current.interview_round_id != round_id:
            return []
        return [
            item for item in versions if item.question_set_id == current.id
        ]

    async def fake_ai_results(*_args, **_kwargs):
        state["ai_results_called"] += 1
        raise AssertionError("must not read resume AiResult")

    enqueue = MagicMock()
    monkeypatch.setattr(f"{MODULE}.get_round_for_update", AsyncMock(return_value=round_))
    monkeypatch.setattr(f"{MODULE}.get_round_by_id", AsyncMock(return_value=round_))
    monkeypatch.setattr(
        f"{MODULE}.get_application_by_id", AsyncMock(return_value=application)
    )
    monkeypatch.setattr(f"{MODULE}.get_job_by_id", AsyncMock(return_value=job))
    monkeypatch.setattr(f"{MODULE}.get_version_by_id", get_version_by_id)
    monkeypatch.setattr(
        f"{MODULE}.get_resume_version_by_id",
        AsyncMock(return_value=resume_version),
    )
    monkeypatch.setattr(
        f"{MODULE}.actor_assigned_to_round",
        AsyncMock(return_value=assigned),
    )
    monkeypatch.setattr(f"{MODULE}.find_idempotency", fake_find_idempotency)
    monkeypatch.setattr(f"{MODULE}.add_idempotency", fake_add_idempotency)
    monkeypatch.setattr(f"{MODULE}.record_audit", fake_record_audit)
    monkeypatch.setattr(f"{MODULE}.add_ai_task", fake_add_ai_task)
    monkeypatch.setattr(f"{MODULE}.find_inflight_task", fake_find_inflight)
    monkeypatch.setattr(
        f"{MODULE}.find_task_by_input_snapshot_hash", fake_find_by_hash
    )
    monkeypatch.setattr(f"{MODULE}.get_ai_task_by_id", fake_get_ai_task)
    monkeypatch.setattr(f"{MODULE}.enqueue_ai_task", enqueue)
    monkeypatch.setattr(f"{MODULE}.get_question_set_by_round", fake_get_set)
    monkeypatch.setattr(
        f"{MODULE}.get_question_set_for_update", fake_get_set_for_update
    )
    monkeypatch.setattr(f"{MODULE}.create_question_set", fake_create_set)
    monkeypatch.setattr(f"{MODULE}.create_question_version", fake_create_version)
    monkeypatch.setattr(f"{MODULE}.create_question_items", fake_create_items)
    monkeypatch.setattr(f"{MODULE}.next_question_version_no", fake_next_no)
    monkeypatch.setattr(f"{MODULE}.get_question_version_by_id", fake_get_version)
    monkeypatch.setattr(
        f"{MODULE}.get_question_version_by_task_id", fake_get_version_by_task
    )
    monkeypatch.setattr(f"{MODULE}.list_question_versions_rows", fake_list_versions)
    monkeypatch.setattr(f"{MODULE}.list_ai_results", fake_ai_results, raising=False)
    monkeypatch.setattr(f"{MODULE}.get_ai_result_by_id", fake_ai_results, raising=False)

    session = AsyncMock()
    return SimpleNamespace(
        session=session,
        application=application,
        job=job,
        frozen=frozen,
        resume_version=resume_version,
        audits=audits,
        added_objects=added_objects,
        added_idempotency=added_idempotency,
        added_tasks=added_tasks,
        versions=versions,
        enqueue=enqueue,
        state=state,
    )


def _snapshot_blob(task: AITask) -> str:
    return json.dumps(task.input_snapshot, ensure_ascii=False, default=str)


def _assert_safe_snapshot(snapshot: dict) -> None:
    blob = json.dumps(snapshot, ensure_ascii=False, default=str)
    for marker in FORBIDDEN_SNAPSHOT_MARKERS:
        assert marker not in blob
    for key in FORBIDDEN_SNAPSHOT_KEYS:
        assert key not in snapshot
    assert snapshot["task_type"] == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    assert snapshot["schema_version"]
    assert snapshot["round_id"]
    assert snapshot["job_version_id"]
    assert snapshot["resume_version_id"]
    assert snapshot["workflow_key"]
    assert snapshot["workflow_version"]
    assert snapshot["input_snapshot_hash"]
    assert snapshot["dimensions"]
    assert snapshot["dimensions"][0]["dimension_key"] == "D001"
    assert snapshot["dimensions"][1]["dimension_key"] == "D002"
    assert "name" in snapshot["dimensions"][0]
    assert "weight" in snapshot["dimensions"][0]
    assert "description" in snapshot["dimensions"][0]
    assert "anchors" in snapshot["dimensions"][0]
    assert "display_order" in snapshot["dimensions"][0]


def _assert_safe_audit(entry: dict) -> None:
    from app.models import sanitize_audit_changes

    changes = entry["changes"]
    blob = json.dumps(changes, ensure_ascii=False, default=str)
    assert CIPHER_PREFIX not in blob
    assert "请说明" not in blob
    assert "考察协作" not in blob
    assert "简历正文" not in blob
    assert "JD 正文" not in blob
    assert "zhang@example.com" not in blob
    sanitize_audit_changes(changes)
    scrubbed = _scrub_value(changes)
    assert CIPHER_PREFIX not in json.dumps(scrubbed, ensure_ascii=False, default=str)
    if "question_count" in changes:
        assert scrubbed["question_count"] == changes["question_count"]
    if "task_type" in changes:
        assert scrubbed["task_type"] == TASK_TYPE_INTERVIEW_QUESTION_GENERATE


# ---------------------------------------------------------------------------
# A. 输入冻结
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_freezes_round_job_version_not_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    frozen_id = uuid4()
    round_ = _make_round(job_version_id=frozen_id)
    job, frozen, newer = _job_bundle(frozen_id=frozen_id)
    env = _patch_base(monkeypatch, round_, job=job)

    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="gen-1",
        actor=_actor(),
        request_context=_ctx(),
    )

    snapshot = task.input_snapshot
    _assert_safe_snapshot(snapshot)
    assert snapshot["job_version_id"] == str(frozen.id)
    assert snapshot["job_version_id"] != str(newer.id)
    assert snapshot["job_version_id"] != str(job.current_version_id)
    assert snapshot["dimensions"][0]["name"] == "协作"
    assert snapshot["dimensions"][0]["description"] == "跨团队协作"
    assert snapshot["resume_version_id"] == str(env.application.resume_version_id)
    assert snapshot["round_id"] == str(round_.id)
    assert env.state["ai_results_called"] == 0
    env.session.commit.assert_not_called()
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_input_snapshot_hash_is_stable_for_same_logical_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()

    first = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="gen-a",
        actor=actor,
        request_context=_ctx(),
    )
    env.state["inflight"] = None
    env.state["idempotency"] = None
    second = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="gen-b",
        actor=actor,
        request_context=_ctx(),
    )
    assert (
        first.input_snapshot["input_snapshot_hash"]
        == second.input_snapshot["input_snapshot_hash"]
    )
    assert first.input_snapshot["dimensions"][0]["dimension_key"] == "D001"


# ---------------------------------------------------------------------------
# B. 权限和状态
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manage_can_generate_unassigned_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=False)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="gen-manage",
        actor=_actor(manage=True, execute=False),
        request_context=_ctx(),
    )
    assert task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE


@pytest.mark.asyncio
async def test_assigned_execute_can_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=True)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="gen-exec",
        actor=_actor(manage=False, execute=True),
        request_context=_ctx(),
    )
    assert task.business_id == round_.id


@pytest.mark.asyncio
async def test_unassigned_execute_is_object_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_, assigned=False)
    with pytest.raises(InterviewNotFoundError):
        await request_question_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="gen-404",
            actor=_actor(manage=False, execute=True),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_missing_round_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    monkeypatch.setattr(f"{MODULE}.get_round_for_update", AsyncMock(return_value=None))
    with pytest.raises(InterviewNotFoundError):
        await request_question_generation(
            env.session,
            round_id=uuid4(),
            idempotency_key="gen-missing",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.parametrize(
    "status",
    [
        INTERVIEW_STATUS_DRAFT,
        INTERVIEW_STATUS_PENDING_TRANSCRIPT,
        INTERVIEW_STATUS_COMPLETED,
        INTERVIEW_STATUS_CANCELLED,
        INTERVIEW_STATUS_ENDED_ABNORMALLY,
    ],
)
@pytest.mark.asyncio
async def test_illegal_round_status_rejects_generate(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round(status=status)
    env = _patch_base(monkeypatch, round_)
    with pytest.raises(InterviewValidationError):
        await request_question_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="gen-status",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.parametrize(
    "status",
    [INTERVIEW_STATUS_SCHEDULED, INTERVIEW_STATUS_CONFIRMED, INTERVIEW_STATUS_IN_PROGRESS],
)
@pytest.mark.asyncio
async def test_allowed_round_status_can_generate(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round(status=status)
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key=f"gen-{status}",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert task.status == AI_TASK_STATUS_PENDING


@pytest.mark.asyncio
async def test_missing_confirmed_resume_returns_actionable_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        MISSING_CONFIRMED_RESUME_MESSAGE,
        request_question_generation,
    )

    round_ = _make_round()
    application = _application(resume_version_id=None)
    env = _patch_base(monkeypatch, round_, application=application)
    with pytest.raises(InterviewValidationError, match=MISSING_CONFIRMED_RESUME_MESSAGE):
        await request_question_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="gen-no-resume",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_resume_not_belonging_to_candidate_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    application = _application()
    foreign = _resume_version(
        version_id=application.resume_version_id,
        candidate_id=uuid4(),
    )
    env = _patch_base(
        monkeypatch, round_, application=application, resume_version=foreign
    )
    with pytest.raises(InterviewValidationError):
        await request_question_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="gen-foreign-resume",
            actor=_actor(),
            request_context=_ctx(),
        )


def test_service_does_not_read_ai_results_or_transcripts() -> None:
    import app.services.interview_questions as module

    source = inspect.getsource(module)
    assert "list_ai_results" not in source
    assert "get_ai_result" not in source
    assert "AiResult" not in source
    assert "interview_transcripts" not in source
    assert "InterviewTranscript" not in source


# ---------------------------------------------------------------------------
# C. 幂等和并发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_idempotency_key_and_hash_returns_same_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same-key",
        actor=actor,
        request_context=_ctx(),
    )
    second = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same-key",
        actor=actor,
        request_context=_ctx(),
    )
    assert first.id == second.id
    assert len(env.added_tasks) == 1
    assert len(env.audits) == 1
    assert env.audits[0]["action"] == "interview_question.generate_requested"
    assert len(env.added_idempotency) == 1
    assert isinstance(env.added_idempotency[0], InterviewIdempotencyKey)


@pytest.mark.asyncio
async def test_same_key_different_request_hash_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="same-key",
        actor=actor,
        request_context=_ctx(),
    )
    env.state["idempotency"].request_hash = "different-hash"
    with pytest.raises(InterviewIdempotencyConflictError):
        await request_question_generation(
            env.session,
            round_id=round_.id,
            idempotency_key="same-key",
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_inflight_same_input_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="inflight-a",
        actor=actor,
        request_context=_ctx(),
    )
    env.state["idempotency"] = None
    second = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="inflight-b",
        actor=actor,
        request_context=_ctx(),
    )
    assert second.id == first.id
    assert len(env.added_tasks) == 1
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_different_input_version_creates_new_task_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="v1",
        actor=actor,
        request_context=_ctx(),
    )
    first.status = AI_TASK_STATUS_SUCCEEDED
    env.state["inflight"] = None
    env.state["idempotency"] = None
    env.application.resume_version_id = uuid4()
    env.resume_version.id = env.application.resume_version_id
    second = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="v2",
        actor=actor,
        request_context=_ctx(),
    )
    assert second.id != first.id
    assert (
        second.input_snapshot["input_snapshot_hash"]
        != first.input_snapshot["input_snapshot_hash"]
    )


@pytest.mark.asyncio
async def test_concurrent_generate_reuses_single_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    first = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="c1",
        actor=actor,
        request_context=_ctx(),
    )
    env.state["idempotency"] = None
    second = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="c2",
        actor=actor,
        request_context=_ctx(),
    )
    pending = [
        task
        for task in env.added_tasks
        if task.status in {AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING}
    ]
    assert first.id == second.id
    assert len(pending) == 1
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_generated_task_fields_and_round_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="fields",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    assert task.business_type == BUSINESS_TYPE_INTERVIEW_ROUND
    assert task.business_id == round_.id
    assert task.status == AI_TASK_STATUS_PENDING
    _assert_safe_snapshot(task.input_snapshot)
    assert env.session.commit.call_count == 0
    env.enqueue.assert_not_called()
    from app.services import interview_questions as svc_mod

    assert svc_mod.get_round_for_update.await_count >= 1


def test_request_question_generation_source_does_not_enqueue() -> None:
    from app.services.interview_questions import (
        dispatch_persisted_question_generation_task,
        request_question_generation,
    )

    request_source = inspect.getsource(request_question_generation)
    dispatch_source = inspect.getsource(dispatch_persisted_question_generation_task)
    assert "enqueue_ai_task" not in request_source
    assert "enqueue_ai_task" in dispatch_source


@pytest.mark.asyncio
async def test_request_flushes_pending_task_without_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import request_question_generation

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="no-enqueue",
        actor=_actor(),
        request_context=_ctx(),
    )
    assert task.id is not None
    assert task.status == AI_TASK_STATUS_PENDING
    assert task.task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    env.session.flush.assert_awaited()
    env.session.commit.assert_not_called()
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_after_request_leaves_no_task_and_no_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        dispatch_persisted_question_generation_task,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="rollback",
        actor=_actor(),
        request_context=_ctx(),
    )
    task_id = task.id
    env.enqueue.assert_not_called()
    env.added_tasks.clear()
    env.state["inflight"] = None
    env.session.rollback.assert_not_called()
    await env.session.rollback()
    with pytest.raises(InterviewNotFoundError):
        await dispatch_persisted_question_generation_task(
            env.session, task_id=task_id
        )
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_requires_persisted_pending_question_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        dispatch_persisted_question_generation_task,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    with pytest.raises(InterviewNotFoundError):
        await dispatch_persisted_question_generation_task(
            env.session, task_id=uuid4()
        )
    env.enqueue.assert_not_called()

    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="dispatch-guard",
        actor=_actor(),
        request_context=_ctx(),
    )
    env.enqueue.assert_not_called()
    task.task_type = "RESUME_SCORE"
    with pytest.raises((InterviewNotFoundError, InterviewValidationError)):
        await dispatch_persisted_question_generation_task(
            env.session, task_id=task.id
        )
    env.enqueue.assert_not_called()
    task.task_type = TASK_TYPE_INTERVIEW_QUESTION_GENERATE
    task.status = AI_TASK_STATUS_SUCCEEDED
    with pytest.raises((InterviewNotFoundError, InterviewValidationError)):
        await dispatch_persisted_question_generation_task(
            env.session, task_id=task.id
        )
    env.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_after_commit_enqueues_once_and_repeat_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        dispatch_persisted_question_generation_task,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="dispatch-ok",
        actor=_actor(),
        request_context=_ctx(),
    )
    env.enqueue.assert_not_called()
    await env.session.commit()
    await dispatch_persisted_question_generation_task(env.session, task_id=task.id)
    env.enqueue.assert_called_once_with(task.id)
    assert len(env.added_tasks) == 1
    audit_count = len(env.audits)
    await dispatch_persisted_question_generation_task(env.session, task_id=task.id)
    assert env.enqueue.call_count == 2
    assert env.enqueue.call_args_list[1].args == (task.id,)
    assert len(env.added_tasks) == 1
    assert len(env.audits) == audit_count
    assert audit_count == 1


# ---------------------------------------------------------------------------
# D. AI 结果持久化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_first_success_creates_q1_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="persist-1",
        actor=actor,
        request_context=_ctx(),
    )
    version = await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    qset = env.state["question_set"]
    assert version.version_label == "Q1"
    assert version.version_no == 1
    assert version.source_type == QUESTION_SOURCE_AI_GENERATED
    assert version.ai_task_id == task.id
    assert version.job_version_id == uuid4().__class__(task.input_snapshot["job_version_id"])
    assert str(version.resume_version_id) == task.input_snapshot["resume_version_id"]
    assert version.input_snapshot_hash == task.input_snapshot["input_snapshot_hash"]
    assert qset.status == QUESTION_SET_STATUS_DRAFT
    assert qset.current_version_id == version.id
    assert qset.confirmed_by is None
    assert qset.confirmed_at is None
    assert len(version.items) == 2
    for item in version.items:
        assert item.question_encrypted.startswith(CIPHER_PREFIX)
        assert item.purpose_encrypted.startswith(CIPHER_PREFIX)
        assert item.follow_up_prompts_encrypted.startswith(CIPHER_PREFIX)
        assert item.risk_flags_encrypted.startswith(CIPHER_PREFIX)
        assert decrypt_secret(item.question_encrypted)
        assert CIPHER_PREFIX not in decrypt_secret(item.question_encrypted)
    generated = [entry for entry in env.audits if entry["action"] == "interview_question.generated"]
    assert generated
    _assert_safe_audit(generated[0])
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_persist_invalid_output_does_not_create_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="persist-bad",
        actor=actor,
        request_context=_ctx(),
    )
    with pytest.raises(AIOutputValidationError) as exc:
        await persist_question_generation_result(
            env.session,
            task_id=task.id,
            payload=_questions_payload(keys=("D999",)),
            actor=actor,
            request_context=_ctx(),
        )
    assert env.state["question_set"] is None
    assert env.versions == []
    message = str(exc.value)
    assert "请说明" not in message
    assert "D999" in message or "dimension" in message.lower() or "unknown" in message.lower()
    assert "简历正文" not in message


@pytest.mark.asyncio
async def test_persist_does_not_clear_ready_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    actor = _actor()
    qset, current = _make_set_with_version(
        round_,
        status=QUESTION_SET_STATUS_READY,
        confirmed=True,
        actor_id=actor.id,
        resume_version_id=uuid4(),
    )
    env = _patch_base(
        monkeypatch, round_, question_set=qset, versions=[current]
    )
    env.application.resume_version_id = current.resume_version_id
    env.resume_version.id = current.resume_version_id
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="persist-keep-ready",
        actor=actor,
        request_context=_ctx(),
    )
    with pytest.raises(AIOutputValidationError):
        await persist_question_generation_result(
            env.session,
            task_id=task.id,
            payload={"questions": []},
            actor=actor,
            request_context=_ctx(),
        )
    assert qset.status == QUESTION_SET_STATUS_READY
    assert qset.current_version_id == current.id
    assert qset.confirmed_by == actor.id
    assert qset.confirmed_at is not None


@pytest.mark.asyncio
async def test_persist_regen_creates_qn_and_returns_to_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    actor = _actor()
    env = _patch_base(monkeypatch, round_)
    first_task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="q1",
        actor=actor,
        request_context=_ctx(),
    )
    q1 = await persist_question_generation_result(
        env.session,
        task_id=first_task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    qset = env.state["question_set"]
    qset.status = QUESTION_SET_STATUS_READY
    qset.confirmed_by = actor.id
    qset.confirmed_at = _now()
    first_task.status = AI_TASK_STATUS_SUCCEEDED
    env.state["inflight"] = None
    env.state["idempotency"] = None
    second_task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="q2",
        actor=actor,
        request_context=_ctx(),
    )
    q2 = await persist_question_generation_result(
        env.session,
        task_id=second_task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    assert q2.version_no == 2
    assert q2.version_label == "Q2"
    assert q2.id != q1.id
    assert qset.current_version_id == q2.id
    assert qset.status == QUESTION_SET_STATUS_DRAFT
    assert qset.confirmed_by is None
    assert qset.confirmed_at is None
    assert q1.version_label == "Q1"


@pytest.mark.asyncio
async def test_persist_same_task_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="dup-callback",
        actor=actor,
        request_context=_ctx(),
    )
    first = await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    audits_after_first = [
        entry for entry in env.audits if entry["action"] == "interview_question.generated"
    ]
    second = await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    assert second.id == first.id
    assert len([item for item in env.versions if item.ai_task_id == task.id]) == 1
    generated = [
        entry for entry in env.audits if entry["action"] == "interview_question.generated"
    ]
    assert generated == audits_after_first


@pytest.mark.asyncio
async def test_persist_null_actor_with_context_writes_generated_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="persist-null-actor",
        actor=actor,
        request_context=_ctx(),
    )
    version = await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=None,
        request_context=_ctx(),
    )
    assert version.version_no == 1
    generated = [
        entry for entry in env.audits if entry["action"] == "interview_question.generated"
    ]
    assert len(generated) == 1
    assert generated[0]["actor_user_id"] is None
    assert generated[0]["resource_id"] == str(round_.id)
    _assert_safe_audit(generated[0])
    blob = json.dumps(generated, ensure_ascii=False, default=str)
    assert CIPHER_PREFIX not in blob
    assert "请说明" not in blob
    changes = generated[0]["changes"]
    for forbidden in (
        "question",
        "purpose",
        "resume_evidence",
        "raw_request",
        "raw_response",
        "result_payload",
        "jd_text",
        "resume_text",
    ):
        assert forbidden not in changes
    assert changes["round_id"] == str(round_.id)
    assert changes["ai_task_id"] == str(task.id)
    assert "version_no" in changes
    assert "question_count" in changes


@pytest.mark.asyncio
async def test_persist_without_request_context_skips_generated_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="persist-no-ctx",
        actor=actor,
        request_context=_ctx(),
    )
    version = await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=None,
    )
    assert version.version_no == 1
    generated = [
        entry for entry in env.audits if entry["action"] == "interview_question.generated"
    ]
    assert generated == []


# ---------------------------------------------------------------------------
# E. 编辑题纲
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_edit_creates_new_version_and_inherits_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import create_manual_question_version

    round_ = _make_round(status=INTERVIEW_STATUS_IN_PROGRESS)
    actor = _actor()
    qset, current = _make_set_with_version(
        round_,
        status=QUESTION_SET_STATUS_READY,
        confirmed=True,
        actor_id=actor.id,
        job_version_id=round_.job_version_id,
        resume_version_id=uuid4(),
        input_snapshot_hash="frozen-hash",
    )
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    env.application.resume_version_id = current.resume_version_id
    env.resume_version.id = current.resume_version_id
    detail = await create_manual_question_version(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        questions=_questions_payload()["questions"],
        idempotency_key="edit-1",
        actor=actor,
        request_context=_ctx(),
    )
    new_version = env.versions[-1]
    assert new_version.source_type == QUESTION_SOURCE_MANUAL_EDIT
    assert new_version.ai_task_id is None
    assert new_version.job_version_id == current.job_version_id
    assert new_version.resume_version_id == current.resume_version_id
    assert new_version.input_snapshot_hash == "frozen-hash"
    assert new_version.version_no == 2
    assert new_version.version_label == "Q2"
    assert qset.current_version_id == new_version.id
    assert qset.status == QUESTION_SET_STATUS_DRAFT
    assert qset.confirmed_by is None
    assert qset.confirmed_at is None
    assert detail.cache_control == "no-store"
    assert all(not item.question.startswith(CIPHER_PREFIX) for item in detail.items)
    edited = [entry for entry in env.audits if entry["action"] == "interview_question.edited"]
    assert edited
    _assert_safe_audit(edited[0])
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_manual_edit_stale_current_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        QUESTION_OPTIMISTIC_LOCK_MESSAGE,
        create_manual_question_version,
    )

    round_ = _make_round()
    qset, current = _make_set_with_version(round_)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    with pytest.raises(InterviewOptimisticLockError, match=QUESTION_OPTIMISTIC_LOCK_MESSAGE):
        await create_manual_question_version(
            env.session,
            round_id=round_.id,
            expected_current_version_id=uuid4(),
            questions=_questions_payload()["questions"],
            idempotency_key="edit-stale",
            actor=_actor(),
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_manual_edit_idempotent_and_payload_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import create_manual_question_version

    round_ = _make_round()
    actor = _actor()
    qset, current = _make_set_with_version(round_, actor_id=actor.id)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    first = await create_manual_question_version(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        questions=_questions_payload()["questions"],
        idempotency_key="edit-same",
        actor=actor,
        request_context=_ctx(),
    )
    second = await create_manual_question_version(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        questions=_questions_payload()["questions"],
        idempotency_key="edit-same",
        actor=actor,
        request_context=_ctx(),
    )
    assert first.id == second.id
    assert len([entry for entry in env.audits if entry["action"] == "interview_question.edited"]) == 1
    env.state["idempotency"].request_hash = "other"
    with pytest.raises(InterviewIdempotencyConflictError):
        await create_manual_question_version(
            env.session,
            round_id=round_.id,
            expected_current_version_id=current.id,
            questions=_questions_payload()["questions"],
            idempotency_key="edit-same",
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_manual_edit_rejects_empty_duplicate_order_and_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import create_manual_question_version

    round_ = _make_round()
    qset, current = _make_set_with_version(round_)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    actor = _actor()
    with pytest.raises(InterviewValidationError):
        await create_manual_question_version(
            env.session,
            round_id=round_.id,
            expected_current_version_id=current.id,
            questions=[],
            idempotency_key="edit-empty",
            actor=actor,
            request_context=_ctx(),
        )
    with pytest.raises((InterviewValidationError, AIOutputValidationError)):
        await create_manual_question_version(
            env.session,
            round_id=round_.id,
            expected_current_version_id=current.id,
            questions=[
                _question_item(display_order=1),
                _question_item(dimension_key="D002", display_order=1),
            ],
            idempotency_key="edit-dup-order",
            actor=actor,
            request_context=_ctx(),
        )
    with pytest.raises((InterviewValidationError, AIOutputValidationError)):
        await create_manual_question_version(
            env.session,
            round_id=round_.id,
            expected_current_version_id=current.id,
            questions=[_question_item(dimension_key="D999")],
            idempotency_key="edit-unknown",
            actor=actor,
            request_context=_ctx(),
        )


# ---------------------------------------------------------------------------
# F. 确认题纲
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_marks_ready_without_copying_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import confirm_question_set

    round_ = _make_round()
    actor = _actor()
    qset, current = _make_set_with_version(round_, actor_id=actor.id)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    result = await confirm_question_set(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        idempotency_key="confirm-1",
        actor=actor,
        request_context=_ctx(),
    )
    assert result.status == QUESTION_SET_STATUS_READY
    assert qset.current_version_id == current.id
    assert qset.confirmed_by == actor.id
    assert qset.confirmed_at is not None
    assert len(env.versions) == 1
    confirmed = [entry for entry in env.audits if entry["action"] == "interview_question.confirmed"]
    assert confirmed
    _assert_safe_audit(confirmed[0])
    env.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_idempotent_and_stale_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        QUESTION_OPTIMISTIC_LOCK_MESSAGE,
        confirm_question_set,
    )

    round_ = _make_round()
    actor = _actor()
    qset, current = _make_set_with_version(round_, actor_id=actor.id)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    await confirm_question_set(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        idempotency_key="confirm-same",
        actor=actor,
        request_context=_ctx(),
    )
    await confirm_question_set(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        idempotency_key="confirm-same",
        actor=actor,
        request_context=_ctx(),
    )
    assert len([entry for entry in env.audits if entry["action"] == "interview_question.confirmed"]) == 1
    env.state["idempotency"] = None
    with pytest.raises(InterviewOptimisticLockError, match=QUESTION_OPTIMISTIC_LOCK_MESSAGE):
        await confirm_question_set(
            env.session,
            round_id=round_.id,
            expected_current_version_id=uuid4(),
            idempotency_key="confirm-stale",
            actor=actor,
            request_context=_ctx(),
        )


@pytest.mark.asyncio
async def test_ready_can_still_be_read_and_edited_back_to_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        confirm_question_set,
        create_manual_question_version,
        get_question_version_detail,
        list_question_versions,
    )

    round_ = _make_round()
    actor = _actor()
    qset, current = _make_set_with_version(round_, actor_id=actor.id)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    await confirm_question_set(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        idempotency_key="confirm-read",
        actor=actor,
        request_context=_ctx(),
    )
    listed = await list_question_versions(
        env.session, round_id=round_.id, actor=actor
    )
    assert listed.status == QUESTION_SET_STATUS_READY
    detail = await get_question_version_detail(
        env.session, round_id=round_.id, version_id=current.id, actor=actor
    )
    assert detail.items
    await create_manual_question_version(
        env.session,
        round_id=round_.id,
        expected_current_version_id=current.id,
        questions=_questions_payload()["questions"],
        idempotency_key="edit-after-ready",
        actor=actor,
        request_context=_ctx(),
    )
    assert qset.status == QUESTION_SET_STATUS_DRAFT


# ---------------------------------------------------------------------------
# G. 读取和解密
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_hides_body_and_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import list_question_versions

    round_ = _make_round(status=INTERVIEW_STATUS_COMPLETED)
    qset, current = _make_set_with_version(round_)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    listed = await list_question_versions(
        env.session, round_id=round_.id, actor=_actor()
    )
    dumped = json.dumps(listed.__dict__, default=str)
    assert CIPHER_PREFIX not in dumped
    assert "请说明协作案例" not in dumped
    assert "考察协作" not in dumped
    assert listed.versions[0].question_count == 1
    assert listed.cache_control == "no-store"


@pytest.mark.asyncio
async def test_detail_decrypts_without_returning_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import get_question_version_detail

    round_ = _make_round()
    qset, current = _make_set_with_version(round_)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    detail = await get_question_version_detail(
        env.session, round_id=round_.id, version_id=current.id, actor=_actor()
    )
    dumped = json.dumps(detail.__dict__, default=str)
    assert CIPHER_PREFIX not in dumped
    assert detail.items[0].question
    assert not detail.items[0].question.startswith(CIPHER_PREFIX)
    assert detail.cache_control == "no-store"


@pytest.mark.asyncio
async def test_execute_cannot_read_unassigned_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import get_question_version_detail

    round_ = _make_round()
    qset, current = _make_set_with_version(round_)
    env = _patch_base(
        monkeypatch, round_, question_set=qset, versions=[current], assigned=False
    )
    with pytest.raises(InterviewNotFoundError):
        await get_question_version_detail(
            env.session,
            round_id=round_.id,
            version_id=current.id,
            actor=_actor(manage=False, execute=True),
        )


@pytest.mark.asyncio
async def test_version_from_other_round_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import get_question_version_detail

    round_ = _make_round()
    other = _make_round()
    qset, current = _make_set_with_version(other)
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    with pytest.raises(InterviewNotFoundError):
        await get_question_version_detail(
            env.session,
            round_id=round_.id,
            version_id=current.id,
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_decrypt_failure_is_safe_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import get_question_version_detail

    round_ = _make_round()
    qset, current = _make_set_with_version(round_)
    current.items[0].question_encrypted = "enc:v1:not-valid-cipher"
    env = _patch_base(monkeypatch, round_, question_set=qset, versions=[current])
    with pytest.raises(InterviewValidationError) as exc:
        await get_question_version_detail(
            env.session,
            round_id=round_.id,
            version_id=current.id,
            actor=_actor(),
        )
    assert CIPHER_PREFIX not in str(exc.value)
    assert "not-valid-cipher" not in str(exc.value)


@pytest.mark.asyncio
async def test_load_provider_input_stays_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.interview_questions import (
        load_question_provider_input,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="provider-input",
        actor=_actor(),
        request_context=_ctx(),
    )
    dto = await load_question_provider_input(env.session, task_id=task.id)
    assert "JD 正文" in dto.jd_text
    assert "简历正文" in dto.resume_text
    assert dto.job_version_id == round_.job_version_id
    assert dto.resume_version_id == env.application.resume_version_id
    blob = _snapshot_blob(task)
    assert "JD 正文" not in blob
    assert "简历正文" not in blob
    assert env.audits[0]["action"] == "interview_question.generate_requested"
    assert "JD 正文" not in json.dumps(env.audits, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# H. 审计
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_events_keep_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models import sanitize_audit_changes
    from app.services.interview_questions import (
        confirm_question_set,
        persist_question_generation_result,
        request_question_generation,
    )

    round_ = _make_round()
    env = _patch_base(monkeypatch, round_)
    actor = _actor()
    task = await request_question_generation(
        env.session,
        round_id=round_.id,
        idempotency_key="audit-1",
        actor=actor,
        request_context=_ctx(),
    )
    await persist_question_generation_result(
        env.session,
        task_id=task.id,
        payload=_questions_payload(),
        actor=actor,
        request_context=_ctx(),
    )
    qset = env.state["question_set"]
    await confirm_question_set(
        env.session,
        round_id=round_.id,
        expected_current_version_id=qset.current_version_id,
        idempotency_key="audit-confirm",
        actor=actor,
        request_context=_ctx(),
    )
    actions = {entry["action"] for entry in env.audits}
    assert "interview_question.generate_requested" in actions
    assert "interview_question.generated" in actions
    assert "interview_question.confirmed" in actions
    for entry in env.audits:
        _assert_safe_audit(entry)
        changes = sanitize_audit_changes(entry["changes"])
        assert changes["task_type"] == TASK_TYPE_INTERVIEW_QUESTION_GENERATE or (
            "question_count" in changes
        )


def test_conflict_error_is_available_for_409() -> None:
    assert issubclass(InterviewConflictError, Exception)
    assert issubclass(InterviewIdempotencyConflictError, Exception)
    assert issubclass(InterviewOptimisticLockError, Exception)
    assert issubclass(InterviewNotFoundError, Exception)
    assert issubclass(InterviewValidationError, Exception)
