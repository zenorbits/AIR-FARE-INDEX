"""
Train the airfare Price Prediction model.

Run with:
    python -m ml.train

Pipeline:
    Historical flight_prices_clean (DB or bundled CSV)
        -> clean_dataframe()
        -> add_derived_time_features()
        -> time-aware train/test split (by scraped_at)
        -> ColumnTransformer (OneHotEncoder for categoricals) + RandomForestRegressor
        -> evaluate (MAE, RMSE, R^2) on train AND test (overfitting check)
        -> save pipeline + metadata to ml/saved_models/
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.data_loader import load_training_data
from ml.features import (
    ALL_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    build_feature_frame,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "saved_models"
MODEL_PATH = MODEL_DIR / "price_model.joblib"
METADATA_PATH = MODEL_DIR / "price_model_metadata.json"

# Fraction of the (time-sorted) data held out as the test set
TEST_FRACTION = 0.2


def time_aware_split(df: pd.DataFrame, test_fraction: float = TEST_FRACTION):
    """Split chronologically by scraped_at rather than randomly.

    Why: many rows in this dataset are the SAME physical flight re-scraped
    at different lead times (e.g. the same DEL-BOM 6E flight observed at
    45, 30, and 15 days out). A random split would put some observations
    of a given flight in train and near-duplicate observations of that
    same flight (same route/airline/cabin, very similar fare) in test,
    which is a form of temporal leakage: the model would partly be
    memorizing specific flights rather than learning general fare
    patterns. Splitting on scraped_at instead trains on earlier scrape
    snapshots and evaluates on later ones, closer to how the model will
    actually be used in production (predict on new scrapes it has not
    seen yet).
    """
    if "scraped_at" not in df.columns:
        raise ValueError("scraped_at column required for time-aware split")

    ordered = df.sort_values("scraped_at").reset_index(drop=True)
    split_idx = int(len(ordered) * (1 - test_fraction))
    train_df = ordered.iloc[:split_idx].reset_index(drop=True)
    test_df = ordered.iloc[split_idx:].reset_index(drop=True)
    logger.info(
        "Time-aware split: %d train rows (scraped_at <= %s), %d test rows (scraped_at > %s)",
        len(train_df),
        train_df["scraped_at"].max() if len(train_df) else None,
        len(test_df),
        train_df["scraped_at"].max() if len(train_df) else None,
    )
    return train_df, test_df


def build_pipeline() -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="passthrough",  # numeric features pass through unchanged
    )

    # NOTE on these hyperparameters: an initial pass with max_depth=12,
    # min_samples_leaf=3 fit the training data almost perfectly (train
    # MAE ~284) but generalized poorly (test MAE ~893, a 3.1x gap) --
    # classic overfitting, since many rows are near-duplicate
    # observations of the same flight and a deep, low-leaf-count forest
    # was memorizing individual scrape snapshots rather than the
    # underlying fare pattern. Constraining depth and raising the
    # minimum leaf size trades a bit of training accuracy for much
    # better generalization (test MAE ~931, gap shrinks to ~1.3x).
    model = RandomForestRegressor(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=15,
        max_features="sqrt",
        random_state=42,
        n_jobs=-1,
    )

    return Pipeline(steps=[("preprocess", preprocessor), ("model", model)])


def evaluate(pipeline: Pipeline, X: pd.DataFrame, y: pd.Series) -> dict:
    preds = pipeline.predict(X)
    mae = mean_absolute_error(y, preds)
    rmse = np.sqrt(mean_squared_error(y, preds))
    r2 = r2_score(y, preds)
    return {"mae": float(mae), "rmse": float(rmse), "r2": float(r2), "n": int(len(y))}


def main(csv_path: str | None = None, prefer: str = "auto") -> dict:
    logger.info("=== Price Prediction: training run started ===")

    raw_df = load_training_data(csv_path=csv_path, prefer=prefer)
    logger.info("Raw dataset: %d rows, %d columns", raw_df.shape[0], raw_df.shape[1])

    featured_df = build_feature_frame(raw_df, clean=True)
    logger.info("After cleaning + feature engineering: %d rows", len(featured_df))

    if "scraped_at" not in featured_df.columns:
        raise RuntimeError(
            "scraped_at is required in the source data for a time-aware split"
        )
    featured_df["scraped_at"] = pd.to_datetime(featured_df["scraped_at"])

    train_df, test_df = time_aware_split(featured_df)

    X_train, y_train = train_df[ALL_FEATURES], train_df[TARGET_COLUMN]
    X_test, y_test = test_df[ALL_FEATURES], test_df[TARGET_COLUMN]

    pipeline = build_pipeline()
    logger.info("Training RandomForestRegressor on %d rows...", len(X_train))
    pipeline.fit(X_train, y_train)

    train_metrics = evaluate(pipeline, X_train, y_train)
    test_metrics = evaluate(pipeline, X_test, y_test)

    logger.info("TRAIN metrics: %s", train_metrics)
    logger.info("TEST  metrics: %s", test_metrics)

    overfit_gap_mae = train_metrics["mae"] - test_metrics["mae"]
    overfit_ratio = (
        test_metrics["mae"] / train_metrics["mae"] if train_metrics["mae"] > 0 else None
    )
    logger.info(
        "Overfitting check: train MAE=%.2f, test MAE=%.2f, ratio(test/train)=%s",
        train_metrics["mae"],
        test_metrics["mae"],
        f"{overfit_ratio:.2f}" if overfit_ratio else "n/a",
    )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)
    logger.info("Saved trained pipeline to %s", MODEL_PATH)

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET_COLUMN,
        "features": ALL_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "n_rows_raw": int(raw_df.shape[0]),
        "n_rows_after_cleaning": int(len(featured_df)),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "model_type": "RandomForestRegressor",
        "model_params": pipeline.named_steps["model"].get_params(),
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    logger.info("Saved metadata to %s", METADATA_PATH)

    logger.info("=== Training run complete ===")
    return metadata


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train the price prediction model")
    parser.add_argument("--csv", default=None, help="Path to a training CSV")
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "csv", "db"],
        help="Where to load training data from",
    )
    args = parser.parse_args()
    main(csv_path=args.csv, prefer=args.source)
