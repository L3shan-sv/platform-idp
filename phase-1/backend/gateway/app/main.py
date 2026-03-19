"""
Nerve IDP — FastAPI Gateway
Single entry point for all API traffic.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import make_asgi_app
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1.routers import health
from app.core.config import settings
from app.core.database import async_session_maker
from app.middleware.audit import AuditMiddleware
from app.middleware.request_id import RequestIdMiddleware

logger = logging.getLogger(__name__)


def setup_telemetry() -> None:
    resource = Resource.create({
        "service.name": "nerve-gateway",
        "service.version": settings.APP_VERSION,
        "deployment.environment": settings.ENVIRONMENT,
    })
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)
    HTTPXClientInstrumentor().instrument()
    logger.info("OTel configured → %s", settings.OTEL_EXPORTER_OTLP_ENDPOINT)


async def wait_for_opa(max_retries: int = 30, delay: float = 1.0) -> None:
    """Block startup until OPA sidecar is healthy. Prevents fail-open."""
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            try:
                r = await client.get(f"{settings.OPA_URL}/health", timeout=2.0)
                if r.status_code == 200:
                    logger.info("OPA sidecar ready")
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            logger.warning("OPA not ready (%d/%d)", attempt + 1, max_retries)
            await asyncio.sleep(delay)
    raise RuntimeError("OPA sidecar not ready — refusing to start")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Nerve IDP Gateway starting — v%s", settings.APP_VERSION)
    setup_telemetry()
    if settings.ENVIRONMENT != "test":
        await wait_for_opa()
    async with async_session_maker() as session:
        await session.execute("SELECT 1")
    logger.info("Database connection via PgBouncer: OK")
    yield
    logger.info("Nerve IDP Gateway shutting down")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Nerve IDP API",
    version=settings.APP_VERSION,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(AuditMiddleware)

app.mount("/metrics", make_asgi_app())
FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/health/ready,/metrics")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception("Unhandled exception [request_id=%s]", request_id)
    return JSONResponse(status_code=500, content={
        "error": "internal_server_error",
        "message": "An unexpected error occurred.",
        "request_id": request_id,
    })


API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX, tags=["health"])
