"""
Nerve IDP — AI Ops Co-pilot (port 8009)

Activates during incidents. Surfaces root cause analysis, similar past incidents,
and one-click remediation actions.

Architecture:
  1. Receive chat message + incident context
  2. pgvector similarity search over past incidents (top-3, threshold 0.75)
  3. Semantic search over TechDocs for relevant runbook excerpts (top-2)
  4. Build context window (capped at 4,000 tokens to prevent bloat)
  5. Call Claude API with structured system prompt
  6. Parse response into RCA + similar incidents + recommended actions

Context window management:
  Token budget: 4,000 tokens for context before Claude call
  Incidents trimmed from least-similar to most-similar if over budget
  TechDocs excerpts capped at 500 tokens each

pgvector index requirement:
  The ivfflat index on incidents.embedding must exist before similarity search.
  Create AFTER seeding with real incident data:
    VACUUM ANALYZE incidents;
    CREATE INDEX idx_incidents_embedding ON incidents
      USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

Incident timeline:
  GET /internal/ai/incidents/{id}/timeline
  Stitches events from audit_log: deploy, alert_fired, freeze, unfreeze, runbook_executed
  Used by the co-pilot to show "what happened and when" during an incident
"""
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.retrieval import search_similar_incidents, search_techdocs

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI co-pilot service starting")
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — AI co-pilot will return mock responses")
    yield


