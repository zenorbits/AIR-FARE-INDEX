import sys

with open('api/main.py', 'r') as f:
    content = f.read()

# 1. Update datetime import
content = content.replace('from datetime import date, datetime', 'from datetime import date, datetime, timedelta')

# 2. Add the models
models_code = '''
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
'''
content = content.replace('app = FastAPI(title="Airfare API")', models_code)

# 3. Append the endpoint
endpoint_code = '''

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
'''
content = content + endpoint_code

with open('api/main.py', 'w') as f:
    f.write(content)

