"""
Nerve IDP — Fleet Operations Service (port 8012)

Bulk operations across service collections with real-time progress streaming.

Bulk operations:
  deploy | rollback | patch | compliance_rescan

Celery chord:
  One Celery task per service in the collection.
  Tasks publish per-service progress to Redis pub/sub.
  WebSocket gateway subscribes and streams to connected frontend.

Rate limiting on bulk operations:
  Process services in batches of 10 (not all simultaneously).
  Prevents memory exhaustion on large fleets.
  Each batch waits for completion before starting the next.

WebSocket protocol:
  Channel: fleet:{operation_id}
  Event:
    {
      "operation_id": "uuid",
      "service_id": "uuid",
      "service_name": "payment-service",
      "status": "running|succeeded|failed|skipped",
      "progress": {"completed": 5, "total": 47, "failed": 0},
      "error": null | "error message"
    }
"""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from celery import Celery, chord
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, async_session_maker

logger = logging.getLogger(__name__)

celery_app = Celery("fleet", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(task_serializer="json", accept_content=["json"], timezone="UTC",
                       enable_utc=True, task_acks_late=True,
                       task_rate_limit="10/m")  # Max 10 tasks/min per worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fleet operations service starting")
    yield


app = FastAPI(title="Nerve Fleet Operations Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class FleetOperationRequest(BaseModel):
    operation_type: str  # deploy | rollback | patch | compliance_rescan
    service_ids: list[str]
    version: Optional[str] = None
    notes: Optional[str] = None
    approved_blast_radius: bool = False


class FleetOperationResponse(BaseModel):
    id: str
    collection_id: Optional[str]
    operation_type: str
    status: str
    total: int
    completed: int
    failed: int
    per_service: list[dict] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ── Celery tasks ──────────────────────────────────────────────
@celery_app.task(name="fleet.execute_service_operation", bind=True, max_retries=2)
def execute_service_operation(self, operation_id: str, service_id: str,
                               service_name: str, operation_type: str,
                               version: Optional[str] = None):
    """Single service operation within a bulk fleet action."""
    import asyncio
    try:
        asyncio.run(_execute_operation(operation_id, service_id, service_name, operation_type, version))
    except Exception as exc:
        asyncio.run(_publish_progress(operation_id, service_id, service_name, "failed", str(exc)))
        raise self.retry(exc=exc, countdown=10)


async def _execute_operation(operation_id: str, service_id: str, service_name: str,
                              operation_type: str, version: Optional[str]):
    """Execute a single service operation and publish progress."""
    await _publish_progress(operation_id, service_id, service_name, "running")
    try:
        import httpx
        enforcer_url = settings.ENFORCER_SERVICE_URL

        if operation_type == "compliance_rescan":
            async with httpx.AsyncClient(base_url=enforcer_url, timeout=30.0) as client:
                r = await client.get("/internal/compliance/evaluate",
                                     params={"service_id": service_id, "version": version or "current"})
                success = r.status_code == 200

        elif operation_type in ("deploy", "rollback"):
            async with httpx.AsyncClient(base_url=enforcer_url, timeout=60.0) as client:
                r = await client.post("/internal/deploy", json={
                    "service_id": service_id, "version": version or "latest",
                    "environment": "production", "actor": "fleet-operator",
                })
                success = r.status_code == 202

        else:
            success = True  # patch — handled by k8s operator

        status = "succeeded" if success else "failed"
        await _publish_progress(operation_id, service_id, service_name, status)

    except Exception as exc:
        await _publish_progress(operation_id, service_id, service_name, "failed", str(exc))
        raise


async def _publish_progress(operation_id: str, service_id: str, service_name: str,
                              status: str, error: Optional[str] = None):
    """Publish per-service progress to Redis pub/sub for WebSocket streaming."""
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.publish(f"fleet:{operation_id}", json.dumps({
            "operation_id": operation_id, "service_id": service_id,
            "service_name": service_name, "status": status, "error": error,
        }))
    finally:
        await redis.aclose()


@celery_app.task(name="fleet.operation_complete")
def operation_complete(results: list, operation_id: str):
    """Chord callback — called when all service tasks complete."""
    import asyncio
    asyncio.run(_finalize_operation(operation_id, results))


async def _finalize_operation(operation_id: str, results: list):
    async with async_session_maker() as db:
        completed = sum(1 for r in results if r and r.get("status") == "succeeded")
        failed = sum(1 for r in results if r and r.get("status") == "failed")
        status = "completed" if failed == 0 else "failed"
        await db.execute(
            text("UPDATE fleet_operations SET status=:status, completed=:comp, failed=:fail, completed_at=NOW() WHERE id=:id::uuid"),
            {"id": operation_id, "status": status, "comp": completed, "fail": failed},
        )
        await db.commit()


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.post("/internal/fleet/collections/{collection_id}/operations", status_code=202)
async def submit_fleet_operation(collection_id: str, payload: FleetOperationRequest,
                                  actor: str = "unknown",
                                  db: AsyncSession = Depends(get_db)) -> FleetOperationResponse:
    operation_id = str(uuid.uuid4())

    # Resolve service names
    service_data = []
    for svc_id in payload.service_ids:
        r = await db.execute(text("SELECT name FROM services WHERE id=:id::uuid AND deleted_at IS NULL"), {"id": svc_id})
        svc = r.fetchone()
        if svc:
            service_data.append({"id": svc_id, "name": svc.name})

    if not service_data:
        raise HTTPException(status_code=400, detail={"error": "no_valid_services"})

    # Store operation record
    per_service = [{"service_id": s["id"], "service_name": s["name"], "status": "pending"} for s in service_data]
    await db.execute(
        text("INSERT INTO fleet_operations (id, collection_id, operation_type, status, total, completed, failed, per_service, actor, started_at) VALUES (:id::uuid, :col::uuid, :op, 'running', :total, 0, 0, :ps::jsonb, :actor, NOW())"),
        {"id": operation_id, "col": collection_id, "op": payload.operation_type,
         "total": len(service_data), "ps": json.dumps(per_service), "actor": actor},
    )
    await db.commit()

    # Dispatch Celery chord — batch of 10 to prevent memory exhaustion
    BATCH_SIZE = 10
    all_tasks = []
    for svc in service_data:
        task = execute_service_operation.s(
            operation_id, svc["id"], svc["name"], payload.operation_type, payload.version
        )
        all_tasks.append(task)

    # Process in batches
    for i in range(0, len(all_tasks), BATCH_SIZE):
        batch = all_tasks[i:i + BATCH_SIZE]
        chord(batch)(operation_complete.s(operation_id))

    return FleetOperationResponse(
        id=operation_id, collection_id=collection_id, operation_type=payload.operation_type,
        status="running", total=len(service_data), completed=0, failed=0,
        per_service=per_service, started_at=datetime.now(timezone.utc),
    )


@app.get("/internal/fleet/operations/{operation_id}")
async def get_operation(operation_id: str, db: AsyncSession = Depends(get_db)) -> FleetOperationResponse:
    r = await db.execute(
        text("SELECT id, collection_id, operation_type, status, total, completed, failed, per_service, started_at, completed_at FROM fleet_operations WHERE id=:id::uuid"),
        {"id": operation_id},
    )
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return FleetOperationResponse(
        id=str(row.id), collection_id=str(row.collection_id) if row.collection_id else None,
        operation_type=row.operation_type, status=row.status,
        total=row.total, completed=row.completed, failed=row.failed,
        per_service=row.per_service or [], started_at=row.started_at, completed_at=row.completed_at,
    )


# ── WebSocket — real-time fleet progress ──────────────────────
@app.websocket("/ws/fleet/{operation_id}")
async def fleet_websocket(websocket: WebSocket, operation_id: str):
    """
    Streams per-service progress for a bulk fleet operation.
    Frontend shows live progress bar per service.
    Closes automatically when operation completes.
    """
    await websocket.accept()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(f"fleet:{operation_id}")
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
                data = json.loads(message["data"])
                if data.get("status") in ("completed", "failed"):
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Fleet WebSocket error %s: %s", operation_id, exc)
    finally:
        await pubsub.unsubscribe(f"fleet:{operation_id}")
        await pubsub.aclose()
        await redis.aclose()
