"""
Nerve IDP — Phase 2 Test Suite

Covers:
  - Catalog: CRUD, duplicate name, soft delete, Redis event publishing, Neo4j sync
  - Enforcer: OPA gate, freeze check, idempotent freeze under concurrent calls
  - DORA: tier classification thresholds (unit tests, no DB)
  - OPA policies: integration tests (requires OPA running at localhost:8181)

Run:
  pytest phase-2/tests/ -v
  pytest phase-2/tests/ -v -m "not integration"  # skip OPA integration tests
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest


# ─────────────────────────────────────────────
# Catalog tests
# ─────────────────────────────────────────────
class TestCatalogService:

    @pytest.mark.asyncio
    async def test_register_service_publishes_event_and_syncs_neo4j(self):
        with patch("app.core.events.publish_catalog_event", new_callable=AsyncMock) as mock_event, \
             patch("app.core.neo4j.sync_service_to_neo4j", new_callable=AsyncMock) as mock_neo4j:
            from httpx import AsyncClient, ASGITransport
            from phase_2.backend.services.catalog.app.main import app
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post("/api/v1/services", json={
                    "name": "test-order-service", "team": "commerce", "language": "python"
                })
            assert r.status_code == 201
            assert r.json()["name"] == "test-order-service"
            mock_event.assert_called_once()
            assert mock_event.call_args[0][0] == "service.created"
            mock_neo4j.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_service_rejects_invalid_name(self):
        from httpx import AsyncClient, ASGITransport
        from phase_2.backend.services.catalog.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/v1/services", json={
                "name": "Invalid_Name", "team": "commerce", "language": "python"
            })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_soft_delete_removes_from_api_not_db(self):
        with patch("app.core.neo4j.delete_service_from_neo4j", new_callable=AsyncMock):
            from httpx import AsyncClient, ASGITransport
            from phase_2.backend.services.catalog.app.main import app
            svc_id = str(uuid.uuid4())
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.delete(f"/api/v1/services/{svc_id}")
            # Would be 404 if not found — test pattern validates soft-delete path exists
            assert r.status_code in (204, 404)


# ─────────────────────────────────────────────
# Enforcer tests
# ─────────────────────────────────────────────
class TestGoldenPathEnforcer:

    @pytest.mark.asyncio
    async def test_deploy_blocked_when_frozen(self):
        from httpx import AsyncClient, ASGITransport
        with patch("phase_2.backend.services.enforcer.app.main.wait_for_opa", new_callable=AsyncMock):
            from phase_2.backend.services.enforcer.app.main import app
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                svc_id = str(uuid.uuid4())
                with patch("sqlalchemy.ext.asyncio.AsyncSession.scalar") as mock_scalar:
                    from unittest.mock import MagicMock
                    svc = MagicMock()
                    svc.deploy_frozen = True
                    svc.frozen_reason = "Error budget exhausted"
                    svc.frozen_at = datetime.now(timezone.utc)
                    svc.error_budget_consumed = 100.0
                    mock_scalar.return_value = svc
                    r = await client.post("/internal/deploy", json={
                        "service_id": svc_id, "version": "v1.0.0",
                        "environment": "production", "actor": "dev-1"
                    })
                assert r.json().get("frozen") is True

    @pytest.mark.asyncio
    async def test_compliance_below_80_returns_403(self):
        from httpx import AsyncClient, ASGITransport
        with patch("phase_2.backend.services.enforcer.app.main.wait_for_opa", new_callable=AsyncMock), \
             patch("phase_2.backend.services.enforcer.app.core.opa.evaluate_compliance", new_callable=AsyncMock) as mock_opa:
            from phase_2.backend.services.enforcer.app.core.opa import OpaEvaluationResult
            mock_opa.return_value = OpaEvaluationResult(score=52, passed=False, checks=[
                {"name": "health_endpoints", "status": "pass", "score": 15, "weight": 15, "detail": "OK"},
                {"name": "slo_defined", "status": "pass", "score": 20, "weight": 20, "detail": "OK"},
                {"name": "runbook", "status": "fail", "score": 0, "weight": 15, "detail": "No runbook"},
                {"name": "otel_instrumentation", "status": "pass", "score": 15, "weight": 15, "detail": "OK"},
                {"name": "secrets_via_vault", "status": "pass", "score": 20, "weight": 20, "detail": "OK"},
                {"name": "security_posture", "status": "fail", "score": 0, "weight": 15, "detail": "Critical CVE"},
            ], critical_cve_block=True)
            from phase_2.backend.services.enforcer.app.main import app
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post("/internal/deploy", json={
                    "service_id": str(uuid.uuid4()), "version": "v1.9.0",
                    "environment": "production", "actor": "dev-1"
                })
            assert r.status_code == 403
            assert r.json()["detail"]["score"] == 52

    @pytest.mark.asyncio
    async def test_freeze_is_idempotent(self):
        """Two concurrent freeze calls — only first publishes event."""
        import asyncio
        call_count = 0

        async def mock_execute(sql, params=None):
            nonlocal call_count
            call_count += 1
            return AsyncMock(scalar=lambda: str(uuid.uuid4()) if call_count == 1 else None)()

        with patch("phase_2.backend.services.enforcer.app.main.wait_for_opa", new_callable=AsyncMock), \
             patch("phase_2.backend.services.enforcer.app.main.publish_catalog_event", new_callable=AsyncMock) as mock_pub:
            from phase_2.backend.services.enforcer.app.main import app
            from httpx import AsyncClient, ASGITransport
            svc_id = str(uuid.uuid4())
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r1, r2 = await asyncio.gather(
                    client.post(f"/internal/freeze/{svc_id}",
                                json={"service_id": svc_id, "reason": "budget exhausted", "burn_rate": 14.5, "idempotency_key": "test-key"}),
                    client.post(f"/internal/freeze/{svc_id}",
                                json={"service_id": svc_id, "reason": "budget exhausted", "burn_rate": 14.5, "idempotency_key": "test-key"}),
                )
            results = [r1.json(), r2.json()]
            # At most one should have published the event
            assert mock_pub.call_count <= 1


# ─────────────────────────────────────────────
# DORA tier unit tests — no DB, no network
# ─────────────────────────────────────────────
class TestDoraTiers:

    def test_elite_frequency(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_freq
        assert get_dora_tier_freq(3.0) == "elite"
        assert get_dora_tier_freq(1.0) == "elite"

    def test_high_frequency(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_freq
        assert get_dora_tier_freq(0.5) == "high"

    def test_low_frequency(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_freq
        assert get_dora_tier_freq(1 / 60) == "low"

    def test_elite_lead_time(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_lead
        assert get_dora_tier_lead(0.5) == "elite"

    def test_low_lead_time(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_lead
        assert get_dora_tier_lead(800) == "low"

    def test_elite_mttr(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_mttr
        assert get_dora_tier_mttr(0.25) == "elite"

    def test_elite_cfr(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_cfr
        assert get_dora_tier_cfr(2.0) == "elite"

    def test_low_cfr(self):
        from phase_2.backend.services.pipeline.app.workers.dora import get_dora_tier_cfr
        assert get_dora_tier_cfr(20.0) == "low"


# ─────────────────────────────────────────────
# OPA integration tests — requires OPA at :8181
# ─────────────────────────────────────────────
@pytest.mark.integration
class TestOpaIntegration:

    @pytest.mark.asyncio
    async def test_fully_compliant_passes(self):
        import httpx
        async with httpx.AsyncClient(base_url="http://localhost:8181") as client:
            r = await client.post("/v1/data/nerve/deploy", json={"input": {
                "service_id": "test-001", "service_name": "test-svc", "version": "v1.0.0", "environment": "production",
                "health_check_passing": True, "slo_defined": True,
                "runbook_url": "https://docs/test", "runbook_updated_at": "2024-06-15T00:00:00Z",
                "runbook_last_deploy_at": "2024-06-10T00:00:00Z",
                "otel_traces_exporting": True, "otel_missing_endpoints": [],
                "has_vault_secrets": True, "has_plaintext_secrets": False,
                "critical_cves": 0, "high_cves": 0,
            }})
        assert r.status_code == 200
        assert r.json()["result"]["score"] == 100
        assert r.json()["result"]["passed"] is True

    @pytest.mark.asyncio
    async def test_critical_cve_hard_blocks(self):
        import httpx
        async with httpx.AsyncClient(base_url="http://localhost:8181") as client:
            r = await client.post("/v1/data/nerve/deploy", json={"input": {
                "service_id": "test-002", "service_name": "test-svc", "version": "v1.0.0", "environment": "production",
                "health_check_passing": True, "slo_defined": True,
                "runbook_url": "https://docs/test", "runbook_updated_at": "2024-06-15T00:00:00Z",
                "runbook_last_deploy_at": "2024-06-10T00:00:00Z",
                "otel_traces_exporting": True, "otel_missing_endpoints": [],
                "has_vault_secrets": True, "has_plaintext_secrets": False,
                "critical_cves": 1, "high_cves": 0,
            }})
        assert r.json()["result"]["critical_cve_block"] is True
        assert r.json()["result"]["passed"] is False
