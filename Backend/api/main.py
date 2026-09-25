# Runnable via: uvicorn api.main:app --reload

import os
import time
from collections import defaultdict, deque
from dotenv import load_dotenv

load_dotenv()

from typing import List, Optional, Any, Dict
from datetime import date, datetime, timedelta
from fastapi import FastAPI, Depends, Query, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func

from ml.predict import predict_fare, get_model_metadata, PredictionInputError

# Import existing database setup and models
from index_calc.models import AirfareIndex
from cleaning.pipeline import FlightPriceClean
from backtest.validate import load_cpi_benchmark, load_apix_monthly, compare as compare_backtest

from api.deps import get_db, verify_api_key, engine
from api.assistant import router as assistant_router

class PredictPriceRequest(BaseModel):
    route: str = Field(..., min_length=1, description="e.g. DEL-BOM")
    airline: str = Field(..., min_length=1, description="IATA code or name, e.g. 6E or IndiGo")
    cabin_class: str = Field(..., min_length=1, description="e.g. Economy")
    lead_time_days: int = Field(..., ge=0, le=365)
    stops: int = Field(..., ge=0, le=5)
    departure_hour: int = Field(..., ge=0, le=23)
    departure_day_of_week: int = Field(..., ge=0, le=6, description="0=Monday, 6=Sunday")
    departure_month: int = Field(..., ge=1, le=12)


class PredictPriceResponse(BaseModel):
    predicted_fare: float
    currency: str = "INR"
    model_trained_at: Optional[str] = None


class CurvePoint(BaseModel):
    lead_time_days: int
    book_by_date: date
    predicted_fare: float

class PredictCurveResponse(BaseModel):
    route: str
    airline: str
    cabin_class: str
    departure_date: date
    currency: str = "INR"
    curve: List[CurvePoint]
    cheapest_lead_time_days: int
    cheapest_fare: float
    recommendation: str
    model_trained_at: Optional[str] = None
    extrapolation_note: Optional[str] = None
    model_test_mae: Optional[float] = None
    confidence_note: Optional[str] = None

app = FastAPI(title="Airfare API")


# Comma-separated list of extra allowed origins (e.g. your deployed frontend's
# URL) via the ALLOWED_ORIGINS env var, on top of the local dev servers --
# so a deployment doesn't require editing code, just setting an env var.
#
# Browsers' Origin header is always scheme://host[:port] with no trailing
# slash, so a pasted URL that has one (an easy copy-paste mistake) would
# otherwise silently fail to match and break CORS -- strip it defensively.
_extra_origins = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:5173", *_extra_origins]

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assistant_router)

# Simple fixed-client, sliding-window rate limit -- no external dependency,
# adequate for a single-instance hackathon deployment. Keyed by client IP
# since callers authenticate with a single shared API key, not per-user
# credentials.
#
# The dashboard's own panels each independently re-fetch the same routes
# (no shared cache), so a single page load can legitimately fire 100+
# requests -- this ceiling has to comfortably clear that, it's here to catch
# actual abuse, not normal use.
RATE_LIMIT_MAX_REQUESTS = 600
RATE_LIMIT_WINDOW_SECONDS = 60
_request_log: Dict[str, deque] = defaultdict(deque)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _request_log[client_ip]
    while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= RATE_LIMIT_MAX_REQUESTS:
        # Raising HTTPException here would NOT be caught by FastAPI's exception
        # handling (BaseHTTPMiddleware.dispatch sits outside ExceptionMiddleware),
        # so it would surface as an unhandled 500 instead of a clean 429.
        #
        # This response also never reaches CORSMiddleware (this middleware
        # wraps outside it), so without adding the header manually here, a
        # rate-limited request from the browser shows up as an opaque CORS
        # failure instead of a readable 429 -- add it ourselves for any
        # origin the app already allows.
        origin = request.headers.get("origin")
        headers = {"Access-Control-Allow-Origin": origin} if origin in ALLOWED_ORIGINS else {}
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded, try again shortly"},
            headers=headers,
        )
    window.append(now)
    return await call_next(request)

@app.get("/")
def read_root():
    return {"message": "Welcome to the Airfare API. See /docs for interactive API documentation."}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/index/current")
