from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_user,
    get_db_session,
    get_session_service,
)
from app.core.config import get_settings
from app.models import User
from app.repositories.rbac import list_user_permission_codes
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshResponse,
)
from app.services.audit import RequestContext
from app.services.auth import (
    build_user_summary,
    change_password,
    login_user,
    logout_user,
    refresh_user,
)
from app.services.sessions import SessionService, SessionUnavailableError

router = APIRouter(prefix="/auth", tags=["auth"])


def _request_context(request: Request) -> RequestContext:
    return RequestContext(
        request_id=getattr(request.state, "request_id", "unknown"),
        ip_address=request.client.host if request.client else None,
    )


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 60 * 60,
        path=settings.REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> LoginResponse:
    try:
        result = await login_user(
            session,
            session_service,
            username=payload.username,
            password=payload.password,
            request_context=_request_context(request),
        )
    except SessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    access_token, refresh_token, user_summary = result
    _set_refresh_cookie(response, refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(access_token=access_token, user=user_summary)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> RefreshResponse:
    settings = get_settings()
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not refresh_token:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    try:
        result = await refresh_user(
            session,
            session_service,
            refresh_token=refresh_token,
            request_context=_request_context(request),
        )
    except SessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        ) from exc

    if result is None:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    access_token, new_refresh = result
    _set_refresh_cookie(response, new_refresh)
    response.headers["Cache-Control"] = "no-store"
    return RefreshResponse(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session_service: SessionService = Depends(get_session_service),
) -> Response:
    session_id: UUID | None = getattr(request.state, "session_id", None)
    await logout_user(session_service, session_id=session_id)
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    permissions = await list_user_permission_codes(user)
    return MeResponse(user=build_user_summary(user, permissions))


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password_endpoint(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> None:
    try:
        await change_password(
            session,
            session_service,
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            request_context=_request_context(request),
            current_session_id=getattr(request.state, "session_id", None),
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
