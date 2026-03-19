"""Audit log middleware — writes every state-changing request to audit_log."""
import logging, uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)
AUDITED_METHODS = {"POST", "PATCH", "DELETE", "PUT"}
EXCLUDED_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/redoc"}

class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in AUDITED_METHODS:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in EXCLUDED_PATHS):
            return await call_next(request)
        response = await call_next(request)
        actor = "anonymous"
        if hasattr(request.state, "current_user"):
            actor = request.state.current_user.username
        outcome = "success" if response.status_code < 400 else "blocked" if response.status_code in (403, 423) else "failure"
        logger.info("AUDIT actor=%s method=%s path=%s status=%d outcome=%s",
                    actor, request.method, request.url.path, response.status_code, outcome)
        return response
