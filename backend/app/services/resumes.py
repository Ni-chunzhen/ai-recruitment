from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.minio_storage import (
    StorageError,
    get_bytes,
    presigned_get_url,
    put_bytes,
)
from app.models import User
from app.models.ai_task import (
    AI_TASK_STATUS_PENDING,
    AI_TASK_STATUS_RUNNING,
    BUSINESS_TYPE_APPLICATION,
    BUSINESS_TYPE_RESUME_VERSION,
    SCORE_SNAPSHOT_SCHEMA_VERSION,
    SCORE_WORKFLOW_KEY,
    TASK_TYPE_RESUME_PARSE,
    TASK_TYPE_RESUME_SCORE,
    AITask,
)
from app.models.candidate import (
    APPLICATION_STATUS_IN_PROGRESS,
    INTERVIEW_TASK_NONE,
    JobApplication,
)
from app.models.job import (
    JOB_STATUS_OPEN,
    JOB_STATUS_PAUSED,
    VERSION_STATUS_PUBLISHED,
)
from app.models.resume import (
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_HR_SCREEN,
    PIPELINE_PENDING_PARSE,
    PIPELINE_REJECTED,
    PIPELINE_TALENT_POOL,
    RESUME_STATUS_CONFIRMED,
    RESUME_STATUS_PARSE_FAILED,
    RESUME_STATUS_PARSING,
    RESUME_STATUS_PENDING_PARSE,
    RESUME_STATUS_PENDING_REVIEW,
    SCREENING_ENTER_INTERVIEW,
    SCREENING_HOLD,
    SCREENING_REASON_CODES,
    SCREENING_REASON_OTHER,
    SCREENING_REASON_REQUIRED_DECISIONS,
    SCREENING_REJECT,
    SCREENING_TALENT_POOL,
    VERSION_KIND_CONFIRMED,
    VERSION_KIND_FILE,
    AiResult,
    ApplicationStatusLog,
    Resume,
    ResumeVersion,
    ScreeningDecision,
)
from app.repositories.ai_tasks import (
    add_ai_task,
    find_ai_task_by_idempotency,
    find_inflight_task,
)
from app.repositories.candidates import (
    create_application,
    create_candidate,
    get_candidate_by_id,
)
from app.repositories.jobs import JobNotFoundError, get_job_by_id, get_version_by_id
from app.repositories.resumes import (
    ResumeNotFoundError,
    add_ai_result,
    add_resume,
    add_resume_version,
    add_screening_decision,
    add_status_log,
    count_ai_results_for_application,
    count_confirmed_versions,
    count_file_versions,
    find_candidates_by_contact,
    find_open_application,
    find_screening_by_idempotency,
    get_ai_result_by_id,
    get_application_by_id,
    get_current_ai_result,
    get_resume_version_by_id,
    list_ai_results,
    list_job_names_for_candidates,
    list_pending_review_versions,
    list_resume_versions,
    mark_previous_results_not_current,
)
from app.schemas.resume import (
    ApplicationOut,
    ConfirmResumeRequest,
    CreateApplicationRequest,
    CreateScoreTaskRequest,
    DuplicateCandidateHint,
    ResumeListItem,
    ResumeListResponse,
    ResumeScoreResult,
    ResumeStructuredContent,
    ResumeUploadItemOut,
    ResumeUploadResponse,
    ResumeVersionOut,
    SaveDraftRequest,
    ScoreHistoryResponse,
    ScoreReportOut,
    ScoreTaskCreated,
    ScreeningDecisionOut,
    ScreeningDecisionRequest,
)
from app.services.ai_tasks import enqueue_ai_task
from app.services.audit import RequestContext, record_audit
from app.services.score_validation import (
    SCORE_RESULT_SCHEMA_VERSION,
    compute_score_totals,
    order_dimensions_by_snapshot,
    snapshot_dimensions,
    validate_score_against_snapshot,
    validate_screening_payload,
)
from app.services.text_extract import TextExtractError, extract_text


class ResumeValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ResumeStateError(Exception):
    pass


class ConflictError(Exception):
    pass


