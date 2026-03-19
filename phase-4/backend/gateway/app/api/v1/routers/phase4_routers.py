"""
Phase 4 — Gateway additions

1. GraphQL endpoint (Strawberry) — /graphql
   Service { doraMetrics, errorBudget, securityPosture, maturityScore }
   Read-only. All mutations go through REST endpoints.
   Solves the n+1 problem via DataLoader.

2. Topology WebSocket — /ws/topology
   Streams TopologyEvent: service health changes, traffic volume, edge changes
   Events sourced from OpenTelemetry trace data via Celery worker
   Frontend renders as force-directed graph

3. New gateway routers for Phase 4 services
"""
import asyncio
import json
import logging
from typing import Optional, List

import httpx
import redis.asyncio as aioredis
import strawberry
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from strawberry.fastapi import GraphQLRouter

from app.core.auth import get_current_user, require_role, CurrentUser
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── GraphQL schema ────────────────────────────────────────────
@strawberry.type
class DoraMetricsGql:
    deployment_frequency: float
    deployment_frequency_tier: str
    lead_time_hours: float
    lead_time_tier: str
    mttr_hours: float
    mttr_tier: str
    change_failure_rate: float
    cfr_tier: str


@strawberry.type
class ErrorBudgetGql:
    slo_target: float
    budget_consumed: float
    budget_remaining: float
    frozen: bool


@strawberry.type
class SecurityPostureGql:
    score: int
    critical_cves: int
    high_cves: int
    sbom_present: bool
    sast_passed: Optional[bool]


@strawberry.type
class MaturityScoreGql:
    overall_score: int
    observability: int
    reliability: int
    security: int
    docs: int
    cost: int
    error_budget_health: int
    template_behind_by: int


@strawberry.type
class ServiceGql:
    id: str
    name: str
    team: str
    language: str
    health_status: str
    compliance_score: int
    maturity_score: int
    deploy_frozen: bool

    @strawberry.field
    async def dora_metrics(self) -> Optional[DoraMetricsGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.PIPELINE_SERVICE_URL, timeout=5.0) as client:
                r = await client.get(f"/api/v1/services/{self.id}/dora")
                if r.status_code == 200:
                    d = r.json()
                    return DoraMetricsGql(
                        deployment_frequency=d.get("deployment_frequency", 0),
                        deployment_frequency_tier=d.get("deployment_frequency_tier", "low"),
                        lead_time_hours=d.get("lead_time_hours", 0),
                        lead_time_tier=d.get("lead_time_tier", "low"),
                        mttr_hours=d.get("mttr_hours", 0),
                        mttr_tier=d.get("mttr_tier", "low"),
                        change_failure_rate=d.get("change_failure_rate", 0),
                        cfr_tier=d.get("cfr_tier", "low"),
                    )
        except Exception as exc:
            logger.warning("GraphQL dora_metrics fetch failed: %s", exc)
        return None

    @strawberry.field
    async def error_budget(self) -> Optional[ErrorBudgetGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.ERROR_BUDGET_SERVICE_URL, timeout=5.0) as client:
                r = await client.get(f"/internal/error-budget/{self.id}")
                if r.status_code == 200:
                    d = r.json()
                    return ErrorBudgetGql(
                        slo_target=d["slo_target"], budget_consumed=d["budget_consumed"],
                        budget_remaining=d["budget_remaining"], frozen=d["frozen"],
                    )
        except Exception as exc:
            logger.warning("GraphQL error_budget fetch failed: %s", exc)
        return None

    @strawberry.field
    async def security_posture(self) -> Optional[SecurityPostureGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.SECURITY_SERVICE_URL, timeout=5.0) as client:
                r = await client.get(f"/internal/security/{self.id}")
                if r.status_code == 200:
                    d = r.json()
                    return SecurityPostureGql(
                        score=d["score"], critical_cves=d["critical_cves"],
                        high_cves=d["high_cves"], sbom_present=d["sbom_present"],
                        sast_passed=d.get("sast_passed"),
                    )
        except Exception as exc:
            logger.warning("GraphQL security_posture fetch failed: %s", exc)
        return None

    @strawberry.field
    async def maturity_score(self) -> Optional[MaturityScoreGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.MATURITY_SERVICE_URL, timeout=5.0) as client:
                r = await client.get(f"/internal/maturity/{self.id}")
                if r.status_code == 200:
                    d = r.json()
                    pillars = d.get("pillars", {})
                    return MaturityScoreGql(
                        overall_score=d["overall_score"],
                        observability=pillars.get("observability", {}).get("score", 0),
                        reliability=pillars.get("reliability", {}).get("score", 0),
                        security=pillars.get("security", {}).get("score", 0),
                        docs=pillars.get("docs", {}).get("score", 0),
                        cost=pillars.get("cost", {}).get("score", 0),
                        error_budget_health=pillars.get("error_budget", {}).get("score", 0),
                        template_behind_by=d.get("template_behind_by", 0),
                    )
        except Exception as exc:
            logger.warning("GraphQL maturity_score fetch failed: %s", exc)
        return None


