"""Catalog service — Redis Streams event publishing with MKSTREAM."""
import json, logging, time
from typing import Any
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)
CATALOG_STREAM = "catalog.events"
CONSUMER_GROUPS = ["maturity-scorer", "neo4j-sync", "cost-intelligence", "dora-metrics"]

async def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True, socket_timeout=5)

async def init_redis_streams() -> None:
    """Create all consumer groups with MKSTREAM. Idempotent on restart."""
    redis = await get_redis()
    try:
        for group in CONSUMER_GROUPS:
            try:
                await redis.xgroup_create(name=CATALOG_STREAM, groupname=group, id="$", mkstream=True)
                logger.info("Created consumer group: %s", group)
            except aioredis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise
    finally:
        await redis.aclose()

async def publish_catalog_event(event_type: str, payload: dict[str, Any]) -> str:
    redis = await get_redis()
    try:
        entry_id = await redis.xadd(
            name=CATALOG_STREAM,
            fields={"type": event_type, "payload": json.dumps(payload),
                    "timestamp": str(int(time.time() * 1000)), "version": "1"},
            maxlen=10_000, approximate=True,
        )
        logger.debug("Published %s [%s]", event_type, entry_id)
        return entry_id
    except Exception as exc:
        logger.error("Failed to publish %s: %s", event_type, exc)
        return ""
    finally:
        await redis.aclose()
