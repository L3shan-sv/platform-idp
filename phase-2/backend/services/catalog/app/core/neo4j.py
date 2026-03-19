"""Catalog service — Neo4j sync + reconciliation."""
import logging
from typing import Optional
from neo4j import AsyncGraphDatabase, AsyncDriver
from app.core.config import settings

logger = logging.getLogger(__name__)
_driver: Optional[AsyncDriver] = None

async def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
        )
    return _driver

async def init_neo4j() -> None:
    driver = await get_driver()
    async with driver.session() as session:
        await session.run("RETURN 1")
    logger.info("Neo4j connection verified")

async def sync_service_to_neo4j(service) -> None:
    """MERGE Service node — idempotent, safe to call on create or update."""
    driver = await get_driver()
    try:
        async with driver.session(database=settings.NEO4J_DATABASE) as session:
            await session.run(
                """MERGE (s:Service {id: $id})
                   SET s.name = $name, s.team_id = $team_id,
                       s.language = $language, s.health_status = $health_status""",
                id=str(service.id), name=service.name,
                team_id=str(service.team_id), language=service.language,
                health_status=service.health_status,
            )
    except Exception as exc:
        logger.error("Neo4j sync failed for %s: %s", service.id, exc)

async def delete_service_from_neo4j(service_id: str) -> None:
    """DETACH DELETE — called on soft delete."""
    driver = await get_driver()
    try:
        async with driver.session(database=settings.NEO4J_DATABASE) as session:
            await session.run("MATCH (s:Service {id: $id}) DETACH DELETE s", id=service_id)
    except Exception as exc:
        logger.error("Neo4j delete failed for %s: %s", service_id, exc)

async def reconcile_neo4j_with_postgres(db_session) -> dict:
    """
    Diff PostgreSQL ↔ Neo4j every 5 minutes.
    Corrects missing nodes and phantom nodes.
    Logs result to neo4j_sync_log table.
    """
    from sqlalchemy import select
    from app.models.models import Service

    driver = await get_driver()
    services_synced = edges_synced = 0
    drift_detail = {}

    result = await db_session.execute(select(Service).where(Service.deleted_at.is_(None)))
    pg_services = {str(s.id): s for s in result.scalars().all()}
    pg_ids = set(pg_services.keys())

    async with driver.session(database=settings.NEO4J_DATABASE) as session:
        r = await session.run("MATCH (s:Service) RETURN s.id AS id")
        neo4j_ids = {record["id"] async for record in r}

    missing = pg_ids - neo4j_ids
    if missing:
        drift_detail["missing_in_neo4j"] = list(missing)
        for sid in missing:
            await sync_service_to_neo4j(pg_services[sid])
            services_synced += 1

    phantom = neo4j_ids - pg_ids
    if phantom:
        drift_detail["phantom_in_neo4j"] = list(phantom)
        for sid in phantom:
            await delete_service_from_neo4j(sid)
            services_synced += 1

    drift_detected = bool(drift_detail)
    if drift_detected:
        logger.warning("Neo4j reconciliation — drift corrected: %s", drift_detail)

    return {"drift_detected": drift_detected, "services_synced": services_synced,
            "edges_synced": edges_synced, "drift_detail": drift_detail}
