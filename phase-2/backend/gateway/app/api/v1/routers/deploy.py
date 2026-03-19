"""Gateway — deploy router (proxies to enforcer service)."""
import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.core.config import settings
from app.core.auth import get_current_user, CurrentUser

router = APIRouter()

@router.post("/services/{service_id}/deploy")
async def submit_deploy(service_id: uuid.UUID, payload: dict,
                        current_user: CurrentUser = Depends(get_current_user)):
    payload["service_id"] = str(service_id)
    payload["actor"] = current_user.username
    async with httpx.AsyncClient(base_url=settings.ENFORCER_SERVICE_URL, timeout=30.0) as client:
        r = await client.post("/internal/deploy", json=payload)
        return r.json(), r.status_code

@router.get("/services/{service_id}/compliance")
async def evaluate_compliance(service_id: uuid.UUID, version: str,
                              current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.ENFORCER_SERVICE_URL, timeout=30.0) as client:
        r = await client.get("/internal/compliance/evaluate",
                             params={"service_id": str(service_id), "version": version})
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()

@router.post("/services/{service_id}/error-budget/freeze")
async def freeze_service(service_id: uuid.UUID, payload: dict,
                         current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.ENFORCER_SERVICE_URL, timeout=10.0) as client:
        r = await client.post(f"/internal/freeze/{service_id}", json=payload)
        return r.json()
