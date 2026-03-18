# Nerve IDP

**Internal Developer Platform** — eliminate the cognitive tax of infrastructure ownership.

> The right way is the only way.

---

## What this is

Nerve IDP is a FAANG-grade platform engineering control plane built for SRE and Platform Engineering teams. It removes the three biggest blockers to engineering velocity:

1. **Policy enforcement** — Golden path enforcer gates every deploy with OPA. 6 checks. 0–100 compliance score. Hard block below 80.
2. **Self-service infrastructure** — Scaffold a production-ready service in under 4 minutes. IaC changes via form, not tickets.
3. **Platform-wide observability** — Google SRE error budget model, DORA metrics, AI-powered incident co-pilot.

---

## Repository structure

```
nerve-idp/
├── docker-compose.yaml       ← Single compose file for the entire stack
├── README.md                 ← This file
├── DOCUMENTATION.md          ← Full technical documentation
├── .env.example              ← Environment variable template
│
├── phase-1/                  ← Foundation: gateway, infra, OpenAPI spec
│   ├── README.md
│   ├── backend/gateway/
│   ├── infra/docker/
│   └── docs/
│
├── phase-2/                  ← Core platform: catalog, enforcer, DORA, scaffold
│   ├── README.md
│   └── backend/services/
│
├── phase-3/                  ← Differentiators: blast radius, error budget, cost, maturity
│   ├── README.md
│   └── backend/services/
│
├── phase-4/                  ← Wow layer: AI co-pilot, TechDocs, chaos, fleet
│   ├── README.md
│   └── backend/services/
│
└── phase-5/                  ← Production hardening: Helm, ArgoCD, load tests
    ├── README.md
    └── infra/
```

---

## Quick start

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Start the full stack
docker compose up -d

# 3. Verify everything is healthy
docker compose ps
curl http://localhost:8000/health/ready
```

**Service URLs once running:**

| Service | URL |
|---|---|
| API Gateway | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Temporal UI | http://localhost:8088 |
| Grafana | http://localhost:3000 |
| Jaeger | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| Neo4j Browser | http://localhost:7474 |
| Vault | http://localhost:8200 |

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Foundation — gateway, infra stack, OpenAPI contract | ✅ |
| **Phase 2** | Core platform — catalog, enforcer, DORA, scaffold | ⏳ |
| **Phase 3** | Differentiators — blast radius, error budget, cost, maturity | ⏳ |
| **Phase 4** | Wow layer — AI co-pilot, TechDocs, chaos, fleet ops | ⏳ |
| **Phase 5** | Production hardening — Helm, ArgoCD, load tests | ⏳ |

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS v3 |
| Backend | FastAPI (Python 3.12), Pydantic v2, SQLAlchemy 2.0 |
| Database | PostgreSQL 15 + PgBouncer + pgvector |
| Cache / Events | Redis 7 + Sentinel HA + Redis Streams |
| Graph | Neo4j 5 |
| Workflow Engine | Temporal.io + Celery |
| Secrets | HashiCorp Vault |
| Observability | Prometheus + Grafana + Loki + Jaeger + OTel |
| Security | Trivy + Semgrep + Syft + OPA + OPA Gatekeeper |
| AI | Anthropic Claude API + pgvector |
| GitOps | ArgoCD + Helm 3 |
