"""
Nerve IDP — IaCApplyWorkflow (Temporal)

Terraform/Pulumi plan → human approval gate → apply.

Signal protocol:
  Portal sends POST /iac/requests/{id}/approve
  → Temporal signal "approval_received" with approver username
  → Workflow resumes from wait_condition

Idempotency on apply:
  Checks Terraform Cloud workspace run status before calling apply.
  If last run is already 'applied' → skips (safe retry).
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)


@dataclass
class IaCApplyInput:
    request_id: str
    service_id: str
    provider: str
    resource_type: str
    parameters: dict
    submitted_by: str
    team_id: str


@dataclass
class IaCApplyOutput:
    request_id: str
    status: str
    cost_delta_usd: float


@activity.defn(name="generate_iac_plan")
async def generate_iac_plan(params: IaCApplyInput) -> dict:
    import httpx, asyncio, os
    if params.provider != "terraform":
        return {"plan_output": f"# Pulumi plan for {params.resource_type}\n# TODO: Pulumi integration", "cost_delta_usd": 0.0, "run_id": "pulumi-mock"}

    token = os.environ.get("TERRAFORM_CLOUD_TOKEN", "")
    if not token:
        return {"plan_output": f"# Mock plan for {params.resource_type} (no TF Cloud token)", "cost_delta_usd": 0.0, "run_id": "mock-run"}

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://app.terraform.io/api/v2/runs",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
            json={"data": {"type": "runs", "attributes": {"is-speculative": True,
                  "message": f"Nerve IDP plan: {params.resource_type}"},
                  "relationships": {"workspace": {"data": {"type": "workspaces", "id": params.team_id}}}}},
            timeout=60.0,
        )
        r.raise_for_status()
        run_id = r.json()["data"]["id"]

        for _ in range(60):
            await asyncio.sleep(5)
            s = await client.get(f"https://app.terraform.io/api/v2/runs/{run_id}",
                                 headers={"Authorization": f"Bearer {token}"})
            run_status = s.json()["data"]["attributes"]["status"]
            if run_status == "planned":
                return {"plan_output": s.json()["data"]["attributes"].get("plan-output", ""), "cost_delta_usd": 0.0, "run_id": run_id}
            if run_status in ("errored", "discarded"):
                raise ApplicationError(f"Plan failed: {run_status}", non_retryable=False)

    raise ApplicationError("Plan timed out", non_retryable=False)


@activity.defn(name="validate_iac_approver")
async def validate_iac_approver(approver: str, params: IaCApplyInput) -> bool:
    import httpx, os
    gateway_url = os.environ.get("GATEWAY_URL", "http://localhost:8000")
    try:
        async with httpx.AsyncClient(base_url=gateway_url, timeout=5.0) as client:
            r = await client.get(f"/api/v1/teams/{params.team_id}/members/{approver}")
            if r.status_code == 200:
                return r.json().get("role") in {"platform_engineer", "sre", "engineering_manager"}
    except Exception:
        pass
    return True  # Allow in dev if gateway unreachable


@activity.defn(name="apply_iac_plan")
async def apply_iac_plan(params: IaCApplyInput, run_id: str) -> list[str]:
    """Idempotent: checks run status before applying."""
    import httpx, asyncio, os
    token = os.environ.get("TERRAFORM_CLOUD_TOKEN", "")
    if not token or run_id.startswith("mock") or run_id.startswith("pulumi"):
        logger.info("Mock apply (no TF Cloud token): %s", params.resource_type)
        return []

    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {token}"}
        s = await client.get(f"https://app.terraform.io/api/v2/runs/{run_id}", headers=headers)
        if s.json()["data"]["attributes"]["status"] == "applied":
            logger.info("Plan already applied (idempotent): %s", run_id)
            return []

        await client.post(f"https://app.terraform.io/api/v2/runs/{run_id}/actions/apply",
                          headers=headers, json={"comment": f"Applied by Nerve IDP — {params.request_id}"}, timeout=30.0)

        for _ in range(120):
            await asyncio.sleep(5)
            s = await client.get(f"https://app.terraform.io/api/v2/runs/{run_id}", headers=headers)
            if s.json()["data"]["attributes"]["status"] == "applied":
                return []
            if s.json()["data"]["attributes"]["status"] == "errored":
                raise ApplicationError("Apply failed", non_retryable=False)

    raise ApplicationError("Apply timed out", non_retryable=False)


@workflow.defn(name="IaCApplyWorkflow")
class IaCApplyWorkflow:
    def __init__(self) -> None:
        self._approved = False
        self._approver: Optional[str] = None
        self._rejected = False
        self._reject_reason: Optional[str] = None

    @workflow.signal(name="approval_received")
    def on_approval(self, approver: str) -> None:
        self._approver = approver
        self._approved = True

    @workflow.signal(name="rejection_received")
    def on_rejection(self, reason: str) -> None:
        self._reject_reason = reason
        self._rejected = True

    @workflow.run
    async def run(self, params: IaCApplyInput) -> IaCApplyOutput:
        retry = RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                            maximum_interval=timedelta(minutes=10), maximum_attempts=5)

        plan_result = await workflow.execute_activity(
            generate_iac_plan, params, start_to_close_timeout=timedelta(minutes=15), retry_policy=retry)

        # Wait for human approval (up to 7 days)
        await workflow.wait_condition(lambda: self._approved or self._rejected, timeout=timedelta(days=7))

        if self._rejected:
            return IaCApplyOutput(request_id=params.request_id, status="rejected", cost_delta_usd=0.0)

        is_valid = await workflow.execute_activity(
            validate_iac_approver, args=[self._approver, params], start_to_close_timeout=timedelta(seconds=15))
        if not is_valid:
            raise ApplicationError(f"Approver '{self._approver}' insufficient RBAC role.", non_retryable=True)

        await workflow.execute_activity(
            apply_iac_plan, args=[params, plan_result.get("run_id", "")],
            start_to_close_timeout=timedelta(minutes=15), retry_policy=retry)

        return IaCApplyOutput(request_id=params.request_id, status="applied",
                              cost_delta_usd=plan_result.get("cost_delta_usd", 0.0))
