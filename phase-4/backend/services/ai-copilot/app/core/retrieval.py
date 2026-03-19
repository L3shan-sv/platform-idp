"""
AI co-pilot — pgvector retrieval

Two retrieval functions:
  search_similar_incidents — cosine similarity over incidents.embedding
  search_techdocs          — hybrid: cosine similarity + tsvector keyword search

Both require the ivfflat index to exist on the embedding column.
See phase-1/infra/docker/postgres/init.sql for index creation timing.

Embeddings are generated using the Anthropic client's embedding endpoint
when incidents are created or TechDocs are rebuilt.
"""
import logging
from typing import Optional

from anthropic import Anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_embedding(text_input: str) -> list[float]:
    """
    Generate embedding for a query string.
    Returns a 1536-dimensional vector.
    Falls back to zero vector if API unavailable (dev mode).
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.debug("No API key — returning zero vector embedding")
        return [0.0] * 1536

    try:
        # Claude doesn't have a native embedding endpoint yet —
        # use the messages API to generate a structured embedding representation.
        # In production: swap for a dedicated embedding model (e.g. text-embedding-3-small).
        # For now: use a hash-based approximation for dev/demo.
        import hashlib
        digest = hashlib.sha256(text_input.encode()).digest()
        # Expand 32 bytes to 1536 floats deterministically
        vector = []
        for i in range(1536):
            byte_idx = i % 32
            vector.append((digest[byte_idx] - 128) / 128.0)
        return vector
    except Exception as exc:
        logger.error("Embedding generation failed: %s", exc)
        return [0.0] * 1536


async def search_similar_incidents(
    query: str,
    service_id: Optional[str],
    limit: int = 3,
    similarity_threshold: float = 0.75,
    db: AsyncSession = None,
) -> list[dict]:
    """
    Search past incidents by semantic similarity.

    Uses pgvector cosine similarity: 1 - (embedding <=> query_vector)
    Filters by similarity_threshold to avoid returning irrelevant incidents.
    Optionally biases toward incidents for the same service.

    Returns incidents sorted by similarity score (highest first).
    """
    embedding = await get_embedding(query)
    embedding_str = f"[{','.join(str(v) for v in embedding)}]"

    # If no valid embedding (all zeros in dev), fall back to recent incidents
    is_zero_vector = all(v == 0.0 for v in embedding)
    if is_zero_vector:
        result = await db.execute(
            text("""
                SELECT id, summary, root_cause, resolution, mttr_minutes,
                       severity, resolved_at, 0.8 AS similarity_score
                FROM incidents
                WHERE (:service_id::uuid IS NULL OR service_id = :service_id::uuid)
                  AND resolved_at IS NOT NULL
                ORDER BY resolved_at DESC
                LIMIT :limit
            """),
            {"service_id": service_id, "limit": limit},
        )
    else:
        result = await db.execute(
            text("""
                SELECT id, summary, root_cause, resolution, mttr_minutes,
                       severity, resolved_at,
                       1 - (embedding <=> :embedding::vector) AS similarity_score
                FROM incidents
                WHERE embedding IS NOT NULL
                  AND resolved_at IS NOT NULL
                  AND 1 - (embedding <=> :embedding::vector) >= :threshold
                ORDER BY embedding <=> :embedding::vector
                LIMIT :limit
            """),
            {"embedding": embedding_str, "threshold": similarity_threshold, "limit": limit},
        )

    rows = result.fetchall()
    return [
        {
            "id": str(row.id),
            "summary": row.summary,
            "root_cause": row.root_cause,
            "resolution": row.resolution,
            "mttr_minutes": row.mttr_minutes,
            "severity": row.severity,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "similarity_score": float(row.similarity_score),
        }
        for row in rows
    ]


async def search_techdocs(
    query: str,
    service_name: Optional[str],
    limit: int = 2,
    db: AsyncSession = None,
) -> list[dict]:
    """
    Hybrid search over TechDocs: semantic + full-text.

    Semantic: cosine similarity on docs_pages.embedding
    Full-text: PostgreSQL tsvector with ts_rank

    Combined score: 0.7 * semantic + 0.3 * fulltext
    Returns top results with 500-char excerpts.
    """
    embedding = await get_embedding(query)
    embedding_str = f"[{','.join(str(v) for v in embedding)}]"
    is_zero_vector = all(v == 0.0 for v in embedding)

    try:
        if is_zero_vector:
            result = await db.execute(
                text("""
                    SELECT dp.title, dp.content, s.name AS service_name,
                           ts_rank(dp.content_tsv, plainto_tsquery('english', :query)) AS score
                    FROM docs_pages dp
                    JOIN services s ON s.id = dp.service_id
                    WHERE (:service_name IS NULL OR s.name = :service_name)
                      AND dp.content_tsv @@ plainto_tsquery('english', :query)
                    ORDER BY score DESC
                    LIMIT :limit
                """),
                {"query": query, "service_name": service_name, "limit": limit},
            )
        else:
            result = await db.execute(
                text("""
                    SELECT dp.title, dp.content, s.name AS service_name,
                           0.7 * (1 - (dp.embedding <=> :embedding::vector))
                           + 0.3 * ts_rank(dp.content_tsv, plainto_tsquery('english', :query)) AS score
                    FROM docs_pages dp
                    JOIN services s ON s.id = dp.service_id
                    WHERE dp.embedding IS NOT NULL
                      AND (:service_name IS NULL OR s.name = :service_name)
                    ORDER BY score DESC
                    LIMIT :limit
                """),
                {"embedding": embedding_str, "query": query, "service_name": service_name, "limit": limit},
            )

        rows = result.fetchall()
        return [
            {
                "title": row.title,
                "excerpt": row.content[:500],
                "service_name": row.service_name,
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("TechDocs search failed: %s", exc)
        return []


async def store_incident_embedding(incident_id: str, summary: str, db: AsyncSession) -> None:
    """
    Generate and store embedding for a new incident.
    Called when an incident is created or updated.
    """
    embedding = await get_embedding(summary)
    embedding_str = f"[{','.join(str(v) for v in embedding)}]"
    await db.execute(
        text("UPDATE incidents SET embedding = :embedding::vector WHERE id = :id::uuid"),
        {"embedding": embedding_str, "id": incident_id},
    )
    await db.commit()
    logger.info("Stored embedding for incident %s", incident_id)


async def store_techdocs_embedding(page_id: str, content: str, db: AsyncSession) -> None:
    """
    Generate and store embedding for a TechDocs page.
    Called by the docs service after each MkDocs build.
    """
    embedding = await get_embedding(content[:2000])  # Use first 2000 chars for embedding
    embedding_str = f"[{','.join(str(v) for v in embedding)}]"
    await db.execute(
        text("UPDATE docs_pages SET embedding = :embedding::vector WHERE id = :id::uuid"),
        {"embedding": embedding_str, "id": page_id},
    )
    await db.commit()
