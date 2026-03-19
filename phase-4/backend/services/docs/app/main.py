"""
Nerve IDP — TechDocs Service (port 8010)

MkDocs-as-code: builds, stores, indexes, and serves TechDocs for every service.

Data flow:
  GitHub Actions pushes to /docs → webhook fires POST /internal/docs/rebuild
  → Docs service clones repo → runs mkdocs build → uploads HTML to S3
  → Indexes content in docs_pages (PostgreSQL tsvector + pgvector embedding)
  → Publishes docs.rebuild_complete to catalog.events (triggers maturity rescore)

Search:
  GET /internal/docs/search?q=...&mode=hybrid
  Hybrid: 0.7 * semantic (pgvector) + 0.3 * fulltext (tsvector)
  Full-text only: tsvector ts_rank
  Semantic only: pgvector cosine similarity

Freshness:
  docs_pages.updated_at tracked per page.
  Maturity scoring checks updated_at > last_deploy_at (anti-gaming).

S3 storage:
  Pages stored at: s3://{S3_BUCKET_TECHDOCS}/{service_name}/{version}/
  Served via pre-signed URLs (1 hour TTL).
  Falls back to DB content if S3 unavailable.
"""
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TechDocs service starting")
    yield


app = FastAPI(title="Nerve TechDocs Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class DocsRebuildPayload(BaseModel):
    service_name: str
    repo_url: str
    commit_sha: str
    triggered_by: Optional[str] = None


class DocsSearchResult(BaseModel):
    service_id: str
    service_name: str
    title: str
    excerpt: str
    score: float
    url: Optional[str] = None
    freshness_days: Optional[int] = None


# ── S3 helpers ────────────────────────────────────────────────
def get_s3_client():
    if not settings.AWS_ACCESS_KEY_ID:
        return None
    try:
        import boto3
        return boto3.client("s3", region_name=settings.AWS_REGION,
                            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)
    except ImportError:
        return None


async def upload_to_s3(local_dir: str, service_name: str, commit_sha: str) -> str:
    """Upload MkDocs build output to S3. Returns the base URL."""
    client = get_s3_client()
    if not client or not settings.S3_BUCKET_TECHDOCS:
        logger.warning("S3 not configured — skipping upload")
        return f"http://localhost:8010/docs/{service_name}"

    prefix = f"{service_name}/{commit_sha[:8]}"
    for root, _, files in os.walk(local_dir):
        for fname in files:
            local_path = os.path.join(root, fname)
            s3_key = f"{prefix}/{os.path.relpath(local_path, local_dir)}"
            content_type = "text/html" if fname.endswith(".html") else "application/octet-stream"
            client.upload_file(local_path, settings.S3_BUCKET_TECHDOCS, s3_key,
                               ExtraArgs={"ContentType": content_type})

    return f"https://{settings.S3_BUCKET_TECHDOCS}.s3.amazonaws.com/{prefix}/index.html"


# ── MkDocs build ─────────────────────────────────────────────
async def build_techdocs(repo_url: str, service_name: str, commit_sha: str) -> tuple[str, str]:
    """
    Clone repo, run mkdocs build, return (output_dir, extracted_content).
    Returns (path to built HTML, raw markdown content for indexing).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "repo")
        build_dir = os.path.join(tmpdir, "build")

        # Clone with shallow fetch
        token = settings.GITHUB_TOKEN
        auth_url = repo_url.replace("https://", f"https://{token}@") if token else repo_url

        result = subprocess.run(
            ["git", "clone", "--depth", "1", auth_url, repo_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")

        # Check for docs dir and mkdocs.yml
        docs_dir = os.path.join(repo_dir, "docs")
        mkdocs_yml = os.path.join(repo_dir, "mkdocs.yml")

        if not os.path.exists(docs_dir):
            # Create minimal docs if none exist
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "index.md"), "w") as f:
                f.write(f"# {service_name}\n\nDocs not yet created. Add /docs/index.md to the repo.\n")

        if not os.path.exists(mkdocs_yml):
            with open(mkdocs_yml, "w") as f:
                f.write(f"site_name: {service_name}\ndocs_dir: docs\nsite_dir: {build_dir}\n")

        # Run mkdocs build
        result = subprocess.run(
            ["mkdocs", "build", "--site-dir", build_dir],
            capture_output=True, text=True, timeout=120, cwd=repo_dir,
        )
        if result.returncode != 0:
            logger.warning("mkdocs build warning: %s", result.stderr)

        # Extract markdown content for indexing
        content_parts = []
        for root, _, files in os.walk(docs_dir):
            for fname in files:
                if fname.endswith(".md"):
                    with open(os.path.join(root, fname)) as f:
                        content_parts.append(f.read())

        return build_dir, "\n\n".join(content_parts)


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.post("/internal/docs/rebuild", status_code=202)
async def trigger_rebuild(payload: DocsRebuildPayload, db: AsyncSession = Depends(get_db)):
    """Called from GitHub Actions CI when /docs changes."""
    # Resolve service
    result = await db.execute(
        text("SELECT id FROM services WHERE name=:name AND deleted_at IS NULL"),
        {"name": payload.service_name},
    )
    svc = result.fetchone()
    if not svc:
        logger.warning("Docs rebuild for unknown service: %s", payload.service_name)
        return {"status": "skipped", "reason": "service_not_found"}

    try:
        build_dir, content = await build_techdocs(
            payload.repo_url, payload.service_name, payload.commit_sha
        )
        doc_url = await upload_to_s3(build_dir, payload.service_name, payload.commit_sha)

        # Upsert docs_pages record
        title = f"{payload.service_name} Documentation"
        await db.execute(
            text("""
                INSERT INTO docs_pages (service_id, title, content, url, built_at, updated_at)
                VALUES (:svc_id::uuid, :title, :content, :url, NOW(), NOW())
                ON CONFLICT (service_id) DO UPDATE
                SET title=:title, content=:content, url=:url, updated_at=NOW()
            """),
            {"svc_id": str(svc.id), "title": title, "content": content[:50000], "url": doc_url},
        )
        await db.commit()

        # Generate embedding for semantic search
        from app.core.retrieval import store_techdocs_embedding
        page_r = await db.execute(
            text("SELECT id FROM docs_pages WHERE service_id=:id::uuid"), {"id": str(svc.id)}
        )
        page = page_r.fetchone()
        if page:
            await store_techdocs_embedding(str(page.id), content, db)

        # Publish to catalog.events — triggers maturity rescore
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await redis.xadd("catalog.events", {
                "type": "docs.rebuild_complete", "version": "1",
                "payload": json.dumps({"service_id": str(svc.id), "service_name": payload.service_name,
                                       "commit_sha": payload.commit_sha, "doc_url": doc_url}),
                "timestamp": str(int(time.time() * 1000)),
            }, maxlen=10_000, approximate=True)
        finally:
            await redis.aclose()

        logger.info("TechDocs rebuilt: %s → %s", payload.service_name, doc_url)
        return {"status": "built", "service_id": str(svc.id), "url": doc_url}

    except Exception as exc:
        logger.error("TechDocs build failed for %s: %s", payload.service_name, exc)
        raise HTTPException(status_code=500, detail={"error": "build_failed", "message": str(exc)})


@app.get("/internal/docs/{service_id}")
async def get_service_docs(service_id: str, db: AsyncSession = Depends(get_db)):
    """Serve TechDocs page — redirects to S3 URL or returns content."""
    result = await db.execute(
        text("SELECT title, content, url FROM docs_pages WHERE service_id=:id::uuid"),
        {"id": service_id},
    )
    page = result.fetchone()
    if not page:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "No docs built yet."})

    if page.url and settings.AWS_ACCESS_KEY_ID:
        # Generate pre-signed URL
        client = get_s3_client()
        if client:
            bucket = settings.S3_BUCKET_TECHDOCS
            key = page.url.split(f"{bucket}.s3.amazonaws.com/")[-1]
            presigned = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600)
            return {"url": presigned, "title": page.title}

    return {"title": page.title, "content": page.content[:10000]}


@app.get("/internal/docs/search")
async def search_docs(q: str, mode: str = "hybrid", limit: int = 10,
                      db: AsyncSession = Depends(get_db)) -> list[DocsSearchResult]:
    """Hybrid full-text + semantic search across all TechDocs."""
    from app.core.retrieval import search_techdocs
    results = await search_techdocs(query=q, service_name=None, limit=limit, db=db)

    output = []
    for r in results:
        svc = await db.execute(text("SELECT id FROM services WHERE name=:name"), {"name": r["service_name"]})
        svc_row = svc.fetchone()
        output.append(DocsSearchResult(
            service_id=str(svc_row.id) if svc_row else "",
            service_name=r["service_name"],
            title=r["title"],
            excerpt=r["excerpt"],
            score=r.get("score", 0.0),
        ))
    return output
