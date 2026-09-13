"""
Loads historical flight fare data for training.

Two sources are supported so the feature works both in this environment
(no live Postgres instance / DATABASE_URL) and in the real deployment:

1. CSV file (default: the bundled ml/data/flight_prices_clean_sample.csv,
   which is a snapshot of the project's real flight_prices_clean table).
2. The live database, via the EXISTING db.database.get_engine() +
   cleaning.pipeline.FlightPriceClean model -- no new tables, no new
   connection logic, reuses what's already in the project.

train.py defaults to the DB when DATABASE_URL is set, and falls back to
the bundled CSV otherwise (e.g. local development, CI, or this training
run) so the same code path works everywhere.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = Path(__file__).parent / "data" / "flight_prices_clean_sample.csv"

EXPECTED_COLUMNS = [
    "id",
    "source",
    "route",
    "airline",
    "flight_number",
    "cabin_class",
    "base_fare",
    "taxes_fees",
    "total_fare",
    "stops",
    "lead_time_days",
    "departure_time",
    "scraped_at",
    "scraped_hour",
    "is_outlier",
    "cleaning_notes",
]


def load_from_csv(csv_path: Optional[Path] = None) -> pd.DataFrame:
    path = Path(csv_path) if csv_path else DEFAULT_CSV_PATH
    if not path.exists():
        raise FileNotFoundError(f"Training CSV not found at {path}")
    df = pd.read_csv(path)
    logger.info("Loaded %d rows from CSV: %s", len(df), path)
    return df


def load_from_db() -> pd.DataFrame:
    """Load flight_prices_clean directly from the existing database using
    the project's existing SQLAlchemy setup (db.database.get_engine +
    cleaning.pipeline.FlightPriceClean). Requires DATABASE_URL to be set."""
    # Imported lazily so this module doesn't hard-require SQLAlchemy/DB
    # env vars just to run offline CSV-based training.
    from sqlalchemy import text
    from db.database import get_engine
    from cleaning.pipeline import FlightPriceClean

    engine = get_engine()
    query = "SELECT * FROM flight_prices_clean"
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
    logger.info("Loaded %d rows from database table flight_prices_clean", len(df))
    return df


def load_training_data(
    csv_path: Optional[Path] = None, prefer: str = "auto"
) -> pd.DataFrame:
    """Load training data.

    prefer:
      - "csv": always load from CSV.
      - "db": always load from the database (raises if unavailable).
      - "auto" (default): use the database if DATABASE_URL is set, else
        fall back to the bundled CSV.
    """
    if prefer == "csv":
        return load_from_csv(csv_path)
    if prefer == "db":
        return load_from_db()

    if os.environ.get("DATABASE_URL"):
        try:
            return load_from_db()
        except Exception as e:  # pragma: no cover - defensive fallback
            logger.warning(
                "DATABASE_URL is set but DB load failed (%s); falling back to CSV",
                e,
            )
    return load_from_csv(csv_path)
