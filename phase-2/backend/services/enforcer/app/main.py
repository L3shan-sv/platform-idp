"""
Nerve IDP — Golden Path Enforcer (port 8002)

OPA startup gate: refuses to start until OPA /health returns 200.
This prevents fail-open (deploys without policy evaluation) on restarts.

Freeze idempotency: UPDATE ... WHERE deploy_frozen = FALSE RETURNING id
Only the first concurrent call publishes the freeze event.
"""
import asyncio, logging, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db, async_session_maker
from app.core.opa import evaluate_compliance, OpaEvaluationResult

logger = logging.getLogger(__name__)

async def wait_for_opa(max_retries: int = 30, delay: float = 1.0) -> None:
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            try:
                r = await client.get(f"{settings.OPA_URL}/health", timeout=2.0)
                if r.status_code == 200:
                    logger.info("OPA sidecar ready")
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            logger.warning("OPA not ready (%d/%d)", attempt + 1, max_retries)
            await asyncio.sleep(delay)
    raise RuntimeError("OPA not ready — refusing to start (prevents fail-open)")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT != "test":
        await wait_for_opa()
    yield

app = FastAPI(title="Nerve Enforcer", version=settings.APP_VERSION, lifespan=lifespan)

# ── Schemas ─────────────────────────────────────────────────
class DeployRequest(BaseModel):
    service_id: uuid.UUID
    version: str
    environment: str
    actor: str
    notes: Optional[str] = None

class ComplianceCheck(BaseModel):
    name: str; status: str; score: int; weight: int; detail: str; fix_url: Optional[str] = None

class DeployResponse(BaseModel):
    deploy_id: uuid.UUID; status: str; compliance_score: int; compliance_annotation: str

class DeployFrozenResponse(BaseModel):
    frozen: bool; reason: str; frozen_at: Optional[datetime]; budget_consumed: float; unfreeze_requires_role: str = "sre"

class FreezeRequest(BaseModel):
    service_id: uuid.UUID; reason: str; burn_rate: float; idempotency_key: str

# ── Endpoints ────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}

@app.post("/internal/deploy", status_code=202)
async def submit_deploy(payload: DeployRequest, db: AsyncSession = Depends(get_db)):
    from app.models.models import Service, DeployHistory
    service = await db.scalar(select(Service).where(Service.id == payload.service_id, Service.deleted_at.is_(None)))
    if not service:
        raise HTTPException(status_code=404, detail={"error":"not_found"})

    # Check freeze
    if service.deploy_frozen:
        return DeployFrozenResponse(frozen=True, reason=service.frozen_reason or "Error budget exhausted",
                                    frozen_at=service.frozen_at, budget_consumed=float(service.error_budget_consumed))

    # OPA evaluation
    opa_result = await evaluate_compliance(str(payload.service_id), service.name, payload.version, payload.environment)

    deploy_record = DeployHistory(
        service_id=payload.service_id, version=payload.version, environment=payload.environment,
        status="blocked" if not opa_result.passed else "queued",
        compliance_score=opa_result.score, actor=payload.actor, notes=payload.notes,
    )
    db.add(deploy_record)
    await db.execute(update(Service).where(Service.id == payload.service_id).values(compliance_score=opa_result.score))
    await db.commit()

    if not opa_result.passed:
        raise HTTPException(status_code=403, detail={
            "score": opa_result.score, "passed": False,
            "message": f"Compliance score {opa_result.score}/100 is below the required 80.",
            "checks": [c.model_dump() for c in [ComplianceCheck(**ch) for ch in opa_result.checks]],
        })

    annotation = f"nerve.io/compliance-score={opa_result.score},nerve.io/compliance-passed=true,nerve.io/enforced-at={datetime.now(timezone.utc).isoformat()}"
    return DeployResponse(deploy_id=deploy_record.id, status="queued", compliance_score=opa_result.score, compliance_annotation=annotation)

@app.post("/internal/compliance/evaluate")
async def evaluate_only(service_id: uuid.UUID, version: str, environment: str = "production", db: AsyncSession = Depends(get_db)):
    from app.models.models import Service
    service = await db.scalar(select(Service).where(Service.id == service_id, Service.deleted_at.is_(None)))
    if not service:
        raise HTTPException(status_code=404, detail={"error":"not_found"})
    result = await evaluate_compliance(str(service_id), service.name, version, environment)
    return {"service_id": str(service_id), "version": version, "score": result.score, "passed": result.passed, "checks": result.checks}

@app.post("/internal/freeze/{service_id}")
async def freeze_service(service_id: uuid.UUID, payload: FreezeRequest, db: AsyncSession = Depends(get_db)):
    """
    Idempotent freeze — UPDATE ... WHERE deploy_frozen = FALSE RETURNING id.
    Only first call publishes the event. Subsequent calls return already_frozen=True.
    """
    result = await db.execute(
        text("UPDATE services SET deploy_frozen=TRUE, frozen_at=NOW(), frozen_reason=:reason, updated_at=NOW() WHERE id=:id::uuid AND deploy_frozen=FALSE AND deleted_at IS NULL RETURNING id"),
        {"service_id": str(service_id), "reason": payload.reason},
    )
    updated = result.scalar()
    await db.commit()

    if updated:
        from app.core.events import publish_catalog_event
        await publish_catalog_event("service.deploy_frozen", {"service_id": str(service_id), "reason": payload.reason, "burn_rate": payload.burn_rate, "idempotency_key": payload.idempotency_key})
        logger.warning("Deploy frozen: %s burn_rate=%.2fx", service_id, payload.burn_rate)
        return {"frozen": True, "already_frozen": False, "frozen_at": datetime.now(timezone.utc)}
    return {"frozen": True, "already_frozen": True}

# Local events stub (enforcer publishes to same stream as catalog)
async def publish_catalog_event(event_type: str, payload: dict):
    import json, time
    import redis.asyncio as aioredis
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.xadd("catalog.events", {"type": event_type, "payload": json.dumps(payload), "timestamp": str(int(time.time()*1000)), "version": "1"}, maxlen=10_000, approximate=True)
    except Exception as exc:
        logger.error("Failed to publish event: %s", exc)
    finally:
        await redis.aclose()
