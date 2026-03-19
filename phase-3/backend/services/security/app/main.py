"""
Nerve IDP — Security Posture Service (port 8008)

Ingests security scan results from GitHub Actions CI via webhooks.

Data sources:
  POST /internal/security/webhooks/trivy   — CVE scan results (called from GitHub Actions)
  POST /internal/security/webhooks/sbom    — SBOM in SPDX JSON
  POST /internal/security/webhooks/semgrep — SAST results
  POST /internal/security/network-policy/{id} — NetworkPolicy present flag

Score computation (0-100):
  Critical CVE → score = 0 (hard zero — no exceptions)
  No Critical:     40 pts
  High CVEs <= 3:  20 pts
  SBOM present:    20 pts
  SAST passed:     10 pts
  NetworkPolicy:   10 pts

After any scan:
  - Updates security_posture table
  - Publishes security.scan_complete to catalog.events
  - Triggers maturity rescore via Redis Streams event

Idempotency:
  Trivy results are keyed by (service_name, image_tag).
  Re-processing the same scan is safe — it updates the existing record.
"""
import json, logging, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Security posture service starting")
    yield


app = FastAPI(title="Nerve Security Posture Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class TrivyWebhookPayload(BaseModel):
    service_name: str
    image_tag: str
    results: list[dict]
    idempotency_key: Optional[str] = None


class SbomWebhookPayload(BaseModel):
    service_name: str
    image_tag: str
    sbom: dict


class SemgrepWebhookPayload(BaseModel):
    service_name: str
    commit_sha: str
    passed: bool
    findings_count: int
    findings: list[dict] = []


class SecurityPostureResponse(BaseModel):
    service_id: str
    score: int
    critical_cves: int
    high_cves: int
    medium_cves: int
    sbom_present: bool
    sbom_generated_at: Optional[datetime]
    sast_passed: Optional[bool]
    network_policy_present: bool
    last_scan_at: Optional[datetime]
    cves: list[dict]


# ── Helpers ───────────────────────────────────────────────────
def parse_trivy(raw: list[dict]) -> tuple[list[dict], int, int, int]:
    """Parse Trivy JSON output. Returns (cves, critical, high, medium)."""
    cves, critical, high, medium = [], 0, 0, 0
    for result in raw:
        for v in (result.get("Vulnerabilities") or []):
            sev = v.get("Severity", "UNKNOWN").lower()
            cves.append({"id": v.get("VulnerabilityID",""), "severity": sev,
                         "package": v.get("PkgName",""), "installed_version": v.get("InstalledVersion",""),
                         "fixed_version": v.get("FixedVersion","N/A"),
                         "description": (v.get("Description","")[:300] if v.get("Description") else None)})
            if sev == "critical": critical += 1
            elif sev == "high": high += 1
            elif sev == "medium": medium += 1
    return cves, critical, high, medium


def compute_score(critical: int, high: int, medium: int,
                  sbom: bool, sast: Optional[bool], network: bool) -> int:
    if critical > 0:
        return 0  # Hard zero — no exceptions
    score = 40  # No critical CVEs
    if high <= 3: score += 20
    if sbom: score += 20
    if sast is True: score += 10
    if network: score += 10
    return min(score, 100)


async def publish_scan_event(service_id: str, event_data: dict) -> None:
    import redis.asyncio as aioredis
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.xadd("catalog.events", {
            "type": "security.scan_complete", "version": "1",
            "payload": json.dumps({"service_id": service_id, **event_data}),
            "timestamp": str(int(time.time() * 1000)),
        }, maxlen=10_000, approximate=True)
    except Exception as exc:
        logger.warning("Failed to publish scan event: %s", exc)
    finally:
        await redis.aclose()


async def resolve_service_id(service_name: str, db: AsyncSession) -> Optional[str]:
    r = await db.execute(text("SELECT id FROM services WHERE name=:name AND deleted_at IS NULL"), {"name": service_name})
    row = r.fetchone()
    return str(row.id) if row else None


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.post("/internal/security/webhooks/trivy")
async def receive_trivy(payload: TrivyWebhookPayload, db: AsyncSession = Depends(get_db)):
    svc_id = await resolve_service_id(payload.service_name, db)
    if not svc_id:
        logger.warning("Trivy result for unknown service: %s", payload.service_name)
        return {"status": "skipped", "reason": "service_not_found"}

    cves, critical, high, medium = parse_trivy(payload.results)

    # Get current state for SBOM/SAST/NetworkPolicy
    existing = await db.execute(text("SELECT sbom_present, sast_passed, network_policy_present FROM security_posture WHERE service_id=:id::uuid"), {"id": svc_id})
    ex = existing.fetchone()
    sbom = ex.sbom_present if ex else False
    sast = ex.sast_passed if ex else None
    network = ex.network_policy_present if ex else False

    new_score = compute_score(critical, high, medium, sbom, sast, network)

    await db.execute(
        text("INSERT INTO security_posture (service_id, score, critical_cves, high_cves, medium_cves, cve_detail, last_scan_at) VALUES (:id::uuid, :score, :crit, :high, :med, :cves::jsonb, NOW()) ON CONFLICT (service_id) DO UPDATE SET score=:score, critical_cves=:crit, high_cves=:high, medium_cves=:med, cve_detail=:cves::jsonb, last_scan_at=NOW(), updated_at=NOW()"),
        {"id": svc_id, "score": new_score, "crit": critical, "high": high, "med": medium,
         "cves": json.dumps([c for c in cves[:50]])},  # Cap at 50 CVEs in DB
    )
    await db.execute(text("UPDATE services SET compliance_score=:score WHERE id=:id::uuid"),
                     {"id": svc_id, "score": new_score})
    await db.commit()

    await publish_scan_event(svc_id, {"scan_type": "trivy", "image_tag": payload.image_tag,
                                       "critical_cves": critical, "score": new_score})

    if critical > 0:
        logger.warning("CRITICAL CVE: service=%s image=%s critical=%d — deploys blocked",
                        payload.service_name, payload.image_tag, critical)
    else:
        logger.info("Trivy scan: service=%s score=%d crit=%d high=%d med=%d",
                     payload.service_name, new_score, critical, high, medium)

    return {"status": "processed", "service_id": svc_id, "score": new_score,
            "critical_cves": critical, "deploy_blocked": critical > 0}


@app.post("/internal/security/webhooks/sbom")
async def receive_sbom(payload: SbomWebhookPayload, db: AsyncSession = Depends(get_db)):
    svc_id = await resolve_service_id(payload.service_name, db)
    if not svc_id:
        return {"status": "skipped", "reason": "service_not_found"}
    ex = await db.execute(text("SELECT critical_cves, high_cves, medium_cves, sast_passed, network_policy_present FROM security_posture WHERE service_id=:id::uuid"), {"id": svc_id})
    row = ex.fetchone()
    if row:
        new_score = compute_score(row.critical_cves, row.high_cves, row.medium_cves, True, row.sast_passed, row.network_policy_present)
        await db.execute(text("UPDATE security_posture SET sbom_present=TRUE, sbom_generated_at=NOW(), score=:score, updated_at=NOW() WHERE service_id=:id::uuid"), {"id": svc_id, "score": new_score})
    else:
        await db.execute(text("INSERT INTO security_posture (service_id, sbom_present, sbom_generated_at) VALUES (:id::uuid, TRUE, NOW()) ON CONFLICT (service_id) DO UPDATE SET sbom_present=TRUE, sbom_generated_at=NOW()"), {"id": svc_id})
    await db.commit()
    await publish_scan_event(svc_id, {"scan_type": "sbom", "image_tag": payload.image_tag})
    return {"status": "processed"}


@app.post("/internal/security/webhooks/semgrep")
async def receive_semgrep(payload: SemgrepWebhookPayload, db: AsyncSession = Depends(get_db)):
    svc_id = await resolve_service_id(payload.service_name, db)
    if not svc_id:
        return {"status": "skipped", "reason": "service_not_found"}
    ex = await db.execute(text("SELECT critical_cves, high_cves, medium_cves, sbom_present, network_policy_present FROM security_posture WHERE service_id=:id::uuid"), {"id": svc_id})
    row = ex.fetchone()
    if row:
        new_score = compute_score(row.critical_cves, row.high_cves, row.medium_cves, row.sbom_present, payload.passed, row.network_policy_present)
        await db.execute(text("UPDATE security_posture SET sast_passed=:passed, score=:score, updated_at=NOW() WHERE service_id=:id::uuid"), {"id": svc_id, "passed": payload.passed, "score": new_score})
    else:
        await db.execute(text("INSERT INTO security_posture (service_id, sast_passed) VALUES (:id::uuid, :passed) ON CONFLICT (service_id) DO UPDATE SET sast_passed=:passed"), {"id": svc_id, "passed": payload.passed})
    await db.commit()
    await publish_scan_event(svc_id, {"scan_type": "semgrep", "passed": payload.passed, "findings": payload.findings_count})
    return {"status": "processed", "passed": payload.passed}


@app.post("/internal/security/network-policy/{service_id}")
async def update_network_policy(service_id: str, present: bool, db: AsyncSession = Depends(get_db)):
    ex = await db.execute(text("SELECT critical_cves, high_cves, medium_cves, sbom_present, sast_passed FROM security_posture WHERE service_id=:id::uuid"), {"id": service_id})
    row = ex.fetchone()
    if row:
        new_score = compute_score(row.critical_cves, row.high_cves, row.medium_cves, row.sbom_present, row.sast_passed, present)
        await db.execute(text("UPDATE security_posture SET network_policy_present=:present, score=:score WHERE service_id=:id::uuid"), {"id": service_id, "present": present, "score": new_score})
        await db.commit()
    return {"status": "updated", "network_policy_present": present}


@app.get("/internal/security/{service_id}")
async def get_security(service_id: str, db: AsyncSession = Depends(get_db)) -> SecurityPostureResponse:
    r = await db.execute(
        text("SELECT score, critical_cves, high_cves, medium_cves, sbom_present, sbom_generated_at, sast_passed, network_policy_present, last_scan_at, cve_detail FROM security_posture WHERE service_id=:id::uuid"),
        {"id": service_id},
    )
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No scan data yet."})
    return SecurityPostureResponse(
        service_id=service_id, score=row.score, critical_cves=row.critical_cves,
        high_cves=row.high_cves, medium_cves=row.medium_cves, sbom_present=row.sbom_present,
        sbom_generated_at=row.sbom_generated_at, sast_passed=row.sast_passed,
        network_policy_present=row.network_policy_present, last_scan_at=row.last_scan_at,
        cves=row.cve_detail or [],
    )
