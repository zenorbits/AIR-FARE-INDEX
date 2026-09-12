"""
Shared cleaning / feature-engineering logic for the Price Prediction feature.

This module is imported by BOTH ml/train.py (offline training) and
ml/predict.py (online inference via the API), so that the exact same
transformations are applied at train time and at prediction time.

Target variable: total_fare
Excluded predictors (target leakage): total_fare, base_fare, taxes_fees

Note: source is included as a feature because Yatra returns only the cheapest
fare per airline while Cleartrip returns every flight, meaning the two sources
have systematically different fare distributions.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Airline canonicalization
# --------------------------------------------------------------------------
# The raw `flight_prices_clean` data mixes IATA airline codes and full
# airline names for the SAME carrier (e.g. "AI" and "Air India" both appear).
# Verified against the flight_number prefix in the dataset:
#   6E -> IndiGo, AI -> Air India, IX -> Air India Express,
#   QP -> Akasa Air, 9I -> Alliance Air, SG -> SpiceJet, S5 -> Star Air
# We canonicalize using the flight_number prefix (the more reliable field)
# rather than trusting the free-text `airline` column, so "AI" and
# "Air India" collapse into a single category instead of being treated as
# two unrelated airlines by the model.
AIRLINE_CODE_MAP = {
    "6E": "IndiGo",
    "AI": "Air India",
    "IX": "Air India Express",
    "QP": "Akasa Air",
    "9I": "Alliance Air",
    "SG": "SpiceJet",
    "S5": "Star Air",
}

# Raw input columns required to build features (excludes target/leakage cols)
REQUIRED_RAW_COLUMNS = [
    "route",
    "flight_number",
    "airline",
    "cabin_class",
    "source",
    "stops",
    "lead_time_days",
    "departure_time",
]

# Final feature columns fed into the model
CATEGORICAL_FEATURES = ["route", "airline", "cabin_class", "source"]
NUMERIC_FEATURES = [
    "stops",
    "lead_time_days",
    "departure_hour",
    "departure_day_of_week",
    "departure_month",
]
ALL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES

TARGET_COLUMN = "total_fare"

# Columns that must NEVER be used as model inputs (target leakage)
LEAKAGE_COLUMNS = ["total_fare", "base_fare", "taxes_fees"]


def canonicalize_airline(row_airline: str, flight_number: str) -> str:
    """Map an airline code/name to a single canonical name using the
    flight_number prefix (e.g. '6E-948' -> '6E' -> 'IndiGo')."""
    if isinstance(flight_number, str) and "-" in flight_number:
        prefix = flight_number.split("-")[0].strip().upper()
        if prefix in AIRLINE_CODE_MAP:
            return AIRLINE_CODE_MAP[prefix]
    # Fallback: keep the original value, stripped, if we can't map it
    return str(row_airline).strip() if pd.notna(row_airline) else "UNKNOWN"


_CANONICAL_AIRLINE_NAMES = {v.upper(): v for v in AIRLINE_CODE_MAP.values()}
_CODE_TO_CANONICAL_UPPER = {k.upper(): v for k, v in AIRLINE_CODE_MAP.items()}


def normalize_airline_input(airline: str) -> str:
    """Normalize a user-supplied airline string (for live predictions) to
    the same canonical form used at training time. Accepts either an IATA
    code ('6E') or a full name ('IndiGo' / 'indigo'), case-insensitively.
    Falls back to the input, title-cased, if it's not recognized -- the
    model's OneHotEncoder(handle_unknown='ignore') will then treat it as
    an unseen category rather than erroring."""
    if not isinstance(airline, str) or not airline.strip():
        return "UNKNOWN"
    key = airline.strip().upper()
    if key in _CODE_TO_CANONICAL_UPPER:
        return _CODE_TO_CANONICAL_UPPER[key]
    if key in _CANONICAL_AIRLINE_NAMES:
        return _CANONICAL_AIRLINE_NAMES[key]
    return airline.strip()


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a raw flight_prices_clean-shaped DataFrame before feature
    engineering. Returns a NEW dataframe; does not mutate the input.

    Cleaning decisions (see project chat for full dataset analysis):
      1. Drop exact duplicate rows (defensive; none found in the reference
         dataset, but scraper re-runs could introduce them later).
      2. Drop rows with a null/non-positive total_fare (can't train on them).
      3. Drop rows where departure_time fails to parse as a datetime.
      4. Normalize cabin_class to upper-case, stripped ("Economy" ==
         "ECONOMY").
      5. Canonicalize airline via flight_number prefix (see
         AIRLINE_CODE_MAP above) so the same carrier isn't split into
         multiple categories.
      6. Drop rows flagged is_outlier == True by the existing cleaning
         pipeline (cleaning/pipeline.py) IF that column is present. Those
         rows were already identified as extreme/likely-bad observations
         by the project's own outlier detector, so we don't want the price
         model learning from them.
      7. Drop rows with negative lead_time_days or negative stops
         (defensive; none found in the reference dataset).
    """
    original_len = len(df)
    out = df.copy()

    missing_cols = [c for c in REQUIRED_RAW_COLUMNS if c not in out.columns]
    if missing_cols:
        raise ValueError(f"Input data is missing required columns: {missing_cols}")

    # 1. Exact duplicates
    before = len(out)
    out = out.drop_duplicates()
    logger.info("Dropped %d exact duplicate rows", before - len(out))

    # 2. Null / non-positive target
    if TARGET_COLUMN in out.columns:
        before = len(out)
        out = out[out[TARGET_COLUMN].notna() & (out[TARGET_COLUMN] > 0)]
        logger.info(
            "Dropped %d rows with missing/non-positive %s",
            before - len(out),
            TARGET_COLUMN,
        )

    # 3. Parse departure_time
    before = len(out)
    out["departure_time"] = pd.to_datetime(out["departure_time"], errors="coerce")
    out = out[out["departure_time"].notna()]
    logger.info("Dropped %d rows with unparseable departure_time", before - len(out))

    # 4. Normalize cabin_class
    out["cabin_class"] = out["cabin_class"].astype(str).str.strip().str.upper()
    out["source"] = out["source"].astype(str).str.strip().str.lower()

    # 5. Canonicalize airline
    out["airline"] = out.apply(
        lambda r: canonicalize_airline(r["airline"], r["flight_number"]), axis=1
    )

    # 6. Drop flagged outliers, if the column exists (it comes from the
    #    project's own cleaning/pipeline.py outlier flag)
    if "is_outlier" in out.columns:
        before = len(out)
        out = out[out["is_outlier"] != True]  # noqa: E712 (explicit bool compare, handles NaN)
        logger.info("Dropped %d rows flagged is_outlier=True", before - len(out))

    # 7. Defensive range checks
    before = len(out)
    out = out[(out["lead_time_days"] >= 0) & (out["stops"] >= 0)]
    logger.info(
        "Dropped %d rows with negative lead_time_days/stops", before - len(out)
    )

    out = out.reset_index(drop=True)
    logger.info(
        "clean_dataframe: %d rows in -> %d rows out (%d dropped total)",
        original_len,
        len(out),
        original_len - len(out),
    )
    return out


