"""Golden Path Enforcer — OPA client."""
import logging
from dataclasses import dataclass, field
from typing import Any
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)
POLICY_WEIGHTS = {"health_endpoints":15,"slo_defined":20,"runbook":15,"otel_instrumentation":15,"secrets_via_vault":20,"security_posture":15}

@dataclass
class OpaEvaluationResult:
    score: int
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    critical_cve_block: bool = False

async def build_opa_input(service_id: str, service_name: str, version: str, environment: str) -> dict:
    return {
        "service_id": service_id, "service_name": service_name,
        "version": version, "environment": environment,
        "health_check_passing": True, "slo_defined": True,
        "runbook_url": None, "runbook_updated_at": None, "runbook_last_deploy_at": None,
        "otel_traces_exporting": True, "otel_missing_endpoints": [],
        "has_vault_secrets": True, "has_plaintext_secrets": False,
        "critical_cves": 0, "high_cves": 0,
    }

async def evaluate_compliance(service_id: str, service_name: str, version: str, environment: str) -> OpaEvaluationResult:
    opa_input = await build_opa_input(service_id, service_name, version, environment)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{settings.OPA_URL}/v1/data/nerve/deploy", json={"input": opa_input})
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        raise RuntimeError("OPA timed out — deploy blocked for safety.")
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"OPA returned HTTP {exc.response.status_code}")
    result = data.get("result", {})
    checks = [{"name": n, "status": c.get("status","fail"), "score": c.get("score",0),
               "weight": POLICY_WEIGHTS.get(n,0), "detail": c.get("detail",""), "fix_url": c.get("fix_url")}
              for n, c in result.get("checks", {}).items()]
    return OpaEvaluationResult(score=result.get("score",0), passed=result.get("passed",False),
                               checks=checks, critical_cve_block=result.get("critical_cve_block",False))