@strawberry.type
class Query:
    @strawberry.field
    async def service(self, id: str) -> Optional[ServiceGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=5.0) as client:
                r = await client.get(f"/api/v1/services/{id}")
                if r.status_code == 200:
                    d = r.json()
                    return ServiceGql(
                        id=d["id"], name=d["name"], team=d["team"], language=d["language"],
                        health_status=d["health_status"], compliance_score=d["compliance_score"],
                        maturity_score=d["maturity_score"], deploy_frozen=d["deploy_frozen"],
                    )
        except Exception as exc:
            logger.warning("GraphQL service fetch failed: %s", exc)
        return None

    @strawberry.field
    async def services(self, limit: int = 20) -> List[ServiceGql]:
        try:
            async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=5.0) as client:
                r = await client.get("/api/v1/services", params={"limit": limit})
                if r.status_code == 200:
                    return [
                        ServiceGql(
                            id=d["id"], name=d["name"], team=d["team"], language=d["language"],
                            health_status=d["health_status"], compliance_score=d["compliance_score"],
                            maturity_score=d["maturity_score"], deploy_frozen=d["deploy_frozen"],
                        )
                        for d in r.json().get("items", [])
                    ]
        except Exception as exc:
            logger.warning("GraphQL services fetch failed: %s", exc)
        return []


schema = strawberry.Schema(query=Query)
graphql_router = GraphQLRouter(schema)


# ── Phase 4 gateway routers ───────────────────────────────────
ai_router = APIRouter()
docs_router = APIRouter()
chaos_router = APIRouter()
fleet_router = APIRouter()
observability_router = APIRouter()


@ai_router.post("/ai/chat")
async def ai_chat(payload: dict, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.AI_COPILOT_SERVICE_URL, timeout=30.0) as client:
        r = await client.post("/internal/ai/chat", json=payload)
        if r.status_code != 200:
            raise __import__("fastapi").HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()


@ai_router.get("/ai/incidents/{incident_id}/timeline")
async def incident_timeline(incident_id: str, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.AI_COPILOT_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/internal/ai/incidents/{incident_id}/timeline")
        return r.json()


@docs_router.get("/docs/{service_id}")
async def get_docs(service_id: str, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.DOCS_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/internal/docs/{service_id}")
        return r.json()


@docs_router.get("/docs/search")
async def search_docs(q: str, mode: str = "hybrid", current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.DOCS_SERVICE_URL, timeout=10.0) as client:
        r = await client.get("/internal/docs/search", params={"q": q, "mode": mode})
        return r.json()


@docs_router.post("/docs/webhooks/rebuild")
async def docs_rebuild_webhook(payload: dict):
    async with httpx.AsyncClient(base_url=settings.DOCS_SERVICE_URL, timeout=10.0) as client:
        r = await client.post("/internal/docs/rebuild", json=payload)
        return r.json()


@chaos_router.post("/chaos/experiments")
async def create_chaos(payload: dict, current_user: CurrentUser = Depends(require_role("platform_engineer"))):
    payload["actor"] = current_user.username
    async with httpx.AsyncClient(base_url=settings.CHAOS_SERVICE_URL, timeout=10.0) as client:
        r = await client.post("/internal/chaos/experiments", json=payload,
                               params={"actor": current_user.username})
        return r.json()


@chaos_router.get("/chaos/experiments/{experiment_id}")
async def get_chaos(experiment_id: str, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CHAOS_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/internal/chaos/experiments/{experiment_id}")
        return r.json()


@fleet_router.post("/fleet/collections/{collection_id}/operations")
async def fleet_operation(collection_id: str, payload: dict,
                           current_user: CurrentUser = Depends(require_role("platform_engineer"))):
    async with httpx.AsyncClient(base_url=settings.FLEET_SERVICE_URL, timeout=10.0) as client:
        r = await client.post(f"/internal/fleet/collections/{collection_id}/operations",
                               json=payload, params={"actor": current_user.username})
        return r.json()


@observability_router.get("/metrics/dora")
async def get_dora(team: Optional[str] = None, window: str = "30d",
                   current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.PIPELINE_SERVICE_URL, timeout=10.0) as client:
        r = await client.get("/api/v1/metrics/dora", params={"team": team, "window": window})
        return r.json()


@observability_router.get("/services/{service_id}/error-budget")
async def get_error_budget(service_id: str, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.ERROR_BUDGET_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/internal/error-budget/{service_id}")
        return r.json()


@observability_router.get("/services/{service_id}/maturity")
async def get_maturity(service_id: str, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.MATURITY_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/internal/maturity/{service_id}")
        return r.json()


# ── Topology WebSocket ────────────────────────────────────────
async def topology_websocket_handler(websocket: WebSocket):
    """
    Streams live topology events to the force-directed graph.
    Events published by OTel trace processor Celery worker.
    Channel: topology:global
    Event shape:
      {
        "event_type": "health_change | traffic_update | edge_change",
        "service_id": "uuid",
        "service_name": "payment-service",
        "health_status": "healthy | degraded | frozen",
        "traffic_volume": 1234,    // requests/min
        "timestamp": "ISO8601"
      }
    """
    await websocket.accept()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe("topology:global")
        logger.info("WebSocket connected: topology")
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: topology")
    except Exception as exc:
        logger.error("Topology WebSocket error: %s", exc)
    finally:
        await pubsub.unsubscribe("topology:global")
        await pubsub.aclose()
        await redis.aclose()
