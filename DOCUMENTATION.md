# Nerve IDP — Technical Documentation

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────┐
│                    React Frontend                        │
│        TypeScript · Vite · Tailwind · React Query        │
└────────────────────────┬────────────────────────────────┘
                         │ REST + WebSocket + GraphQL
┌────────────────────────▼────────────────────────────────┐
│                  FastAPI Gateway :8000                   │
│         JWT Auth · Rate Limiting · OTel · CORS           │
└──┬───────┬──────┬──────┬──────┬──────┬──────┬──────────┘
   │       │      │      │      │      │      │
:8001   :8002  :8003  :8004  :8005  :8006  :8007/:8008
Catalog Enforcer Pipeline Blast  Error  Cost  Maturity/
                        Radius Budget Intel  Security
   │       │
┌──▼───────▼──────────────────────────────────────────────┐
│                 Infrastructure Layer                     │
│  PostgreSQL 15+PgBouncer  Redis 7+Sentinel  Neo4j 5      │
│  Temporal.io  HashiCorp Vault  Celery                    │
│  Prometheus · Grafana · Jaeger · OTel Collector          │
└─────────────────────────────────────────────────────────┘
```

---

## Service port map

| Port | Service | Purpose |
|------|---------|---------|
| 8000 | Gateway | Single API entry point for all traffic |
| 8001 | Catalog | Service registry — source of truth |
| 8002 | Enforcer | Golden path OPA policy gate |
| 8003 | Pipeline | GitHub Actions polling + WebSocket streaming |
| 8004 | Blast Radius | Neo4j dependency graph traversal |
| 8005 | Error Budget | Multi-window burn rate + deploy freeze |
| 8006 | Cost Intelligence | AWS Cost Explorer + anomaly detection |
| 8007 | Maturity | 6-pillar service maturity scoring |
| 8008 | Security | Trivy/Semgrep/SBOM ingestion |
| 5432 | PostgreSQL | Direct (migrations only — never app traffic) |
| 6432 | PgBouncer | All app DB connections go here |
| 6379 | Redis | Cache, sessions, Celery broker, Streams |
| 7474 | Neo4j Browser | Graph visualization UI |
| 7687 | Neo4j Bolt | Driver connections |
| 8200 | Vault | Secrets management |
| 7233 | Temporal | Workflow engine gRPC |
| 8088 | Temporal UI | Workflow visibility dashboard |
| 9090 | Prometheus | Metrics scraping + querying |
| 3000 | Grafana | Dashboards |
| 16686 | Jaeger | Distributed tracing UI |
| 9093 | Alertmanager | Alert routing (burn rate → freeze webhook) |
| 8181 | OPA | Policy evaluation sidecar |

---

## Key architectural decisions

### PgBouncer is mandatory (ADR-005)
All application services connect to PostgreSQL **through PgBouncer on port 6432**, never directly on port 5432. PgBouncer runs in transaction mode and multiplexes thousands of application connections through a small pool of real PostgreSQL connections. Without it, the platform hits a connection wall at ~150 concurrent users. With it, the platform handles 2,000+.

**Exception:** Alembic migrations connect directly to PostgreSQL (port 5432) because DDL transactions are incompatible with PgBouncer transaction mode.

### OPA enforces policy at two layers (ADR-003)
- **Layer 1 — API level:** The enforcer service calls OPA before every deploy. Score < 80 returns 403.
- **Layer 2 — Kubernetes admission:** OPA Gatekeeper rejects any pod without a valid compliance annotation. This closes the bypass path of direct `kubectl apply`.

### Temporal for durable workflows (ADR-001)
IaC apply, service scaffolding, and runbook execution run as Temporal workflows. If a worker crashes mid-scaffold (after creating the GitHub repo but before pushing the first commit), Temporal resumes from exactly that step. Celery is kept for lightweight stateless tasks: DORA computation, cost polling, maturity scoring.

### Neo4j for blast radius (ADR-002)
5-hop graph traversal on a relational database degrades to ~800ms at 2,000 services. Neo4j with proper indexes runs the same query in ~18ms. Traversal results are cached in Redis with a 60-second TTL — cache hits return in <1ms.

### Contract-first API (ADR-006)
The OpenAPI spec in `phase-1/docs/openapi.yaml` is written before any backend code. All Pydantic models are derived from it. The CI pipeline validates that FastAPI's generated schema matches the committed spec on every PR.

---

## Error budget model

Nerve IDP implements the Google SRE multi-window burn rate model:

| Burn rate | Windows | Severity | Action |
|-----------|---------|----------|--------|
| 14x | 1h AND 6h | Page | Deploy freeze + PagerDuty |
| 6x | 6h | Page | PagerDuty only |
| 3x | 1d | Ticket | Jira auto-created |
| 1x | 3d | Warning | Dashboard only |

When a service's error budget is exhausted, all non-emergency deploys are automatically frozen. Unfreezing requires an SRE with the `sre` RBAC role.

---

## Capacity model

| Configuration | Concurrent users | Services |
|---|---|---|
| Default (out of box) | 150 | 300 |
| Tuned (4 config changes) | 2,000 | 2,000 |

The 4 config changes that unlock the higher tier:
1. PgBouncer transaction mode (already in from day one)
2. Pod ulimit raised to 65,535
3. Redis TTL on Neo4j traversals (60s)
4. Event-driven maturity scoring instead of cron

---

## RBAC roles

| Role | Permissions |
|---|---|
| `developer` | Read catalog, submit deploys, scaffold services |
| `sre` | + Execute runbooks, unfreeze services, override blast radius |
| `platform_engineer` | + Manage policies, bulk fleet operations, chaos experiments |
| `engineering_manager` | + View team costs, read-only on everything |

---

## Golden path compliance checks

| Check | Weight | Hard block? |
|---|---|---|
| Health endpoints | 15 | No |
| SLO defined | 20 | No |
| Runbook (updated after last deploy) | 15 | No |
| OTel instrumentation | 15 | No |
| Secrets via Vault | 20 | No |
| Security posture (no Critical CVEs) | 15 | **Yes — Critical CVE = score 0** |

Minimum passing score: **80/100**

---

## Running migrations

Alembic connects **directly to PostgreSQL**, bypassing PgBouncer:

```bash
cd phase-1/backend/gateway
DATABASE_URL_MIGRATIONS=postgresql+psycopg2://nerve:nerve_dev_secret@localhost:5432/nerve \
  alembic upgrade head
```

---

## Environment variables

See `.env.example` in the repo root. Copy to `.env` before running `docker compose up`.

Required for full functionality:
- `GITHUB_TOKEN` + `GITHUB_ORG` — scaffold service + pipeline polling
- `ANTHROPIC_API_KEY` — AI co-pilot (Phase 4)
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` — cost intelligence (Phase 3)
- `SLACK_WEBHOOK_URL` — cost anomaly alerts (Phase 3)
