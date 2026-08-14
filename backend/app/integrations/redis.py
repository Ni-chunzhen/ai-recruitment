from redis.asyncio import Redis
from redis.exceptions import RedisError


def create_redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def check_redis(client: Redis) -> bool:
    try:
        await client.ping()
        return True
    except RedisError:
        return False


async def close_redis(client: Redis) -> None:
    await client.aclose()
