"""Health endpoints — liveness and readiness."""
import asyncio, logging, time
from typing import Optional
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase
from app.core.config import settings
from app.core.database import check_db_health

logger = logging.getLogger(__name__)
router = APIRouter()


async def check_redis() -> dict:
    start = time.monotonic()
    try:
        client = aioredis.from_url(settings.REDIS_URL, socket_timeout=2)
        await client.ping()
        await client.aclose()
        return {"healthy": True, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"healthy": False, "latency_ms": -1, "error": str(exc)}


async def check_neo4j() -> dict:
    start = time.monotonic()
    try:
        driver = AsyncGraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
        async with driver.session() as session:
            await session.run("RETURN 1")
        await driver.close()
        return {"healthy": True, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"healthy": False, "latency_ms": -1, "error": str(exc)}


async def check_temporal() -> dict:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"http://{settings.TEMPORAL_HOST}:{settings.TEMPORAL_PORT}/health")
            return {"healthy": r.status_code == 200, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"healthy": False, "latency_ms": -1, "error": str(exc)}


async def check_vault() -> dict:
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.VAULT_URL}/v1/sys/health")
            return {"healthy": r.status_code in (200, 429), "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"healthy": False, "latency_ms": -1, "error": str(exc)}


@router.get("/health")
async def liveness():
    return {"status": "ok", "version": settings.APP_VERSION}


@router.get("/health/ready")
async def readiness():
    results = await asyncio.gather(
        check_db_health(), check_redis(), check_neo4j(), check_temporal(), check_vault(),
        return_exceptions=True,
    )

    def normalize(r):
        return r if isinstance(r, dict) else {"healthy": False, "error": str(r)}

    checks = {
        "postgres": normalize(results[0]),
        "redis": normalize(results[1]),
        "neo4j": normalize(results[2]),
        "temporal": normalize(results[3]),
        "vault": normalize(results[4]),
    }
    all_healthy = all(c.get("healthy", False) for c in checks.values())
    return JSONResponse(status_code=200 if all_healthy else 503,
                        content={"ready": all_healthy, "checks": checks})
