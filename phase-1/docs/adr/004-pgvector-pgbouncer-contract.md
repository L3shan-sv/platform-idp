# ADR-004: pgvector, PgBouncer, and Contract-First API

**Status:** Accepted  
**Date:** 2024-01-01

---

## pgvector for semantic search

**Decision:** Use pgvector as the vector store, colocated in the existing PostgreSQL instance.

**Why not Pinecone/Weaviate/Qdrant:** pgvector runs inside PostgreSQL — no additional service to deploy or monitor. Similarity search can be combined with metadata filters in a single SQL query. PostgreSQL's `tsvector` handles keyword search; pgvector handles semantic search. Hybrid ranking in one query.

**Context window management:** AI co-pilot retrieval is capped at top-3 similar incidents (configurable), similarity threshold 0.75, and 4,000 token budget. Prevents context bloat on large incident history.

**Index timing:** The `ivfflat` index must NOT be created on an empty table. Create it after seeding data:
```sql
VACUUM ANALYZE incidents;
CREATE INDEX idx_incidents_embedding ON incidents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

---

## PgBouncer from day one

**Decision:** All application connections go through PgBouncer (port 6432) in transaction mode. Direct PostgreSQL (port 5432) is for Alembic migrations only.

**Why:** PostgreSQL hits max_connections (~150 concurrent users) without pooling. PgBouncer transaction mode allows 2,000+ concurrent users with 20 real PostgreSQL connections.

**Critical:** SQLAlchemy must use `NullPool` when connecting through PgBouncer. Using SQLAlchemy's pool on top of PgBouncer creates double-pooling and connection exhaustion.

**Migration exception:** DDL transactions are incompatible with PgBouncer transaction mode. Alembic connects directly to PostgreSQL via `DATABASE_URL_MIGRATIONS`.

---

## Contract-first API

**Decision:** Write the OpenAPI spec before writing any backend code. Pydantic models are derived from the spec, not the other way around.

**Why:** The frontend was built against TypeScript types in `types/index.ts`. The backend must return data matching those types exactly. A committed spec is the bridge.

**React Query stale times** must match backend cache TTLs:

| Data | staleTime | Backend TTL |
|---|---|---|
| Service catalog | 30s | CACHE_TTL_CATALOG=30 |
| Blast radius | 60s | CACHE_TTL_BLAST_RADIUS=60 |
| DORA metrics | 60s | CACHE_TTL_DORA=60 |
| Cost data | 5min | CACHE_TTL_COST=300 |
| Maturity scores | 30s | CACHE_TTL_MATURITY=30 |

**CI validation:** On every PR, the pipeline fetches the generated OpenAPI JSON from a running FastAPI instance and diffs it against the committed `docs/openapi.yaml`. Any mismatch fails the build.