app = FastAPI(title="Nerve AI Co-pilot", version=settings.APP_VERSION, lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────
class IncidentContext(BaseModel):
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    error_rate: Optional[float] = None
    recent_deploys: list[dict] = []
    active_alerts: list[str] = []
    burn_rate: Optional[float] = None
    budget_consumed: Optional[float] = None


class ConversationMessage(BaseModel):
    role: str  # user | assistant
    content: str


class AiChatRequest(BaseModel):
    message: str
    incident_context: Optional[IncidentContext] = None
    conversation_history: list[ConversationMessage] = []
    max_similar_incidents: int = 3


class SimilarIncident(BaseModel):
    incident_id: str
    similarity_score: float
    summary: str
    root_cause: Optional[str]
    resolution: Optional[str]
    resolved_at: Optional[datetime]
    mttr_minutes: Optional[int]


class RecommendedAction(BaseModel):
    label: str
    action_type: str
    parameters: dict = {}
    estimated_mttr_minutes: Optional[int] = None


class AiChatResponse(BaseModel):
    message: str
    root_cause_analysis: Optional[dict] = None
    similar_incidents: list[SimilarIncident] = []
    recommended_actions: list[RecommendedAction] = []
    tokens_used: int = 0


# ── System prompt ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are the Nerve IDP AI ops co-pilot. You help SRE and platform engineering teams diagnose and resolve production incidents.

You receive:
- A message from the engineer
- Current incident context (service name, error rate, burn rate, budget consumed, recent deploys)
- Similar past incidents retrieved via semantic search
- Relevant runbook excerpts

Your response must be structured JSON with these fields:
{
  "message": "conversational response to the engineer",
  "root_cause_analysis": {
    "summary": "one-sentence root cause",
    "confidence": 0.0-1.0,
    "contributing_factors": ["factor1", "factor2"]
  },
  "recommended_actions": [
    {
      "label": "Rollback to v1.8.3",
      "action_type": "rollback|scale|restart_pod|execute_runbook|open_url",
      "parameters": {},
      "estimated_mttr_minutes": 4
    }
  ]
}

Guidelines:
- Be direct and specific. Name the likely root cause with a confidence percentage.
- Reference similar past incidents by their MTTR and resolution.
- Prioritize actionable recommendations over analysis.
- If a Critical CVE caused the incident, flag it prominently.
- If the error budget is exhausted, recommend against non-emergency deploys.
- Keep the message field conversational and under 3 sentences.
"""


def build_context_window(
    incident_context: Optional[IncidentContext],
    similar_incidents: list[dict],
    techdocs_excerpts: list[dict],
    max_tokens: int = 4000,
) -> str:
    """
    Build context string for Claude. Caps at max_tokens.
    Trims incidents from least-similar to most-similar if over budget.
    """
    parts = []

    if incident_context:
        ctx = incident_context.model_dump(exclude_none=True)
        parts.append(f"## Current Incident Context\n{json.dumps(ctx, indent=2)}")

    if similar_incidents:
        parts.append("## Similar Past Incidents")
        for inc in similar_incidents:
            parts.append(
                f"- [{inc['similarity_score']:.0%} match] {inc['summary']}\n"
                f"  Root cause: {inc.get('root_cause', 'Unknown')}\n"
                f"  Resolution: {inc.get('resolution', 'Unknown')}\n"
                f"  MTTR: {inc.get('mttr_minutes', 'Unknown')} minutes"
            )

    if techdocs_excerpts:
        parts.append("## Relevant Runbook Excerpts")
        for doc in techdocs_excerpts[:2]:
            parts.append(f"### {doc['title']}\n{doc['excerpt'][:500]}")

    full_context = "\n\n".join(parts)

    # Rough token estimation: 4 chars ≈ 1 token
    estimated_tokens = len(full_context) // 4
    if estimated_tokens > max_tokens:
        # Trim by removing least-similar incidents first
        logger.warning("Context window too large (%d tokens) — trimming incidents", estimated_tokens)
        if len(similar_incidents) > 1:
            return build_context_window(
                incident_context, similar_incidents[:-1], techdocs_excerpts, max_tokens
            )

    return full_context


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.post("/internal/ai/chat")
async def ai_chat(payload: AiChatRequest, db: AsyncSession = Depends(get_db)) -> AiChatResponse:

    # 1. Retrieve similar incidents via pgvector
    similar_raw = await search_similar_incidents(
        query=payload.message,
        service_id=payload.incident_context.service_id if payload.incident_context else None,
        limit=payload.max_similar_incidents,
        similarity_threshold=settings.AI_SIMILARITY_THRESHOLD,
        db=db,
    )

    # 2. Retrieve relevant TechDocs excerpts
    techdocs = await search_techdocs(
        query=payload.message,
        service_name=payload.incident_context.service_name if payload.incident_context else None,
        limit=2,
        db=db,
    )

    # 3. Build context window
    context = build_context_window(
        payload.incident_context, similar_raw, techdocs,
        max_tokens=settings.AI_MAX_CONTEXT_TOKENS,
    )

    # 4. Call Claude
    if not settings.ANTHROPIC_API_KEY:
        return _mock_response(payload.message, similar_raw)

    messages = [{"role": m.role, "content": m.content} for m in payload.conversation_history]
    messages.append({"role": "user", "content": f"{context}\n\n## Engineer's Question\n{payload.message}"})

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        raw_content = response.content[0].text
        tokens_used = response.usage.input_tokens + response.usage.output_tokens

        # Parse structured response
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            # Claude returned prose — wrap it
            parsed = {"message": raw_content, "root_cause_analysis": None, "recommended_actions": []}

    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        raise HTTPException(status_code=502, detail={"error": "ai_unavailable", "message": str(exc)})

    similar_incidents = [
        SimilarIncident(
            incident_id=inc["id"],
            similarity_score=inc["similarity_score"],
            summary=inc["summary"],
            root_cause=inc.get("root_cause"),
            resolution=inc.get("resolution"),
            resolved_at=inc.get("resolved_at"),
            mttr_minutes=inc.get("mttr_minutes"),
        )
        for inc in similar_raw
    ]

    recommended_actions = [
        RecommendedAction(**action)
        for action in parsed.get("recommended_actions", [])
    ]

    return AiChatResponse(
        message=parsed.get("message", raw_content),
        root_cause_analysis=parsed.get("root_cause_analysis"),
        similar_incidents=similar_incidents,
        recommended_actions=recommended_actions,
        tokens_used=tokens_used,
    )


def _mock_response(message: str, similar_incidents: list[dict]) -> AiChatResponse:
    """Dev fallback when ANTHROPIC_API_KEY not set."""
    return AiChatResponse(
        message=f"[Mock response — set ANTHROPIC_API_KEY for real AI] Analysing: '{message[:50]}...'",
        root_cause_analysis={
            "summary": "Mock: likely a recent deploy introduced a regression",
            "confidence": 0.7,
            "contributing_factors": ["Recent deploy", "No canary rollout"],
        },
        similar_incidents=[
            SimilarIncident(
                incident_id=inc["id"], similarity_score=inc["similarity_score"],
                summary=inc["summary"], root_cause=inc.get("root_cause"),
                resolution=inc.get("resolution"), mttr_minutes=inc.get("mttr_minutes"),
            ) for inc in similar_incidents
        ],
        recommended_actions=[
            RecommendedAction(label="Rollback to previous version", action_type="rollback",
                              parameters={}, estimated_mttr_minutes=4),
        ],
        tokens_used=0,
    )


@app.get("/internal/ai/incidents/{incident_id}/timeline")
async def get_incident_timeline(incident_id: str, db: AsyncSession = Depends(get_db)):
    """
    Stitch incident timeline from audit_log.
    Events: deploy, alert_fired, alert_resolved, runbook_executed, budget_frozen, budget_unfrozen.
    """
    result = await db.execute(
        text("""
            SELECT actor, action, resource_type, resource_id, payload, outcome, timestamp
            FROM audit_log
            WHERE payload->>'incident_id' = :incident_id
               OR (resource_type = 'service' AND timestamp BETWEEN
                   (SELECT created_at FROM incidents WHERE id = :incident_id::uuid) - INTERVAL '10 minutes'
                   AND COALESCE((SELECT resolved_at FROM incidents WHERE id = :incident_id::uuid), NOW()))
            ORDER BY timestamp ASC
        """),
        {"incident_id": incident_id},
    )
    rows = result.fetchall()

    events = []
    for row in rows:
        event_type = "deploy" if "deploy" in row.action else \
                     "alert_fired" if "freeze" in row.action else \
                     "runbook_executed" if "runbook" in row.action else "audit_event"
        events.append({
            "timestamp": row.timestamp.isoformat(),
            "event_type": event_type,
            "description": f"{row.actor} — {row.action} ({row.outcome})",
            "actor": row.actor,
            "metadata": row.payload or {},
        })

    return {"incident_id": incident_id, "events": events}
