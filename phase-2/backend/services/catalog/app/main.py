"""Nerve IDP — Catalog Service (port 8001)"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routers import services
from app.core.config import settings
from app.core.events import init_redis_streams
from app.core.neo4j import init_neo4j

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Catalog service starting")
    await init_redis_streams()
    await init_neo4j()
    yield

app = FastAPI(title="Nerve Catalog Service", version=settings.APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(services.router, prefix="/api/v1")

@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}
