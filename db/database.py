import os
import logging
from typing import List, Dict, Any
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv

from .models import Base, FlightPrice

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def get_engine():
    """Create and return a SQLAlchemy engine using the DATABASE_URL environment variable."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return create_engine(database_url, pool_pre_ping=True)

def init_db(engine):
    """Create all tables if they do not exist."""
    Base.metadata.create_all(engine)
    logger.info("Database tables initialized.")

def insert_flights(engine, flights_data: List[Dict[str, Any]]) -> int:
    """
    Insert flights into the database. 
    Deduplicates using ON CONFLICT DO NOTHING based on the unique constraint.
    """
    if not flights_data:
        return 0

    Session = sessionmaker(bind=engine)
    inserted_count = 0

    with Session() as session:
        for flight in flights_data:
            stmt = insert(FlightPrice).values(**flight)
            
            # On conflict (source, flight_number, departure_time, scraped_hour), do nothing
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["source", "flight_number", "departure_time", "scraped_hour"]
            )
            
            result = session.execute(stmt)
            if result.rowcount > 0:
                inserted_count += 1
                
        session.commit()
    
    return inserted_count
