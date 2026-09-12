import sys
with open('api/main.py', 'r') as f:
    content = f.read()

# Add timedelta to imports
content = content.replace('from datetime import date, datetime', 'from datetime import date, datetime, timedelta')

# Add models
models_code = '''
class PredictPriceRequest(BaseModel):
    route: str = Field(..., min_length=1, description=\"e.g. DEL-BOM\")
    airline: str = Field(..., min_length=1, description=\"IATA code or name, e.g. 6E or IndiGo\")
    cabin_class: str = Field(..., min_length=1, description=\"e.g. Economy\")
    lead_time_days: int = Field(..., ge=0, le=365)
    stops: int = Field(..., ge=0, le=5)
    departure_hour: int = Field(..., ge=0, le=23)
    departure_day_of_week: int = Field(..., ge=0, le=6, description=\"0=Monday, 6=Sunday\")
    departure_month: int = Field(..., ge=1, le=12)

class PredictPriceResponse(BaseModel):
    predicted_fare: float
    currency: str = \"INR\"
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
    currency: str = \"INR\"
    curve: List[CurvePoint]
    cheapest_lead_time_days: int
    cheapest_fare: float
    recommendation: str
    model_trained_at: Optional[str] = None
    extrapolation_note: Optional[str] = None
    model_test_mae: Optional[float] = None
    confidence_note: Optional[str] = None
'''
content = content.replace('app = FastAPI(title=\"Airfare API\")', models_code + '\napp = FastAPI(title=\"Airfare API\")')

# Add imports for predict
content = content.replace('from sqlalchemy import desc, asc, func\n\n# Import existing database setup and models', 'from sqlalchemy import desc, asc, func\nfrom pydantic import BaseModel, Field\nfrom ml.predict import predict_fare, get_model_metadata, PredictionInputError\n\n# Import existing database setup and models')

with open('api/main.py', 'w') as f:
    f.write(content)

