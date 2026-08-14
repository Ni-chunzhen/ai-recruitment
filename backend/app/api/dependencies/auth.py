from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.models import User
from app.repositories.rbac import user_has_permission
from app.repositories.users import get_user_by_id
from app.services.sessions import SessionService, SessionUnavailableError

bearer_scheme = HTTPBearer(auto_error=False)

FORCE_CHANGE_ALLOWED_PATHS = {
    "/api/v1/auth/me",
    "/api/v1/auth/change-password",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
}


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        yield session


def get_session_service(request: Request) -> SessionService:
    return SessionService(request.app.state.redis)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
    session_service: SessionService = Depends(get_session_service),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    try:
        claims = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        ) from exc

    user = await get_user_by_id(session, claims.sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    if claims.sid:
        try:
            session_record = await session_service.get_session(claims.sid)
        except SessionUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from exc
        if (
            session_record is None
            or session_record.status != "active"
            or session_record.user_id != user.id
            or session_record.token_version != user.token_version
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            )

    if user.must_change_password and request.url.path not in FORCE_CHANGE_ALLOWED_PATHS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="password change required",
        )

    request.state.current_user = user
    request.state.session_id = claims.sid
    return user


def require_permission(permission_code: str):
    async def dependency(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        if not await user_has_permission(user, permission_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="forbidden",
            )
        return user

    return dependency
