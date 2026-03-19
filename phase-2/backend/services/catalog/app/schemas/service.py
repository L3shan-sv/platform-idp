"""Catalog service — Pydantic schemas."""
import uuid, re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator

class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    team: str = ""
    language: str
    version: Optional[str] = None
    repo_url: Optional[str] = None
    health_status: str
    compliance_score: int
    maturity_score: int
    error_budget_consumed: float
    deploy_frozen: bool
    replica_count: int
    template_version: Optional[str] = None
    last_deploy_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    @field_validator("team", mode="before")
    @classmethod
    def resolve_team(cls, v):
        return v.slug if hasattr(v, "slug") else (v or "")

class CatalogSummary(BaseModel):
    total_services: int
    healthy: int
    degraded: int
    frozen: int
    avg_maturity_score: float
    critical_cves: int

class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]
    total: int
    page: int
    limit: int
    summary: Optional[CatalogSummary] = None

class ServiceRegistration(BaseModel):
    name: str
    team: str
    language: str
    repo_url: Optional[HttpUrl] = None
    description: Optional[str] = None
    upstream_dependencies: list[uuid.UUID] = []

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r"^[a-z][a-z0-9-]{2,62}$", v):
            raise ValueError("name must be lowercase letters/numbers/hyphens, 3-63 chars")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v):
        if v not in {"python","go","typescript","rust","java"}:
            raise ValueError("invalid language")
        return v

class ServiceUpdate(BaseModel):
    version: Optional[str] = None
    replica_count: Optional[int] = None
    health_status: Optional[str] = None
    description: Optional[str] = None
