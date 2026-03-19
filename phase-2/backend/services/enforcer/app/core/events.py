import json, time, logging
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

async def publish_event(event_type: str, payload: dict) -> None:
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.xadd(
            name="catalog.events",
            fields={"type": event_type, "payload": json.dumps(payload),
                    "timestamp": str(int(time.time() * 1000)), "version": "1"},
            maxlen=10_000, approximate=True,
        )
    except Exception as exc:
        logger.error("Failed to publish event %s: %s", event_type, exc)
    finally:
        await redis.aclose()
