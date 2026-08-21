"""Post-interview hiring decisions (immutable history; no Offer / AI)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.candidate import APPLICATION_STATUS_IN_PROGRESS
from app.models.resume import (
    HIRING_DECISIONS,
    HIRING_HOLD,
    HIRING_REASON_CATALOG,
    HIRING_RECOMMEND_HIRE,
    HIRING_REJECT,
    PIPELINE_INTERVIEWING,
    PIPELINE_PENDING_OFFER,
    PIPELINE_REJECTED,
    ApplicationStatusLog,
    HiringDecision,
)
from app.repositories.hiring_decisions import (
    add_hiring_decision,
    find_hiring_by_idempotency,
    list_hiring_by_application,
)
from app.repositories.interview_analyses import (
    get_analysis_by_id,
    get_analysis_version_by_pk,
)
from app.repositories.interview_transcripts import get_transcript_by_round_id
from app.repositories.interviews import get_round_by_id
from app.repositories.resumes import (
    add_status_log,
    get_application_by_id,
    get_application_by_id_for_update,
)
from app.services.audit import RequestContext, record_audit
from app.services.interview_analyses import is_analysis_version_stale

_DECISION_TO_PIPELINE = {
    HIRING_RECOMMEND_HIRE: PIPELINE_PENDING_OFFER,
    HIRING_REJECT: PIPELINE_REJECTED,
    HIRING_HOLD: PIPELINE_INTERVIEWING,
}

_REASON_ALLOWED: dict[str, frozenset[str]] = {
    code: allowed for code, _label, allowed in HIRING_REASON_CATALOG
}

_AUDIT_CHANGE_KEYS = frozenset(
    {
        "decision",
        "reason_code",
        "from",
        "to",
        "lock_version",
        "analysis_version_id",
        "round_id",
        "overall_score",
        "idempotency_key",
    }
)


class HiringNotFoundError(Exception):
    pass


class HiringStateError(Exception):
    pass


class HiringValidationError(Exception):
    pass


class HiringConflictError(Exception):
    pass


@dataclass(frozen=True)
class HiringDecisionRequestData:
    decision: str
    reason_code: str
    analysis_version_id: UUID
    lock_version: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class HiringDecisionResult:
    id: UUID
    application_id: UUID
    decision: str
    reason_code: str
    round_id: UUID
    analysis_version_id: UUID
    overall_score: float | None
    analysis_version_no: int | None
    from_pipeline_status: str
    to_pipeline_status: str
    lock_version: int
    created_at: datetime
    decided_by: UUID | None


def list_hiring_reason_catalog() -> list[dict[str, object]]:
    from app.models.resume import list_hiring_reason_catalog as _catalog

    return _catalog()


def _to_result(row: HiringDecision, *, lock_version: int) -> HiringDecisionResult:
    score = row.overall_score
    if score is not None:
        score = float(score)
    return HiringDecisionResult(
        id=row.id,
        application_id=row.application_id,
        decision=row.decision,
        reason_code=row.reason_code,
        round_id=row.round_id,
        analysis_version_id=row.analysis_version_id,
        overall_score=score,
        analysis_version_no=row.analysis_version_no,
        from_pipeline_status=row.from_pipeline_status,
        to_pipeline_status=row.to_pipeline_status,
        lock_version=lock_version,
        created_at=row.created_at,
        decided_by=row.decided_by,
    )


def _validate_reason(*, decision: str, reason_code: str) -> None:
    if decision not in HIRING_DECISIONS:
        raise HiringValidationError("invalid decision")
    allowed = _REASON_ALLOWED.get(reason_code)
    if allowed is None or decision not in allowed:
        raise HiringValidationError("invalid reason_code for decision")


async def _recover_idempotent_decision(
    session: AsyncSession,
    *,
    application_id: UUID,
    idempotency_key: str,
) -> HiringDecisionResult:
    existing = await find_hiring_by_idempotency(
        session,
        application_id=application_id,
        idempotency_key=idempotency_key,
    )
    if existing is None:
        raise HiringConflictError(
            "application was updated by another user; refresh and retry"
        )
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise HiringNotFoundError("application not found")
    return _to_result(existing, lock_version=application.lock_version)


async def create_hiring_decision(
    session: AsyncSession,
    *,
    application_id: UUID,
    payload: HiringDecisionRequestData,
    actor: User,
    request_context: RequestContext,
) -> HiringDecisionResult:
    application = await get_application_by_id_for_update(session, application_id)
    if application is None:
        raise HiringNotFoundError("application not found")

    if payload.idempotency_key:
        existing = await find_hiring_by_idempotency(
            session,
            application_id=application.id,
            idempotency_key=payload.idempotency_key,
        )
        if existing is not None:
            return _to_result(existing, lock_version=application.lock_version)

    if application.lock_version != payload.lock_version:
        raise HiringConflictError(
            "application was updated by another user; refresh and retry"
        )

    if application.status != APPLICATION_STATUS_IN_PROGRESS:
        raise HiringStateError("closed application cannot receive hiring decision")
    if application.pipeline_status != PIPELINE_INTERVIEWING:
        raise HiringStateError(
            "only interviewing applications can receive hiring decisions"
        )

    _validate_reason(decision=payload.decision, reason_code=payload.reason_code)

    version = await get_analysis_version_by_pk(
        session, version_id=payload.analysis_version_id
    )
    if version is None:
        raise HiringNotFoundError("analysis version not found")

    analysis = await get_analysis_by_id(session, analysis_id=version.analysis_id)
    if analysis is None:
        raise HiringNotFoundError("analysis not found")

    round_ = await get_round_by_id(session, analysis.interview_round_id)
    if round_ is None:
        raise HiringNotFoundError("interview round not found")
    if round_.application_id != application.id:
        raise HiringValidationError(
            "analysis version does not belong to this application"
        )

    if analysis.current_version_id != version.id:
        raise HiringStateError("analysis version is not current")

    transcript = await get_transcript_by_round_id(session, round_.id)
    if is_analysis_version_stale(version, transcript):
        raise HiringStateError("analysis version is stale")

    to_status = _DECISION_TO_PIPELINE[payload.decision]
    from_status = PIPELINE_INTERVIEWING
    now = datetime.now(UTC)
    overall = version.overall_score
    if overall is not None:
        overall = float(overall)

    decision_row = HiringDecision(
        application_id=application.id,
        decision=payload.decision,
        reason_code=payload.reason_code,
        round_id=round_.id,
        analysis_version_id=version.id,
        overall_score=overall,
        analysis_version_no=version.version_no,
        transcript_version_id=version.transcript_version_id,
        job_version_id=version.job_version_id,
        from_pipeline_status=from_status,
        to_pipeline_status=to_status,
        decided_by=actor.id,
        idempotency_key=payload.idempotency_key,
        created_at=now,
    )
    try:
        await add_hiring_decision(session, decision_row)

        application.pipeline_status = to_status
        application.lock_version += 1
        application.updated_at = now
        if payload.decision == HIRING_REJECT:
            application.status = "rejected"
            application.close_action = "reject"
            application.close_reason = payload.reason_code

        await add_status_log(
            session,
            ApplicationStatusLog(
                application_id=application.id,
                from_status=from_status,
                to_status=to_status,
                reason=payload.reason_code,
                actor_id=actor.id,
            ),
        )

        changes = {
            "decision": payload.decision,
            "reason_code": payload.reason_code,
            "from": from_status,
            "to": to_status,
            "lock_version": application.lock_version,
            "analysis_version_id": str(version.id),
            "round_id": str(round_.id),
            "overall_score": overall,
            "idempotency_key": payload.idempotency_key,
        }
        assert set(changes.keys()) <= _AUDIT_CHANGE_KEYS
        await record_audit(
            session,
            action="application.hiring_decision",
            result="success",
            resource_type="job_application",
            request_context=request_context,
            actor_user_id=actor.id,
            resource_id=str(application.id),
            changes=changes,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if payload.idempotency_key:
            return await _recover_idempotent_decision(
                session,
                application_id=application_id,
                idempotency_key=payload.idempotency_key,
            )
        raise HiringConflictError(
            "application was updated by another user; refresh and retry"
        ) from None
    return _to_result(decision_row, lock_version=application.lock_version)


async def list_hiring_decisions(
    session: AsyncSession, *, application_id: UUID
) -> list[HiringDecisionResult]:
    application = await get_application_by_id(session, application_id)
    if application is None:
        raise HiringNotFoundError("application not found")
    rows = await list_hiring_by_application(session, application_id=application_id)
    return [
        _to_result(row, lock_version=application.lock_version) for row in rows
    ]
