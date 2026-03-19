"""
Nerve IDP — Cost Intelligence Service (port 8006)

Per-service cloud spend with anomaly detection and team rollup.

Data flow:
  Celery beat (every 5 min) → AWS Cost Explorer API
  → service_cost table (daily granularity, tagged by nerve:service)
  → Anomaly detection: rolling 7-day avg + 2σ threshold
  → Slack alert on spike

Cost attribution:
  Services tagged at scaffold time: nerve:service={name}, nerve:team={team}
  AWS Cost Explorer groups by this tag.

Anomaly detection algorithm:
  1. Compute 7-day rolling mean and stdev
  2. Flag if today > mean + (2 * stdev)
  3. Requires >= 3 data points (returns False on insufficient history)

Team rollup:
  Sum all service costs per team.
  Budget vs actual from team_quotas.cost_usd.
  EOM forecast: linear extrapolation from MTD daily average.
"""
import logging, statistics
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, async_session_maker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Cost intelligence service starting")
    yield


app = FastAPI(title="Nerve Cost Intelligence Service", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class ServiceCostResponse(BaseModel):
    service_id: str
    service_name: str
    current_month_usd: float
    daily_average_usd: float
    seven_day_average_usd: float
    anomaly_detected: bool
    anomaly_spike_percent: float
    trend: list[dict]


class TeamCostResponse(BaseModel):
    team_id: str
    team_name: str
    budget_usd: float
    actual_usd: float
    forecast_eom_usd: float
    variance_percent: float
    services: list[dict]


# ── AWS Cost Explorer ─────────────────────────────────────────
def fetch_costs_from_aws(service_names: list[str], start: date, end: date) -> dict[str, dict[str, float]]:
    """Pull daily costs per service. Falls back to mock data if no AWS credentials."""
    if not settings.AWS_ACCESS_KEY_ID:
        return _mock_costs(service_names, start, end)
    try:
        import boto3
        ce = boto3.client("ce", region_name=settings.AWS_REGION,
                          aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                          aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)
        costs: dict[str, dict[str, float]] = {n: {} for n in service_names}
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="DAILY",
            Filter={"Tags": {"Key": "nerve:service", "Values": service_names, "MatchOptions": ["EQUALS"]}},
            GroupBy=[{"Type": "TAG", "Key": "nerve:service"}],
            Metrics=["UnblendedCost"],
        )
        for result in r.get("ResultsByTime", []):
            date_str = result["TimePeriod"]["Start"]
            for group in result.get("Groups", []):
                svc = group["Keys"][0].replace("nerve:service$", "")
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                if svc in costs:
                    costs[svc][date_str] = amount
        return costs
    except Exception as exc:
        logger.error("AWS Cost Explorer error: %s", exc)
        return {n: {} for n in service_names}


def _mock_costs(service_names: list[str], start: date, end: date) -> dict[str, dict[str, float]]:
    """Realistic mock data for dev — includes occasional spike for anomaly demo."""
    import random, hashlib
    result = {}
    for name in service_names:
        seed = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
        random.seed(seed)
        base = random.uniform(5.0, 80.0)
        daily = {}
        current = start
        while current < end:
            spike = 3.5 if current.day == 15 else 1.0  # Spike on 15th for demo
            daily[current.isoformat()] = round(base * spike + random.gauss(0, base * 0.08), 4)
            current += timedelta(days=1)
        result[name] = daily
    return result


# ── Anomaly detection ─────────────────────────────────────────
def detect_anomaly(history: list[float], current: float, threshold_std: float = 2.0) -> tuple[bool, float]:
    """Returns (is_anomaly, spike_percent_above_average). Needs >= 3 data points."""
    if len(history) < 3 or not current:
        return False, 0.0
    avg = statistics.mean(history)
    if avg <= 0:
        return False, 0.0
    std = statistics.stdev(history) if len(history) > 1 else 0.0
    is_anomaly = current > avg + (threshold_std * std)
    return is_anomaly, round((current - avg) / avg * 100, 2)


