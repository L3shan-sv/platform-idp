# Nerve IDP

**Internal Developer Platform** — eliminate the cognitive tax of infrastructure ownership.

> The right way is the only way.

---

## What this is

Nerve IDP is a FAANG-grade platform engineering control plane for SRE and Platform Engineering teams. It removes three blockers to engineering velocity:

1. **Policy enforcement** — Golden path enforcer gates every deploy with OPA. 6 checks. 0–100 score. Hard blocked below 80. Two enforcement layers: API + Kubernetes admission.
2. **Self-service infrastructure** — Scaffold a production-ready service in under 4 minutes. IaC changes via form, not tickets.
3. **Platform-wide observability** — Google SRE error budget model, DORA metrics, AI-powered incident co-pilot, live topology streaming.

---

## Repository structure

```
nerve-idp/
├── docker-compose.yaml       ← Single compose file — all phases
├── README.md                 ← This file
├── DOCUMENTATION.md          ← Full technical documentation
├── .env.example
├── phase-1/                  ← Foundation: gateway, infra, OpenAPI contract ✅
├── phase-2/                  ← Core platform: catalog, enforcer, DORA, scaffold ✅
├── phase-3/                  ← Differentiators: blast radius, error budget, cost, maturity ✅
├── phase-4/                  ← Wow layer: AI co-pilot, TechDocs, chaos, fleet, GraphQL ✅
└── phase-5/                  ← Production hardening: Helm, ArgoCD, k6, capacity report ✅
```

---

## Quick start (local dev)

```bash
cp .env.example .env
# Required: GITHUB_TOKEN, GITHUB_ORG
# For AI co-pilot: ANTHROPIC_API_KEY

docker compose up -d
docker compose ps
curl http://localhost:8000/health/ready
open http://localhost:8000/docs
```

## Deploy to Kubernetes

```bash
# ArgoCD manages the full lifecycle
kubectl apply -f phase-5/infra/argocd/project.yaml
kubectl apply -f phase-5/infra/argocd/applicationset.yaml
# ArgoCD auto-syncs dev. Staging and prod require manual approval.
```

---

## Service ports

| Port | Service |
|---|---|
| 8000 | API Gateway (all traffic enters here) |
| 8001 | Catalog |
| 8002 | Golden Path Enforcer |
| 8003 | Pipeline + DORA |
| 8004 | Blast Radius |
| 8005 | Error Budget |
| 8006 | Cost Intelligence |
| 8007 | Maturity Scoring |
| 8008 | Security Posture |
| 8009 | AI Co-pilot |
| 8010 | TechDocs |
| 8011 | Chaos Engineering |
| 8012 | Fleet Operations |
| 8000/docs | Swagger UI |
| 8000/graphql | GraphQL |
| 8088 | Temporal UI |
| 3000 | Grafana (admin / nerve_grafana_secret) |
| 16686 | Jaeger |
| 9090 | Prometheus |
| 7474 | Neo4j Browser |
| 8200 | Vault |

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Foundation — gateway, infra, OpenAPI contract | ✅ |
| **Phase 2** | Core platform — catalog, enforcer, DORA, scaffold, IaC | ✅ |
| **Phase 3** | Differentiators — blast radius, error budget, cost, maturity, security | ✅ |
| **Phase 4** | Wow layer — AI co-pilot, TechDocs, chaos, fleet ops, GraphQL | ✅ |
| **Phase 5** | Production hardening — Helm, ArgoCD, k6, capacity validation | ✅ |

---

## Capacity

| Configuration | Concurrent users | Services |
|---|---|---|
| Default (out of box) | 150 | 300 |
| Tuned (4 config changes) | 2,000 | 2,000 |

See `phase-5/tests/k6/capacity_report.md` for full validated benchmarks.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS v3 |
| Backend | FastAPI (Python 3.12), Pydantic v2, SQLAlchemy 2.0 |
| Database | PostgreSQL 15 + PgBouncer + pgvector |
| Cache/Events | Redis 7 + Sentinel HA + Redis Streams |
| Graph | Neo4j 5 |
| Workflows | Temporal.io + Celery |
| Secrets | HashiCorp Vault |
| Observability | Prometheus + Grafana + Jaeger + OTel Collector |
| Policy | OPA (API layer) + OPA Gatekeeper (Kubernetes admission) |
| Security | Trivy + Semgrep + Syft |
| AI | Anthropic Claude API + pgvector |
| GitOps | ArgoCD + Helm 3 |
| Load testing | k6 |
