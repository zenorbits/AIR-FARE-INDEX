import os
import math
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

# Import our models
from index_calc.models import Base as IndexBase, AirfareIndex
# Import the cleaned data model
from cleaning.pipeline import FlightPriceClean

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# Reduce sqlalchemy logging noise
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# 1. Configurable constants for Base Period
# If both are None, they will be auto-detected from MIN(scraped_hour)
BASE_PERIOD_START = None
BASE_PERIOD_END = None

# Route weights based on DGCA city-pair passenger traffic, May 2026 (latest month with data for all 6 routes). Source: DGCA-published monthly domestic city-pair statistics.
ROUTE_WEIGHTS = {
    "DEL-BOM": 0.2987,
    "DEL-BLR": 0.2090,
    "BOM-BLR": 0.1877,
    "DEL-CCU": 0.1260,
    "MAA-DEL": 0.0918,
    "BLR-HYD": 0.0868,
}

def get_geometric_mean(data):
    """Calculate geometric mean. Uses statistics module if available (Python 3.8+), else custom math."""
    try:
        import statistics
        if hasattr(statistics, 'geometric_mean'):
            return statistics.geometric_mean(data)
    except ImportError:
        pass
    
    # Fallback
    return math.exp(sum(math.log(x) for x in data) / len(data))

def get_engine():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    return create_engine(database_url, pool_pre_ping=True)

def get_weekly_period(dt):
    """Returns (start_date, end_date) for the ISO week containing dt."""
    start = dt.date() - timedelta(days=dt.date().weekday())
    end = start + timedelta(days=6)
    return start, end

def get_monthly_period(dt):
    """Returns (start_date, end_date) for the calendar month containing dt."""
    start = dt.date().replace(day=1)
    next_month = start.replace(day=28) + timedelta(days=4)
    end = next_month - timedelta(days=next_month.day)
    return start, end

def get_daily_period(dt):
    """Returns (start_date, end_date) for the calendar date containing dt."""
    return dt.date(), dt.date()

