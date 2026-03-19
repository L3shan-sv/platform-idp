"""
Nerve IDP — Error Budget Service (port 8005)

Implements the Google SRE multi-window burn rate model.

Data flow:
  Prometheus recording rules compute burn rates (phase-1/infra/docker/prometheus/rules/)
  → This service queries Prometheus HTTP API
  → Returns structured ErrorBudget with live burn alerts
  → PostgreSQL caches budget_consumed for services without Prometheus coverage

Freeze webhook:
  Called by Alertmanager when NerveBurnRateCritical or NerveBudgetExhausted fires.
  Idempotency: UPDATE ... WHERE deploy_frozen = FALSE RETURNING id
  Only the first of potentially simultaneous multi-window alerts publishes the event.
  The idempotency_key in the payload is for logging only — the SQL is the mechanism.

Unfreeze:
  SRE manual override. Requires 'sre' role (enforced at gateway).
  Writes reason to audit log.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, async_session_maker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Error budget service starting")
    yield


app = FastAPI(title="Nerve Error Budget Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class BurnAlert(BaseModel):
    burn_rate: float
    window: str
    severity: str
    firing: bool
    time_to_exhaustion_hours: Optional[float] = None


class ErrorBudgetResponse(BaseModel):
    service_id: str
    slo_target: float
    budget_consumed: float
    budget_remaining: float
    frozen: bool
    frozen_at: Optional[datetime] = None
    burn_alerts: list[BurnAlert]
    computed_at: datetime


class FreezeRequest(BaseModel):
    service_id: str
    reason: str
    burn_rate: float
    idempotency_key: str


class FreezeResponse(BaseModel):
    service_id: str
    frozen: bool
    already_frozen: bool
    frozen_at: Optional[datetime] = None


# ── Prometheus helpers ────────────────────────────────────────
async def query_prometheus(promql: str) -> Optional[float]:
    """Execute instant PromQL query. Returns None if metric not found."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.PROMETHEUS_URL}/api/v1/query", params={"query": promql})
            r.raise_for_status()
            results = r.json().get("data", {}).get("result", [])
            if results:
                return float(results[0]["value"][1])
    except Exception as exc:
        logger.warning("Prometheus query failed (%s): %s", promql, exc)
    return None


async def get_burn_rate(service_name: str, window: str) -> Optional[float]:
    return await query_prometheus(f'nerve:burn_rate:{window}{{service="{service_name}"}}')


async def get_budget_consumed(service_name: str) -> Optional[float]:
    result = await query_prometheus(
        f'(1 - (1 - nerve:service_error_rate:1d{{service="{service_name}"}}) / (1 - nerve_slo_target{{service="{service_name}"}})) * 100'
    )
    if result is not None:
        return min(max(result, 0.0), 100.0)
    return None


def time_to_exhaustion(burn_rate: float, budget_remaining: float) -> Optional[float]:
    """Hours until budget exhausted at current burn rate. None if burn_rate <= 0."""
    if burn_rate <= 0 or budget_remaining <= 0:
        return None
    return (budget_remaining / 100.0) * 720 / burn_rate  # 720h = 30 days


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/internal/error-budget/{service_id}")
async def get_error_budget(service_id: str, db: AsyncSession = Depends(get_db)) -> ErrorBudgetResponse:
    from sqlalchemy import text as sql_text
    row = await db.execute(
        sql_text("SELECT s.name, s.deploy_frozen, s.frozen_at, s.error_budget_consumed, sl.target FROM services s LEFT JOIN slo_definitions sl ON sl.service_id = s.id WHERE s.id = :id::uuid AND s.deleted_at IS NULL"),
        {"id": service_id},
    )
    svc = row.fetchone()
    if not svc:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    slo_target = float(svc.target) if svc.target else 99.9

    # Try live Prometheus data, fall back to cached DB value
    budget_consumed = await get_budget_consumed(svc.name)
    if budget_consumed is None:
        budget_consumed = float(svc.error_budget_consumed or 0)
    budget_remaining = max(100.0 - budget_consumed, 0.0)

    # Collect burn rate alerts across all windows
    burn_alerts = []
    for window, threshold, severity in [("1h", 14.0, "page"), ("6h", 14.0, "page"),
                                         ("1d", 3.0, "ticket"), ("3d", 1.0, "warning")]:
        rate = await get_burn_rate(svc.name, window)
        if rate is None:
            continue
        firing = rate > threshold
        burn_alerts.append(BurnAlert(
            burn_rate=rate, window=window, severity=severity, firing=firing,
            time_to_exhaustion_hours=time_to_exhaustion(rate, budget_remaining) if firing else None,
        ))

    return ErrorBudgetResponse(
        service_id=service_id, slo_target=slo_target,
        budget_consumed=budget_consumed, budget_remaining=budget_remaining,
        frozen=svc.deploy_frozen, frozen_at=svc.frozen_at,
        burn_alerts=burn_alerts, computed_at=datetime.now(timezone.utc),
    )


