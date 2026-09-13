"""
Tests for the Price Prediction feature (Step 13 of the project plan):

1. Dataset loading            -> TestDataLoading
2. Feature preprocessing      -> TestFeatureEngineering
3. Model training             -> TestModelTraining
4. Prediction function        -> TestPredictionFunction
5. API prediction endpoint    -> TestPredictPriceEndpoint (skipped if
   fastapi isn't installed in the environment running pytest)

Run with:  pytest tests/test_price_prediction.py -v
(run from the "AIR-FARE-INDEX Backend" directory)
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from ml.data_loader import DEFAULT_CSV_PATH, load_from_csv, load_training_data
from ml.features import (
    ALL_FEATURES,
    TARGET_COLUMN,
    build_feature_frame,
    canonicalize_airline,
    clean_dataframe,
    normalize_airline_input,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RAW_COLUMNS = [
    "id", "source", "route", "airline", "flight_number", "cabin_class",
    "base_fare", "taxes_fees", "total_fare", "stops", "lead_time_days",
    "departure_time", "scraped_at", "scraped_hour", "is_outlier", "cleaning_notes",
]


def _make_synthetic_rows(n_per_group=8):
    """Build a small but structurally-realistic synthetic dataset so
    training/splitting has enough rows per group to run without error."""
    rows = []
    rid = 1
    routes = ["DEL-BOM", "DEL-BLR"]
    airlines = [("6E", "6E-101"), ("Air India", "AI-202")]
    lead_times = [1, 7, 15, 30]
    for route in routes:
        for airline, fn in airlines:
            for lead in lead_times:
                for i in range(n_per_group):
                    base = 5000 + hash((route, airline, lead)) % 2000
                    fare = base + i * 10
                    rows.append({
                        "id": rid,
                        "source": "testsrc",
                        "route": route,
                        "airline": airline,
                        "flight_number": fn,
                        "cabin_class": "ECONOMY" if i % 2 == 0 else "Economy",
                        "base_fare": fare * 0.8,
                        "taxes_fees": fare * 0.2,
                        "total_fare": float(fare),
                        "stops": 0,
                        "lead_time_days": lead,
                        "departure_time": f"2026-11-{(i % 27) + 1:02d} {(i % 24):02d}:00:00",
                        "scraped_at": pd.Timestamp("2026-09-01") + pd.Timedelta(hours=rid),
                        "scraped_hour": pd.Timestamp("2026-09-01") + pd.Timedelta(hours=rid),
                        "is_outlier": False,
                        "cleaning_notes": None,
                    })
                    rid += 1
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


@pytest.fixture
def synthetic_df():
    return _make_synthetic_rows()


# ---------------------------------------------------------------------------
# 1. Dataset loading
# ---------------------------------------------------------------------------

class TestDataLoading:
    def test_bundled_csv_exists(self):
        assert DEFAULT_CSV_PATH.exists(), "bundled sample CSV should ship with the project"

    def test_load_from_csv_returns_expected_columns(self):
        df = load_from_csv()
        for col in RAW_COLUMNS:
            assert col in df.columns
        assert len(df) > 0

    def test_load_training_data_csv_path_override(self, tmp_path, synthetic_df):
        path = tmp_path / "custom.csv"
        synthetic_df.to_csv(path, index=False)
        df = load_training_data(csv_path=path, prefer="csv")
        assert len(df) == len(synthetic_df)

    def test_load_training_data_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_training_data(csv_path=tmp_path / "nope.csv", prefer="csv")


# ---------------------------------------------------------------------------
# 2. Feature preprocessing
# ---------------------------------------------------------------------------

class TestFeatureEngineering:
    def test_airline_canonicalization_by_flight_number(self):
        assert canonicalize_airline("AI", "AI-2524") == "Air India"
        assert canonicalize_airline("Air India", "AI-2524") == "Air India"
        assert canonicalize_airline("6E", "6E-948") == "IndiGo"
        assert canonicalize_airline("IndiGo", "6E-948") == "IndiGo"

    def test_normalize_airline_input_accepts_code_or_name_case_insensitive(self):
        assert normalize_airline_input("6e") == "IndiGo"
        assert normalize_airline_input("indigo") == "IndiGo"
        assert normalize_airline_input("AI") == "Air India"
        assert normalize_airline_input("SomeNewAirline") == "SomeNewAirline"

    def test_clean_dataframe_normalizes_cabin_class(self, synthetic_df):
        cleaned = clean_dataframe(synthetic_df)
        assert set(cleaned["cabin_class"].unique()) == {"ECONOMY"}

    def test_clean_dataframe_drops_outliers(self, synthetic_df):
        df = synthetic_df.copy()
        df.loc[0, "is_outlier"] = True
        cleaned = clean_dataframe(df)
        assert len(cleaned) == len(df) - 1

    def test_clean_dataframe_drops_non_positive_fares(self, synthetic_df):
        df = synthetic_df.copy()
        df.loc[0, "total_fare"] = 0
        cleaned = clean_dataframe(df)
        assert len(cleaned) == len(df) - 1

    def test_clean_dataframe_raises_on_missing_columns(self):
        with pytest.raises(ValueError):
            clean_dataframe(pd.DataFrame({"route": ["DEL-BOM"]}))

    def test_build_feature_frame_has_no_leakage_columns(self, synthetic_df):
        feat = build_feature_frame(synthetic_df)
        assert "base_fare" not in feat.columns
        assert "taxes_fees" not in feat.columns
        for col in ALL_FEATURES:
            assert col in feat.columns
        assert TARGET_COLUMN in feat.columns

    def test_build_feature_frame_derives_time_features(self, synthetic_df):
        feat = build_feature_frame(synthetic_df)
        assert feat["departure_hour"].between(0, 23).all()
        assert feat["departure_day_of_week"].between(0, 6).all()
        assert feat["departure_month"].between(1, 12).all()


# ---------------------------------------------------------------------------
# 3. Model training
# ---------------------------------------------------------------------------

class TestModelTraining:
    def test_training_smoke_test_produces_model_and_metrics(self, tmp_path, synthetic_df, monkeypatch):
        import ml.train as train_module

        csv_path = tmp_path / "synthetic.csv"
        synthetic_df.to_csv(csv_path, index=False)

        # Redirect saved-model output so this test never overwrites the
        # real trained model artifact.
        monkeypatch.setattr(train_module, "MODEL_DIR", tmp_path / "saved_models")
        monkeypatch.setattr(train_module, "MODEL_PATH", tmp_path / "saved_models" / "model.joblib")
        monkeypatch.setattr(train_module, "METADATA_PATH", tmp_path / "saved_models" / "meta.json")

        metadata = train_module.main(csv_path=str(csv_path), prefer="csv")

        assert train_module.MODEL_PATH.exists()
        assert train_module.METADATA_PATH.exists()
        assert metadata["n_train"] > 0
        assert metadata["n_test"] > 0
        assert "mae" in metadata["test_metrics"]
        assert "rmse" in metadata["test_metrics"]
        assert "r2" in metadata["test_metrics"]
        # MAE should be a finite, non-negative number
        assert metadata["test_metrics"]["mae"] >= 0


# ---------------------------------------------------------------------------
# 4. Prediction function
# ---------------------------------------------------------------------------

class TestPredictionFunction:
    """Uses the REAL trained model at ml/saved_models/price_model.joblib
    (produced by `python -m ml.train`). Skips if it hasn't been trained
    yet in this environment."""

    @pytest.fixture(autouse=True)
    def _require_trained_model(self):
        from ml.train import MODEL_PATH
        if not MODEL_PATH.exists():
            pytest.skip("Run `python -m ml.train` before running prediction tests")

    def test_valid_prediction_returns_positive_float(self):
        from ml.predict import predict_fare
        fare = predict_fare(
            route="DEL-BOM", airline="IndiGo", cabin_class="Economy",
            lead_time_days=7, stops=0, departure_hour=10,
            departure_day_of_week=5, departure_month=9,
        )
        assert isinstance(fare, float)
        assert fare > 0

    def test_airline_code_and_name_give_same_prediction(self):
        from ml.predict import predict_fare
        p1 = predict_fare("DEL-BOM", "6E", "Economy", 7, 0, 10, 5, 9)
        p2 = predict_fare("DEL-BOM", "IndiGo", "Economy", 7, 0, 10, 5, 9)
        assert p1 == p2

    def test_unknown_airline_does_not_crash(self):
        from ml.predict import predict_fare
        fare = predict_fare("DEL-BOM", "BrandNewAirline", "Economy", 7, 0, 10, 5, 9)
        assert isinstance(fare, float)

    def test_unknown_route_does_not_crash(self):
        from ml.predict import predict_fare
        fare = predict_fare("XXX-YYY", "IndiGo", "Economy", 7, 0, 10, 5, 9)
        assert isinstance(fare, float)

    def test_negative_lead_time_days_raises(self):
        from ml.predict import predict_fare, PredictionInputError
        with pytest.raises(PredictionInputError):
            predict_fare("DEL-BOM", "IndiGo", "Economy", -5, 0, 10, 5, 9)

    def test_negative_stops_raises(self):
        from ml.predict import predict_fare, PredictionInputError
        with pytest.raises(PredictionInputError):
            predict_fare("DEL-BOM", "IndiGo", "Economy", 7, -1, 10, 5, 9)

    def test_invalid_departure_hour_raises(self):
        from ml.predict import predict_fare, PredictionInputError
        with pytest.raises(PredictionInputError):
            predict_fare("DEL-BOM", "IndiGo", "Economy", 7, 0, 25, 5, 9)


# ---------------------------------------------------------------------------
# 5. API prediction endpoint
# ---------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed in this environment")


class TestPredictPriceEndpoint:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb"
        )
        monkeypatch.setenv("API_KEY", "test-api-key")

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app, headers={"X-API-Key": "test-api-key"})

    def _payload(self, **overrides):
        base = {
            "route": "DEL-BOM",
            "airline": "IndiGo",
            "cabin_class": "Economy",
            "lead_time_days": 7,
            "stops": 0,
            "departure_hour": 10,
            "departure_day_of_week": 5,
            "departure_month": 9,
        }
        base.update(overrides)
        return base

    def test_valid_request_returns_predicted_fare(self, client):
        r = client.post("/predict-price", json=self._payload())
        assert r.status_code == 200
        body = r.json()
        assert "predicted_fare" in body
        assert isinstance(body["predicted_fare"], (int, float))
        assert body["predicted_fare"] > 0

    def test_unknown_airline_still_succeeds(self, client):
        r = client.post("/predict-price", json=self._payload(airline="SomeNewAirline"))
        assert r.status_code == 200

    def test_unknown_route_still_succeeds(self, client):
        r = client.post("/predict-price", json=self._payload(route="XXX-YYY"))
        assert r.status_code == 200

    def test_negative_lead_time_days_returns_422(self, client):
        r = client.post("/predict-price", json=self._payload(lead_time_days=-5))
        assert r.status_code == 422

    def test_invalid_stops_returns_422(self, client):
        r = client.post("/predict-price", json=self._payload(stops=-1))
        assert r.status_code == 422

    def test_missing_required_field_returns_422(self, client):
        payload = self._payload()
        del payload["route"]
        r = client.post("/predict-price", json=payload)
        assert r.status_code == 422

    def test_invalid_departure_hour_returns_422(self, client):
        r = client.post("/predict-price", json=self._payload(departure_hour=99))
        assert r.status_code == 422
