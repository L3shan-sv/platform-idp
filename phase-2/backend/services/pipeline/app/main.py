"""Nerve IDP — Pipeline Service (port 8003)"""
import asyncio, json, logging, time
from contextlib import asynccontextmanager
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from app.core.config import settings
from app.core.database import async_session_maker

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(run_poller())
    yield

app = FastAPI(title="Nerve Pipeline Service", version=settings.APP_VERSION, lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

async def run_poller():
    from app.models.pipeline import PipelineRun
    headers = {"Authorization": f"token {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    rate_remaining = 5000
    rate_reset = 0

    async with httpx.AsyncClient() as client:
        while True:
            try:
                if rate_remaining < settings.GITHUB_RATE_LIMIT_BUFFER:
                    wait = max(rate_reset - int(time.time()), 60)
                    logger.warning("Rate limit low — pausing %ds", wait)
                    await asyncio.sleep(wait)

                svc_r = await client.get(f"{settings.CATALOG_SERVICE_URL}/api/v1/services", params={"limit":100}, timeout=10.0)
                for svc in svc_r.json().get("items", []):
                    try:
                        r = await client.get(
                            f"https://api.github.com/repos/{settings.GITHUB_ORG}/{svc['name']}/actions/runs",
                            headers=headers, params={"per_page": 5}, timeout=15.0,
                        )
                        rate_remaining = int(r.headers.get("X-RateLimit-Remaining", rate_remaining))
                        rate_reset = int(r.headers.get("X-RateLimit-Reset", rate_reset))
                        if r.status_code != 200:
                            continue
                        for run in r.json().get("workflow_runs", []):
                            status = {"queued":"queued","in_progress":"running","completed":run.get("conclusion","succeeded")}.get(run["status"],"unknown")
                            if status == "success": status = "succeeded"
                            elif status == "failure": status = "failed"
                            async with async_session_maker() as db:
                                existing = await db.scalar(select(PipelineRun).where(PipelineRun.id == str(run["id"])))
                                if not existing:
                                    db.add(PipelineRun(id=str(run["id"]), service_id=svc["id"], run_number=run["run_number"],
                                                       status=status, branch=run.get("head_branch"), commit_sha=run.get("head_sha","")[:40]))
                                    await db.commit()
                                if status == "running":
                                    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
                                    try:
                                        await redis.publish(f"pipeline:{svc['id']}", json.dumps({"run_id": str(run["id"]), "status": status}))
                                    finally:
                                        await redis.aclose()
                    except Exception as exc:
                        logger.debug("Poller error for %s: %s", svc["name"], exc)
            except Exception as exc:
                logger.error("Poller loop error: %s", exc)
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

@app.websocket("/ws/pipelines/{service_id}")
async def pipeline_ws(websocket: WebSocket, service_id: str):
    await websocket.accept()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(f"pipeline:{service_id}")
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"pipeline:{service_id}")
        await pubsub.aclose()
        await redis.aclose()
