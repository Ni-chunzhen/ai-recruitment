from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db_session, require_permission
from app.models import User
from app.repositories.candidates import CandidateNotFoundError
from app.schemas.candidate_center import (
    CandidateCenterDetailOut,
    CandidateCenterListQuery,
    CandidateCenterListResponse,
)
from app.services.candidate_center import (
    get_candidate_center_application_detail,
    list_candidate_center_applications,
)

router = APIRouter(prefix="/candidate-center", tags=["candidate-center"])


def parse_candidate_center_list_query(request: Request) -> CandidateCenterListQuery:
    try:
        return CandidateCenterListQuery.model_validate(dict(request.query_params))
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.errors(include_context=False),
        ) from exc


@router.get("/applications", response_model=CandidateCenterListResponse)
async def list_candidate_center_applications_endpoint(
    query: Annotated[
        CandidateCenterListQuery, Depends(parse_candidate_center_list_query)
    ],
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> CandidateCenterListResponse:
    return await list_candidate_center_applications(session, query=query)


@router.get(
    "/candidates/{candidate_id}/applications/{application_id}",
    response_model=CandidateCenterDetailOut,
)
async def get_candidate_center_application_detail_endpoint(
    candidate_id: UUID,
    application_id: UUID,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("recruitment.manage")),
) -> CandidateCenterDetailOut:
    try:
        result = await get_candidate_center_application_detail(
            session,
            candidate_id=candidate_id,
            application_id=application_id,
        )
    except CandidateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found"
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    return result
