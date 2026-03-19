"""Cost intelligence — Celery beat worker. Polls AWS every 5 minutes."""
import asyncio, logging, statistics
from datetime import date, timedelta
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

logger = logging.getLogger(__name__)
celery_app = Celery("cost", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True,
    task_acks_late=True, worker_prefetch_multiplier=1,
    beat_schedule={"poll-costs": {"task": "cost.poll_all_services", "schedule": crontab(minute="*/5")}},
)


@celery_app.task(name="cost.poll_all_services", bind=True, max_retries=3)
def poll_all_services(self):
    try:
        asyncio.run(_poll())
    except Exception as exc:
        logger.error("Cost poll failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


async def _poll():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.main import fetch_costs_from_aws, detect_anomaly, fire_slack_alert

    engine = create_async_engine(settings.DATABASE_URL, pool_class=NullPool)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    today = date.today()
    window_start = today - timedelta(days=8)

    async with session_maker() as db:
        rows = await db.execute(
            text("SELECT s.id, s.name, t.slug FROM services s JOIN teams t ON t.id=s.team_id WHERE s.deleted_at IS NULL")
        )
        services = rows.fetchall()

    if not services:
        return

    svc_names = [s.name for s in services]
    svc_map = {s.name: s for s in services}
    raw = fetch_costs_from_aws(svc_names, window_start, today)

    async with session_maker() as db:
        for name, daily in raw.items():
            if name not in svc_map:
                continue
            svc = svc_map[name]
            sorted_dates = sorted(daily.keys())
            if not sorted_dates:
                continue

            history = [daily[d] for d in sorted_dates[:-1] if daily[d] > 0]
            today_str = today.isoformat()
            today_amount = daily.get(today_str, daily.get(sorted_dates[-1], 0.0))
            is_anomaly, spike_pct = detect_anomaly(history, today_amount, settings.COST_ANOMALY_STD_DEVS)

            for date_str, amount in daily.items():
                is_today = (date_str == today_str)
                await db.execute(
                    text("INSERT INTO service_cost (service_id, date, amount_usd, anomaly_detected, anomaly_spike_pct) VALUES (:id::uuid, :date, :amount, :anomaly, :spike) ON CONFLICT (service_id, date) DO UPDATE SET amount_usd=:amount, anomaly_detected=:anomaly, anomaly_spike_pct=:spike"),
                    {"id": str(svc.id), "date": date_str, "amount": amount,
                     "anomaly": is_anomaly if is_today else False,
                     "spike": spike_pct if (is_today and is_anomaly) else None},
                )

            if is_anomaly:
                avg = statistics.mean(history) if history else 0.0
                await fire_slack_alert(name, svc.slug, today_amount, avg, spike_pct)
                logger.warning("Cost anomaly: %s today=$%.2f spike=+%.0f%%", name, today_amount, spike_pct)

        await db.commit()
    logger.info("Cost poll complete: %d services", len(services))
