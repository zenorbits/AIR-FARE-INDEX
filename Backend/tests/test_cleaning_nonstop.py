import os
import pytest
import logging
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, FlightPrice
from db.database import insert_flights
from cleaning.pipeline import run_pipeline, FlightPriceClean, BaseClean

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL environment variable is not set. Skipping postgres tests."
)

@pytest.fixture
def test_engine():
    engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    BaseClean.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    BaseClean.metadata.drop_all(engine)
    engine.dispose()

def get_base_flight_data():
    return {
        "source": "yatra",
        "route": "DEL-BOM",
        "airline": "IndiGo",
        "cabin_class": "ECONOMY",
        "base_fare": 3000.0,
        "taxes_fees": 500.0,
        "total_fare": 3500.0,
        "lead_time_days": 7,
        "departure_time": datetime(2026, 10, 1, 10, 0, 0),
        "scraped_hour": datetime(2026, 9, 24, 10, 0, 0)
    }

def test_pipeline_excludes_connecting_flights(test_engine, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    
    # Create flights with stops = 0, stops = 1, and stops = None
    flight_0 = get_base_flight_data()
    flight_0["flight_number"] = "6E-100"
    flight_0["stops"] = 0
    
    flight_1 = get_base_flight_data()
    flight_1["flight_number"] = "6E-101"
    flight_1["stops"] = 1
    
    # flight_none can't be inserted because stops is NOT NULL in the schema
    flight_none = get_base_flight_data()
    flight_none["flight_number"] = "6E-102"
    flight_none["stops"] = None
    
    # We will insert flight_0 and flight_1 to test the DB logic and mock raw_rows to include flight_none
    insert_flights(test_engine, [flight_0, flight_1])
    
    # We must patch session.query to return flight_none as well
    from cleaning.pipeline import FlightPrice
    
    real_sessionmaker = sessionmaker(bind=test_engine)
    
    class DummyRow:
        def __init__(self, d):
            for k, v in d.items():
                setattr(self, k, v)
                
    class MockSession:
        def __init__(self, *args, **kwargs):
            self.real_session = real_sessionmaker()
        def query(self, *args, **kwargs):
            return self.real_session.query(*args, **kwargs)
        def add_all(self, *args, **kwargs):
            return self.real_session.add_all(*args, **kwargs)
        def commit(self):
            return self.real_session.commit()
        def close(self):
            return self.real_session.close()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.real_session.close()

    def mock_all(query_obj):
        res = query_obj.session.execute(query_obj.statement).scalars().all()
        # If querying FlightPrice, append flight_none
        if hasattr(query_obj, 'column_descriptions') and len(query_obj.column_descriptions) > 0:
            if query_obj.column_descriptions[0]['type'] == FlightPrice:
                res.append(DummyRow(flight_none))
        return res
        
    monkeypatch.setattr("sqlalchemy.orm.Query.all", mock_all)
    
    with caplog.at_level(logging.INFO):
        run_pipeline()
    
    # Check logs
    assert "Connecting rows excluded (stops > 0): 2" in caplog.text
    
    # Check DB
    Session = sessionmaker(bind=test_engine)
    with Session() as session:
        clean_rows = session.query(FlightPriceClean).all()
        assert len(clean_rows) == 1
        assert clean_rows[0].stops == 0
        assert clean_rows[0].flight_number == "6E-100"
