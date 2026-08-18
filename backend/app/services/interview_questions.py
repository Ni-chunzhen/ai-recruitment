"""Interview question outline generation, edit, confirm and read services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    BUSINESS_TYPE_INTERVIEW_ROUND,
    QUESTION_SNAPSHOT_SCHEMA_VERSION,
    QUESTION_WORKFLOW_KEY,
    QUESTION_WORKFLOW_VERSION,
    TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
    AITask,
)
from app.models.interview import (
    INTERVIEW_STATUS_CONFIRMED,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_SCHEDULED,
    InterviewIdempotencyKey,
    InterviewRound,
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
from app.models.resume import RESUME_STATUS_CONFIRMED
from app.repositories.ai_tasks import (
    add_ai_task,
    find_inflight_task,
    find_task_by_input_snapshot_hash,
    get_ai_task_by_id,
)
from app.repositories.interview_questions import (
    create_question_items,
    create_question_set,
    create_question_version,
    get_question_set_by_round,
    get_question_set_for_update,
    get_question_version_by_id,
    get_question_version_by_task_id,
    next_question_version_no,
)
from app.repositories.interview_questions import (
    list_question_versions as list_question_versions_rows,
)
from app.repositories.interviews import (
    InterviewNotFoundError,
    actor_assigned_to_round,
    add_idempotency,
    find_idempotency,
    get_round_by_id,
    get_round_for_update,
)
from app.repositories.jobs import get_job_by_id, get_version_by_id
from app.repositories.rbac import user_has_permission
from app.repositories.resumes import get_application_by_id, get_resume_version_by_id
from app.schemas.interview_ai import (
    InterviewDimensionSnapshot,
    InterviewQuestionGenerateResult,
)
from app.services.ai_providers.base import validate_ai_result
from app.services.ai_tasks import enqueue_ai_task
from app.services.audit import RequestContext, record_audit
from app.services.crypto import EncryptionError, decrypt_secret, encrypt_secret
from app.services.interview_ai_validation import (
    AIOutputValidationError,
    build_dimension_snapshot,
    validate_question_result_against_snapshot,
)
from app.services.interviews import (
    InterviewConflictError,
    InterviewIdempotencyConflictError,
    InterviewOptimisticLockError,
    InterviewValidationError,
)

QUESTION_DETAIL_CACHE_CONTROL = "no-store"
QUESTION_OPTIMISTIC_LOCK_MESSAGE = "面试题纲已被其他人员更新，请刷新后重新编辑"
MISSING_CONFIRMED_RESUME_MESSAGE = "请先确认候选人简历版本后再生成面试题纲"
ALLOWED_QUESTION_MUTATION_STATUSES = frozenset(
    {
        INTERVIEW_STATUS_SCHEDULED,
        INTERVIEW_STATUS_CONFIRMED,
        INTERVIEW_STATUS_IN_PROGRESS,
    }
)
_INFLIGHT_STATUSES = {AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING}
IDEMPOTENCY_ACTION_GENERATE = "question.generate"
IDEMPOTENCY_ACTION_EDIT = "question.edit"
IDEMPOTENCY_ACTION_CONFIRM = "question.confirm"


@dataclass
class QuestionVersionSummary:
    id: UUID
    question_set_id: UUID
    round_id: UUID
    version_no: int
    version_label: str
    source_type: str
    job_version_id: UUID
    resume_version_id: UUID
    input_snapshot_hash: str
    question_count: int
    is_current: bool
    created_at: datetime
    created_by: UUID | None
    ai_task_id: UUID | None


@dataclass
class QuestionSetSummary:
    id: UUID | None
    round_id: UUID
    status: str | None
    current_version_id: UUID | None
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    versions: list[QuestionVersionSummary]
    cache_control: str = QUESTION_DETAIL_CACHE_CONTROL


@dataclass
class QuestionItemDetail:
    id: UUID
    dimension_key: str
    question: str
    purpose: str
    evidence_source: str
    resume_evidence: str | None
    follow_up_prompts: list[str]
    risk_flags: list[str]
    display_order: int


@dataclass
class QuestionVersionDetail:
    id: UUID
    question_set_id: UUID
    round_id: UUID
    version_no: int
    version_label: str
    source_type: str
    job_version_id: UUID
    resume_version_id: UUID
    input_snapshot_hash: str
    question_count: int
    is_current: bool
    created_at: datetime
    created_by: UUID | None
    ai_task_id: UUID | None
    items: list[QuestionItemDetail]
    cache_control: str = QUESTION_DETAIL_CACHE_CONTROL


@dataclass
class QuestionProviderInput:
    task_id: UUID
    round_id: UUID
    job_version_id: UUID
    resume_version_id: UUID
    job_title: str
    jd_text: str
    resume_text: str
    dimensions: list[dict[str, Any]]
    workflow_key: str
    workflow_version: str
    input_snapshot_hash: str
    extra: dict[str, Any] = field(default_factory=dict)


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


def _require_mutable_status(round_: InterviewRound) -> None:
    if round_.status not in ALLOWED_QUESTION_MUTATION_STATUSES:
        raise InterviewValidationError(
            "interview round cannot generate or edit questions"
        )


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
    key: str | None,
    request_payload: dict[str, Any],
) -> InterviewIdempotencyKey | None:
    if not key:
        raise InterviewValidationError("idempotency_key is required")
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
    await _assert_can_access_round(session, round_=round_, actor=actor)
    await _load_application(session, round_.application_id)
    _require_mutable_status(round_)
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
        plain = decrypt_secret(cipher)
    except EncryptionError as exc:
        raise InterviewValidationError("question decryption failed") from exc
    return plain


def _decrypt_required_text(cipher: str) -> str:
    plain = _decrypt_text(cipher)
    if plain is None:
        raise InterviewValidationError("question decryption failed")
    return plain


def _decrypt_json_list(cipher: str) -> list[str]:
    raw = _decrypt_required_text(cipher)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InterviewValidationError("question decryption failed") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise InterviewValidationError("question decryption failed")
    return parsed


def _dimension_dicts(
    snapshots: list[InterviewDimensionSnapshot],
) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in snapshots]


def _hash_input_snapshot(
    *,
    round_id: UUID,
    job_version_id: UUID,
    resume_version_id: UUID,
    dimensions: list[dict[str, Any]],
) -> str:
    return _canonical_hash(
        {
            "schema_version": QUESTION_SNAPSHOT_SCHEMA_VERSION,
            "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            "round_id": str(round_id),
            "job_version_id": str(job_version_id),
            "resume_version_id": str(resume_version_id),
            "workflow_key": QUESTION_WORKFLOW_KEY,
            "workflow_version": QUESTION_WORKFLOW_VERSION,
            "dimensions": dimensions,
        }
    )


async def _load_frozen_inputs(
    session: AsyncSession, round_: InterviewRound
) -> tuple[Any, Any, Any, Any, list[InterviewDimensionSnapshot]]:
    application = await _load_application(session, round_.application_id)
    if application.resume_version_id is None:
        raise InterviewValidationError(MISSING_CONFIRMED_RESUME_MESSAGE)
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_version_by_id(job, round_.job_version_id)
    if job_version is None:
        raise InterviewValidationError("frozen job version is missing")
    resume_version = await get_resume_version_by_id(
        session, application.resume_version_id
    )
    if resume_version is None or resume_version.status != RESUME_STATUS_CONFIRMED:
        raise InterviewValidationError(MISSING_CONFIRMED_RESUME_MESSAGE)
    parent = getattr(resume_version, "resume", None)
    parent_candidate_id = getattr(parent, "candidate_id", None)
    if parent_candidate_id != application.candidate_id:
        raise InterviewValidationError(
            "resume version does not belong to this application"
        )
    try:
        dimensions = build_dimension_snapshot(job_version.score_dimensions or [])
    except AIOutputValidationError as exc:
        raise InterviewValidationError(str(exc)) from exc
    return application, job, job_version, resume_version, dimensions


def _resume_plaintext(resume_version: Any) -> str:
    text = str(getattr(resume_version, "standardized_text", None) or "").strip()
    if text:
        return text
    confirmed = getattr(resume_version, "confirmed_content", None) or {}
    if isinstance(confirmed, dict):
        return str(confirmed.get("standardized_text") or "").strip()
    return ""


def _job_plaintext(job_version: Any) -> str:
    text = str(getattr(job_version, "raw_jd_text", None) or "").strip()
    if text:
        return text
    structured = getattr(job_version, "structured_jd", None)
    return str(structured or "").strip()


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


def _require_snapshot_ids(snapshot: dict[str, Any]) -> tuple[UUID, UUID, UUID, str]:
    try:
        round_id = UUID(str(snapshot["round_id"]))
        job_version_id = UUID(str(snapshot["job_version_id"]))
        resume_version_id = UUID(str(snapshot["resume_version_id"]))
        input_hash = str(snapshot["input_snapshot_hash"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        ) from exc
    if not input_hash:
        raise AIOutputValidationError(
            "task snapshot is incomplete",
            code="output_validation_failed",
        )
    return round_id, job_version_id, resume_version_id, input_hash


def _build_encrypted_items(
    *,
    version_id: UUID,
    result: InterviewQuestionGenerateResult,
) -> list[InterviewQuestionItem]:
    items: list[InterviewQuestionItem] = []
    for question in result.questions:
        items.append(
            InterviewQuestionItem(
                id=uuid4(),
                question_version_id=version_id,
                dimension_key=question.dimension_key,
                question_encrypted=_encrypt_text(question.question),
                purpose_encrypted=_encrypt_text(question.purpose),
                evidence_source=question.evidence_source,
                resume_evidence_encrypted=(
                    _encrypt_text(question.resume_evidence)
                    if question.resume_evidence
                    else None
                ),
                follow_up_prompts_encrypted=_encrypt_json(
                    list(question.follow_up_prompts)
                ),
                risk_flags_encrypted=_encrypt_json(list(question.risk_flags)),
                display_order=question.display_order,
            )
        )
    return items


def _to_version_summary(
    version: InterviewQuestionVersion,
    *,
    round_id: UUID,
    current_version_id: UUID | None,
) -> QuestionVersionSummary:
    return QuestionVersionSummary(
        id=version.id,
        question_set_id=version.question_set_id,
        round_id=round_id,
        version_no=version.version_no,
        version_label=version.version_label,
        source_type=version.source_type,
        job_version_id=version.job_version_id,
        resume_version_id=version.resume_version_id,
        input_snapshot_hash=version.input_snapshot_hash,
        question_count=len(version.items or []),
        is_current=version.id == current_version_id,
        created_at=version.created_at,
        created_by=version.created_by,
        ai_task_id=version.ai_task_id,
    )


def _to_version_detail(
    version: InterviewQuestionVersion,
    *,
    round_id: UUID,
    current_version_id: UUID | None,
) -> QuestionVersionDetail:
    items = [
        QuestionItemDetail(
            id=item.id,
            dimension_key=item.dimension_key,
            question=_decrypt_required_text(item.question_encrypted),
            purpose=_decrypt_required_text(item.purpose_encrypted),
            evidence_source=item.evidence_source,
            resume_evidence=_decrypt_text(item.resume_evidence_encrypted),
            follow_up_prompts=_decrypt_json_list(item.follow_up_prompts_encrypted),
            risk_flags=_decrypt_json_list(item.risk_flags_encrypted),
            display_order=item.display_order,
        )
        for item in sorted(version.items or [], key=lambda row: row.display_order)
    ]
    return QuestionVersionDetail(
        id=version.id,
        question_set_id=version.question_set_id,
        round_id=round_id,
        version_no=version.version_no,
        version_label=version.version_label,
        source_type=version.source_type,
        job_version_id=version.job_version_id,
        resume_version_id=version.resume_version_id,
        input_snapshot_hash=version.input_snapshot_hash,
        question_count=len(items),
        is_current=version.id == current_version_id,
        created_at=version.created_at,
        created_by=version.created_by,
        ai_task_id=version.ai_task_id,
        items=items,
        cache_control=QUESTION_DETAIL_CACHE_CONTROL,
    )


def _to_set_summary(
    *,
    round_id: UUID,
    question_set: InterviewQuestionSet | None,
    versions: list[InterviewQuestionVersion],
) -> QuestionSetSummary:
    current_id = question_set.current_version_id if question_set else None
    return QuestionSetSummary(
        id=question_set.id if question_set else None,
        round_id=round_id,
        status=question_set.status if question_set else None,
        current_version_id=current_id,
        confirmed_by=question_set.confirmed_by if question_set else None,
        confirmed_at=question_set.confirmed_at if question_set else None,
        versions=[
            _to_version_summary(
                item, round_id=round_id, current_version_id=current_id
            )
            for item in versions
        ],
        cache_control=QUESTION_DETAIL_CACHE_CONTROL,
    )


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


async def request_question_generation(
    session: AsyncSession,
    *,
    round_id: UUID,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> AITask:
    """Create and flush a PENDING question-generation task.

    Does not enqueue. The API layer must commit, then call
    dispatch_persisted_question_generation_task(task_id=task.id).
    """
    round_ = await _load_round_for_mutation(
        session, round_id=round_id, actor=actor
    )
    _application, _job, job_version, resume_version, dimensions = (
        await _load_frozen_inputs(session, round_)
    )
    dimension_dicts = _dimension_dicts(dimensions)
    input_snapshot_hash = _hash_input_snapshot(
        round_id=round_.id,
        job_version_id=job_version.id,
        resume_version_id=resume_version.id,
        dimensions=dimension_dicts,
    )
    request_payload = {
        "round_id": str(round_.id),
        "input_snapshot_hash": input_snapshot_hash,
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
            task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            input_snapshot_hash=input_snapshot_hash,
        )
        if existing_task is None:
            existing_task = await find_inflight_task(
                session,
                business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
                business_id=round_.id,
                task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
                inflight_statuses=_INFLIGHT_STATUSES,
            )
        if existing_task is None:
            raise InterviewIdempotencyConflictError("idempotency conflict")
        return existing_task

    inflight = await find_inflight_task(
        session,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_.id,
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
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
            "a question generation task is already pending or running"
        )

    now = _now()
    snapshot = {
        "schema_version": QUESTION_SNAPSHOT_SCHEMA_VERSION,
        "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        "round_id": str(round_.id),
        "job_version_id": str(job_version.id),
        "resume_version_id": str(resume_version.id),
        "workflow_key": QUESTION_WORKFLOW_KEY,
        "workflow_version": QUESTION_WORKFLOW_VERSION,
        "requested_by": str(actor.id),
        "requested_at": now.isoformat(),
        "idempotency_key": idempotency_key,
        "request_hash": _canonical_hash(request_payload),
        "input_snapshot_hash": input_snapshot_hash,
        "dimensions": dimension_dicts,
    }
    task = AITask(
        id=uuid4(),
        task_type=TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_INTERVIEW_ROUND,
        business_id=round_.id,
        version_id=job_version.id,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        input_snapshot=snapshot,
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
        action="interview_question.generate_requested",
        actor=actor,
        request_context=request_context,
        round_id=round_.id,
        changes={
            "round_id": str(round_.id),
            "ai_task_id": str(task.id),
            "job_version_id": str(job_version.id),
            "resume_version_id": str(resume_version.id),
            "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            "workflow_version": QUESTION_WORKFLOW_VERSION,
            "status": task.status,
        },
    )
    return task


async def dispatch_persisted_question_generation_task(
    session: AsyncSession, *, task_id: UUID
) -> None:
    """Enqueue a committed PENDING INTERVIEW_QUESTION_GENERATE task.

    API call order:
    1. request_question_generation(...)  # flush PENDING task, do not enqueue
    2. session.commit()
    3. dispatch_persisted_question_generation_task(session, task_id=task.id)

    Flush is not a dispatch signal. This helper re-reads the task and assumes
    the outer transaction has already committed. It does not create tasks,
    write question versions, mutate snapshots, or emit audit events. Celery
    publish failure must not roll back the committed PENDING row; keep the
    task_id for a later retry.
    """
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    if task is None or task.task_type != TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        raise InterviewNotFoundError("ai task not found")
    if task.status != AI_TASK_STATUS_PENDING:
        raise InterviewValidationError("question generation task is not pending")
    enqueue_ai_task(task.id)


async def load_question_provider_input(
    session: AsyncSession, *, task_id: UUID
) -> QuestionProviderInput:
    task = await get_ai_task_by_id(session, task_id, with_attempts=False)
    if task is None or task.task_type != TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        raise InterviewNotFoundError("ai task not found")
    snapshot = task.input_snapshot or {}
    round_id, job_version_id, resume_version_id, input_hash = _require_snapshot_ids(
        snapshot
    )
    round_ = await get_round_by_id(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    application = await _load_application(session, round_.application_id)
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_version_by_id(job, job_version_id)
    resume_version = await get_resume_version_by_id(session, resume_version_id)
    if job_version is None or resume_version is None:
        raise AIOutputValidationError(
            "frozen input is no longer available",
            code="output_validation_failed",
        )
    return QuestionProviderInput(
        task_id=task.id,
        round_id=round_id,
        job_version_id=job_version_id,
        resume_version_id=resume_version_id,
        job_title=str(getattr(job, "name", "") or ""),
        jd_text=_job_plaintext(job_version),
        resume_text=_resume_plaintext(resume_version),
        dimensions=list(snapshot.get("dimensions") or []),
        workflow_key=str(snapshot.get("workflow_key") or QUESTION_WORKFLOW_KEY),
        workflow_version=str(
            snapshot.get("workflow_version") or QUESTION_WORKFLOW_VERSION
        ),
        input_snapshot_hash=input_hash,
    )


async def persist_question_generation_result(
    session: AsyncSession,
    *,
    task_id: UUID,
    payload: dict[str, Any] | InterviewQuestionGenerateResult,
    actor: User | None = None,
    request_context: RequestContext | None = None,
) -> InterviewQuestionVersion:
    task = await get_ai_task_by_id(session, task_id)
    if task is None:
        raise InterviewNotFoundError("ai task not found")
    if task.task_type != TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
        raise InterviewValidationError("unsupported task_type")
    snapshot = task.input_snapshot or {}
    round_id, job_version_id, resume_version_id, input_hash = _require_snapshot_ids(
        snapshot
    )
    existing = await get_question_version_by_task_id(
        session, task.id, round_id=round_id
    )
    if existing is not None:
        return existing
    round_ = await get_round_for_update(session, round_id)
    if round_ is None:
        raise InterviewNotFoundError("interview round not found")
    application = await _load_application(session, round_.application_id)
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_version_by_id(job, job_version_id)
    resume_version = await get_resume_version_by_id(session, resume_version_id)
    if job_version is None or resume_version is None:
        raise AIOutputValidationError(
            "frozen input is no longer available",
            code="output_validation_failed",
        )
    dimensions = _snapshot_dimensions(snapshot)
    raw_payload = (
        payload.model_dump(mode="json")
        if isinstance(payload, InterviewQuestionGenerateResult)
        else payload
    )
    validated = validate_ai_result(
        TASK_TYPE_INTERVIEW_QUESTION_GENERATE, raw_payload
    )
    result = InterviewQuestionGenerateResult.model_validate(validated)
    validate_question_result_against_snapshot(result, dimensions)

    question_set = await get_question_set_for_update(session, round_id)
    actor_id = actor.id if actor is not None else task.created_by
    if question_set is None:
        question_set = InterviewQuestionSet(
            id=uuid4(),
            interview_round_id=round_id,
            current_version_id=None,
            status=QUESTION_SET_STATUS_DRAFT,
            created_by=actor_id,
        )
        await create_question_set(session, question_set)

    version_no = await next_question_version_no(session, question_set.id)
    version = InterviewQuestionVersion(
        id=uuid4(),
        question_set_id=question_set.id,
        version_no=version_no,
        version_label=f"Q{version_no}",
        source_type=QUESTION_SOURCE_AI_GENERATED,
        ai_task_id=task.id,
        job_version_id=job_version_id,
        resume_version_id=resume_version_id,
        input_snapshot_hash=input_hash,
        created_by=actor_id,
    )
    await create_question_version(session, version)
    items = _build_encrypted_items(version_id=version.id, result=result)
    await create_question_items(session, items)
    version.items = items
    question_set.current_version_id = version.id
    question_set.status = QUESTION_SET_STATUS_DRAFT
    question_set.confirmed_by = None
    question_set.confirmed_at = None
    question_set.updated_at = _now()
    await session.flush()

    if request_context is not None:
        await _audit(
            session,
            action="interview_question.generated",
            actor=actor,
            request_context=request_context,
            round_id=round_id,
            changes={
                "round_id": str(round_id),
                "question_set_id": str(question_set.id),
                "question_version_id": str(version.id),
                "ai_task_id": str(task.id),
                "job_version_id": str(job_version_id),
                "resume_version_id": str(resume_version_id),
                "version_no": version.version_no,
                "version_label": version.version_label,
                "question_count": len(items),
                "status": question_set.status,
                "workflow_version": str(
                    snapshot.get("workflow_version") or QUESTION_WORKFLOW_VERSION
                ),
                "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            },
        )
    return version


def _validate_manual_questions(
    questions: list[dict[str, Any]],
    dimensions: list[InterviewDimensionSnapshot],
) -> InterviewQuestionGenerateResult:
    if not questions:
        raise InterviewValidationError("questions must not be empty")
    try:
        result = InterviewQuestionGenerateResult.model_validate(
            {"questions": questions}
        )
        validate_question_result_against_snapshot(result, dimensions)
    except ValidationError as exc:
        raise InterviewValidationError("invalid question outline") from exc
    except AIOutputValidationError as exc:
        raise InterviewValidationError(str(exc)) from exc
    return result


async def create_manual_question_version(
    session: AsyncSession,
    *,
    round_id: UUID,
    expected_current_version_id: UUID,
    questions: list[dict[str, Any]],
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> QuestionVersionDetail:
    round_ = await _load_round_for_mutation(
        session, round_id=round_id, actor=actor
    )
    application = await _load_application(session, round_.application_id)
    question_set = await get_question_set_for_update(session, round_id)
    if question_set is None or question_set.current_version_id is None:
        raise InterviewValidationError("current question version is required")
    current = await get_question_version_by_id(
        session, round_id=round_id, version_id=question_set.current_version_id
    )
    if current is None:
        raise InterviewValidationError("current question version is required")

    request_payload = {
        "round_id": str(round_id),
        "expected_current_version_id": str(expected_current_version_id),
        "questions": questions,
    }
    existing_key = await _consume_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_EDIT,
        scope_id=round_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if existing_key is not None:
        expected = await get_question_version_by_id(
            session, round_id=round_id, version_id=expected_current_version_id
        )
        rows = await list_question_versions_rows(session, round_id)
        if expected is not None:
            replay = next(
                (row for row in rows if row.version_no == expected.version_no + 1),
                None,
            )
            if replay is not None:
                return _to_version_detail(
                    replay,
                    round_id=round_id,
                    current_version_id=question_set.current_version_id,
                )
        if question_set.current_version_id is not None:
            replay = await get_question_version_by_id(
                session,
                round_id=round_id,
                version_id=question_set.current_version_id,
            )
            if replay is not None:
                return _to_version_detail(
                    replay,
                    round_id=round_id,
                    current_version_id=question_set.current_version_id,
                )
        raise InterviewIdempotencyConflictError("idempotency conflict")

    if question_set.current_version_id != expected_current_version_id:
        raise InterviewOptimisticLockError(QUESTION_OPTIMISTIC_LOCK_MESSAGE)

    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise InterviewNotFoundError("job not found")
    job_version = get_version_by_id(job, current.job_version_id)
    if job_version is None:
        raise InterviewValidationError("frozen job version is missing")
    try:
        dimensions = build_dimension_snapshot(job_version.score_dimensions or [])
    except AIOutputValidationError as exc:
        raise InterviewValidationError(str(exc)) from exc
    result = _validate_manual_questions(questions, dimensions)

    version_no = await next_question_version_no(session, question_set.id)
    version = InterviewQuestionVersion(
        id=uuid4(),
        question_set_id=question_set.id,
        version_no=version_no,
        version_label=f"Q{version_no}",
        source_type=QUESTION_SOURCE_MANUAL_EDIT,
        ai_task_id=None,
        job_version_id=current.job_version_id,
        resume_version_id=current.resume_version_id,
        input_snapshot_hash=current.input_snapshot_hash,
        created_by=actor.id,
    )
    await create_question_version(session, version)
    items = _build_encrypted_items(version_id=version.id, result=result)
    await create_question_items(session, items)
    version.items = items
    question_set.current_version_id = version.id
    question_set.status = QUESTION_SET_STATUS_DRAFT
    question_set.confirmed_by = None
    question_set.confirmed_at = None
    question_set.updated_at = _now()
    await session.flush()
    await _store_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_EDIT,
        scope_id=round_id,
        key=idempotency_key,
        request_payload=request_payload,
        round_id=round_id,
    )
    await _audit(
        session,
        action="interview_question.edited",
        actor=actor,
        request_context=request_context,
        round_id=round_id,
        changes={
            "round_id": str(round_id),
            "question_set_id": str(question_set.id),
            "question_version_id": str(version.id),
            "job_version_id": str(version.job_version_id),
            "resume_version_id": str(version.resume_version_id),
            "version_no": version.version_no,
            "version_label": version.version_label,
            "question_count": len(items),
            "status": question_set.status,
            "workflow_version": QUESTION_WORKFLOW_VERSION,
            "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        },
    )
    return _to_version_detail(
        version,
        round_id=round_id,
        current_version_id=question_set.current_version_id,
    )


async def confirm_question_set(
    session: AsyncSession,
    *,
    round_id: UUID,
    expected_current_version_id: UUID,
    idempotency_key: str,
    actor: User,
    request_context: RequestContext,
) -> QuestionSetSummary:
    await _load_round_for_mutation(session, round_id=round_id, actor=actor)
    question_set = await get_question_set_for_update(session, round_id)
    if question_set is None or question_set.current_version_id is None:
        raise InterviewValidationError("current question version is required")
    request_payload = {
        "round_id": str(round_id),
        "expected_current_version_id": str(expected_current_version_id),
    }
    existing_key = await _consume_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_CONFIRM,
        scope_id=round_id,
        key=idempotency_key,
        request_payload=request_payload,
    )
    versions = await list_question_versions_rows(session, round_id)
    if existing_key is not None:
        return _to_set_summary(
            round_id=round_id, question_set=question_set, versions=versions
        )
    if question_set.current_version_id != expected_current_version_id:
        raise InterviewOptimisticLockError(QUESTION_OPTIMISTIC_LOCK_MESSAGE)
    current = await get_question_version_by_id(
        session, round_id=round_id, version_id=question_set.current_version_id
    )
    if current is None or not (current.items or []):
        raise InterviewValidationError("current question version is required")

    question_set.status = QUESTION_SET_STATUS_READY
    question_set.confirmed_by = actor.id
    question_set.confirmed_at = _now()
    question_set.updated_at = _now()
    await session.flush()
    await _store_idempotency(
        session,
        actor=actor,
        action=IDEMPOTENCY_ACTION_CONFIRM,
        scope_id=round_id,
        key=idempotency_key,
        request_payload=request_payload,
        round_id=round_id,
    )
    await _audit(
        session,
        action="interview_question.confirmed",
        actor=actor,
        request_context=request_context,
        round_id=round_id,
        changes={
            "round_id": str(round_id),
            "question_set_id": str(question_set.id),
            "question_version_id": str(current.id),
            "job_version_id": str(current.job_version_id),
            "resume_version_id": str(current.resume_version_id),
            "version_no": current.version_no,
            "version_label": current.version_label,
            "question_count": len(current.items or []),
            "status": question_set.status,
            "workflow_version": QUESTION_WORKFLOW_VERSION,
            "task_type": TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
        },
    )
    return _to_set_summary(
        round_id=round_id, question_set=question_set, versions=versions
    )


async def list_question_versions(
    session: AsyncSession, *, round_id: UUID, actor: User
) -> QuestionSetSummary:
    await _load_round_for_read(session, round_id=round_id, actor=actor)
    question_set = await get_question_set_by_round(session, round_id)
    versions = await list_question_versions_rows(session, round_id)
    return _to_set_summary(
        round_id=round_id, question_set=question_set, versions=versions
    )


async def get_question_version_detail(
    session: AsyncSession,
    *,
    round_id: UUID,
    version_id: UUID,
    actor: User,
) -> QuestionVersionDetail:
    await _load_round_for_read(session, round_id=round_id, actor=actor)
    question_set = await get_question_set_by_round(session, round_id)
    version = await get_question_version_by_id(
        session, round_id=round_id, version_id=version_id
    )
    if question_set is None or version is None:
        raise InterviewNotFoundError("question version not found")
    return _to_version_detail(
        version,
        round_id=round_id,
        current_version_id=question_set.current_version_id,
    )
