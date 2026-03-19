"""Gateway — IaC router (triggers Temporal IaCApplyWorkflow)."""
import logging, uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.core.auth import require_role, CurrentUser
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

class IacRequestInput(BaseModel):
    service_id: uuid.UUID; provider: str; resource_type: str
    parameters: dict = {}; description: Optional[str] = None

@router.post("/iac/requests", status_code=202)
async def submit_iac_request(payload: IacRequestInput,
                             current_user: CurrentUser = Depends(require_role("developer"))):
    try:
        from temporalio.client import Client
        from phase_2.workflows.temporal.iac_workflow import IaCApplyWorkflow, IaCApplyInput
        request_id = str(uuid.uuid4())
        client = await Client.connect(settings.temporal_address)
        handle = await client.start_workflow(
            IaCApplyWorkflow.run,
            IaCApplyInput(request_id=request_id, service_id=str(payload.service_id),
                          provider=payload.provider, resource_type=payload.resource_type,
                          parameters=payload.parameters, submitted_by=current_user.username,
                          team_id=current_user.team or ""),
            id=f"iac-{request_id}", task_queue="nerve-iac",
        )
        return {"id": request_id, "status": "pending", "workflow_id": f"iac-{request_id}", "submitted_by": current_user.username}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "iac_failed", "message": str(exc)})

@router.post("/iac/requests/{request_id}/approve")
async def approve_iac_request(request_id: uuid.UUID,
                              current_user: CurrentUser = Depends(require_role("platform_engineer"))):
    try:
        from temporalio.client import Client
        client = await Client.connect(settings.temporal_address)
        handle = client.get_workflow_handle(f"iac-{request_id}")
        await handle.signal("approval_received", current_user.username)
        return {"id": str(request_id), "status": "approved", "approved_by": current_user.username}
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"error": "workflow_not_found", "message": str(exc)})
