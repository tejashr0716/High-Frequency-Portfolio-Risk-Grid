import json
import os
import redis.asyncio as redis
from typing import Optional, Dict, Any

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "4"))

redis_client: Optional[redis.Redis] = None

async def init_redis() -> redis.Redis:
    global redis_client
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None

    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True
    )
    return redis_client

async def get_redis() -> redis.Redis:
    global redis_client
    if redis_client is None:
        await init_redis()
    return redis_client

async def close_redis():
    global redis_client
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None

async def cache_portfolio_risk(portfolio_id: str, data: Dict[str, Any], ttl: int = 300):
    client = await get_redis()
    key = f"riskgrid:portfolio:{portfolio_id}:risk"
    await client.set(key, json.dumps(data), ex=ttl)

async def get_cached_portfolio_risk(portfolio_id: str) -> Optional[Dict[str, Any]]:
    client = await get_redis()
    key = f"riskgrid:portfolio:{portfolio_id}:risk"
    val = await client.get(key)
    return json.loads(val) if val else None

async def publish_risk_update(event: Dict[str, Any]):
    client = await get_redis()
    await client.publish("riskgrid:risk_events", json.dumps(event))