def calculate_index():
    logger.info("Starting Jevons Airfare Index Calculation...")
    engine = get_engine()
    
    # Initialize index table if it doesn't exist
    IndexBase.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        global BASE_PERIOD_START, BASE_PERIOD_END
        
        # 1. Auto-detect base period if not set
        if BASE_PERIOD_START is None or BASE_PERIOD_END is None:
            min_date_val = session.query(func.min(FlightPriceClean.scraped_hour)).filter(FlightPriceClean.is_outlier == False).scalar()
            if min_date_val is None:
                logger.error("No valid data available in flight_prices_clean to determine base period.")
                return
            BASE_PERIOD_START = min_date_val.date()
            BASE_PERIOD_END = min_date_val.date()
            logger.info(f"Auto-detected BASE_PERIOD_START={BASE_PERIOD_START}, BASE_PERIOD_END={BASE_PERIOD_END}")
        
        # 2. Fetch base period data and calculate base prices
        logger.info("Fetching base period data...")
        base_data = session.query(
            FlightPriceClean.route,
            FlightPriceClean.lead_time_days,
            FlightPriceClean.total_fare
        ).filter(
            FlightPriceClean.is_outlier == False,
            FlightPriceClean.scraped_hour >= BASE_PERIOD_START,
            FlightPriceClean.scraped_hour < BASE_PERIOD_END + timedelta(days=1)
        ).all()
        
        base_prices = defaultdict(list)
        for row in base_data:
            base_prices[(row.route, row.lead_time_days)].append(row.total_fare)
            
        base_geomean = {}
        for key, fares in base_prices.items():
            base_geomean[key] = get_geometric_mean(fares)
            
        logger.info(f"Calculated base geometric means for {len(base_geomean)} route+lead_time combinations.")
        if not base_geomean:
            logger.warning("No base prices available! Exiting...")
            return

        # 3. Fetch all clean data to compute current periods
        logger.info("Fetching all clean data for index calculation...")
        all_data = session.query(
            FlightPriceClean.route,
            FlightPriceClean.lead_time_days,
            FlightPriceClean.scraped_hour,
            FlightPriceClean.total_fare
        ).filter(
            FlightPriceClean.is_outlier == False
        ).all()
        
        # Structure: period_map[frequency][(p_start, p_end)][(route, lead_time_days)] = [fare1, fare2...]
        period_map = {
            'daily': defaultdict(lambda: defaultdict(list)),
            'weekly': defaultdict(lambda: defaultdict(list)),
            'monthly': defaultdict(lambda: defaultdict(list)),
        }
        
        for row in all_data:
            dt = row.scraped_hour
            r_l_key = (row.route, row.lead_time_days)
            
            d_period = get_daily_period(dt)
            period_map['daily'][d_period][r_l_key].append(row.total_fare)
            
            w_period = get_weekly_period(dt)
            period_map['weekly'][w_period][r_l_key].append(row.total_fare)
            
            m_period = get_monthly_period(dt)
            period_map['monthly'][m_period][r_l_key].append(row.total_fare)
            
        # 4 & 5. Compute indices and prepare upsert
        indices_to_insert = []
        overall_index_latest = None
        latest_date = None
        
        for frequency, periods in period_map.items():
            for (p_start, p_end), route_lead_groups in periods.items():
                period_route_indices = defaultdict(list)
                
                # Per route per lead_time_days
                for (route, lead_time_days), fares in route_lead_groups.items():
                    if len(fares) < 2:
                        logger.debug(f"Skipping index for {route} T+{lead_time_days} {frequency} {p_start}: only {len(fares)} observations")
                        continue
                        
                    b_price = base_geomean.get((route, lead_time_days))
                    if not b_price:
                        logger.debug(f"Skipping index for {route} T+{lead_time_days} {frequency} {p_start}: no base period price")
                        continue
                        
                    c_geomean = get_geometric_mean(fares)
                    idx_val = 100.0 * (c_geomean / b_price)
                    
                    indices_to_insert.append({
                        'route': route,
                        'lead_time_days': lead_time_days,
                        'frequency': frequency,
                        'period_start': p_start,
                        'period_end': p_end,
                        'index_value': idx_val,
                        'num_observations': len(fares)
                    })
                    
                    period_route_indices[route].append(idx_val)
                    
                # Aggregate "overall" index
                # We compute a single overall APIx per period by taking a weighted sum 
                # across all routes' individual route-level indices using DGCA traffic weights.
                if period_route_indices:
                    present_routes = list(period_route_indices.keys())
                    raw_weights = {}
                    for r in present_routes:
                        if r not in ROUTE_WEIGHTS:
                            raise ValueError(f"Route '{r}' not found in ROUTE_WEIGHTS. Cannot compute overall index.")
                        raw_weights[r] = ROUTE_WEIGHTS[r]
                        
                    weight_sum = sum(raw_weights.values())
                    normalized_weights = {r: w / weight_sum for r, w in raw_weights.items()}
                    
                    overall_index = 0.0
                    for r, indices in period_route_indices.items():
                        r_idx = get_geometric_mean(indices)
                        overall_index += r_idx * normalized_weights[r]
                    
                    # Compute total observations that contributed to the index
                    total_obs = 0
                    for (route, lead_time_days), fares in route_lead_groups.items():
                        if len(fares) >= 2 and (route, lead_time_days) in base_geomean:
                            total_obs += len(fares)
                            
                    indices_to_insert.append({
                        'route': None,
                        'lead_time_days': None,
                        'frequency': frequency,
                        'period_start': p_start,
                        'period_end': p_end,
                        'index_value': overall_index,
                        'num_observations': total_obs
                    })
                    
                    # Track latest overall daily APIx
                    if frequency == 'daily':
                        if latest_date is None or p_start > latest_date:
                            latest_date = p_start
                            overall_index_latest = overall_index
                            
        # 6. Upsert records
        logger.info(f"Upserting {len(indices_to_insert)} computed index values...")
        existing_rows = session.query(AirfareIndex).all()
        existing_map = {
            (r.route, r.lead_time_days, r.frequency, r.period_start): r
            for r in existing_rows
        }
        
        insert_count = 0
        update_count = 0
        
        for row_dict in indices_to_insert:
            key = (row_dict['route'], row_dict['lead_time_days'], row_dict['frequency'], row_dict['period_start'])
            if key in existing_map:
                existing_obj = existing_map[key]
                existing_obj.index_value = row_dict['index_value']
                existing_obj.num_observations = row_dict['num_observations']
                existing_obj.period_end = row_dict['period_end']
                existing_obj.calculated_at = datetime.now()
                update_count += 1
            else:
                new_obj = AirfareIndex(**row_dict)
                session.add(new_obj)
                existing_map[key] = new_obj
                insert_count += 1
                
        session.commit()
        
        # 7. Print summary
        freq_counts = defaultdict(int)
        for row_dict in indices_to_insert:
            freq_counts[row_dict['frequency']] += 1
            
        logger.info("--- APIx Calculation Summary ---")
        for freq in ['daily', 'weekly', 'monthly']:
            logger.info(f"Calculated {freq_counts[freq]} {freq} index values.")
        
        if overall_index_latest is not None:
            logger.info(f"Most recent overall daily APIx (for {latest_date}): {overall_index_latest:.2f}")
        else:
            logger.info("No overall daily APIx was computed.")
        logger.info("--------------------------------")

if __name__ == "__main__":
    calculate_index()
