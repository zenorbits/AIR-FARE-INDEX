"""
Reusable prediction function for the Price Prediction feature.

Loads the trained pipeline (saved by ml/train.py) once and reuses it for
every call, so the API doesn't retrain or reload the model per-request.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd

from ml.features import (
    AIRLINE_CODE_MAP,
    build_single_prediction_row,
    normalize_airline_input,
)
from ml.train import MODEL_PATH, METADATA_PATH

logger = logging.getLogger(__name__)

VALID_CABIN_CLASSES = {"ECONOMY"}  # only cabin class present in training data today
KNOWN_ROUTES = {
    "DEL-BOM",
    "DEL-BLR",
    "BLR-HYD",
    "BOM-BLR",
    "DEL-CCU",
    "MAA-DEL",
}
KNOWN_AIRLINES = set(AIRLINE_CODE_MAP.values())

_model_lock = threading.Lock()
_pipeline = None
_metadata: Optional[dict] = None


class PredictionInputError(ValueError):
    """Raised when the caller supplies invalid prediction inputs."""


def _load_model():
    global _pipeline, _metadata
    with _model_lock:
        if _pipeline is None:
            if not MODEL_PATH.exists():
                raise FileNotFoundError(
                    f"No trained model found at {MODEL_PATH}. Run `python -m ml.train` first."
                )
            _pipeline = joblib.load(MODEL_PATH)
            logger.info("Loaded price prediction model from %s", MODEL_PATH)
            if METADATA_PATH.exists():
                with open(METADATA_PATH) as f:
                    _metadata = json.load(f)
    return _pipeline


def get_model_metadata() -> Optional[dict]:
    _load_model()  # ensures _metadata is populated if the file exists
    return _metadata


def _validate_inputs(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
) -> None:
    errors = []

    if not isinstance(route, str) or not route.strip():
        errors.append("route is required")
    if not isinstance(airline, str) or not airline.strip():
        errors.append("airline is required")
    if not isinstance(cabin_class, str) or not cabin_class.strip():
        errors.append("cabin_class is required")

    if not isinstance(lead_time_days, int) or lead_time_days < 0:
        errors.append("lead_time_days must be a non-negative integer")
    if not isinstance(stops, int) or stops < 0:
        errors.append("stops must be a non-negative integer")
    if not isinstance(departure_hour, int) or not (0 <= departure_hour <= 23):
        errors.append("departure_hour must be an integer between 0 and 23")
    if not isinstance(departure_day_of_week, int) or not (
        0 <= departure_day_of_week <= 6
    ):
        errors.append(
            "departure_day_of_week must be an integer between 0 (Mon) and 6 (Sun)"
        )
    if not isinstance(departure_month, int) or not (1 <= departure_month <= 12):
        errors.append("departure_month must be an integer between 1 and 12")

    if errors:
        raise PredictionInputError("; ".join(errors))


def predict_fare(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
    source: str = "cleartrip",
) -> float:
    """Predict total_fare (INR) for a single flight, using the trained
    HistGradientBoostingRegressor pipeline. Raises PredictionInputError on invalid
    input (unknown-but-well-formed categories are allowed through and
    handled by the encoder as an unseen category, rather than rejected --
    only structurally invalid values raise)."""
    _validate_inputs(
        route,
        airline,
        cabin_class,
        lead_time_days,
        stops,
        departure_hour,
        departure_day_of_week,
        departure_month,
    )

    pipeline = _load_model()

    normalized_airline = normalize_airline_input(airline)
    row = build_single_prediction_row(
        route=route,
        airline=normalized_airline,
        cabin_class=cabin_class,
        source=source,
        lead_time_days=lead_time_days,
        stops=stops,
        departure_hour=departure_hour,
        departure_day_of_week=departure_day_of_week,
        departure_month=departure_month,
    )

    prediction = pipeline.predict(row)[0]
    return round(float(prediction), 2)