async def fire_slack_alert(service_name: str, team: str, current: float, avg: float, spike: float) -> None:
    if not settings.SLACK_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(settings.SLACK_WEBHOOK_URL, json={
                "text": f":money_with_wings: *Cost anomaly* — `{service_name}` (team: {team})",
                "attachments": [{"color": "warning", "fields": [
                    {"title": "Today", "value": f"${current:.2f}", "short": True},
                    {"title": "7-day avg", "value": f"${avg:.2f}", "short": True},
                    {"title": "Spike", "value": f"+{spike:.0f}% above average"},
                ]}],
            })
    except Exception as exc:
        logger.warning("Slack alert failed: %s", exc)


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/internal/cost/services/{service_id}")
async def get_service_cost(service_id: str, window: str = "30d",
                           db: AsyncSession = Depends(get_db)) -> ServiceCostResponse:
    from sqlalchemy import text as sql_text
    svc = await db.execute(
        sql_text("SELECT name FROM services WHERE id=:id::uuid AND deleted_at IS NULL"), {"id": service_id})
    svc_row = svc.fetchone()
    if not svc_row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    window_days = int(window.replace("d", ""))
    since = date.today() - timedelta(days=window_days)

    costs_r = await db.execute(
        sql_text("SELECT date, amount_usd, anomaly_detected, anomaly_spike_pct FROM service_cost WHERE service_id=:id::uuid AND date >= :since ORDER BY date ASC"),
        {"id": service_id, "since": since},
    )
    costs = costs_r.fetchall()

    if not costs:
        return ServiceCostResponse(service_id=service_id, service_name=svc_row.name,
                                   current_month_usd=0.0, daily_average_usd=0.0, seven_day_average_usd=0.0,
                                   anomaly_detected=False, anomaly_spike_percent=0.0, trend=[])

    amounts = [float(c.amount_usd) for c in costs]
    month_start = date.today().replace(day=1)
    mtd = sum(float(c.amount_usd) for c in costs if c.date >= month_start)
    seven_day = amounts[-7:] if len(amounts) >= 7 else amounts
    today_cost = costs[-1]

    return ServiceCostResponse(
        service_id=service_id, service_name=svc_row.name,
        current_month_usd=round(mtd, 2),
        daily_average_usd=round(statistics.mean(amounts), 2),
        seven_day_average_usd=round(statistics.mean(seven_day), 2),
        anomaly_detected=today_cost.anomaly_detected,
        anomaly_spike_percent=float(today_cost.anomaly_spike_pct or 0.0),
        trend=[{"date": c.date.isoformat(), "amount_usd": float(c.amount_usd)} for c in costs],
    )


@app.get("/internal/cost/teams/{team_id}")
async def get_team_cost(team_id: str, db: AsyncSession = Depends(get_db)) -> TeamCostResponse:
    from sqlalchemy import text as sql_text
    team_r = await db.execute(sql_text("SELECT name, budget_usd FROM teams WHERE id=:id::uuid"), {"id": team_id})
    team = team_r.fetchone()
    if not team:
        raise HTTPException(status_code=404, detail={"error": "not_found"})

    quota_r = await db.execute(sql_text("SELECT cost_usd FROM team_quotas WHERE team_id=:id::uuid"), {"id": team_id})
    quota_row = quota_r.fetchone()
    budget = float(quota_row.cost_usd) if quota_row else 0.0

    month_start = date.today().replace(day=1)
    svcs_r = await db.execute(
        sql_text("SELECT s.id, s.name, COALESCE(SUM(sc.amount_usd),0) as mtd FROM services s LEFT JOIN service_cost sc ON sc.service_id=s.id AND sc.date >= :ms WHERE s.team_id=:tid::uuid AND s.deleted_at IS NULL GROUP BY s.id, s.name"),
        {"tid": team_id, "ms": month_start},
    )
    svcs = svcs_r.fetchall()
    total = sum(float(s.mtd) for s in svcs)
    days_elapsed = date.today().day
    days_in_month = (date.today().replace(month=date.today().month % 12 + 1, day=1) - timedelta(days=1)).day
    forecast = (total / days_elapsed * days_in_month) if days_elapsed > 0 else 0.0

    return TeamCostResponse(
        team_id=team_id, team_name=team.name, budget_usd=budget, actual_usd=round(total, 2),
        forecast_eom_usd=round(forecast, 2),
        variance_percent=round((total - budget) / budget * 100, 2) if budget > 0 else 0.0,
        services=[{"service_id": str(s.id), "service_name": s.name, "current_month_usd": round(float(s.mtd), 2)} for s in svcs],
    )