def get_index_current(
    frequency: str = Query("daily", description="daily, weekly, or monthly"),
    route: Optional[str] = Query(None, description="e.g. DEL-BOM or 'overall'"),
    lead_time_days: Optional[str] = Query(None, description="e.g. 7 or 'overall'"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    if frequency not in ["daily", "weekly", "monthly"]:
        raise HTTPException(status_code=422, detail="Frequency must be one of: daily, weekly, monthly")

    query = db.query(AirfareIndex).filter(AirfareIndex.frequency == frequency)

    if route is not None:
        if route.lower() == "overall":
            query = query.filter(AirfareIndex.route.is_(None))
        else:
            query = query.filter(AirfareIndex.route == route)
            
    if lead_time_days is not None:
        if lead_time_days.lower() == "overall":
            query = query.filter(AirfareIndex.lead_time_days.is_(None))
        else:
            try:
                lead_val = int(lead_time_days)
                query = query.filter(AirfareIndex.lead_time_days == lead_val)
            except ValueError:
                raise HTTPException(status_code=422, detail="lead_time_days must be an integer or 'overall'")

    results = query.order_by(AirfareIndex.period_start.desc()).all()

    # Get most recent for each (route, lead_time_days) combination
    most_recent: Dict[tuple, Any] = {}
    for r in results:
        key = (r.route, r.lead_time_days)
        if key not in most_recent:
            most_recent[key] = {
                "route": r.route if r.route is not None else "overall",
                "lead_time_days": r.lead_time_days if r.lead_time_days is not None else "overall",
                "frequency": r.frequency,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "index_value": r.index_value,
                "num_observations": r.num_observations
            }
            
    return list(most_recent.values())

@app.get("/index/history")
def get_index_history(
    route: str = Query(..., description="Route or 'overall'"),
    lead_time_days: str = Query(..., description="Lead time in days or 'overall'"),
    frequency: str = Query(..., description="daily, weekly, or monthly"),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    if frequency not in ["daily", "weekly", "monthly"]:
        raise HTTPException(status_code=422, detail="Frequency must be one of: daily, weekly, monthly")

    query = db.query(AirfareIndex).filter(AirfareIndex.frequency == frequency)
    
    if route.lower() == "overall":
        query = query.filter(AirfareIndex.route.is_(None))
    else:
        query = query.filter(AirfareIndex.route == route)
        
    if lead_time_days.lower() == "overall":
        query = query.filter(AirfareIndex.lead_time_days.is_(None))
    else:
        try:
            lead_time_val = int(lead_time_days)
            query = query.filter(AirfareIndex.lead_time_days == lead_time_val)
        except ValueError:
            raise HTTPException(status_code=422, detail="lead_time_days must be an integer or 'overall'")

    if start_date:
        query = query.filter(AirfareIndex.period_start >= start_date)
    if end_date:
        query = query.filter(AirfareIndex.period_start <= end_date)

    results = query.order_by(AirfareIndex.period_start.asc()).all()

    response = []
    for r in results:
        response.append({
            "route": r.route if r.route is not None else "overall",
            "lead_time_days": r.lead_time_days if r.lead_time_days is not None else "overall",
            "frequency": r.frequency,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "index_value": r.index_value,
            "num_observations": r.num_observations
        })
        
    return response

@app.get("/fares/raw")
def get_fares_raw(
    route: Optional[str] = None,
    lead_time_days: Optional[int] = None,
    source: Optional[str] = None,
    is_outlier: Optional[bool] = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    query = db.query(FlightPriceClean)

    if route:
        query = query.filter(FlightPriceClean.route == route)
    if lead_time_days is not None:
        query = query.filter(FlightPriceClean.lead_time_days == lead_time_days)
    if source:
        query = query.filter(FlightPriceClean.source == source)
    if is_outlier is not None:
        query = query.filter(FlightPriceClean.is_outlier == is_outlier)

    results = query.offset(offset).limit(limit).all()

    response = []
    for r in results:
        response.append({
            "route": r.route,
            "lead_time_days": r.lead_time_days,
            "source": r.source,
            "total_fare": r.total_fare,
            "base_fare": r.base_fare,
            "taxes_fees": r.taxes_fees,
            "is_outlier": r.is_outlier,
            "scraped_hour": r.scraped_hour,
            "departure_time": r.departure_time
        })

    return response


@app.get("/sources")
def get_sources(
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    results = db.query(FlightPriceClean.source).distinct().all()
    return [r[0] for r in results if r[0] is not None]

@app.get("/routes")
def get_routes(
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    results = db.query(FlightPriceClean.route).distinct().all()
    return [r[0] for r in results if r[0] is not None]

@app.post("/predict-price", response_model=PredictPriceResponse)
def predict_price(
    payload: PredictPriceRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    try:
        fare = predict_fare(**payload.model_dump(), db=db)
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=503,
            detail="Price model has not been trained yet. Run `python -m ml.train`.",
        ) from e
    except PredictionInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    metadata = get_model_metadata() or {}
    return PredictPriceResponse(
        predicted_fare=fare,
        model_trained_at=metadata.get("trained_at"),
    )


# Lead times the model was actually trained on (see config.yaml lead_time_days).
# We only score at these values because the model has no observations in
# between, so intermediate points would be interpolation artefacts.
TRAINED_LEAD_TIMES = [1, 7, 15, 30, 45]


@app.get("/predict-price/curve", response_model=PredictCurveResponse)
def predict_price_curve(
    route: str = Query(..., min_length=1, description="e.g. DEL-BOM"),
    departure_date: date = Query(..., description="ISO date, e.g. 2026-10-15"),
    departure_hour: int = Query(..., ge=0, le=23),
    airline: str = Query(..., min_length=1, description="IATA code or name, e.g. 6E"),
    cabin_class: str = Query("ECONOMY", min_length=1),
    stops: int = Query(0, ge=0, le=5),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """Predicted fare for the same flight booked at different lead times.

    Larger lead_time_days means booking EARLIER. Booking today corresponds to
    the largest lead time in the curve.
    """
    days_until_departure = (departure_date - date.today()).days
    if days_until_departure < 1:
        raise HTTPException(
            status_code=422,
            detail="departure_date must be at least 1 day in the future",
        )

    max_trained_lead_time = max(TRAINED_LEAD_TIMES)
    book_now_lead_time = min(days_until_departure, max_trained_lead_time)
    lead_times = sorted(
        {lt for lt in TRAINED_LEAD_TIMES if lt <= book_now_lead_time}
        | {book_now_lead_time}
    )
    if not lead_times:
        raise HTTPException(
            status_code=422, detail="No valid lead times for this departure_date"
        )

    try:
        curve = [
            CurvePoint(
                lead_time_days=lt,
                book_by_date=departure_date - timedelta(days=lt),
                predicted_fare=predict_fare(
                    route=route,
                    airline=airline,
                    cabin_class=cabin_class,
                    lead_time_days=lt,
                    stops=stops,
                    departure_hour=departure_hour,
                    departure_day_of_week=departure_date.weekday(),
                    departure_month=departure_date.month,
                    db=db,
                ),
            )
            for lt in lead_times
        ]
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=503,
            detail="Price model has not been trained yet. Run python -m ml.train.",
        ) from e
    except PredictionInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    cheapest = min(curve, key=lambda p: p.predicted_fare)

    if cheapest.lead_time_days == book_now_lead_time:
        recommendation = "Book now - fares are predicted to rise closer to departure."
    else:
        savings = curve[-1].predicted_fare - cheapest.predicted_fare
        recommendation = (
            f"Consider waiting until around {cheapest.book_by_date.isoformat()} "
            f"({cheapest.lead_time_days} days before departure) - predicted to be "
            f"about INR {abs(savings):.0f} cheaper than booking today."
        )

    metadata = get_model_metadata() or {}
    
    extrapolation_note = None
    if days_until_departure > max_trained_lead_time:
        extrapolation_note = (
            f"Departure is {days_until_departure} days away, beyond the "
            f"{max_trained_lead_time}-day maximum lead time in the training data. "
            f"The curve starts at {max_trained_lead_time} days."
        )

    mae = (metadata.get("test_metrics") or {}).get("mae")
    fare_spread = max(p.predicted_fare for p in curve) - min(
        p.predicted_fare for p in curve
    )
    confidence_note = None
    if mae is not None and fare_spread < mae:
        confidence_note = (
            f"Predicted fares across this curve vary by about INR {fare_spread:.0f}, "
            f"which is smaller than the model's average error of INR {mae:.0f}. "
            f"Treat the timing recommendation as weak for this flight."
        )

    return PredictCurveResponse(
        route=route,
        airline=airline,
        cabin_class=cabin_class,
        departure_date=departure_date,
        curve=curve,
        cheapest_lead_time_days=cheapest.lead_time_days,
        cheapest_fare=cheapest.predicted_fare,
        recommendation=recommendation,
        model_trained_at=metadata.get("trained_at"),
        extrapolation_note=extrapolation_note,
        model_test_mae=mae,
        confidence_note=confidence_note,
    )


class BacktestPoint(BaseModel):
    period_start: str
    cpi_index: float
    apix_index: float
    abs_diff: float
    pct_diff: float


class BacktestResponse(BaseModel):
    benchmark: str = "MoSPI CPI Airfare item index (COICOP 07.3.3.1.2.01, base year 2024)"
    points: List[BacktestPoint]
    correlation: Optional[float] = None
    mean_absolute_deviation: Optional[float] = None
    note: Optional[str] = None


@app.get("/backtest/cpi-comparison", response_model=BacktestResponse)
def backtest_cpi_comparison(api_key: str = Depends(verify_api_key)):
    """APIx vs the official CPI Airfare index, monthly. Route-level DGCA
    average-fare data isn't publicly available, so CPI is the only benchmark
    (see Backend/backtest/validate.py)."""
    cpi_df = load_cpi_benchmark()
    apix_df = load_apix_monthly(engine)

    if apix_df.empty:
        return BacktestResponse(points=[], note="No monthly APIx data available yet.")

    merged, stats = compare_backtest(cpi_df, apix_df)
    if merged.empty:
        note = stats if isinstance(stats, str) else "No overlapping periods found."
        return BacktestResponse(points=[], note=note)

    points = [
        BacktestPoint(
            period_start=row.period_start.strftime("%Y-%m-%d"),
            cpi_index=row.cpi_index,
            apix_index=row.apix_index,
            abs_diff=row.abs_diff,
            pct_diff=row.pct_diff,
        )
        for row in merged.itertuples()
    ]

    if isinstance(stats, dict):
        return BacktestResponse(
            points=points,
            correlation=stats["correlation"],
            mean_absolute_deviation=stats["mean_absolute_deviation"],
        )
    return BacktestResponse(points=points, note=stats)
