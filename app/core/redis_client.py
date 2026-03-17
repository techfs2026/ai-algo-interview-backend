import redis.asyncio as aioredis
from app.core.config import get_settings

settings    = get_settings()
redis_client: aioredis.Redis | None = None


async def init_redis() -> None:
    global redis_client
    redis_client = await aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )


async def close_redis() -> None:
    if redis_client:
        await redis_client.close()


async def get_redis() -> aioredis.Redis:
    """FastAPI依赖注入"""
    if redis_client is None:
        raise RuntimeError("Redis未初始化，请先调用init_redis()")
    return redis_client