import os
import logging
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime
from dotenv import load_dotenv

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

from db.models import FlightPrice

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BaseClean = declarative_base()

class FlightPriceClean(BaseClean):
    __tablename__ = "flight_prices_clean"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    route = Column(String(20), nullable=False) 
    airline = Column(String(100), nullable=False)
    flight_number = Column(String(50), nullable=False)
    cabin_class = Column(String(50), nullable=False)
    base_fare = Column(Float, nullable=True)
    taxes_fees = Column(Float, nullable=True)
    total_fare = Column(Float, nullable=False)
    stops = Column(Integer, nullable=False)
    lead_time_days = Column(Integer, nullable=False)
    departure_time = Column(DateTime, nullable=False)
    scraped_at = Column(DateTime, nullable=False)
    scraped_hour = Column(DateTime, nullable=False)
    
    is_outlier = Column(Boolean, default=False)
    cleaning_notes = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "flight_number", 
            "departure_time", 
            "scraped_hour", 
            "source",
            name="uq_flight_dep_scraped_src_clean"
        ),
    )


def get_engine():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return create_engine(database_url, pool_pre_ping=True)


class CleanRow:
    def __init__(self, raw_row):
        self.source = raw_row.source
        self.route = raw_row.route
        self.airline = raw_row.airline
        self.flight_number = raw_row.flight_number
        self.cabin_class = raw_row.cabin_class
        self.base_fare = raw_row.base_fare
        self.taxes_fees = raw_row.taxes_fees
        self.total_fare = raw_row.total_fare
        self.stops = raw_row.stops
        self.lead_time_days = raw_row.lead_time_days
        self.departure_time = raw_row.departure_time
        self.scraped_at = raw_row.scraped_at
        self.scraped_hour = raw_row.scraped_hour
        self.is_outlier = False
        self.notes = []


def run_pipeline():
    logger.info("Starting cleaning pipeline...")
    engine = get_engine()
    
    # Create the clean table if it doesn't exist
    BaseClean.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        logger.info("Fetching existing clean records to support incremental runs...")
        # To avoid reprocessing, get keys already in flight_prices_clean
        # Keys: (flight_number, departure_time, scraped_hour, source)
        clean_keys_query = session.query(
            FlightPriceClean.flight_number,
            FlightPriceClean.departure_time,
            FlightPriceClean.scraped_hour,
            FlightPriceClean.source
        ).all()
        processed_keys = set(clean_keys_query)
        
        logger.info("Fetching raw records from flight_prices...")
        raw_rows = session.query(FlightPrice).all()
        total_rows_read = len(raw_rows)
        
        zero_flight_rows_excluded = 0
        null_fare_rows_excluded = 0
        connecting_rows_excluded = 0
        rows_flagged_as_outliers = 0
        rows_with_mismatch = 0
        rows_with_missing_base_tax = 0
        duplicates_skipped = 0
        
        valid_rows = []
        # Local deduplication set for the current batch
        batch_keys = set()
        
        for raw in raw_rows:
            key = (raw.flight_number, raw.departure_time, raw.scraped_hour, raw.source)
            if key in processed_keys:
                duplicates_skipped += 1
                continue
                
            if key in batch_keys:
                duplicates_skipped += 1
                continue
                
            batch_keys.add(key)
            
            # Step 1: Zero-fare / no-flights-found
            if raw.total_fare == 0 or (raw.base_fare == 0 and raw.taxes_fees == 0 and raw.total_fare == 0):
                zero_flight_rows_excluded += 1
                continue
                
            # Step 2: Missing total_fare
            if raw.total_fare is None:
                null_fare_rows_excluded += 1
                continue
                
            # Step 2.5: Nonstop flights only. 
            # Cleartrip returns only nonstop flights and Yatra returns connecting ones too, 
            # so nonstop-only keeps the sources comparable for the Jevons index.
            if raw.stops is None or raw.stops != 0:
                connecting_rows_excluded += 1
                continue
                
            c_row = CleanRow(raw)
            
            # Step 3: Base/tax consistency check
            has_mismatch = False
            has_missing = False
            
            if c_row.base_fare is not None and c_row.taxes_fees is not None:
                if abs((c_row.base_fare + c_row.taxes_fees) - c_row.total_fare) > (0.01 * c_row.total_fare):
                    c_row.notes.append("base_fare + taxes_fees mismatch")
                    has_mismatch = True
            elif c_row.total_fare > 0 and (c_row.base_fare is None or c_row.taxes_fees is None):
                c_row.notes.append("base_fare/taxes_fees missing")
                has_missing = True
                
            if has_mismatch:
                rows_with_mismatch += 1
            if has_missing:
                rows_with_missing_base_tax += 1
                
            valid_rows.append(c_row)
            
        # Step 4: Outlier detection
        groups = defaultdict(list)
        for r in valid_rows:
            groups[(r.route, r.lead_time_days)].append(r)
            
        for (route, lead_time), group_rows in groups.items():
            if len(group_rows) < 5:
                for r in group_rows:
                    r.notes.append("insufficient data for outlier check")
            else:
                fares = [r.total_fare for r in group_rows]
                m = mean(fares)
                s = stdev(fares)
                for r in group_rows:
                    if r.total_fare < 500 or r.total_fare > 100000:
                        r.is_outlier = True
                        rows_flagged_as_outliers += 1
                    elif s > 0 and abs(r.total_fare - m) > 3 * s:
                        r.is_outlier = True
                        rows_flagged_as_outliers += 1
                        
        # Insert into FlightPriceClean
        clean_objects = []
        for r in valid_rows:
            note_str = " | ".join(r.notes) if r.notes else None
            
            clean_obj = FlightPriceClean(
                source=r.source,
                route=r.route,
                airline=r.airline,
                flight_number=r.flight_number,
                cabin_class=r.cabin_class,
                base_fare=r.base_fare,
                taxes_fees=r.taxes_fees,
                total_fare=r.total_fare,
                stops=r.stops,
                lead_time_days=r.lead_time_days,
                departure_time=r.departure_time,
                scraped_at=r.scraped_at,
                scraped_hour=r.scraped_hour,
                is_outlier=r.is_outlier,
                cleaning_notes=note_str
            )
            clean_objects.append(clean_obj)
            
        if clean_objects:
            logger.info(f"Inserting {len(clean_objects)} clean records into flight_prices_clean...")
            session.add_all(clean_objects)
            session.commit()
            
        logger.info("--- Cleaning Pipeline Summary ---")
        logger.info(f"Total rows read: {total_rows_read}")
        logger.info(f"Previously processed / Duplicates skipped: {duplicates_skipped}")
        logger.info(f"Zero flight rows excluded: {zero_flight_rows_excluded}")
        logger.info(f"Null fare rows excluded: {null_fare_rows_excluded}")
        logger.info(f"Connecting rows excluded (stops > 0): {connecting_rows_excluded}")
        logger.info(f"Rows flagged as outliers: {rows_flagged_as_outliers}")
        logger.info(f"Rows with base/tax mismatch notes: {rows_with_mismatch}")
        logger.info(f"Rows with missing base/tax data notes: {rows_with_missing_base_tax}")
        logger.info(f"Successfully processed and inserted: {len(clean_objects)}")
        logger.info("---------------------------------")

if __name__ == "__main__":
    run_pipeline()
