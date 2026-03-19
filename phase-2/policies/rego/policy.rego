# nerve/deploy/policy.rego
# All 6 golden path compliance checks.
# Run tests: opa test phase-2/policies/rego/ -v

package nerve.deploy

import future.keywords.if
import future.keywords.in

weights := {
    "health_endpoints":     15,
    "slo_defined":          20,
    "runbook":              15,
    "otel_instrumentation": 15,
    "secrets_via_vault":    20,
    "security_posture":     15,
}

# ── Health endpoints ──────────────────────────
check_health_endpoints := {"status":"pass","score":weights.health_endpoints,"detail":"Health endpoints /health and /ready responding correctly."} if {
    input.health_check_passing == true
}
check_health_endpoints := {"status":"fail","score":0,"detail":"Health endpoints not returning 200.","fix_url":sprintf("https://nerve.internal/docs/%v#health-endpoints",[input.service_name])} if {
    input.health_check_passing != true
}

# ── SLO defined ───────────────────────────────
check_slo_defined := {"status":"pass","score":weights.slo_defined,"detail":"SLO definition found."} if { input.slo_defined == true }
check_slo_defined := {"status":"fail","score":0,"detail":"No SLO definition found.","fix_url":sprintf("https://nerve.internal/docs/%v#slo-setup",[input.service_name])} if { input.slo_defined != true }

# ── Runbook — anti-gaming check ───────────────
# Must exist AND be updated AFTER the last deploy.
# A 6-month-old placeholder runbook scores 0.
check_runbook := {"status":"pass","score":weights.runbook,"detail":"Runbook found and updated after last deploy."} if {
    input.runbook_url != null
    input.runbook_updated_at != null
    input.runbook_last_deploy_at != null
    input.runbook_updated_at >= input.runbook_last_deploy_at
}
check_runbook := {"status":"fail","score":0,"detail":"Runbook exists but not updated since last deploy. Update the runbook to reflect current behaviour.","fix_url":input.runbook_url} if {
    input.runbook_url != null
    input.runbook_updated_at != null
    input.runbook_last_deploy_at != null
    input.runbook_updated_at < input.runbook_last_deploy_at
}
check_runbook := {"status":"fail","score":0,"detail":"No TechDocs runbook found. Create /docs/runbook.md.","fix_url":sprintf("https://nerve.internal/docs/%v#runbook-template",[input.service_name])} if {
    input.runbook_url == null
}

# ── OTel instrumentation ──────────────────────
check_otel := {"status":"pass","score":weights.otel_instrumentation,"detail":"OTel traces exporting from all endpoints."} if {
    input.otel_traces_exporting == true
    count(input.otel_missing_endpoints) == 0
}
check_otel := {"status":"warn","score":round(weights.otel_instrumentation * 0.5),"detail":sprintf("OTel active but %d endpoints missing instrumentation.",[count(input.otel_missing_endpoints)])} if {
    input.otel_traces_exporting == true
    count(input.otel_missing_endpoints) > 0
}
check_otel := {"status":"fail","score":0,"detail":"No OTel traces detected. Wire the OTel SDK.","fix_url":sprintf("https://nerve.internal/docs/%v#otel-setup",[input.service_name])} if {
    input.otel_traces_exporting != true
}

# ── Secrets via Vault ─────────────────────────
check_secrets := {"status":"pass","score":weights.secrets_via_vault,"detail":"Secrets sourced from Vault. No plaintext secrets."} if {
    input.has_vault_secrets == true
    input.has_plaintext_secrets == false
}
check_secrets := {"status":"fail","score":0,"detail":"Plaintext secrets detected. Move all secrets to Vault.","fix_url":sprintf("https://nerve.internal/docs/%v#vault-secrets",[input.service_name])} if {
    input.has_plaintext_secrets == true
}
check_secrets := {"status":"fail","score":0,"detail":"No Vault secret configuration found.","fix_url":sprintf("https://nerve.internal/docs/%v#vault-secrets",[input.service_name])} if {
    input.has_vault_secrets != true
    input.has_plaintext_secrets != true
}

# ── Security posture — HARD BLOCK on Critical CVE ─
# Critical CVE zeros the entire security pillar regardless of other checks.
check_security := {"status":"fail","score":0,"detail":sprintf("HARD BLOCK: %d Critical CVE(s). Patch before deploying.",[input.critical_cves]),"fix_url":sprintf("https://nerve.internal/services/%v/security",[input.service_id])} if {
    input.critical_cves > 0
}
check_security := {"status":"warn","score":round(weights.security_posture * 0.5),"detail":sprintf("No Critical CVEs but %d High CVEs detected.",[input.high_cves])} if {
    input.critical_cves == 0
    input.high_cves > 3
}
check_security := {"status":"pass","score":weights.security_posture,"detail":sprintf("Security: 0 Critical CVEs, %d High CVEs.",[input.high_cves])} if {
    input.critical_cves == 0
    input.high_cves <= 3
}

# ── Aggregate ─────────────────────────────────
checks := {
    "health_endpoints":     check_health_endpoints,
    "slo_defined":          check_slo_defined,
    "runbook":              check_runbook,
    "otel_instrumentation": check_otel,
    "secrets_via_vault":    check_secrets,
    "security_posture":     check_security,
}

score := total if {
    total := sum([v.score | v := checks[_]])
}

critical_cve_block if { input.critical_cves > 0 }

passed if {
    score >= 80
    not critical_cve_block
}
