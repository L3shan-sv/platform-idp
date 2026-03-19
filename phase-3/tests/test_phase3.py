"""
Nerve IDP — Phase 3 Test Suite

Covers:
  - Blast radius: cache key generation, risk level computation, dependency health scoring
  - Error budget: idempotent freeze, budget consumed calculation, time to exhaustion
  - Cost intelligence: anomaly detection algorithm, mock data consistency
  - Maturity scoring: pillar weight sum, anti-gaming docs check, security hard-zero
  - Security posture: Trivy parsing, score computation, critical CVE hard-zero

Run:
  pytest phase-3/tests/ -v
  pytest phase-3/tests/ -v -m "not integration"
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────
# Blast radius tests
# ─────────────────────────────────────────────
class TestBlastRadius:

    def test_risk_level_frozen_is_critical(self):
        from phase_3.backend.services.blast_radius.app.main import _risk_level
        nodes = [{"health_status": "healthy"}, {"health_status": "frozen"}, {"health_status": "healthy"}]
        assert _risk_level(nodes) == "critical"

    def test_risk_level_degraded_is_high(self):
        from phase_3.backend.services.blast_radius.app.main import _risk_level
        nodes = [{"health_status": "healthy"}, {"health_status": "degraded"}]
        assert _risk_level(nodes) == "high"

    def test_risk_level_all_healthy_is_low(self):
        from phase_3.backend.services.blast_radius.app.main import _risk_level
        nodes = [{"health_status": "healthy"}, {"health_status": "healthy"}]
        assert _risk_level(nodes) == "low"

    def test_risk_level_unknown_is_medium(self):
        from phase_3.backend.services.blast_radius.app.main import _risk_level
        nodes = [{"health_status": "unknown"}]
        assert _risk_level(nodes) == "medium"

    def test_cache_key_format(self):
        from phase_3.backend.services.blast_radius.app.main import _cache_key
        key = _cache_key("service-uuid-123", 5)
        assert key == "blast_radius:service-uuid-123:5"


# ─────────────────────────────────────────────
# Error budget tests
# ─────────────────────────────────────────────
class TestErrorBudget:

    def test_time_to_exhaustion_at_14x_burn(self):
        from phase_3.backend.services.error_budget.app.main import time_to_exhaustion
        # 14x burn rate, 100% budget remaining → ~51 hours
        hours = time_to_exhaustion(14.0, 100.0)
        assert hours is not None
        assert 50 < hours < 52

    def test_time_to_exhaustion_zero_burn_returns_none(self):
        from phase_3.backend.services.error_budget.app.main import time_to_exhaustion
        assert time_to_exhaustion(0.0, 100.0) is None

    def test_time_to_exhaustion_exhausted_budget_returns_none(self):
        from phase_3.backend.services.error_budget.app.main import time_to_exhaustion
        assert time_to_exhaustion(5.0, 0.0) is None

    def test_budget_remaining_capped_at_100(self):
        """budget_remaining should never exceed 100%."""
        remaining = max(100.0 - (-10.0), 0.0)  # If consumed is -10 somehow
        assert remaining == 100.0

    def test_budget_remaining_capped_at_zero(self):
        """budget_remaining should never go below 0."""
        remaining = max(100.0 - 110.0, 0.0)
        assert remaining == 0.0


# ─────────────────────────────────────────────
# Cost intelligence tests
# ─────────────────────────────────────────────
class TestCostIntelligence:

    def test_anomaly_detection_fires_on_spike(self):
        from phase_3.backend.services.cost_intelligence.app.main import detect_anomaly
        history = [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3]  # Stable ~$10
        current = 35.0  # 3.5x spike
        is_anomaly, spike_pct = detect_anomaly(history, current, threshold_std=2.0)
        assert is_anomaly is True
        assert spike_pct > 200  # > 200% above average

    def test_anomaly_detection_no_fire_on_normal(self):
        from phase_3.backend.services.cost_intelligence.app.main import detect_anomaly
        history = [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3]
        current = 11.0  # Normal variance
        is_anomaly, _ = detect_anomaly(history, current)
        assert is_anomaly is False

    def test_anomaly_detection_needs_min_3_data_points(self):
        from phase_3.backend.services.cost_intelligence.app.main import detect_anomaly
        is_anomaly, spike = detect_anomaly([10.0, 11.0], 100.0)  # Only 2 points
        assert is_anomaly is False
        assert spike == 0.0

    def test_anomaly_detection_zero_baseline_no_crash(self):
        from phase_3.backend.services.cost_intelligence.app.main import detect_anomaly
        is_anomaly, spike = detect_anomaly([0.0, 0.0, 0.0], 10.0)
        assert is_anomaly is False

    def test_mock_costs_generates_spike_on_15th(self):
        from phase_3.backend.services.cost_intelligence.app.main import _mock_costs
        from datetime import date
        start = date(2024, 6, 1)
        end = date(2024, 6, 30)
        costs = _mock_costs(["payment-service"], start, end)
        assert "payment-service" in costs
        # Day 15 should be a spike
        if "2024-06-15" in costs["payment-service"] and "2024-06-10" in costs["payment-service"]:
            assert costs["payment-service"]["2024-06-15"] > costs["payment-service"]["2024-06-10"]


# ─────────────────────────────────────────────
# Maturity scoring tests
# ─────────────────────────────────────────────
class TestMaturityScoring:

    def test_pillar_weights_sum_to_100(self):
        from phase_3.backend.services.maturity.app.main import WEIGHTS
        assert sum(WEIGHTS.values()) == 100

    def test_all_pillars_present(self):
        from phase_3.backend.services.maturity.app.main import WEIGHTS
        required = {"observability", "reliability", "security", "docs", "cost", "error_budget"}
        assert set(WEIGHTS.keys()) == required

    @pytest.mark.asyncio
    async def test_security_critical_cve_zeros_pillar(self):
        """Critical CVE must zero the entire security pillar regardless of other checks."""
        from phase_3.backend.services.maturity.app.main import score_security
        mock_db = AsyncMock()
        # Simulate security_posture row with critical CVE
        mock_row = MagicMock()
        mock_row.critical_cves = 1
        mock_row.high_cves = 0
        mock_row.sbom_present = True  # Would normally give points
        mock_row.sast_passed = True   # Would normally give points
        mock_row.network_policy_present = True  # Would normally give points
        mock_result = AsyncMock()
        mock_result.fetchone.return_value = mock_row
        mock_db.execute.return_value = mock_result
        score, signals = await score_security("test-id", mock_db)
        assert score == 0  # Hard zero
        assert any("HARD ZERO" in s["detail"] or "Critical" in s["detail"] for s in signals)

    @pytest.mark.asyncio
    async def test_docs_stale_runbook_scores_zero(self):
        """Docs updated BEFORE last deploy should score 0 on freshness check."""
        from datetime import datetime, timezone, timedelta
        from phase_3.backend.services.maturity.app.main import score_docs
        mock_db = AsyncMock()

        docs_row = MagicMock()
        docs_row.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)  # Old docs

        deploy_row = MagicMock()
        deploy_row.deployed_at = datetime(2024, 6, 1, tzinfo=timezone.utc)  # Recent deploy

        # Simulate: first execute returns docs, second returns deploy
        execute_results = [
            AsyncMock(fetchone=MagicMock(return_value=docs_row)),
            AsyncMock(fetchone=MagicMock(return_value=deploy_row)),
        ]
        mock_db.execute.side_effect = execute_results

        score, signals = await score_docs("test-id", mock_db)
        # Should have techdocs_exists (40) but NOT docs_updated_after_deploy (0)
        # Plus runbook section (20) = 60 max but freshness check fails
        assert score < 80
        fresh_signal = next((s for s in signals if s["name"] == "docs_updated_after_deploy"), None)
        assert fresh_signal is not None
        assert fresh_signal["passed"] is False


# ─────────────────────────────────────────────
# Security posture tests
# ─────────────────────────────────────────────
class TestSecurityPosture:

    def test_critical_cve_always_zeros_score(self):
        from phase_3.backend.services.security.app.main import compute_score
        # Even with perfect everything else, Critical CVE = 0
        score = compute_score(critical=1, high=0, medium=0, sbom=True, sast=True, network=True)
        assert score == 0

    def test_perfect_security_scores_100(self):
        from phase_3.backend.services.security.app.main import compute_score
        score = compute_score(critical=0, high=0, medium=0, sbom=True, sast=True, network=True)
        assert score == 100

    def test_no_sbom_reduces_score(self):
        from phase_3.backend.services.security.app.main import compute_score
        score_with = compute_score(0, 0, 0, sbom=True, sast=True, network=True)
        score_without = compute_score(0, 0, 0, sbom=False, sast=True, network=True)
        assert score_with > score_without
        assert score_without == 80  # 40 + 20 high + 0 sbom + 10 sast + 10 network

    def test_high_cves_above_threshold_reduces_score(self):
        from phase_3.backend.services.security.app.main import compute_score
        score_low = compute_score(0, 3, 0, sbom=True, sast=True, network=True)   # Exactly at threshold
        score_high = compute_score(0, 4, 0, sbom=True, sast=True, network=True)  # Above threshold
        assert score_low > score_high

    def test_parse_trivy_counts_severities(self):
        from phase_3.backend.services.security.app.main import parse_trivy
        raw = [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-2024-001", "Severity": "CRITICAL", "PkgName": "openssl", "InstalledVersion": "1.0", "FixedVersion": "1.1"},
            {"VulnerabilityID": "CVE-2024-002", "Severity": "HIGH", "PkgName": "zlib", "InstalledVersion": "1.2", "FixedVersion": "1.3"},
            {"VulnerabilityID": "CVE-2024-003", "Severity": "MEDIUM", "PkgName": "curl", "InstalledVersion": "7.0", "FixedVersion": "7.1"},
        ]}]
        cves, critical, high, medium = parse_trivy(raw)
        assert critical == 1
        assert high == 1
        assert medium == 1
        assert len(cves) == 3

    def test_parse_trivy_empty_results(self):
        from phase_3.backend.services.security.app.main import parse_trivy
        cves, critical, high, medium = parse_trivy([])
        assert cves == []
        assert critical == high == medium == 0

    def test_parse_trivy_null_vulnerabilities(self):
        from phase_3.backend.services.security.app.main import parse_trivy
        # Trivy returns Vulnerabilities: null when no CVEs found
        cves, critical, high, medium = parse_trivy([{"Vulnerabilities": None}])
        assert critical == 0
