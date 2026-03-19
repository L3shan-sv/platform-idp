import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Service(Base):
    __tablename__ = "services"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    language: Mapped[str] = mapped_column(String)
    health_status: Mapped[str] = mapped_column(String, default="unknown")
    compliance_score: Mapped[int] = mapped_column(SmallInteger, default=0)
    error_budget_consumed: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    deploy_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    frozen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    frozen_reason: Mapped[Optional[str]] = mapped_column(Text)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class DeployHistory(Base):
    __tablename__ = "deploy_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("services.id"))
    version: Mapped[str] = mapped_column(String)
    environment: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    compliance_score: Mapped[Optional[int]] = mapped_column(SmallInteger)
    actor: Mapped[str] = mapped_column(String)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deploy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deploy_history.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("services.id"))
    check_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    score: Mapped[int] = mapped_column(SmallInteger)
    weight: Mapped[int] = mapped_column(SmallInteger)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    fix_url: Mapped[Optional[str]] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
