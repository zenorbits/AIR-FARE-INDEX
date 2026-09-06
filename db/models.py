from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class FlightPrice(Base):
    __tablename__ = "flight_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    route = Column(String(20), nullable=False) # e.g., DEL-BOM
    airline = Column(String(100), nullable=False)
    flight_number = Column(String(50), nullable=False)
    cabin_class = Column(String(50), nullable=False)
    base_fare = Column(Float, nullable=True)
    taxes_fees = Column(Float, nullable=True)
    total_fare = Column(Float, nullable=False)
    stops = Column(Integer, nullable=False)
    lead_time_days = Column(Integer, nullable=False)
    departure_time = Column(DateTime, nullable=False)
    scraped_at = Column(DateTime, default=datetime.now, nullable=False)
    scraped_hour = Column(DateTime, nullable=False) # Date + Hour only for deduplication

    __table_args__ = (
        UniqueConstraint(
            "flight_number", 
            "departure_time", 
            "scraped_hour", 
            name="uq_flight_departure_scraped_hour"
        ),
    )

    def __repr__(self):
        return f"<FlightPrice(route={self.route}, flight={self.flight_number}, fare={self.total_fare})>"
