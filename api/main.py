# Runnable via: uvicorn api.main:app --reload

import os
from dotenv import load_dotenv

load_dotenv()

from typing import List, Optional, Any, Dict
from datetime import date, datetime
from fastapi import FastAPI, Depends, Query, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import desc, asc, func

# Import existing database setup and models
from db.database import get_engine
from index_calc.models import AirfareIndex
from cleaning.pipeline import FlightPriceClean

API_KEY = os.environ.get("API_KEY")
if not API_KEY:
    raise ValueError("API_KEY environment variable is not set")

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key"
        )
    return api_key

app = FastAPI(title="Airfare API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    if is_outlier is not None:
        query = query.filter(FlightPriceClean.is_outlier == is_outlier)

    results = query.offset(offset).limit(limit).all()

    response = []
    for r in results:
        response.append({
            "route": r.route,
            "lead_time_days": r.lead_time_days,
            "total_fare": r.total_fare,
            "base_fare": r.base_fare,
            "taxes_fees": r.taxes_fees,
            "is_outlier": r.is_outlier,
            "scraped_hour": r.scraped_hour,
            "departure_time": r.departure_time
        })
        
    return response

@app.get("/routes")
def get_routes(
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    results = db.query(FlightPriceClean.route).distinct().all()
    return [r[0] for r in results if r[0] is not None]
