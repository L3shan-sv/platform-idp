"""
Nerve IDP — Blast Radius Service (port 8004)

Computes the full dependency subgraph of any service via Neo4j traversal.

Performance:
  Neo4j 5-hop traversal: ~18ms on 2,000 nodes with proper indexes
  Redis cache (60s TTL):  <1ms on cache hit

Cache invalidation:
  Cache is invalidated when catalog.events fires service.updated or
  service.deleted. Without this, blast radius returns stale graph data
  after a service's dependencies change.

Dependency health risk score:
  Pre-deploy signal: how degraded are this service's direct upstreams?
  Risk 0-100: each degraded upstream +25, each frozen upstream +50, capped at 100.
  Used by the deploy screen to show "HIGH RISK: 2 upstreams degraded" before deploying.

ADR-002 explains Neo4j vs PostgreSQL decision and capacity benchmarks.
"""
import json, logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from neo4j import AsyncGraphDatabase, AsyncDriver
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)
_driver: Optional[AsyncDriver] = None


async def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
        )
    return _driver


@asynccontextmanager
async def lifespan(app: FastAPI):
    driver = await get_driver()
    async with driver.session() as session:
        await session.run("RETURN 1")
    logger.info("Neo4j connection verified: %s", settings.NEO4J_URI)
    yield
    if _driver:
        await _driver.close()


