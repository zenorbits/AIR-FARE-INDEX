import os
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, FlightPrice
from db.database import insert_flights

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL environment variable is not set. Skipping postgres tests."
)

@pytest.fixture
def test_engine():
    engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()

def get_base_flight_data():
    return {
        "source": "yatra",
        "route": "DEL-BOM",
        "airline": "IndiGo",
        "flight_number": "6E-123",
        "cabin_class": "ECONOMY",
        "base_fare": 3000.0,
        "taxes_fees": 500.0,
        "total_fare": 3500.0,
        "stops": 0,
        "lead_time_days": 7,
        "departure_time": datetime(2026, 10, 1, 10, 0, 0),
        "scraped_hour": datetime(2026, 9, 24, 10, 0, 0)
    }

# Note: insert_flights() returns 0 even on successful inserts due to a known 
# SQLAlchemy rowcount quirk with ON CONFLICT DO NOTHING. These tests verify
# persisted state directly rather than checking the return value.

def test_same_flight_different_sources_both_insert(test_engine):
    flight1 = get_base_flight_data()
    flight1["source"] = "yatra"
    
    flight2 = get_base_flight_data()
    flight2["source"] = "cleartrip"
    
    # Insert the yatra row, then the cleartrip row
    insert_flights(test_engine, [flight1])
    insert_flights(test_engine, [flight2])
    
    Session = sessionmaker(bind=test_engine)
    with Session() as session:
        rows = session.query(FlightPrice).all()
        assert len(rows) == 2
        sources = {r.source for r in rows}
        assert sources == {"yatra", "cleartrip"}

def test_true_duplicate_is_ignored(test_engine):
    flight1 = get_base_flight_data()
    flight2 = get_base_flight_data()
    
    # Insert the same row twice (identical source)
    insert_flights(test_engine, [flight1])
    # Assert no exception was raised by doing this a second time
    insert_flights(test_engine, [flight2])
    
    Session = sessionmaker(bind=test_engine)
    with Session() as session:
        rows = session.query(FlightPrice).all()
        assert len(rows) == 1
