"""
Nerve IDP — FastAPI Gateway (Phase 2)
Extends Phase 1 gateway with catalog, deploy, scaffold, iac, and pipeline routers.
"""
import asyncio, logging, uuid
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import make_asgi_app
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1.routers import health, catalog, deploy, scaffold, iac, pipelines
from app.core.config import settings
from app.core.database import async_session_maker
from app.middleware.audit import AuditMiddleware
from app.middleware.request_id import RequestIdMiddleware

logger = logging.getLogger(__name__)

def setup_telemetry():
    resource = Resource.create({"service.name": "nerve-gateway", "service.version": settings.APP_VERSION})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)))
    trace.set_tracer_provider(tp)

async def wait_for_opa(max_retries=30, delay=1.0):
    async with httpx.AsyncClient() as client:
        for i in range(max_retries):
            try:
                r = await client.get(f"{settings.OPA_URL}/health", timeout=2.0)
                if r.status_code == 200:
                    logger.info("OPA ready"); return
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(delay)
    raise RuntimeError("OPA not ready")

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_telemetry()
    if settings.ENVIRONMENT != "test":
        await wait_for_opa()
    async with async_session_maker() as s:
        await s.execute("SELECT 1")
    yield

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Nerve IDP API", version=settings.APP_VERSION,
              openapi_url="/api/v1/openapi.json", docs_url="/docs", redoc_url="/redoc", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RequestIdMiddleware)
app.add_middleware(AuditMiddleware)
app.mount("/metrics", make_asgi_app())
FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/health/ready,/metrics")

@app.exception_handler(Exception)
async def global_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception("Unhandled exception [%s]", rid)
    return JSONResponse(status_code=500, content={"error":"internal_server_error","message":"Unexpected error.","request_id":rid})

API = "/api/v1"
app.include_router(health.router, prefix=API, tags=["health"])
app.include_router(catalog.router, prefix=API, tags=["catalog"])
app.include_router(deploy.router, prefix=API, tags=["deploy"])
app.include_router(scaffold.router, prefix=API, tags=["scaffold"])
app.include_router(iac.router, prefix=API, tags=["iac"])
app.include_router(pipelines.router, prefix=API, tags=["pipelines"])
