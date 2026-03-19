# ADR-002: Neo4j for Blast Radius Graph Traversal

**Status:** Accepted  
**Date:** 2024-01-01

---

## Decision

Use **Neo4j 5** for service dependency graph traversal.

---

## Why not PostgreSQL

A 5-hop recursive CTE on 2,000 services with 10,000 edges takes 800ms–2s. The same query in Neo4j with proper indexes takes ~18ms. The query must run on every deploy request — 800ms is unacceptable as a deploy gate.

```cypher
MATCH path = (start:Service {id: $id})-[:DEPENDS_ON|USES*1..5]->(dep)
RETURN dep, length(path) AS hop_distance
```

---

## Consistency model

Neo4j is authoritative for traversal only. PostgreSQL is authoritative for service metadata. A reconciliation Celery task runs every 5 minutes, diffs both stores, and corrects any drift. Drift is logged to `neo4j_sync_log`.

---

## Mandatory indexes

Without these, traversal degrades to full graph scan:
```cypher
CREATE INDEX service_team_index FOR (s:Service) ON (s.team_id);
CREATE INDEX service_health_index FOR (s:Service) ON (s.health_status);
```

Created in `infra/docker/neo4j/init.cypher`.

---

## Redis cache

Traversal results cached at `blast_radius:{service_id}:{hops}` with 60s TTL. Cache hit = <1ms. Invalidated on catalog change events.

---

## Capacity benchmarks

| Services | Edges | Neo4j (5 hops) | PostgreSQL CTE |
|---|---|---|---|
| 300 | 1,500 | ~8ms | ~50ms |
| 2,000 | 10,000 | ~18ms | ~800ms |
| 2,000 + cache | 10,000 | <1ms | N/A |
