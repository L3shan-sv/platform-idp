"""Gateway — pipelines router (proxies to pipeline service)."""
import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.core.config import settings
from app.core.auth import get_current_user, CurrentUser

router = APIRouter()

@router.get("/services/{service_id}/pipelines")
async def list_pipeline_runs(service_id: uuid.UUID, limit: int = 20,
                             current_user: CurrentUser = Depends(get_current_user)):
    async with httpx.AsyncClient(base_url=settings.PIPELINE_SERVICE_URL, timeout=10.0) as client:
        r = await client.get(f"/api/v1/services/{service_id}/pipelines", params={"limit": limit})
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.json())
        return r.json()