def add_derived_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive departure_hour, departure_day_of_week, departure_month from
    the (already-parsed) departure_time column."""
    out = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(out["departure_time"]):
        out["departure_time"] = pd.to_datetime(out["departure_time"], errors="coerce")

    out["departure_hour"] = out["departure_time"].dt.hour
    out["departure_day_of_week"] = out["departure_time"].dt.dayofweek  # Mon=0
    out["departure_month"] = out["departure_time"].dt.month
    return out


def build_feature_frame(df: pd.DataFrame, clean: bool = True) -> pd.DataFrame:
    """Full pipeline: (optionally) clean, then derive features. Returns a
    dataframe containing ALL_FEATURES (+ TARGET_COLUMN and scraped_at if
    present, for time-aware splitting)."""
    work = clean_dataframe(df) if clean else df.copy()
    work = add_derived_time_features(work)

    keep_cols = list(ALL_FEATURES)
    if TARGET_COLUMN in work.columns:
        keep_cols.append(TARGET_COLUMN)
    if "scraped_at" in work.columns:
        keep_cols.append("scraped_at")

    return work[keep_cols]


def build_single_prediction_row(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
    source: str = "cleartrip",
) -> pd.DataFrame:
    """Build a single-row DataFrame in the exact shape the trained model
    pipeline expects, for a live prediction request. No cleaning is applied
    here (that's for historical data) -- instead we validate directly in
    ml/predict.py before calling this."""
    row = {
        "route": str(route).strip().upper(),
        "airline": str(airline).strip(),
        "cabin_class": str(cabin_class).strip().upper(),
        "source": str(source).strip().lower(),
        "stops": int(stops),
        "lead_time_days": int(lead_time_days),
        "departure_hour": int(departure_hour),
        "departure_day_of_week": int(departure_day_of_week),
        "departure_month": int(departure_month),
    }
    return pd.DataFrame([row], columns=ALL_FEATURES)
