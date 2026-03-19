"""Gateway — catalog router (proxies to catalog service)."""
import uuid, logging
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.core.config import settings
from app.core.auth import get_current_user, CurrentUser

router = APIRouter()
logger = logging.getLogger(__name__)

async def catalog_client():
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        yield client

@router.get("/services")
async def list_services(request: Request, team: Optional[str]=None, language: Optional[str]=None,
                        health: Optional[str]=None, q: Optional[str]=None, page: int=1, limit: int=20,
                        current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        r = await client.get("/api/v1/services", params={k:v for k,v in {"team":team,"language":language,"health":health,"q":q,"page":page,"limit":limit}.items() if v is not None})
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()

@router.post("/services", status_code=201)
async def register_service(payload: dict, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        r = await client.post("/api/v1/services", json=payload)
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()

@router.get("/services/{service_id}")
async def get_service(service_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/api/v1/services/{service_id}")
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()

@router.patch("/services/{service_id}")
async def update_service(service_id: uuid.UUID, payload: dict, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        r = await client.patch(f"/api/v1/services/{service_id}", json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()

@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.CATALOG_SERVICE_URL, timeout=10.0) as client:
        r = await client.delete(f"/api/v1/services/{service_id}")
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=r.status_code, detail=r.json())
