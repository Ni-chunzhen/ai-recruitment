from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.integrations.redis import check_redis, close_redis, create_redis_client


def test_create_redis_client_decodes_responses() -> None:
    client = create_redis_client("redis://127.0.0.1:6379/0")

    assert isinstance(client, Redis)
    assert client.connection_pool.connection_kwargs["decode_responses"] is True


@pytest.mark.asyncio
async def test_check_redis_returns_true_on_success() -> None:
    client = AsyncMock(spec=Redis)
    client.ping = AsyncMock(return_value=True)

    result = await check_redis(client)

    assert result is True
    client.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_redis_returns_false_on_redis_error() -> None:
    client = AsyncMock(spec=Redis)
    client.ping = AsyncMock(side_effect=RedisError("connection failed"))

    result = await check_redis(client)

    assert result is False


@pytest.mark.asyncio
async def test_close_redis_calls_aclose() -> None:
    client = AsyncMock(spec=Redis)
    client.aclose = AsyncMock()

    await close_redis(client)

    client.aclose.assert_awaited_once()
