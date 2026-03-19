"""Gateway — scaffold router (triggers Temporal ScaffoldWorkflow)."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.core.auth import require_role, CurrentUser
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

class ScaffoldRequest(BaseModel):
    name: str; team: str; language: str; description: str
    template_version: Optional[str] = None
    upstream_dependencies: list[str] = []

@router.post("/scaffold", status_code=202)
async def scaffold_service(payload: ScaffoldRequest,
                           current_user: CurrentUser = Depends(require_role("developer"))):
    try:
        from temporalio.client import Client
        from phase_2.workflows.temporal.scaffold_workflow import ScaffoldWorkflow, ScaffoldInput
        import uuid
        client = await Client.connect(settings.temporal_address)
        workflow_id = f"scaffold-{payload.name}-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            ScaffoldWorkflow.run,
            ScaffoldInput(name=payload.name, team=payload.team, language=payload.language,
                          description=payload.description, template_version=payload.template_version,
                          upstream_dependencies=payload.upstream_dependencies,
                          requested_by=current_user.username, workflow_id=workflow_id),
            id=workflow_id, task_queue="nerve-scaffold",
        )
        return {"workflow_id": workflow_id, "status": "started", "estimated_duration_seconds": 240}
    except Exception as exc:
        logger.error("Scaffold failed: %s", exc)
        raise HTTPException(status_code=500, detail={"error": "scaffold_failed", "message": str(exc)})

@router.get("/scaffold/{workflow_id}")
async def get_scaffold_status(workflow_id: str, current_user: CurrentUser = Depends(require_role("developer"))):
    try:
        from temporalio.client import Client
        client = await Client.connect(settings.temporal_address)
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        return {"workflow_id": workflow_id, "status": desc.status.name.lower() if desc.status else "unknown"}
    except Exception as exc:
        raise HTTPException(status_code=404, detail={"error": "workflow_not_found", "message": str(exc)})
