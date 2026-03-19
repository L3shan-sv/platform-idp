# Phase 1 — Foundation

This phase stands up the entire infrastructure stack and the FastAPI gateway with JWT auth, rate limiting, OTel instrumentation, and the full OpenAPI contract.

**By the end of Phase 1 you have:**
- Every infrastructure service running locally via `docker compose up -d`
- A FastAPI gateway on port 8000 with `/health` and `/health/ready` endpoints live
- The full PostgreSQL schema with pgvector, audit log, and all 20 tables
- The OpenAPI 3.1 spec covering all 24 REST endpoints — the contract every backend service builds against
- GitHub Actions CI running lint, type check, tests, and OpenAPI contract validation on every PR
- 4 Architecture Decision Records explaining every major technology choice

---

## What's in this directory

```
phase-1/
├── README.md                          ← This file
├── backend/
│   └── gateway/
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── alembic/
│       │   └── env.py                 ← Migrations (direct PostgreSQL, not PgBouncer)
│       └── app/
│           ├── main.py                ← FastAPI app, OTel, lifespan
│           ├── core/
│           │   ├── config.py          ← All settings via env vars
│           │   ├── database.py        ← NullPool — PgBouncer handles pooling
│           │   └── auth.py            ← JWT + RBAC role hierarchy
│           ├── middleware/
│           │   ├── audit.py           ← Every write logged to audit_log
│           │   └── request_id.py      ← X-Request-ID on every request
│           └── api/v1/routers/
│               └── health.py          ← /health and /health/ready
├── infra/
│   └── docker/
│       ├── postgres/init.sql          ← Full schema + pgvector + audit log
│       ├── neo4j/init.cypher          ← Indexes for blast radius traversal
│       ├── otel/config.yaml           ← OTel collector → Jaeger + Prometheus
│       ├── prometheus/
│       │   ├── prometheus.yaml
│       │   └── rules/error-budget.yaml ← Multi-window burn rate alerts
│       ├── grafana/provisioning/
│       │   └── datasources/datasources.yaml
│       ├── alertmanager/alertmanager.yaml ← Routes burn rate alerts → freeze webhook
│       └── temporal/dynamicconfig/
│           └── development-sql.yaml
├── docs/
│   ├── openapi.yaml                   ← Full API contract (contract-first)
│   └── adr/
│       ├── 001-temporal-over-celery.md
│       ├── 002-neo4j-blast-radius.md
│       ├── 003-opa-policy-enforcement.md
│       └── 004-pgvector-pgbouncer-contract.md
└── .github/
    └── workflows/
        └── ci.yaml
```

---

## How to run Phase 1

### Prerequisites
- Docker Desktop
- Python 3.12+

### 1. Start infrastructure

```bash
# From the nerve-idp root
cp .env.example .env
docker compose up -d postgres pgbouncer redis redis-replica-1 redis-replica-2 redis-sentinel neo4j vault temporal temporal-ui opa otel-collector jaeger prometheus grafana alertmanager
```

Wait for everything to be healthy:
```bash
docker compose ps
```

### 2. Run database migrations

```bash
cd phase-1/backend/gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Migrations connect direct to PostgreSQL (bypasses PgBouncer — required for DDL)
DATABASE_URL_MIGRATIONS=postgresql+psycopg2://nerve:nerve_dev_secret@localhost:5432/nerve \
  alembic upgrade head
```

### 3. Start the gateway

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Verify

```bash
# Liveness
curl http://localhost:8000/health

# Readiness (checks all dependencies)
curl http://localhost:8000/health/ready

# Swagger UI
open http://localhost:8000/docs
```

---

## Service URLs (Phase 1)

| Service | URL | Notes |
|---|---|---|
| Gateway API | http://localhost:8000 | |
| Swagger docs | http://localhost:8000/docs | Full interactive API |
| Temporal UI | http://localhost:8088 | Workflow visibility |
| Grafana | http://localhost:3000 | admin / nerve_grafana_secret |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics + alert rules |
| Neo4j Browser | http://localhost:7474 | neo4j / nerve_neo4j_secret |
| Vault | http://localhost:8200 | Token: nerve-vault-dev-token |

---

## Key decisions made in Phase 1

**PgBouncer is mandatory from day one.** All app connections go through PgBouncer on port 6432, never direct PostgreSQL on 5432. Without this, the platform hits a connection wall at 150 concurrent users. See `docs/adr/004-pgvector-pgbouncer-contract.md`.

**Contract-first API.** The OpenAPI spec in `docs/openapi.yaml` is the source of truth. Pydantic models are written to match it. CI validates the match on every PR.

**OTel wired from startup.** Traces and metrics are instrumented from day one, not retrofitted later. Every Phase 2+ service inherits this pattern.

**Audit log is append-only at the database level.** `UPDATE` and `DELETE` are revoked on `audit_log` for the `nerve_app` role. This is enforced in PostgreSQL, not just application code.

---

## What Phase 2 adds

Phase 2 builds on this foundation with the catalog service, golden path enforcer, OPA policies, DORA metrics engine, scaffold workflow, IaC workflow, and pipeline service. When you receive `phase-2.zip`, unzip it into the root alongside this directory and uncomment the Phase 2 services in `docker-compose.yaml`.
