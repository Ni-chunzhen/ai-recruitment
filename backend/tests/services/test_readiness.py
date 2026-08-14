import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.readiness import run_readiness_checks


@pytest.mark.asyncio
async def test_run_readiness_checks_both_up() -> None:
    engine = AsyncMock()
    redis_client = AsyncMock()

    with (
        patch("app.services.readiness.check_database", return_value=True) as mock_db,
        patch("app.services.readiness.check_redis", return_value=True) as mock_redis,
    ):
        result = await run_readiness_checks(engine, redis_client)

    assert result == {"postgresql": "up", "redis": "up"}
    mock_db.assert_awaited_once_with(engine)
    mock_redis.assert_awaited_once_with(redis_client)


@pytest.mark.asyncio
async def test_run_readiness_checks_postgresql_down() -> None:
    engine = AsyncMock()
    redis_client = AsyncMock()

    with (
        patch("app.services.readiness.check_database", return_value=False),
        patch("app.services.readiness.check_redis", return_value=True),
    ):
        result = await run_readiness_checks(engine, redis_client)

    assert result == {"postgresql": "down", "redis": "up"}


@pytest.mark.asyncio
async def test_run_readiness_checks_redis_down() -> None:
    engine = AsyncMock()
    redis_client = AsyncMock()

    with (
        patch("app.services.readiness.check_database", return_value=True),
        patch("app.services.readiness.check_redis", return_value=False),
    ):
        result = await run_readiness_checks(engine, redis_client)

    assert result == {"postgresql": "up", "redis": "down"}


@pytest.mark.asyncio
async def test_run_readiness_checks_both_down() -> None:
    engine = AsyncMock()
    redis_client = AsyncMock()

    with (
        patch("app.services.readiness.check_database", return_value=False),
        patch("app.services.readiness.check_redis", return_value=False),
    ):
        result = await run_readiness_checks(engine, redis_client)

    assert result == {"postgresql": "down", "redis": "down"}


@pytest.mark.asyncio
async def test_run_readiness_checks_runs_in_parallel() -> None:
    engine = AsyncMock()
    redis_client = AsyncMock()
    call_order: list[str] = []

    async def mock_check_database(*_args, **_kwargs) -> bool:
        call_order.append("db_start")
        await asyncio.sleep(0)
        call_order.append("db_end")
        return True

    async def mock_check_redis(*_args, **_kwargs) -> bool:
        call_order.append("redis_start")
        await asyncio.sleep(0)
        call_order.append("redis_end")
        return True

    with (
        patch("app.services.readiness.check_database", side_effect=mock_check_database),
        patch("app.services.readiness.check_redis", side_effect=mock_check_redis),
    ):
        result = await run_readiness_checks(engine, redis_client)

    assert result == {"postgresql": "up", "redis": "up"}
    assert "db_start" in call_order
    assert "redis_start" in call_order
