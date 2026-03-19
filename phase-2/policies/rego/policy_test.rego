# policy_test.rego — Run with: opa test phase-2/policies/rego/ -v
package nerve.deploy_test

import data.nerve.deploy

_base := {
    "service_id": "test-001", "service_name": "test-service",
    "version": "v1.0.0", "environment": "production",
    "health_check_passing": true, "slo_defined": true,
    "runbook_url": "https://nerve.internal/docs/test-service",
    "runbook_updated_at": "2024-06-15T10:00:00Z",
    "runbook_last_deploy_at": "2024-06-10T09:00:00Z",
    "otel_traces_exporting": true, "otel_missing_endpoints": [],
    "has_vault_secrets": true, "has_plaintext_secrets": false,
    "critical_cves": 0, "high_cves": 0,
}

test_fully_compliant_scores_100 if {
    deploy.score == 100 with input as _base
}

test_fully_compliant_passes if {
    deploy.passed with input as _base
}

test_critical_cve_hard_blocks if {
    not deploy.passed with input as object.union(_base, {"critical_cves": 1})
}

test_critical_cve_zeros_security_pillar if {
    result := deploy.checks with input as object.union(_base, {"critical_cves": 1})
    result.security_posture.score == 0
}

test_stale_runbook_fails if {
    result := deploy.checks with input as object.union(_base, {
        "runbook_updated_at": "2024-01-01T00:00:00Z",
        "runbook_last_deploy_at": "2024-06-01T00:00:00Z",
    })
    result.runbook.status == "fail"
    result.runbook.score == 0
}

test_missing_runbook_fails if {
    result := deploy.checks with input as object.union(_base, {"runbook_url": null, "runbook_updated_at": null, "runbook_last_deploy_at": null})
    result.runbook.status == "fail"
}

test_score_below_80_blocked if {
    not deploy.passed with input as object.union(_base, {"health_check_passing": false, "slo_defined": false})
}

test_otel_warn_partial_score if {
    result := deploy.checks with input as object.union(_base, {"otel_missing_endpoints": ["/checkout", "/refund"]})
    result.otel_instrumentation.status == "warn"
    result.otel_instrumentation.score == 7
}

test_plaintext_secrets_fail if {
    result := deploy.checks with input as object.union(_base, {"has_plaintext_secrets": true})
    result.secrets_via_vault.status == "fail"
    result.secrets_via_vault.score == 0
}

test_high_cves_warn if {
    result := deploy.checks with input as object.union(_base, {"high_cves": 5})
    result.security_posture.status == "warn"
}

test_no_health_endpoint_reduces_score if {
    s := deploy.score with input as object.union(_base, {"health_check_passing": false})
    s == 85
}
