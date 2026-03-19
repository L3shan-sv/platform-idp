# ADR-009: Capacity Model — 2,000 Concurrent Users

**Status:** Accepted  
**Date:** 2024-06-01

## The claim

Nerve IDP supports 150 concurrent users out of the box, and 2,000 concurrent users with 4 configuration changes.

## The 4 changes

### 1. PgBouncer transaction mode (Phase 1)
Default PostgreSQL: 150 concurrent connections, each held for the lifetime of the request (typically 20–200ms). At 150 req/s this is fine. At 2,000 req/s this exhausts connections instantly.

PgBouncer transaction mode: 1,000 clients share 20 real PostgreSQL connections. Each connection is released after every transaction, not every request. This works because FastAPI uses async SQLAlchemy — the connection is idle between `await db.execute()` calls.

**Critical:** SQLAlchemy must use `NullPool`. Without this, SQLAlchemy maintains its own connection pool on top of PgBouncer — doubling the connections and breaking transaction mode.

### 2. Pod ulimit (Phase 5 Helm)
Linux kernel default `somaxconn` (TCP backlog) is 128. At 500+ concurrent users, the accept backlog fills and connections are dropped with `ECONNREFUSED`. Setting `net.core.somaxconn=65535` in the pod's securityContext removes this limit.

### 3. Redis TTL on blast radius (Phase 3)
Without caching, every deploy check triggers a Neo4j 5-hop traversal (~18ms). At 150 deploys/min this is 180 Neo4j queries/min — fine. At 2,000 concurrent users with multiple deploy checks, it becomes the bottleneck. The 60-second Redis cache reduces Neo4j load by 95%.

### 4. Event-driven maturity scoring (Phase 4)
The naive approach: Celery beat scores all 2,000 services every 5 minutes = 400 services/minute continuously. At 2,000 services, this is the dominant CPU consumer. The Redis Streams consumer rescores only the service that changed — reducing scoring CPU by 97%.

## What 2,000 concurrent users actually requires

2,000 concurrent users does not mean 2,000 simultaneous requests. It means 2,000 users with realistic think time (0.5–2s between actions), yielding roughly 1,000–4,000 requests/second depending on action mix. The capacity report validates this.

## Hard limits not addressed

At 10,000+ concurrent users:
- PostgreSQL itself becomes the bottleneck (read replicas needed)
- Redis Sentinel should be replaced with Redis Cluster
- Neo4j requires a causal cluster for write HA

These are out of scope for this platform. A 10,000-user IDP would be Backstage running in a dedicated k8s cluster with a dedicated DBA team.
