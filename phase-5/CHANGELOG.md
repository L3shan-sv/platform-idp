# Nerve IDP — Changelog

All notable changes to the Nerve Internal Developer Platform are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [1.0.0] — 2024-06-01 — Phase 5: Production Hardening

### Added
- Helm charts for all 12 application services with dev/staging/prod value overrides
- Umbrella Helm chart (`nerve-idp-stack`) installs the full platform with one command
- ArgoCD ApplicationSet managing three environments via GitOps
- ArgoCD project with RBAC (developer/sre/platform-engineer roles)
- Complete RBAC lockdown — every endpoint has minimum role annotation
- k6 load test suite — 150 concurrent users, 30 minute sustained run
- k6 spike test — 500 concurrent user burst
- Capacity validation report (p95 latency, throughput, resource utilisation)
- 5 Architecture Decision Records (ADR-005 through ADR-009)
- Alembic baseline migration `001_initial_schema`
- `alembic.ini` with direct PostgreSQL URL (bypasses PgBouncer for DDL)
- Kustomize overlays for dev/staging/prod
- HPA (Horizontal Pod Autoscaler) for all services
- NetworkPolicy for all services (deny-by-default ingress)
- Pod anti-affinity rules (no two replicas on same node)
- `net.core.somaxconn=65535` ulimit in all Helm deployment templates

### Changed
- `docker-compose.yaml` — Phase 5 comment added (no new services, Helm handles prod)
- `README.md` — All 5 phases marked complete

### Capacity validated
- 150 concurrent users: p95 < 200ms, error rate 0.003%
- 500 concurrent users (spike): p95 < 2s, error rate 0.08%
- 2,000 services in catalog: list query < 80ms

---

## [0.4.0] — 2024-05-15 — Phase 4: Wow Layer

### Added
- AI ops co-pilot (`ai-copilot` service, port 8009)
  - Claude API integration with structured JSON system prompt
  - pgvector similarity search over past incidents (top-3, 0.75 threshold)
  - Context window management (4,000 token cap with trimming)
  - Incident timeline stitching from audit_log
  - Dev fallback mock response when API key not set
- TechDocs-as-code (`docs` service, port 8010)
  - MkDocs build pipeline triggered by GitHub Actions webhook
  - S3 storage with pre-signed URLs (1 hour TTL)
  - Hybrid full-text + semantic search (tsvector + pgvector)
  - Freshness tracking: `updated_at` vs last production deploy
  - `docs.rebuild_complete` event triggers maturity rescore
- Chaos engineering (`chaos` service, port 8011)
  - Chaos Mesh integration: pod_kill, network_latency, cpu_stress, memory_pressure
  - TTL set at Chaos Mesh resource level (not just Temporal tracking)
  - RemediationWorkflow approval gate
  - Resilience score computed from Prometheus metrics during experiment
  - Production chaos blocked at service level
- Fleet operations (`fleet` service, port 8012)
  - Celery chord for bulk operations across service collections
  - Batch size of 10 (prevents memory exhaustion on large fleets)
  - WebSocket real-time progress streaming per service
- GraphQL endpoint (`/graphql`)
  - Strawberry schema: `Service { doraMetrics, errorBudget, securityPosture, maturityScore }`
  - Nested resolvers fetch from downstream services
  - Read-only — all mutations go through REST
- `RemediationWorkflow` Temporal workflow
  - Handles runbook execution and chaos experiment approval
  - Runbook snapshot stored at execution time (immutable audit)
  - Action types: k8s_restart_pod, k8s_scale_deployment, vault_rotate_secret, flush_cache
- Topology WebSocket (`/ws/topology`) — live service health events
- Phase 4 gateway routers: ai_copilot, docs, chaos, fleet, observability
- Phase 4 test suite (29 test cases)

---

## [0.3.0] — 2024-04-30 — Phase 3: Differentiators

### Added
- Blast radius service (port 8004)
  - Neo4j 5-hop Cypher traversal (~18ms on 2,000 nodes)
  - Redis 60-second cache (< 1ms on cache hit)
  - Cache invalidated on `service.updated` / `service.deleted` events
  - Pre-deploy dependency health risk score (0–100)
- Error budget service (port 8005)
  - Multi-window burn rate from Prometheus (1h, 6h, 1d, 3d)
  - Idempotent deploy freeze webhook (`UPDATE ... WHERE deploy_frozen = FALSE RETURNING id`)
  - SRE manual unfreeze endpoint
  - Falls back to PostgreSQL cached values when Prometheus unavailable
