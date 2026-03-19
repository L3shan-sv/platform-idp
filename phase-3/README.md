# Phase 3 — Differentiators

This phase builds the five services that separate Nerve IDP from any tutorial project. By the end of Phase 3 the platform has real-time blast radius analysis, the Google SRE error budget model running end-to-end, per-service cloud cost intelligence with anomaly detection, a 6-pillar maturity scoring engine, and a full security posture pipeline.

**By the end of Phase 3 you have:**
- Blast radius service (port 8004) — Neo4j 5-hop traversal, 60s Redis cache, pre-deploy dependency health risk score
- Error budget service (port 8005) — multi-window burn rate from Prometheus, idempotent deploy freeze webhook
- Cost intelligence (port 8006) — AWS Cost Explorer polling every 5 min, anomaly detection (2σ), team rollup + EOM forecast
- Maturity scoring (port 8007) — event-driven 6-pillar engine, anti-gaming docs check, template version tracking
- Security posture (port 8008) — Trivy/Semgrep/SBOM webhook ingestion, Critical CVE hard-zero

---

## Directory structure

```
phase-3/
├── README.md
├── backend/services/
│   ├── blast-radius/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py          ← Neo4j traversal + Redis cache + risk score
│   │       └── core/
│   │           ├── config.py
│   │           └── database.py
│   ├── error-budget/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py          ← Prometheus queries + freeze webhook (idempotent)
│   │       └── core/
│   │           ├── config.py
│   │           └── database.py
│   ├── cost-intelligence/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py          ← FastAPI endpoints
│   │       ├── core/
│   │       │   ├── config.py
│   │       │   └── database.py
│   │       └── workers/
│   │           └── cost.py      ← Celery beat — polls every 5 min
│   ├── maturity/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py          ← FastAPI endpoints + Redis Streams consumer
│   │       ├── core/
│   │       │   ├── config.py
│   │       │   └── database.py
│   │       └── workers/
│   │           └── maturity.py  ← 6-pillar scoring engine
│   └── security/
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/
│           ├── main.py          ← Trivy/SBOM/Semgrep webhooks + read endpoint
│           └── core/
│               ├── config.py
│               └── database.py
└── tests/
    └── test_phase3.py
```

---

## How to run

### 1. Uncomment Phase 3 services in root docker-compose.yaml

### 2. Build and start
```bash
docker compose up -d --build blast-radius error-budget cost-intelligence maturity security worker-cost worker-maturity
```

### 3. Verify
```bash
curl http://localhost:8004/health   # blast-radius
curl http://localhost:8005/health   # error-budget
curl http://localhost:8006/health   # cost-intelligence
curl http://localhost:8007/health   # maturity
curl http://localhost:8008/health   # security
```

### 4. Test blast radius
```bash
# Replace {service_id} with a real service UUID from your catalog
curl "http://localhost:8000/api/v1/services/{service_id}/blast-radius?hops=5" \
  -H "Authorization: Bearer $TOKEN"
```

### 5. Test Trivy webhook (simulates GitHub Actions)
```bash
curl -X POST http://localhost:8008/internal/security/webhooks/trivy \
  -H "Content-Type: application/json" \
  -d '{"service_name":"payment-service","image_tag":"v1.9.0","results":[]}'
```

---

## Critical correctness notes

**Blast radius cache invalidation** — cache is invalidated when a `service.updated` or `service.deleted` event fires on `catalog.events`. Without this, blast radius queries return stale graph data after topology changes.

**Neo4j index required** — without `service_id_unique` constraint and `service_team_index`, 5-hop traversal degrades from ~18ms to seconds. Created in `phase-1/infra/docker/neo4j/init.cypher`.

**Freeze webhook idempotency** — the error budget freeze uses `UPDATE ... WHERE deploy_frozen = FALSE RETURNING id`. Alertmanager may fire multiple simultaneous alerts (1h window + 6h window both crossing threshold). Only the first call publishes the freeze event to Redis Streams.

**Anomaly detection minimum data** — cost anomaly detection requires at least 3 data points. Services with fewer than 3 days of cost history return `anomaly_detected: false` regardless of spend.

**Maturity anti-gaming** — the docs pillar checks that TechDocs was updated **after** the last production deploy. A 6-month-old placeholder runbook scores 0. Security pillar zeros entirely on any Critical CVE — not just the security check.

**pgvector index timing** — the incident embeddings index must be created AFTER seeding data. An empty-table ivfflat index has zero lists and returns wrong results. See `phase-1/infra/docker/postgres/init.sql` comments.

---

## What Phase 4 adds

AI ops co-pilot (Claude API + pgvector similarity search over incidents), TechDocs-as-code (MkDocs + S3 + semantic search), live topology WebSocket, chaos engineering panel with approval gate, fleet operations WebSocket streaming.