PHONE_RE = re.compile(r"^1\d{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits or None


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def _guess_name_from_filename(filename: str) -> str:
    stem = re.sub(r"\.(pdf|docx?|txt)$", "", filename, flags=re.I)
    stem = re.split(r"[_\-\s·]+", stem)[0].strip()
    return stem[:64] or "待补充"


def empty_structured(*, standardized_text: str = "", name: str = "") -> dict[str, Any]:
    content = ResumeStructuredContent(
        name=name or "",
        name_pending=not bool(name and name != "待补充"),
        standardized_text=standardized_text,
        work_experience=[],
        projects=[],
        education=[],
        skills=[],
        field_sources={},
    )
    return content.model_dump()


def to_version_out(
    version: ResumeVersion,
    *,
    candidate_id: UUID,
    preview_url: str | None = None,
    duplicate_hints: list[DuplicateCandidateHint] | None = None,
) -> ResumeVersionOut:
    return ResumeVersionOut(
        id=version.id,
        resume_id=version.resume_id,
        candidate_id=candidate_id,
        kind=version.kind,
        version_label=version.version_label,
        status=version.status,  # type: ignore[arg-type]
        original_filename=version.original_filename,
        content_type=version.content_type,
        file_size=version.file_size,
        extracted_text=version.extracted_text,
        ai_structured=version.ai_structured,
        draft_content=version.draft_content,
        confirmed_content=version.confirmed_content,
        standardized_text=version.standardized_text,
        parse_task_id=version.parse_task_id,
        confirmed_by=version.confirmed_by,
        confirmed_at=version.confirmed_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
        preview_url=preview_url,
        duplicate_hints=duplicate_hints or [],
    )


async def _duplicate_hints(
    session: AsyncSession,
    *,
    phone: str | None,
    email: str | None,
    exclude_id: UUID | None,
) -> list[DuplicateCandidateHint]:
    matches = await find_candidates_by_contact(
        session, phone=phone, email=email, exclude_id=exclude_id
    )
    hints: list[DuplicateCandidateHint] = []
    for cand in matches:
        match_on: list[str] = []
        if phone and cand.phone == phone:
            match_on.append("phone")
        if email and cand.email and cand.email.lower() == email.lower():
            match_on.append("email")
        hints.append(
            DuplicateCandidateHint(
                id=cand.id,
                name=cand.name,
                phone=cand.phone,
                email=cand.email,
                match_on=match_on,
            )
        )
    return hints


async def upload_resumes(
    session: AsyncSession,
    *,
    files: list[tuple[str, str, bytes]],
    actor: User,
    request_context: RequestContext,
    job_id: UUID | None = None,
    link_candidate_id: UUID | None = None,
) -> ResumeUploadResponse:
    settings = get_settings()
    if not files:
        raise ResumeValidationError("at least one file is required")
    if len(files) > settings.RESUME_UPLOAD_MAX_FILES:
        raise ResumeValidationError(
            f"at most {settings.RESUME_UPLOAD_MAX_FILES} files per upload"
        )

    job = None
    published = None
    if job_id is not None:
        job = await get_job_by_id(session, job_id)
        if job is None:
            raise JobNotFoundError("job not found")
        if job.status not in {JOB_STATUS_OPEN, JOB_STATUS_PAUSED}:
            raise ResumeStateError("job must be open or paused to accept resumes")
        published = get_version_by_id(job, job.current_version_id)
        if published is None or published.status != VERSION_STATUS_PUBLISHED:
            raise ResumeStateError("job has no published version to bind")

    items: list[ResumeUploadItemOut] = []
    for filename, content_type, data in files:
        if len(data) > settings.RESUME_UPLOAD_MAX_BYTES:
            raise ResumeValidationError(f"{filename}: file too large")
        item = await _upload_one(
            session,
            filename=filename,
            content_type=content_type,
            data=data,
            actor=actor,
            request_context=request_context,
            job=job,
            published=published,
            link_candidate_id=link_candidate_id,
        )
        items.append(item)

    await session.commit()
    return ResumeUploadResponse(items=items)


async def _upload_one(
    session: AsyncSession,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    actor: User,
    request_context: RequestContext,
    job: Any,
    published: Any,
    link_candidate_id: UUID | None,
) -> ResumeUploadItemOut:
    guessed_name = _guess_name_from_filename(filename)
    if link_candidate_id:
        candidate = await get_candidate_by_id(session, link_candidate_id)
        if candidate is None:
            raise ResumeNotFoundError("candidate not found")
    else:
        candidate = await create_candidate(
            session,
            name=guessed_name if guessed_name != "待补充" else "待补充",
            phone=None,
            email=None,
        )

    resume = await add_resume(session, Resume(candidate_id=candidate.id))
    file_count = await count_file_versions(session, resume.id)
    version_label = f"R{file_count + 1}"
    storage_key = f"resumes/{candidate.id}/{uuid.uuid4().hex}/{filename}"

    try:
        put_bytes(key=storage_key, data=data, content_type=content_type)
    except StorageError as exc:
        raise ResumeValidationError(str(exc)) from exc

    extracted: str | None = None
    extract_error: str | None = None
    try:
        extracted = extract_text(filename=filename, data=data)
    except TextExtractError as exc:
        extract_error = str(exc)

    now = datetime.now(UTC)
    version = ResumeVersion(
        resume_id=resume.id,
        kind=VERSION_KIND_FILE,
        version_label=version_label,
        status=RESUME_STATUS_PENDING_PARSE,
        original_filename=filename,
        content_type=content_type,
        file_size=len(data),
        storage_key=storage_key,
        extracted_text=extracted,
        draft_content=empty_structured(
            standardized_text=extracted or "",
            name=candidate.name,
        ),
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    await add_resume_version(session, version)
    resume.current_file_version_id = version.id

    application: JobApplication | None = None
    if job is not None and published is not None:
        application = await create_application(
            session,
            candidate_id=candidate.id,
            job_id=job.id,
            job_version_id=published.id,
            status=APPLICATION_STATUS_IN_PROGRESS,
            pipeline_status=PIPELINE_PENDING_PARSE,
            resume_version_id=version.id,
            interview_started=False,
            interview_task_state=INTERVIEW_TASK_NONE,
            lock_version=1,
        )

    parse_task_id: UUID | None = None
    if extracted:
        task = AITask(
            task_type=TASK_TYPE_RESUME_PARSE,
            status=AI_TASK_STATUS_PENDING,
            business_type=BUSINESS_TYPE_RESUME_VERSION,
            business_id=version.id,
            version_id=version.id,
            created_by=actor.id,
            input_snapshot={
                "resume_text": extracted,
                "candidate_id": str(candidate.id),
                "original_filename": filename,
            },
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        await add_ai_task(session, task)
        version.parse_task_id = task.id
        version.status = RESUME_STATUS_PARSING
        parse_task_id = task.id
        enqueue_ai_task(task.id)
    else:
        version.status = RESUME_STATUS_PARSE_FAILED
        version.draft_content = {
            **(version.draft_content or {}),
            "parse_error": extract_error or "extract failed",
        }

    await record_audit(
        session,
        action="resume.upload",
        result="success",
        resource_type="resume_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(version.id),
        changes={
            "resume_id": str(resume.id),
            "candidate_id": str(candidate.id),
            "job_id": str(job.id) if job else None,
            "application_id": str(application.id) if application else None,
            "filename": filename,
            "status": version.status,
        },
    )

    return ResumeUploadItemOut(
        resume_id=resume.id,
        resume_version_id=version.id,
        candidate_id=candidate.id,
        application_id=application.id if application else None,
        parse_task_id=parse_task_id,
        status=version.status,  # type: ignore[arg-type]
        original_filename=filename,
        duplicate_hints=[],
    )


async def get_resume_version(
    session: AsyncSession,
    version_id: UUID,
    *,
    include_preview: bool = True,
) -> ResumeVersionOut:
    version = await get_resume_version_by_id(session, version_id)
    if version is None or version.resume is None:
        raise ResumeNotFoundError("resume version not found")
    candidate = version.resume.candidate
    preview = None
    if include_preview and version.storage_key:
        try:
            preview = presigned_get_url(
                version.storage_key,
                expires_seconds=get_settings().MINIO_PRESIGN_SECONDS,
            )
        except StorageError:
            preview = None
    phone = None
    email = None
    content = version.draft_content or version.ai_structured or {}
    if isinstance(content, dict):
        phone = normalize_phone(str(content.get("phone") or "") or None)
        email = normalize_email(str(content.get("email") or "") or None)
    hints = await _duplicate_hints(
        session, phone=phone, email=email, exclude_id=candidate.id
    )
    return to_version_out(
        version,
        candidate_id=candidate.id,
        preview_url=preview,
        duplicate_hints=hints,
    )


async def list_resumes(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    status: str | None = None,
    linked: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ResumeListResponse:
    rows, total = await list_resume_versions(
        session,
        keyword=keyword,
        status=status,
        linked=linked,
        offset=offset,
        limit=limit,
    )
    candidate_ids = [cand.id for _, _, cand in rows]
    job_map = await list_job_names_for_candidates(session, candidate_ids)
    items: list[ResumeListItem] = []
    for version, resume, candidate in rows:
        jobs = job_map.get(candidate.id) or []
        confirmed = resume.current_confirmed_version_id == version.id
        awaiting = confirmed and len(jobs) == 0
        items.append(
            ResumeListItem(
                resume_id=resume.id,
                resume_version_id=version.id,
                candidate_id=candidate.id,
                candidate_name=candidate.name,
                phone=candidate.phone,
                email=candidate.email,
                status=version.status,  # type: ignore[arg-type]
                has_application=bool(jobs),
                job_names=jobs,
                original_filename=version.original_filename,
                updated_at=version.updated_at,
                awaiting_match=awaiting,
            )
        )
    return ResumeListResponse(items=items, total=total)


async def list_workbench_pending(session: AsyncSession) -> ResumeListResponse:
    rows = await list_pending_review_versions(session)
    items: list[ResumeListItem] = []
    for version, resume, candidate in rows:
        items.append(
            ResumeListItem(
                resume_id=resume.id,
                resume_version_id=version.id,
                candidate_id=candidate.id,
                candidate_name=candidate.name,
                phone=candidate.phone,
                email=candidate.email,
                status=version.status,  # type: ignore[arg-type]
                has_application=False,
                job_names=[],
                original_filename=version.original_filename,
                updated_at=version.updated_at,
                awaiting_match=False,
            )
        )
    return ResumeListResponse(items=items, total=len(items))


async def save_draft(
    session: AsyncSession,
    *,
    version_id: UUID,
    payload: SaveDraftRequest,
    actor: User,
    request_context: RequestContext,
) -> ResumeVersionOut:
    version = await get_resume_version_by_id(session, version_id)
    if version is None or version.resume is None:
        raise ResumeNotFoundError("resume version not found")
    if version.status == RESUME_STATUS_CONFIRMED:
        raise ResumeStateError(
            "confirmed version is read-only; create a new confirmation"
        )
    if version.status not in {
        RESUME_STATUS_PENDING_REVIEW,
        RESUME_STATUS_PARSE_FAILED,
        RESUME_STATUS_PARSING,
        RESUME_STATUS_PENDING_PARSE,
    }:
        # allow drafting on file versions still in review cycle
        if version.kind != VERSION_KIND_FILE:
            raise ResumeStateError("cannot draft this version")

    version.draft_content = payload.content.model_dump()
    version.standardized_text = payload.content.standardized_text
    version.updated_at = datetime.now(UTC)
    await record_audit(
        session,
        action="resume.draft_save",
        result="success",
        resource_type="resume_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(version.id),
        changes={"resume_id": str(version.resume_id)},
    )
    await session.commit()
    return await get_resume_version(session, version.id)


def _validate_confirm_content(content: ResumeStructuredContent) -> None:
    if not content.standardized_text.strip():
        raise ResumeValidationError("standardized_text is required")
    if not content.name.strip():
        content.name = "待补充"
        content.name_pending = True
    phone = normalize_phone(content.phone or None)
    if content.phone and phone and not PHONE_RE.match(phone):
        raise ResumeValidationError("invalid phone format")
    if content.email and not EMAIL_RE.match(content.email.strip()):
        raise ResumeValidationError("invalid email format")
    if phone:
        content.phone = phone
    if content.email:
        content.email = content.email.strip()


async def confirm_resume_version(
    session: AsyncSession,
    *,
    version_id: UUID,
    payload: ConfirmResumeRequest,
    actor: User,
    request_context: RequestContext,
) -> ResumeVersionOut:
    file_version = await get_resume_version_by_id(session, version_id)
    if file_version is None or file_version.resume is None:
        raise ResumeNotFoundError("resume version not found")
    resume = file_version.resume
    candidate = resume.candidate
    content = payload.content
    _validate_confirm_content(content)

    # link to existing candidate if HR chose duplicate merge target
    if payload.link_candidate_id and payload.link_candidate_id != candidate.id:
        target = await get_candidate_by_id(session, payload.link_candidate_id)
        if target is None:
            raise ResumeNotFoundError("link candidate not found")
        resume.candidate_id = target.id
        candidate = target
        await record_audit(
            session,
            action="resume.duplicate_link",
            result="success",
            resource_type="resume",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(resume.id),
            changes={"linked_candidate_id": str(target.id)},
        )

    # sync candidate contact
    if content.name and content.name != "待补充":
        candidate.name = content.name[:128]
    phone = normalize_phone(content.phone or None)
    email = normalize_email(content.email or None)
    if phone:
        candidate.phone = phone
    if email:
        candidate.email = email

    confirmed_count = await count_confirmed_versions(session, resume.id)
    now = datetime.now(UTC)
    confirmed = ResumeVersion(
        resume_id=resume.id,
        kind=VERSION_KIND_CONFIRMED,
        version_label=f"C{confirmed_count + 1}",
        status=RESUME_STATUS_CONFIRMED,
        source_file_version_id=file_version.id
        if file_version.kind == VERSION_KIND_FILE
        else file_version.source_file_version_id or file_version.id,
        original_filename=file_version.original_filename,
        content_type=file_version.content_type,
        file_size=file_version.file_size,
        storage_key=file_version.storage_key,
        extracted_text=file_version.extracted_text,
        ai_structured=file_version.ai_structured,
        draft_content=content.model_dump(),
        confirmed_content=content.model_dump(),
        standardized_text=content.standardized_text.strip(),
        confirmed_by=actor.id,
        confirmed_at=now,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    await add_resume_version(session, confirmed)
    resume.current_confirmed_version_id = confirmed.id
    if file_version.kind == VERSION_KIND_FILE:
        file_version.status = RESUME_STATUS_CONFIRMED
        file_version.updated_at = now

    # advance applications still waiting on parse
    from sqlalchemy import select

    apps = await session.execute(
        select(JobApplication).where(
            JobApplication.candidate_id == candidate.id,
            JobApplication.pipeline_status == PIPELINE_PENDING_PARSE,
            JobApplication.status == APPLICATION_STATUS_IN_PROGRESS,
        )
    )
    for app in apps.scalars().all():
        app.resume_version_id = confirmed.id
        app.pipeline_status = PIPELINE_PENDING_HR_SCREEN
        app.updated_at = now
        await add_status_log(
            session,
            ApplicationStatusLog(
                application_id=app.id,
                from_status=PIPELINE_PENDING_PARSE,
                to_status=PIPELINE_PENDING_HR_SCREEN,
                reason="resume confirmed",
                actor_id=actor.id,
            ),
        )

    await record_audit(
        session,
        action="resume.confirm",
        result="success",
        resource_type="resume_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(confirmed.id),
        changes={
            "source_version_id": str(file_version.id),
            "version_label": confirmed.version_label,
        },
    )
    await session.commit()
    return await get_resume_version(session, confirmed.id)


async def create_application_for_resume(
    session: AsyncSession,
    *,
    payload: CreateApplicationRequest,
    actor: User,
    request_context: RequestContext,
) -> ApplicationOut:
    job = await get_job_by_id(session, payload.job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status not in {JOB_STATUS_OPEN, JOB_STATUS_PAUSED}:
        raise ResumeStateError("job must be open or paused")
    published = get_version_by_id(job, job.current_version_id)
    if published is None or published.status != VERSION_STATUS_PUBLISHED:
        raise ResumeStateError("job has no published version")

    candidate = await get_candidate_by_id(session, payload.candidate_id)
    if candidate is None:
        raise ResumeNotFoundError("candidate not found")

    existing = await find_open_application(
        session, candidate_id=candidate.id, job_id=job.id
    )
    if existing is not None:
        return await _to_application_out(session, existing, job_name=job.name)

    resume_version_id = payload.resume_version_id
    if resume_version_id is None:
        # use current confirmed
        from sqlalchemy import select

        resume = (
            await session.execute(
                select(Resume).where(Resume.candidate_id == candidate.id)
            )
        ).scalar_one_or_none()
        if resume is None or resume.current_confirmed_version_id is None:
            raise ResumeStateError("candidate has no confirmed resume version")
        resume_version_id = resume.current_confirmed_version_id

    version = await get_resume_version_by_id(session, resume_version_id)
    if version is None or version.status != RESUME_STATUS_CONFIRMED:
        raise ResumeStateError("resume must be confirmed before creating application")

    now = datetime.now(UTC)
    application = await create_application(
        session,
        candidate_id=candidate.id,
        job_id=job.id,
        job_version_id=published.id,
        status=APPLICATION_STATUS_IN_PROGRESS,
        pipeline_status=PIPELINE_PENDING_HR_SCREEN,
        resume_version_id=version.id,
        interview_started=False,
        interview_task_state=INTERVIEW_TASK_NONE,
        lock_version=1,
    )
    application.created_at = now
    application.updated_at = now
    await add_status_log(
        session,
        ApplicationStatusLog(
            application_id=application.id,
            from_status=None,
            to_status=PIPELINE_PENDING_HR_SCREEN,
            reason="application created",
            actor_id=actor.id,
        ),
    )
    await record_audit(
        session,
        action="application.create",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "job_id": str(job.id),
            "job_version_id": str(published.id),
            "resume_version_id": str(version.id),
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    refreshed = await get_application_by_id(session, application.id)
    assert refreshed is not None
    return await _to_application_out(session, refreshed, job_name=job.name)


async def _to_application_out(
    session: AsyncSession,
    application: JobApplication,
    *,
    job_name: str | None = None,
) -> ApplicationOut:
    job = await get_job_by_id(session, application.job_id)
    version = get_version_by_id(job, application.job_version_id) if job else None
    candidate = application.candidate
    return ApplicationOut(
        id=application.id,
        candidate_id=application.candidate_id,
        candidate_name=candidate.name if candidate else "",
        job_id=application.job_id,
        job_name=job_name or (job.name if job else None),
        job_version_id=application.job_version_id,
        job_version_label=(
            (version.version_label or f"V{version.major}.{version.minor}")
            if version is not None
            else None
        ),
        resume_version_id=application.resume_version_id,
        pipeline_status=application.pipeline_status,  # type: ignore[arg-type]
        status=application.status,
        lock_version=application.lock_version,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def get_application_detail(
    session: AsyncSession, application_id: UUID
) -> ApplicationOut:
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise ResumeNotFoundError("application not found")
    return await _to_application_out(session, application)


def _dimensions_for_score(job_version: Any) -> list[dict[str, Any]]:
    raw = list(job_version.score_dimensions or [])
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or ""),
                "weight": float(item.get("weight") or 0),
            }
        )
    return [d for d in cleaned if d["name"] and d["weight"] > 0]


async def create_resume_score_task(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: CreateScoreTaskRequest,
    actor: User,
    request_context: RequestContext,
) -> ScoreTaskCreated:
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise ResumeNotFoundError("application not found")
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise ResumeStateError("closed application cannot be scored")
    job = await get_job_by_id(session, application.job_id)
    if job is None:
        raise JobNotFoundError("job not found")
    if job.status not in {JOB_STATUS_OPEN, JOB_STATUS_PAUSED}:
        raise ResumeStateError("job must be open or paused to score")
    job_version = get_version_by_id(job, application.job_version_id)
    if job_version is None or job_version.status != VERSION_STATUS_PUBLISHED:
        raise ResumeStateError("application must bind a published job version")

    if payload.idempotency_key:
        existing = await find_ai_task_by_idempotency(
            session,
            created_by=actor.id,
            business_id=application.id,
            task_type=TASK_TYPE_RESUME_SCORE,
            idempotency_key=payload.idempotency_key,
        )
        if existing is not None:
            return ScoreTaskCreated(
                task_id=existing.id,
                application_id=application.id,
                status=existing.status,
            )

    inflight = await find_inflight_task(
        session,
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application.id,
        task_type=TASK_TYPE_RESUME_SCORE,
        inflight_statuses={AI_TASK_STATUS_PENDING, AI_TASK_STATUS_RUNNING},
    )
    if inflight is not None:
        raise ConflictError("a resume score task is already pending or running")

    resume_version_id = payload.resume_version_id or application.resume_version_id
    if resume_version_id is None:
        raise ResumeStateError("resume version is required")
    resume_version = await get_resume_version_by_id(session, resume_version_id)
    if resume_version is None or resume_version.status != RESUME_STATUS_CONFIRMED:
        raise ResumeStateError("resume must be confirmed before scoring")

    dims = _dimensions_for_score(job_version)
    if not dims:
        raise ResumeStateError("job version has no score dimensions")
    weight_sum = sum(float(item["weight"]) for item in dims)
    if abs(weight_sum - 100.0) > 0.01:
        raise ResumeStateError("job version dimension weights must sum to 100%")
    jd_content = (job_version.raw_jd_text or "").strip()
    if not jd_content:
        jd_content = str(job_version.structured_jd or "")
    if not jd_content.strip():
        raise ResumeStateError("job version missing JD content")

    resume_text = (resume_version.standardized_text or "").strip()
    if not resume_text and resume_version.confirmed_content:
        resume_text = str(
            (resume_version.confirmed_content or {}).get("standardized_text") or ""
        ).strip()
    if not resume_text:
        raise ResumeStateError("confirmed resume text is empty")

    settings = get_settings()
    now = datetime.now(UTC)
    job_version_label = job_version.version_label or (
        f"V{job_version.major}.{job_version.minor}"
    )
    input_snapshot = {
        "schema_version": SCORE_SNAPSHOT_SCHEMA_VERSION,
        "candidate_id": str(application.candidate_id),
        "application_id": str(application.id),
        "job_id": str(job.id),
        "job_version_id": str(job_version.id),
        "job_version": job_version_label,
        "job_version_label": job_version_label,
        "job_title": job.name,
        "resume_id": str(resume_version.resume_id),
        "resume_version_id": str(resume_version.id),
        "resume_confirmed_version": resume_version.version_label,
        "resume_version_label": resume_version.version_label,
        "jd_content": jd_content,
        "dimensions": dims,
        "dimensions_json": dims,
        "resume_text": resume_text,
        "workflow_key": SCORE_WORKFLOW_KEY,
        "workflow_version": settings.DIFY_RESUME_SCORE_WORKFLOW_ID or "configured",
        "requested_by": str(actor.id),
        "requested_at": now.isoformat(),
        "idempotency_key": payload.idempotency_key,
    }
    task = AITask(
        task_type=TASK_TYPE_RESUME_SCORE,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_APPLICATION,
        business_id=application.id,
        version_id=job_version.id,
        created_by=actor.id,
        idempotency_key=payload.idempotency_key,
        input_snapshot=input_snapshot,
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    await add_ai_task(session, task)
    await record_audit(
        session,
        action="application.resume_score.create",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "ai_task_id": str(task.id),
            "resume_version_id": str(resume_version.id),
            "job_version_id": str(job_version.id),
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    enqueue_ai_task(task.id)
    return ScoreTaskCreated(
        task_id=task.id,
        application_id=application.id,
        status=task.status,
    )


def recompute_weighted_total(
    *,
    dimensions: list[dict[str, Any]],
    weight_map: dict[str, float],
) -> tuple[list[dict[str, Any]], float]:
    recomputed, total, _difference, _warnings = compute_score_totals(
        dimensions=dimensions,
        weight_map=weight_map,
        model_total=None,
    )
    return recomputed, total


async def persist_resume_score_result(
    session: AsyncSession,
    *,
    task: AITask,
    raw_output: dict[str, Any] | None,
    normalized: dict[str, Any],
) -> AiResult:
    """Called by worker after successful RESUME_SCORE schema validation."""
    snapshot = task.input_snapshot or {}
    validate_score_against_snapshot(normalized=normalized, snapshot=snapshot)
    application_id = UUID(str(snapshot["application_id"]))
    dims_snapshot = snapshot_dimensions(snapshot)
    weight_map = {
        str(item["name"]): float(item["weight"])
        for item in dims_snapshot
        if item.get("name")
    }
    desc_map = {
        str(item["name"]): str(item.get("description") or "")
        for item in dims_snapshot
        if item.get("name")
    }
    dims_in = order_dimensions_by_snapshot(
        list(normalized.get("dimensions") or []), snapshot
    )
    for item in dims_in:
        if item.get("name") in desc_map and not item.get("description"):
            item["description"] = desc_map[str(item["name"])]
    model_total = normalized.get("total_score")
    model_total_f = float(model_total) if model_total is not None else None
    recomputed_dims, calculated, difference, warnings = compute_score_totals(
        dimensions=dims_in,
        weight_map=weight_map,
        model_total=model_total_f,
    )
    normalized = {
        **normalized,
        "dimensions": recomputed_dims,
        "total_score": calculated,
        "model_total_score": model_total_f,
        "calculated_total_score": calculated,
        "score_difference": difference,
        "validation_warnings": warnings,
        "schema_version": SCORE_RESULT_SCHEMA_VERSION,
        "requested_by": snapshot.get("requested_by"),
    }
    await mark_previous_results_not_current(
        session,
        application_id=application_id,
        result_type=TASK_TYPE_RESUME_SCORE,
    )
    count = await count_ai_results_for_application(
        session, application_id=application_id, result_type=TASK_TYPE_RESUME_SCORE
    )
    result = AiResult(
        task_id=task.id,
        result_type=TASK_TYPE_RESUME_SCORE,
        version_label=f"M{count + 1}",
        schema_version=SCORE_RESULT_SCHEMA_VERSION,
        application_id=application_id,
        candidate_id=UUID(str(snapshot["candidate_id"])),
        job_version_id=UUID(str(snapshot["job_version_id"])),
        resume_version_id=UUID(str(snapshot["resume_version_id"])),
        raw_output=raw_output,
        normalized_result=normalized,
        model_total_score=model_total_f,
        calculated_total_score=calculated,
        score_difference=difference,
        validation_warnings=warnings,
        is_current=True,
        is_stale=False,
    )
    await add_ai_result(session, result)
    return result


async def apply_resume_parse_success(
    session: AsyncSession,
    *,
    task: AITask,
    result_payload: dict[str, Any],
) -> None:
    version = await get_resume_version_by_id(session, task.business_id)
    if version is None:
        return
    version.ai_structured = result_payload
    # seed draft from AI without overwriting manual draft fields already edited
    if not version.draft_content or not (version.draft_content or {}).get(
        "_manual_edited"
    ):
        structured = ResumeStructuredContent.model_validate(
            {
                **result_payload,
                "name_pending": not bool(str(result_payload.get("name") or "").strip()),
                "field_sources": {
                    key: "ai"
                    for key in (
                        "name",
                        "phone",
                        "email",
                        "education",
                        "work_experience",
                        "projects",
                        "skills",
                        "standardized_text",
                    )
                },
            }
        )
        if not structured.name.strip():
            structured.name = "待补充"
            structured.name_pending = True
        version.draft_content = structured.model_dump()
        version.standardized_text = structured.standardized_text
    version.status = RESUME_STATUS_PENDING_REVIEW
    version.updated_at = datetime.now(UTC)


async def apply_resume_parse_failure(
    session: AsyncSession,
    *,
    task: AITask,
) -> None:
    version = await get_resume_version_by_id(session, task.business_id)
    if version is None:
        return
    if version.status == RESUME_STATUS_PARSING:
        version.status = RESUME_STATUS_PARSE_FAILED
        version.updated_at = datetime.now(UTC)


async def _report_from_result(
    session: AsyncSession,
    result: AiResult,
) -> ScoreReportOut:
    application = await get_application_by_id(session, result.application_id)  # type: ignore[arg-type]
    if application is None:
        raise ResumeNotFoundError("application not found")
    job = await get_job_by_id(session, application.job_id)
    assert job is not None
    job_version = get_version_by_id(
        job, result.job_version_id or application.job_version_id
    )
    resume_version = await get_resume_version_by_id(
        session, result.resume_version_id  # type: ignore[arg-type]
    )
    payload = ResumeScoreResult.model_validate(result.normalized_result)
    candidate = application.candidate
    requested_by = None
    if isinstance(result.normalized_result, dict):
        requested_by = result.normalized_result.get("requested_by")
    calculated = (
        result.calculated_total_score
        if result.calculated_total_score is not None
        else float(payload.total_score or 0)
    )
    warnings = list(result.validation_warnings or [])
    if not warnings and isinstance(result.normalized_result, dict):
        warnings = list(result.normalized_result.get("validation_warnings") or [])
    return ScoreReportOut(
        application_id=application.id,
        result_id=result.id,
        version_label=result.version_label,
        schema_version=result.schema_version or SCORE_RESULT_SCHEMA_VERSION,
        candidate_id=application.candidate_id,
        candidate_name=candidate.name if candidate else "",
        job_id=job.id,
        job_name=job.name,
        job_version_id=result.job_version_id or application.job_version_id,
        job_version_label=(
            (job_version.version_label or f"V{job_version.major}.{job_version.minor}")
            if job_version
            else ""
        ),
        resume_version_id=result.resume_version_id or application.resume_version_id,  # type: ignore[arg-type]
        resume_version_label=resume_version.version_label if resume_version else "",
        total_score=float(calculated or 0),
        calculated_total_score=float(calculated or 0),
        model_total_score=result.model_total_score,
        score_difference=result.score_difference,
        validation_warnings=warnings,
        recommendation=payload.recommendation,
        score_band=payload.score_band,
        summary=payload.summary,
        information_insufficient=payload.information_insufficient,
        dimensions=payload.dimensions,
        risks=payload.risks,
        must_have_check=payload.must_have_check,
        is_current=result.is_current,
        is_stale=result.is_stale,
        requested_by=str(requested_by) if requested_by else None,
        created_at=result.created_at,
        lock_version=application.lock_version,
    )


async def get_score_report(
    session: AsyncSession, application_id: UUID
) -> ScoreReportOut:
    result = await get_current_ai_result(
        session, application_id=application_id, result_type=TASK_TYPE_RESUME_SCORE
    )
    if result is None:
        raise ResumeNotFoundError("score report not found")
    return await _report_from_result(session, result)


async def get_score_history(
    session: AsyncSession, application_id: UUID
) -> ScoreHistoryResponse:
    results = await list_ai_results(
        session, application_id=application_id, result_type=TASK_TYPE_RESUME_SCORE
    )
    items = [await _report_from_result(session, item) for item in results]
    return ScoreHistoryResponse(items=items)


async def get_score_history_item(
    session: AsyncSession,
    *,
    application_id: UUID,
    result_id: UUID,
) -> ScoreReportOut:
    result = await get_ai_result_by_id(session, result_id)
    if (
        result is None
        or result.application_id != application_id
        or result.result_type != TASK_TYPE_RESUME_SCORE
    ):
        raise ResumeNotFoundError("score report not found")
    return await _report_from_result(session, result)


def _screening_out(
    decision: ScreeningDecision, *, lock_version: int
) -> ScreeningDecisionOut:
    return ScreeningDecisionOut(
        id=decision.id,
        application_id=decision.application_id,
        decision=decision.decision,  # type: ignore[arg-type]
        reason_code=decision.reason_code,
        reason=decision.reason,
        from_pipeline_status=decision.from_pipeline_status,  # type: ignore[arg-type]
        to_pipeline_status=decision.to_pipeline_status,  # type: ignore[arg-type]
        lock_version=lock_version,
        created_at=decision.created_at,
    )


_DECISION_TO_PIPELINE = {
    SCREENING_ENTER_INTERVIEW: PIPELINE_INTERVIEWING,
    SCREENING_HOLD: PIPELINE_PENDING_HR_SCREEN,
    SCREENING_REJECT: PIPELINE_REJECTED,
    SCREENING_TALENT_POOL: PIPELINE_TALENT_POOL,
}


async def create_screening_decision(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: ScreeningDecisionRequest,
    actor: User,
    request_context: RequestContext,
) -> ScreeningDecisionOut:
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise ResumeNotFoundError("application not found")
    if application.lock_version != payload.lock_version:
        raise ConflictError(
            "application was updated by another user; refresh and retry"
        )
    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise ResumeStateError("closed application cannot be screened")
    if application.pipeline_status in {PIPELINE_REJECTED, PIPELINE_TALENT_POOL}:
        raise ResumeStateError("terminal application cannot be screened again")
    if payload.decision not in _DECISION_TO_PIPELINE:
        raise ResumeValidationError("invalid decision")
    try:
        validate_screening_payload(
            decision=payload.decision,
            reason_code=payload.reason_code,
            reason=payload.reason,
            required_decisions=set(SCREENING_REASON_REQUIRED_DECISIONS),
            allowed_codes=set(SCREENING_REASON_CODES),
            other_code=SCREENING_REASON_OTHER,
        )
    except ValueError as exc:
        raise ResumeValidationError(str(exc)) from exc

    if payload.idempotency_key:
        existing = await find_screening_by_idempotency(
            session,
            application_id=application.id,
            idempotency_key=payload.idempotency_key,
        )
        if existing is not None:
            return _screening_out(existing, lock_version=application.lock_version)

    from_status = application.pipeline_status
    to_status = _DECISION_TO_PIPELINE[payload.decision]
    now = datetime.now(UTC)
    current_result = await get_current_ai_result(
        session, application_id=application.id, result_type=TASK_TYPE_RESUME_SCORE
    )
    decision = ScreeningDecision(
        application_id=application.id,
        decision=payload.decision,
        reason_code=payload.reason_code,
        reason=payload.reason,
        from_pipeline_status=from_status,
        to_pipeline_status=to_status,
        decided_by=actor.id,
        ai_result_id=current_result.id if current_result else None,
        idempotency_key=payload.idempotency_key,
        created_at=now,
    )
    await add_screening_decision(session, decision)
    application.pipeline_status = to_status
    application.lock_version += 1
    application.updated_at = now
    if payload.decision == SCREENING_ENTER_INTERVIEW:
        application.interview_started = True
    if payload.decision == SCREENING_REJECT:
        application.status = "rejected"
        application.close_action = "reject"
        application.close_reason = payload.reason
    if payload.decision == SCREENING_TALENT_POOL:
        application.status = "terminated"
        application.close_action = "terminate"
        application.close_reason = payload.reason or "talent_pool"

    await add_status_log(
        session,
        ApplicationStatusLog(
            application_id=application.id,
            from_status=from_status,
            to_status=to_status,
            reason=payload.reason,
            actor_id=actor.id,
        ),
    )
    await record_audit(
        session,
        action="application.screening_decision",
        result="success",
        resource_type="job_application",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(application.id),
        changes={
            "decision": payload.decision,
            "reason_code": payload.reason_code,
            "from": from_status,
            "to": to_status,
            "lock_version": application.lock_version,
            "score_result_id": str(current_result.id) if current_result else None,
            "idempotency_key": payload.idempotency_key,
        },
    )
    await session.commit()
    return _screening_out(decision, lock_version=application.lock_version)


async def retry_resume_parse_manual_text(
    session: AsyncSession,
    *,
    version_id: UUID,
    text: str,
    actor: User,
    request_context: RequestContext,
) -> ResumeVersionOut:
    """Allow HR to paste resume text and re-queue parse, or keep for manual confirm."""
    version = await get_resume_version_by_id(session, version_id)
    if version is None:
        raise ResumeNotFoundError("resume version not found")
    cleaned = text.strip()
    if not cleaned:
        raise ResumeValidationError("text is required")
    version.extracted_text = cleaned
    version.standardized_text = cleaned
    draft = dict(version.draft_content or {})
    draft["standardized_text"] = cleaned
    version.draft_content = draft
    now = datetime.now(UTC)
    task = AITask(
        task_type=TASK_TYPE_RESUME_PARSE,
        status=AI_TASK_STATUS_PENDING,
        business_type=BUSINESS_TYPE_RESUME_VERSION,
        business_id=version.id,
        version_id=version.id,
        created_by=actor.id,
        input_snapshot={
            "resume_text": cleaned,
            "candidate_id": str(version.resume.candidate_id),
            "original_filename": version.original_filename,
            "manual_text": True,
        },
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    await add_ai_task(session, task)
    version.parse_task_id = task.id
    version.status = RESUME_STATUS_PARSING
    version.updated_at = now
    await record_audit(
        session,
        action="resume.parse_retry",
        result="success",
        resource_type="resume_version",
        request_context=request_context,
        actor_user_id=actor.id,
        resource_id=str(version.id),
        changes={"ai_task_id": str(task.id)},
    )
    await session.commit()
    enqueue_ai_task(task.id)
    return await get_resume_version(session, version.id)


# used by worker without importing circular get_bytes in success path
async def load_resume_file_bytes(storage_key: str) -> bytes:
    return get_bytes(storage_key)