app = FastAPI(title="Nerve Blast Radius Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class GraphNode(BaseModel):
    id: str
    name: str
    team_id: str
    health_status: str
    hop_distance: int
    is_target: bool = False


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    relationship: str


class BlastRadiusResponse(BaseModel):
    target_service_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    risk_level: str
    affected_count: int
    cached: bool = False
    computed_at: datetime


class DependencyHealthResponse(BaseModel):
    service_id: str
    risk_score: int
    degraded_upstreams: list[dict]
    recommendation: str


# ── Cache helpers ─────────────────────────────────────────────
def _cache_key(service_id: str, hops: int) -> str:
    return f"blast_radius:{service_id}:{hops}"


async def _get_cached(service_id: str, hops: int) -> Optional[dict]:
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        raw = await redis.get(_cache_key(service_id, hops))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache get failed: %s", exc)
        return None
    finally:
        await redis.aclose()


async def _set_cached(service_id: str, hops: int, data: dict) -> None:
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.set(_cache_key(service_id, hops), json.dumps(data, default=str),
                        ex=settings.CACHE_TTL_BLAST_RADIUS)
    except Exception as exc:
        logger.warning("Cache set failed: %s", exc)
    finally:
        await redis.aclose()


async def invalidate_cache(service_id: str) -> None:
    """Called by catalog change event consumer."""
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        for hops in range(1, 6):
            await redis.delete(_cache_key(service_id, hops))
    finally:
        await redis.aclose()


# ── Core traversal ────────────────────────────────────────────
async def traverse(service_id: str, hops: int) -> dict:
    """
    Neo4j 5-hop traversal from the target service.

    IMPORTANT: This query uses the NodeIndexSeek on s.id (constraint index).
    Run EXPLAIN to verify — if you see AllNodesScan, the index is missing.
    Check phase-1/infra/docker/neo4j/init.cypher was executed.
    """
    driver = await get_driver()

    async with driver.session(database=settings.NEO4J_DATABASE) as session:
        # Fetch reachable nodes
        nodes_result = await session.run(
            """
            MATCH (start:Service {id: $id})
            OPTIONAL MATCH path = (start)-[:DEPENDS_ON|USES*1..$hops]->(dep:Service)
            RETURN DISTINCT
                dep.id AS id, dep.name AS name, dep.team_id AS team_id,
                dep.health_status AS health_status,
                CASE WHEN path IS NULL THEN 0 ELSE length(path) END AS hop_distance
            """,
            id=service_id, hops=hops,
        )
        nodes = []
        async for r in nodes_result:
            if r["id"]:
                nodes.append({"id": r["id"], "name": r["name"] or "",
                               "team_id": r["team_id"] or "", "health_status": r["health_status"] or "unknown",
                               "hop_distance": r["hop_distance"], "is_target": False})

        # Fetch target node itself
        target_r = await session.run(
            "MATCH (s:Service {id: $id}) RETURN s.id AS id, s.name AS name, s.team_id AS team_id, s.health_status AS health_status",
            id=service_id,
        )
        target = await target_r.single()
        if not target:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Service not found in Neo4j graph."})

        target_node = {"id": target["id"], "name": target["name"] or "", "team_id": target["team_id"] or "",
                       "health_status": target["health_status"] or "unknown", "hop_distance": 0, "is_target": True}

        # Fetch edges within the subgraph
        all_ids = [n["id"] for n in nodes] + [service_id]
        edges_r = await session.run(
            "MATCH (a:Service)-[r:DEPENDS_ON|USES]->(b:Service) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id AS src, b.id AS tgt, type(r) AS rel",
            ids=all_ids,
        )
        edges = []
        async for r in edges_r:
            edges.append({"source_id": r["src"], "target_id": r["tgt"], "relationship": r["rel"]})

    return {"target_node": target_node, "nodes": nodes, "edges": edges}


def _risk_level(nodes: list[dict]) -> str:
    statuses = {n["health_status"] for n in nodes}
    if "frozen" in statuses:    return "critical"
    if "degraded" in statuses:  return "high"
    if "unknown" in statuses:   return "medium"
    return "low"


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/internal/blast-radius/{service_id}")
async def get_blast_radius(service_id: str, hops: int = 5) -> BlastRadiusResponse:
    if not 1 <= hops <= 5:
        raise HTTPException(status_code=400, detail="hops must be 1–5")

    cached = await _get_cached(service_id, hops)
    if cached:
        cached["cached"] = True
        return BlastRadiusResponse(**cached)

    result = await traverse(service_id, hops)
    all_nodes = [result["target_node"]] + result["nodes"]

    response_data = {
        "target_service_id": service_id,
        "nodes": all_nodes,
        "edges": result["edges"],
        "risk_level": _risk_level(result["nodes"]),
        "affected_count": len(result["nodes"]),
        "cached": False,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    await _set_cached(service_id, hops, response_data)
    return BlastRadiusResponse(**response_data)


@app.get("/internal/dependency-health/{service_id}")
async def get_dependency_health(service_id: str) -> DependencyHealthResponse:
    """Pre-deploy risk score based on health of direct upstream dependencies (1 hop only)."""
    driver = await get_driver()
    async with driver.session(database=settings.NEO4J_DATABASE) as session:
        r = await session.run(
            "MATCH (s:Service {id: $id})-[:DEPENDS_ON|USES]->(dep:Service) RETURN dep.id AS id, dep.name AS name, dep.health_status AS health",
            id=service_id,
        )
        degraded = []
        risk_score = 0
        async for record in r:
            h = record["health"] or "unknown"
            if h in ("degraded", "frozen", "unknown"):
                degraded.append({"service_id": record["id"], "service_name": record["name"], "health_status": h})
                risk_score += 50 if h == "frozen" else 25 if h == "degraded" else 10

    risk_score = min(risk_score, 100)
    if risk_score == 0:
        rec = "All upstream dependencies are healthy. Safe to deploy."
    elif risk_score < 50:
        rec = f"{len(degraded)} upstream dependenc{'y' if len(degraded)==1 else 'ies'} currently degraded. Consider monitoring closely after deploy."
    else:
        rec = f"HIGH RISK: {len(degraded)} upstream dependencies are degraded or frozen. Strongly recommend delaying this deploy."

    return DependencyHealthResponse(service_id=service_id, risk_score=risk_score,
                                    degraded_upstreams=degraded, recommendation=rec)


@app.post("/internal/cache/invalidate/{service_id}", status_code=204)
async def invalidate_service_cache(service_id: str):
    """Called by catalog change event consumer when service topology changes."""
    await invalidate_cache(service_id)
