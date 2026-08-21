import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import (
    create_database_engine,
    create_session_factory,
    dispose_database,
)
from app.integrations.redis import close_redis, create_redis_client


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    redis_client = create_redis_client(settings.redis_url)

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.redis = redis_client

    # Process-local integration overlay (env ← enabled DB). No hot reload.
    from app.services.integration_config import bootstrap_integration_overlay

    await bootstrap_integration_overlay(session_factory=session_factory)

    try:
        yield
    finally:
        await close_redis(redis_client)
        await dispose_database(engine)


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return application


app = create_app()
