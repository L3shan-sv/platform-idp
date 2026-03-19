"""
DORA Metrics Engine — Celery worker.

Google 2023 DevOps tier thresholds:
  Deployment frequency: Elite >= 1/day, High >= 1/week, Medium >= 1/month, Low < 1/month
  Lead time:           Elite < 1h, High < 1 week, Medium < 1 month, Low > 1 month
  MTTR:                Elite < 1h, High < 1 day, Medium < 1 week, Low > 1 week
  Change failure rate: Elite 0-5%, High 5-10%, Medium 10-15%, Low > 15%
"""
import asyncio, logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from celery import Celery
from app.core.config import settings

logger = logging.getLogger(__name__)
celery_app = Celery("dora", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(task_serializer="json", accept_content=["json"], timezone="UTC",
                       enable_utc=True, task_acks_late=True, worker_prefetch_multiplier=1)

def get_dora_tier_freq(d: float) -> str:
    return "elite" if d >= 1.0 else "high" if d >= 1/7 else "medium" if d >= 1/30 else "low"

def get_dora_tier_lead(h: float) -> str:
    return "elite" if h < 1 else "high" if h < 168 else "medium" if h < 720 else "low"

def get_dora_tier_mttr(h: float) -> str:
    return "elite" if h < 1 else "high" if h < 24 else "medium" if h < 168 else "low"

def get_dora_tier_cfr(p: float) -> str:
    return "elite" if p <= 5 else "high" if p <= 10 else "medium" if p <= 15 else "low"

@celery_app.task(name="dora.compute_service_metrics", bind=True, max_retries=3, default_retry_delay=30)
def compute_service_dora_metrics(self, service_id: str, window_days: int = 30):
    try:
        asyncio.run(_compute(service_id, window_days))
    except Exception as exc:
        logger.error("DORA failed for %s: %s", service_id, exc)
        raise self.retry(exc=exc)

async def _compute(service_id: str, window_days: int):
    from sqlalchemy import select, func, and_
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.models.pipeline import PipelineRun

    engine = create_async_engine(settings.DATABASE_URL, pool_class=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    window_start = datetime.now(timezone.utc) - timedelta(days=window_days)

    async with session_maker() as db:
        deploys = await db.scalar(
            select(func.count()).where(
                PipelineRun.service_id == UUID(service_id),
                PipelineRun.status == "succeeded",
                PipelineRun.branch == "main",
                PipelineRun.started_at >= window_start,
            )
        ) or 0
        deploys_per_day = deploys / window_days
        failed = await db.scalar(
            select(func.count()).where(
                PipelineRun.service_id == UUID(service_id),
                PipelineRun.status == "failed",
                PipelineRun.started_at >= window_start,
            )
        ) or 0
        cfr = (failed / deploys * 100) if deploys > 0 else 0.0

    logger.info("DORA: service=%s freq=%.2f/day cfr=%.1f%%", service_id, deploys_per_day, cfr)
    return {"deployment_frequency": deploys_per_day, "tier": get_dora_tier_freq(deploys_per_day),
            "change_failure_rate": cfr, "cfr_tier": get_dora_tier_cfr(cfr)}
