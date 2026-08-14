from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.schemas.health import (
    ApiResponse,
    HealthData,
    LiveData,
    ReadyChecks,
    ReadyData,
)
from app.services.readiness import run_readiness_checks

router = APIRouter()


@router.get("/health", response_model=ApiResponse[HealthData])
def health_check() -> ApiResponse[HealthData]:
    settings = get_settings()
    return ApiResponse(
        code=0,
        message="ok",
        data=HealthData(status="ok", service=settings.APP_NAME),
    )


@router.get("/health/live", response_model=ApiResponse[LiveData])
def health_live() -> ApiResponse[LiveData]:
    return ApiResponse(
        code=0,
        message="alive",
        data=LiveData(status="alive"),
    )


@router.get("/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    checks = await run_readiness_checks(
        request.app.state.db_engine,
        request.app.state.redis,
    )
    all_up = all(status == "up" for status in checks.values())

    if all_up:
        response = ApiResponse(
            code=0,
            message="ready",
            data=ReadyData(status="ready", checks=ReadyChecks(**checks)),
        )
        return JSONResponse(status_code=200, content=response.model_dump())

    response = ApiResponse(
        code=50300,
        message="service not ready",
        data=ReadyData(status="not_ready", checks=ReadyChecks(**checks)),
    )
    return JSONResponse(status_code=503, content=response.model_dump())
