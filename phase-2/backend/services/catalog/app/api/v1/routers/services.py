"""Catalog service — /services router."""
import uuid, logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.events import publish_catalog_event
from app.core.neo4j import sync_service_to_neo4j, delete_service_from_neo4j
from app.models.models import Service, Team, ServiceDependency
from app.schemas.service import ServiceResponse, ServiceListResponse, ServiceRegistration, ServiceUpdate, CatalogSummary

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/services", response_model=ServiceListResponse)
async def list_services(
    team: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    health: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    query = (select(Service).options(selectinload(Service.team))
             .where(Service.deleted_at.is_(None)))
    if team:
        query = query.join(Team).where(Team.slug == team)
    if language:
        query = query.where(Service.language == language)
    if health:
        query = query.where(Service.health_status == health)
    if q:
        query = query.where(Service.name.ilike(f"%{q}%"))

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await db.execute(query.order_by(Service.name).offset((page - 1) * limit).limit(limit))
    services = result.scalars().all()

    summary_r = await db.execute(
        select(func.count(Service.id), func.avg(Service.maturity_score))
        .where(Service.deleted_at.is_(None))
    )
    sr = summary_r.first()
    summary = CatalogSummary(total_services=sr[0] or 0, healthy=0, degraded=0, frozen=0,
                             avg_maturity_score=float(sr[1] or 0), critical_cves=0)

    return ServiceListResponse(items=[ServiceResponse.model_validate(s) for s in services],
                               total=total, page=page, limit=limit, summary=summary)

@router.post("/services", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
async def register_service(payload: ServiceRegistration, db: AsyncSession = Depends(get_db)):
    existing = await db.scalar(select(Service).where(Service.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail={"error":"conflict","message":f"Service '{payload.name}' already exists."})

    team = await db.scalar(select(Team).where(Team.slug == payload.team))
    if not team:
        raise HTTPException(status_code=400, detail={"error":"invalid_team","message":f"Team '{payload.team}' not found."})

    service = Service(name=payload.name, team_id=team.id, language=payload.language,
                      repo_url=str(payload.repo_url) if payload.repo_url else None,
                      description=payload.description)
    db.add(service)
    await db.flush()

    for dep_id in payload.upstream_dependencies:
        db.add(ServiceDependency(source_id=service.id, target_id=dep_id, relationship="DEPENDS_ON"))

    await db.commit()
    await db.refresh(service)
    await publish_catalog_event("service.created", {"service_id": str(service.id), "name": service.name})
    await sync_service_to_neo4j(service)
    logger.info("Service registered: %s", service.name)
    return ServiceResponse.model_validate(service)

@router.get("/services/{service_id}", response_model=ServiceResponse)
async def get_service(service_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    service = await db.scalar(
        select(Service).options(selectinload(Service.team))
        .where(Service.id == service_id, Service.deleted_at.is_(None))
    )
    if not service:
        raise HTTPException(status_code=404, detail={"error":"not_found"})
    return ServiceResponse.model_validate(service)

@router.patch("/services/{service_id}", response_model=ServiceResponse)
async def update_service(service_id: uuid.UUID, payload: ServiceUpdate, db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if not service or service.deleted_at:
        raise HTTPException(status_code=404, detail={"error":"not_found"})
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(service, field, value)
    await db.commit()
    await db.refresh(service)
    await publish_catalog_event("service.updated", {"service_id": str(service.id)})
    await sync_service_to_neo4j(service)
    return ServiceResponse.model_validate(service)

@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(service_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if not service or service.deleted_at:
        raise HTTPException(status_code=404, detail={"error":"not_found"})
    from datetime import datetime, timezone
    service.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    await publish_catalog_event("service.deleted", {"service_id": str(service_id), "name": service.name})
    await delete_service_from_neo4j(str(service_id))