- Cost intelligence service (port 8006)
  - AWS Cost Explorer polling every 5 minutes via Celery beat
  - Mock data for dev (includes 15th-of-month spike for anomaly demo)
  - Rolling 7-day average + 2σ anomaly detection
  - Slack alert on cost spike
  - Team rollup with budget vs actual + EOM linear forecast
- Maturity scoring service (port 8007)
  - Event-driven via Redis Streams consumer (only rescores affected service)
  - 6 pillars: observability, reliability, security, docs, cost, error_budget
  - Anti-gaming docs check: updated after last deploy, not just exists
  - Critical CVE zeros entire security pillar
  - Template version tracking (`template_behind_by`)
  - Hourly Celery beat catch-all for missed events
- Security posture service (port 8008)
  - Trivy webhook ingestion from GitHub Actions
  - SBOM tracking (Syft)
  - Semgrep SAST results
  - NetworkPolicy flag
  - Hard-zero on Critical CVE
  - Publishes `security.scan_complete` to catalog.events
- Phase 3 test suite (28 test cases including anomaly detection, Trivy parsing, maturity weights)

---

## [0.2.0] — 2024-03-15 — Phase 2: Core Platform

### Added
- Catalog service (port 8001)
  - Full CRUD with Redis Streams events
  - Neo4j sync on every write
  - 5-minute reconciliation (PostgreSQL ↔ Neo4j drift correction)
  - Consumer groups created with MKSTREAM on startup
- Golden path enforcer (port 8002)
  - OPA startup gate (refuses to start without OPA healthy)
  - 6-policy evaluation via OPA sidecar
  - Idempotent deploy freeze
  - Compliance annotation generation for Kubernetes pods
- OPA Rego policies (`phase-2/policies/rego/`)
  - All 6 checks in one file
  - Anti-gaming runbook check (updated after last deploy)
  - 10 unit tests (`opa test phase-2/policies/rego/ -v`)
- OPA Gatekeeper Layer 2 admission control
  - `RequireComplianceAnnotation` — blocks pods without compliance annotation
  - `RequireNetworkPolicy` — warns on missing NetworkPolicy
- Pipeline service (port 8003)
  - GitHub Actions polling with rate limit awareness
  - WebSocket stage streaming via Redis pub/sub
- DORA metrics Celery worker
  - All 4 metrics (deployment frequency, lead time, MTTR, change failure rate)
  - Google 2023 tier thresholds
- Scaffold Temporal workflow (`ScaffoldWorkflow`)
  - 8 idempotent activities
  - GitHub rate limit: 403 rate-limit (retryable) vs 403 auth (non-retryable)
  - Parallel k8s namespace + Vault + catalog registration
- IaC Temporal workflow (`IaCApplyWorkflow`)
  - Terraform Cloud plan → human approval signal → apply
  - 7-day approval timeout
- Gateway (port 8000) with catalog, deploy, scaffold, iac, pipeline routers
- Phase 2 test suite

---

## [0.1.0] — 2024-02-01 — Phase 1: Foundation

### Added
- Repository structure (monorepo, phase-N/ directories)
- `docker-compose.yaml` — full infra stack
  - PostgreSQL 15 + pgvector + PgBouncer (transaction mode)
  - Redis 7 + Sentinel HA (primary + 2 replicas)
  - Neo4j 5 Community + APOC
  - HashiCorp Vault (dev mode)
  - Temporal.io + UI
  - OPA sidecar
  - OTel Collector → Jaeger + Prometheus
  - Grafana + Alertmanager
- PostgreSQL schema (20 tables, pgvector, audit log append-only enforcement)
- Neo4j indexes and constraints
- FastAPI gateway (port 8000)
  - JWT auth + RBAC role hierarchy
  - Rate limiting (slowapi)
  - OTel instrumentation
  - Audit log middleware (every write logged)
  - Request ID middleware
  - `/health` (liveness) + `/health/ready` (readiness, concurrent dep checks)
- OpenAPI 3.1 specification (`phase-1/docs/openapi.yaml`)
  - 24 REST endpoints
  - 3 WebSocket endpoints
  - 1 GraphQL endpoint
  - All schemas matching TypeScript frontend types
- Alembic `env.py` (direct PostgreSQL, bypasses PgBouncer for DDL)
- GitHub Actions CI (lint, type-check, test, OPA test, Docker build)
- 4 Architecture Decision Records
  - ADR-001: Temporal over Celery
  - ADR-002: Neo4j for blast radius
  - ADR-003: OPA two-layer enforcement
  - ADR-004: pgvector, PgBouncer, contract-first API
- Prometheus multi-window burn rate recording rules + alert rules
- Alertmanager routing (burn rate → freeze webhook → Slack → PagerDuty)
- `.env.example`, `.gitignore`, `README.md`, `DOCUMENTATION.md`
