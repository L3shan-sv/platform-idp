"""Temporal worker — Phase 4 adds RemediationWorkflow to nerve-runbooks queue."""
import argparse, asyncio, logging, os
from temporalio.client import Client
from temporalio.worker import Worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_worker(task_queue: str) -> None:
    host = os.environ.get("TEMPORAL_HOST", "localhost")
    port = os.environ.get("TEMPORAL_PORT", "7233")
    client = await Client.connect(f"{host}:{port}")
    workflows, activities = [], []

    if task_queue == "nerve-runbooks":
        from remediation_workflow import (
            RemediationWorkflow, validate_remediation_rbac, execute_runbook_action,
            write_remediation_audit_log, create_chaos_experiment, compute_chaos_resilience,
        )
        workflows = [RemediationWorkflow]
        activities = [validate_remediation_rbac, execute_runbook_action,
                      write_remediation_audit_log, create_chaos_experiment, compute_chaos_resilience]

    worker = Worker(client, task_queue=task_queue, workflows=workflows, activities=activities)
    logger.info("Temporal worker starting: queue=%s", task_queue)
    await worker.run()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    args = parser.parse_args()
    asyncio.run(run_worker(args.queue))
