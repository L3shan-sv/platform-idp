# Phase 4 — Wow Layer

This phase adds the features that make people stop and look twice. AI-powered incident co-pilot with similarity search, TechDocs as code, live topology streaming, chaos engineering with approval gates, fleet operations with real-time progress, and GraphQL for nested queries.

**By the end of Phase 4 you have:**
- AI ops co-pilot (port 8009) — Claude API + pgvector similarity search over past incidents, incident timeline stitching, one-click remediation actions
- TechDocs service (port 8010) — MkDocs build pipeline, S3 storage, full-text + semantic search, freshness tracking against last deploy
- Live topology WebSocket — real-time service health and traffic flow events streamed to the force-directed graph
- Chaos engineering (port 8011) — Chaos Mesh integration, RemediationWorkflow approval gate, resilience scoring
- Fleet operations WebSocket — bulk action progress streamed per service in real time
- GraphQL endpoint — `Service { doraMetrics, errorBudget, securityPosture, maturityScore }` in one round trip

---

## Directory structure

```
phase-4/
├── README.md
├── backend/
│   ├── gateway/
│   │   └── app/
│   │       ├── api/v1/routers/
│   │       │   ├── ai_copilot.py       ← /ai/chat + /ai/incidents/{id}/timeline
│   │       │   ├── docs.py             ← /docs/{serviceId} + /docs/search
│   │       │   ├── chaos.py            ← /chaos/experiments
│   │       │   ├── fleet.py            ← /fleet/collections + bulk operations
│   │       │   ├── observability.py    ← /metrics/dora + /services/{id}/error-budget
│   │       │   └── graphql.py          ← /graphql endpoint (Strawberry)
│   │       └── core/
│   │           └── websocket.py        ← Topology + fleet progress WebSocket handlers
│   └── services/
│       ├── ai-copilot/
│       │   ├── Dockerfile
│       │   ├── requirements.txt
│       │   └── app/
│       │       ├── main.py             ← Claude API + pgvector retrieval
│       │       └── core/
│       │           ├── config.py
│       │           ├── database.py
│       │           └── retrieval.py    ← pgvector similarity search
│       ├── docs/
│       │   ├── Dockerfile
│       │   ├── requirements.txt
│       │   └── app/
│       │       ├── main.py             ← MkDocs build + S3 + search webhooks
│       │       └── core/
│       │           ├── config.py
│       │           └── database.py
│       ├── chaos/
│       │   ├── Dockerfile
│       │   ├── requirements.txt
│       │   └── app/
│       │       ├── main.py             ← Chaos Mesh integration + resilience scoring
│       │       └── core/
│       │           ├── config.py
│       │           └── database.py
│       └── fleet/
│           ├── Dockerfile
│           ├── requirements.txt
│           └── app/
│               ├── main.py             ← Bulk operations + WebSocket progress
│               └── core/
│                   ├── config.py
│                   └── database.py
├── workflows/
│   └── temporal/
│       └── remediation_workflow.py     ← RemediationWorkflow (runbooks + chaos approval)
└── tests/
    └── test_phase4.py
```

---

## How to run

### 1. Set required env vars
```bash
# In .env
ANTHROPIC_API_KEY=your-key-here   # Required for AI co-pilot
AWS_ACCESS_KEY_ID=...             # Required for TechDocs S3 storage
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_TECHDOCS=nerve-techdocs
```

### 2. Uncomment Phase 4 services in root docker-compose.yaml

### 3. Build and start
```bash
docker compose up -d --build ai-copilot docs-service chaos fleet temporal-worker-runbooks
```

### 4. Test AI co-pilot
```bash
curl -X POST http://localhost:8000/api/v1/ai/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "payment-service error budget is exhausted, what happened?",
    "incident_context": {
      "service_name": "payment-service",
      "error_rate": 0.15,
      "burn_rate": 14.5,
      "budget_consumed": 100
    }
  }'
```

### 5. Test GraphQL
```bash
curl -X POST http://localhost:8000/graphql \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ service(id: \"uuid\") { name doraMetrics { deploymentFrequency { value tier } } errorBudget { budgetRemaining frozen } } }"}'
```

### 6. Connect to topology WebSocket
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/topology?token=JWT');
ws.onmessage = (e) => console.log(JSON.parse(e.data)); // TopologyEvent
```

---

## AI co-pilot context window strategy

The co-pilot retrieves context from three sources:
1. **Similar past incidents** — pgvector cosine similarity search, top-3, threshold 0.75
2. **Relevant TechDocs** — semantic search in docs_pages, top-2 excerpts
3. **Live incident context** — service name, error rate, burn rate, recent deploys, active alerts

Total context is capped at 4,000 tokens before passing to Claude. If over budget, incidents are trimmed from least-similar to most-similar. This prevents context bloat on large incident histories.

The pgvector ivfflat index must exist before similarity search works. See `phase-1/infra/docker/postgres/init.sql` for the index creation instructions (must be run after seeding data).

---

## Chaos engineering safety model

Every chaos experiment goes through the `RemediationWorkflow`:
1. Experiment submitted → status `pending_approval`
2. Platform engineer approves via portal → Temporal signal
3. Chaos Mesh experiment created with TTL matching `duration_seconds`
4. Resilience score computed during experiment from live health metrics
5. Experiment completes or TTL expires — Chaos Mesh cleans up automatically

**TTL is set at the Chaos Mesh level**, not just tracked by Temporal. If the Temporal worker crashes mid-experiment, Chaos Mesh still terminates the experiment at TTL. Never rely solely on the application layer to clean up fault injection.

---

## What Phase 5 adds

Production hardening: Helm charts for all services, ArgoCD ApplicationSets for dev/staging/prod, complete RBAC, k6 load testing at 150 concurrent users, capacity validation report, final ADRs.
