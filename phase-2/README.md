# Phase 2 — Core Platform

This phase builds the five application services that make Nerve IDP functional. By the end of Phase 2 the platform is genuinely demonstrable.

**By the end of Phase 2 you have:**
- Service catalog (port 8001) — CRUD, Redis Streams events, Neo4j sync, reconciliation job
- Golden path enforcer (port 8002) — 6 OPA policies, scored 0-100, hard blocked below 80
- OPA Rego policies with unit tests — anti-gaming runbook check included
- OPA Gatekeeper — Layer 2 Kubernetes admission control
- DORA metrics Celery worker — all 4 metrics, correct Google 2023 tier thresholds
- Pipeline service (port 8003) — GitHub Actions polling, WebSocket streaming
- ScaffoldWorkflow — Temporal, golden-path service in under 4 minutes
- IaCApplyWorkflow — Temporal, human approval gate
- Gateway (port 8000) — full routing to all Phase 2 services

---

## Directory structure

```
phase-2/
├── README.md
├── backend/
│   ├── gateway/app/          ← Updated gateway with all Phase 2 routers
│   └── services/
│       ├── catalog/          ← Service catalog microservice
│       ├── enforcer/         ← Golden path enforcer + OPA client
│       └── pipeline/         ← GitHub poller + DORA worker + WebSocket
├── policies/
│   ├── rego/                 ← OPA policies + unit tests
│   └── gatekeeper/           ← Kubernetes admission constraints
├── workflows/temporal/       ← ScaffoldWorkflow + IaCApplyWorkflow
└── tests/                    ← Full Phase 2 test suite
```

---

## How to run

### 1. Uncomment Phase 2 services in root docker-compose.yaml

### 2. Build and start
```bash
docker compose up -d --build gateway catalog enforcer pipeline worker-dora temporal-worker-scaffold temporal-worker-iac
```

### 3. Verify
```bash
curl http://localhost:8000/health/ready
curl http://localhost:8001/health
curl http://localhost:8002/health
```

### 4. Run OPA policy unit tests
```bash
opa test phase-2/policies/rego/ -v
```

### 5. Register a service
```bash
curl -X POST http://localhost:8000/api/v1/services \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"payment-service","team":"commerce","language":"python"}'
```

---

## Critical correctness notes

- **OPA startup gate** — enforcer refuses to start until OPA /health returns 200
- **Idempotent freeze** — `UPDATE ... WHERE deploy_frozen = FALSE RETURNING id` prevents duplicate events
- **Redis Streams MKSTREAM** — groups created before first event, catches all messages
- **Neo4j reconciliation** — 5-minute Celery beat task corrects PostgreSQL↔Neo4j drift
- **Runbook anti-gaming** — runbook must be updated AFTER last deploy, not just exist
- **GitHub rate limits** — scaffold workflow distinguishes 403 rate-limit (retryable) from 403 auth (non-retryable)

---

## What Phase 3 adds

Blast radius (Neo4j traversal + Redis cache), error budget engine (multi-window burn rate + deploy freeze webhook), cost intelligence (AWS Cost Explorer + anomaly detection), maturity scoring (6-pillar event-driven), security posture (Trivy/SBOM/Semgrep).
