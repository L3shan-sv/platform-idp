# ADR-003: OPA Policy Enforcement — Two Layers

**Status:** Accepted  
**Date:** 2024-01-01

---

## Decision

Enforce golden path compliance at two independent layers.

**Layer 1 — API level (Enforcer service):**  
OPA sidecar evaluates 6 Rego policies on every deploy request. Score < 80 → 403. Critical CVE → 403 regardless of score.

**Layer 2 — Kubernetes admission (OPA Gatekeeper):**  
Admission webhook rejects any pod without `nerve.io/compliance-passed=true`. Closes the bypass path of direct `kubectl apply`.

---

## The 6 policies

| Policy | Weight | Hard block? |
|---|---|---|
| health_endpoints | 15 | No |
| slo_defined | 20 | No |
| runbook | 15 | No |
| otel_instrumentation | 15 | No |
| secrets_via_vault | 20 | No |
| security_posture | 15 | **Yes — Critical CVE = score 0** |

---

## Anti-gaming: runbook check

The runbook check requires the runbook to be updated **after the last deploy**. A team with a stale 6-month-old runbook scores 0 even if the page exists. This prevents the placeholder-runbook pattern.

---

## OPA startup gate

The enforcer refuses to start until OPA's `/health` returns 200. This prevents fail-open (allowing deploys without policy evaluation) on pod restarts. Implemented in `lifespan()` in the enforcer's `main.py`.

---

## Policy governance

All `.rego` files live in `phase-2/policies/rego/`. Changes require a PR with `opa test` passing in CI. Platform engineers can view active policies and weights in the portal but cannot edit them directly — all changes go through Git.
