from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_db_session,
    get_session_service,
    require_permission,
)
from app.models import User
from app.repositories.users import (
    UserAlreadyExistsError,
    get_user_by_id,
    list_users,
)
from app.schemas.user import (
    CreateUserRequest,
    CreateUserResponse,
    ReplaceRolesRequest,
    ResetPasswordResponse,
    UpdateUserRequest,
    UserListItem,
    UserListResponse,
)
from app.services.audit import RequestContext
from app.services.sessions import SessionService, SessionUnavailableError
from app.services.users import (
    create_managed_user,
    replace_managed_user_roles,
    reset_user_password,
    to_user_list_item,
    update_managed_user,
)

router = APIRouter(prefix="/users", tags=["users"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


@router.get("", response_model=UserListResponse)
async def get_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    username: str | None = Query(default=None, max_length=64),
    is_active: bool | None = None,
    role_name: str | None = Query(default=None, max_length=64),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(require_permission("account.read")),
) -> UserListResponse:
    users, total = await list_users(
        session,
        page=page,
        page_size=page_size,
        username=username,
        is_active=is_active,
        role_name=role_name,
    )
    return UserListResponse(
        items=[to_user_list_item(user) for user in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    payload: CreateUserRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    actor: User = Depends(require_permission("account.create")),
) -> CreateUserResponse:
    try:
        user, temporary_password = await create_managed_user(
            session,
            session_service,
            username=payload.username,
            display_name=payload.display_name,
            role_names=payload.role_names,
            request_context=_request_context(request),
            actor=actor,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conflict",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        ) from exc

    return CreateUserResponse(user=user, temporary_password=temporary_password)


@router.patch("/{user_id}", response_model=UserListItem)
async def update_user_endpoint(
    user_id: UUID,
    payload: UpdateUserRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    actor: User = Depends(require_permission("account.update")),
) -> UserListItem:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    try:
        return await update_managed_user(
            session,
            session_service,
            user=user,
            actor=actor,
            request_context=_request_context(request),
            display_name=payload.display_name,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        ) from exc


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password_endpoint(
    user_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    actor: User = Depends(require_permission("account.reset_password")),
) -> ResetPasswordResponse:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    temporary_password = await reset_user_password(
        session,
        session_service,
        user=user,
        request_context=_request_context(request),
        actor=actor,
    )
    return ResetPasswordResponse(temporary_password=temporary_password)


@router.put("/{user_id}/roles", response_model=UserListItem)
async def replace_roles_endpoint(
    user_id: UUID,
    payload: ReplaceRolesRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
    actor: User = Depends(require_permission("role.assign")),
) -> UserListItem:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    try:
        return await replace_managed_user_roles(
            session,
            session_service,
            user=user,
            actor=actor,
            role_names=payload.role_names,
            request_context=_request_context(request),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        ) from exc