@app.post("/internal/error-budget/freeze")
async def freeze_service(payload: FreezeRequest, db: AsyncSession = Depends(get_db)) -> FreezeResponse:
    """
    Idempotent freeze. Called by Alertmanager.
    UPDATE ... WHERE deploy_frozen = FALSE RETURNING id ensures only one
    call publishes the event even when multiple windows fire simultaneously.
    """
    result = await db.execute(
        text("UPDATE services SET deploy_frozen=TRUE, frozen_at=NOW(), frozen_reason=:reason, updated_at=NOW() WHERE id=:id::uuid AND deploy_frozen=FALSE AND deleted_at IS NULL RETURNING id"),
        {"id": payload.service_id, "reason": payload.reason},
    )
    updated = result.scalar()
    await db.commit()

    if updated:
        # Publish to catalog.events — triggers maturity rescore
        import json, time
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await redis.xadd("catalog.events", {
                "type": "service.deploy_frozen",
                "payload": json.dumps({"service_id": payload.service_id, "reason": payload.reason,
                                       "burn_rate": payload.burn_rate, "idempotency_key": payload.idempotency_key}),
                "timestamp": str(int(time.time() * 1000)), "version": "1",
            }, maxlen=10_000, approximate=True)
        finally:
            await redis.aclose()
        logger.warning("Deploy frozen: %s burn_rate=%.2fx", payload.service_id, payload.burn_rate)
        return FreezeResponse(service_id=payload.service_id, frozen=True, already_frozen=False,
                              frozen_at=datetime.now(timezone.utc))

    return FreezeResponse(service_id=payload.service_id, frozen=True, already_frozen=True)


@app.post("/internal/error-budget/{service_id}/unfreeze")
async def unfreeze_service(service_id: str, reason: str, db: AsyncSession = Depends(get_db)) -> FreezeResponse:
    """SRE manual unfreeze. Role check enforced at gateway."""
    result = await db.execute(
        text("UPDATE services SET deploy_frozen=FALSE, frozen_at=NULL, frozen_reason=NULL, updated_at=NOW() WHERE id=:id::uuid AND deleted_at IS NULL RETURNING id"),
        {"id": service_id},
    )
    if not result.scalar():
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    await db.commit()
    logger.info("Deploy unfrozen: %s reason=%s", service_id, reason)
    return FreezeResponse(service_id=service_id, frozen=False, already_frozen=False)


@app.post("/internal/error-budget/sync")
async def sync_budgets_from_prometheus(db: AsyncSession = Depends(get_db)):
    """
    Celery beat task — updates error_budgets table from Prometheus every 5 min.
    Services without Prometheus coverage fall back to cached DB values.
    """
    from sqlalchemy import text as sql_text
    rows = await db.execute(sql_text("SELECT id, name FROM services WHERE deleted_at IS NULL"))
    services = rows.fetchall()
    updated = 0
    for svc_id, svc_name in services:
        consumed = await get_budget_consumed(svc_name)
        if consumed is None:
            continue
        await db.execute(
            sql_text("INSERT INTO error_budgets (service_id, budget_consumed, budget_remaining) VALUES (:id, :consumed, :remaining) ON CONFLICT (service_id) DO UPDATE SET budget_consumed=:consumed, budget_remaining=:remaining, computed_at=NOW()"),
            {"id": str(svc_id), "consumed": consumed, "remaining": max(100.0 - consumed, 0.0)},
        )
        updated += 1
    await db.commit()
    return {"updated": updated, "total": len(services)}
