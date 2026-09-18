"""RAG-style explanations for the Airfare Price Index.

Every answer is grounded in two retrieved sources instead of the model's
own knowledge:
  1. Structured retrieval: the route's actual recent AirfareIndex rows —
     the same real numbers /index/history serves, queried fresh each call.
  2. Unstructured retrieval: a methodology summary kept in sync with the
     real calculation logic in index_calc/jevons.py (Jevons index, DGCA
     route weights, outlier handling), so explanations stay accurate even
     as that logic evolves — not a fixed, driftable doc.

The model is only ever asked to *narrate* what was retrieved. It is never
asked to compute or invent an index value — those come from the database.
"""
import os
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_db, verify_api_key
from index_calc.models import AirfareIndex

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Grounding source #2. Keep this in sync with index_calc/jevons.py if the
# calculation logic there changes — it is quoted verbatim to the model, so
# a stale summary here would produce a confidently wrong explanation.
METHODOLOGY = """
The Airfare Price Index (APIx) is a Jevons index (geometric mean of price
relatives), computed per route and lead-time-days bucket:
  index_value = 100 * (geometric_mean(current_period_fares) / geometric_mean(base_period_fares))
The base period is auto-detected as the earliest date with clean scraped
data. An "overall" index (route = null) is a weighted average across
routes, using DGCA-published domestic city-pair passenger traffic as
weights: DEL-BOM 29.87%, DEL-BLR 20.90%, BOM-BLR 18.77%, DEL-CCU 12.60%,
MAA-DEL 9.18%, BLR-HYD 8.68%.
Fares flagged as outliers (see cleaning/pipeline.py) are excluded from
both the base period and current period calculations. A period with fewer
than 2 observations for a given route + lead-time is skipped entirely, as
there isn't enough data for a stable geometric mean.
Known limitations: data currently comes from a single source (Yatra); the
historical data window is limited, since hourly scraping only started
recently.
""".strip()

MAX_HISTORY_DAYS = 14


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    route: Optional[str] = Field(None, description="e.g. DEL-BOM — narrows retrieval to this route's recent index history")


class Source(BaseModel):
    label: str
    detail: str


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]


def _retrieve_route_history(db: Session, route: str):
    """Real recent index_value rows for `route` — the structured half of
    the retrieval step. Returns (context_text, Source) or (None, None) if
    there's nothing to retrieve."""
    cutoff = date.today() - timedelta(days=MAX_HISTORY_DAYS)
    rows = (
        db.query(AirfareIndex)
        .filter(
            AirfareIndex.route == route,
            AirfareIndex.lead_time_days.is_(None),
            AirfareIndex.frequency == "daily",
            AirfareIndex.period_start >= cutoff,
        )
        .order_by(AirfareIndex.period_start.asc())
        .all()
    )
    if not rows:
        return None, None

    lines = [f"{r.period_start.isoformat()}: {r.index_value:.1f} ({r.num_observations} observations)" for r in rows]
    context = f"Recent daily APIx for {route} (last {len(rows)} days):\n" + "\n".join(lines)
    source = Source(
        label=f"{route} index history",
        detail=f"{len(rows)} daily observations, {rows[0].period_start.isoformat()} to {rows[-1].period_start.isoformat()}",
    )
    return context, source


def _get_gemini_client():
    # Google's Gemini API has a free tier (no billing required) — get a key
    # at https://aistudio.google.com/apikey. See Backend/.env.example.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY is not set — the assistant endpoint is unavailable until it's configured.",
        )
    from google import genai  # imported lazily: only required when this endpoint is actually called

    return genai.Client(api_key=api_key)


SYSTEM_PROMPT = """You are the Airfare Price Index (APIx) assistant for a MoSPI-style \
airfare index dashboard. Answer the user's question using ONLY the context \
provided below — never invent or recompute an index value yourself, and \
never state a number that isn't present in the context. If the context \
doesn't contain enough information to answer, say so plainly rather than \
guessing. Keep answers concise (2-4 sentences) and in plain English, \
suitable for someone with no statistics background."""


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db), api_key: str = Depends(verify_api_key)):
    sources: List[Source] = []
    context_parts = [f"Index methodology:\n{METHODOLOGY}"]
    sources.append(Source(label="Index methodology", detail="Jevons formula, DGCA route weights, outlier handling"))

    if payload.route:
        history_context, history_source = _retrieve_route_history(db, payload.route)
        if history_context:
            context_parts.append(history_context)
            sources.append(history_source)

    client = _get_gemini_client()
    context = "\n\n".join(context_parts)

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"Context:\n{context}\n\nQuestion: {payload.question}",
            config={"system_instruction": SYSTEM_PROMPT, "max_output_tokens": 400},
        )
    except Exception as e:  # Gemini SDK errors (rate limit, auth, etc.)
        raise HTTPException(status_code=502, detail=f"Assistant request failed: {e}") from e

    return AskResponse(answer=response.text, sources=sources)
