import asyncio
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import check_database
from app.integrations.redis import check_redis

CheckStatus = Literal["up", "down"]


async def run_readiness_checks(
    engine: AsyncEngine,
    redis_client: Redis,
) -> dict[str, CheckStatus]:
    postgres_result, redis_result = await asyncio.gather(
        check_database(engine),
        check_redis(redis_client),
    )

    return {
        "postgresql": "up" if postgres_result else "down",
        "redis": "up" if redis_result else "down",
    }
