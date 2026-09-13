import os
import yaml
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from scraper.yatra import YatraScraper
from scraper.cleartrip import ClearTripScraper
from db.database import get_engine, init_db, insert_flights
from cleaning.pipeline import run_pipeline
from index_calc.jevons import calculate_index

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Retrain the price prediction model once per day rather than on every
# 2-hourly run: retraining is cheap but rewrites the model artifact, and a
# stable daily version makes predictions reproducible between runs.
RETRAIN_AT_HOUR = 2

def load_config(config_path="config.yaml"):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

def get_travel_date(lead_time_days: int) -> str:
    """Returns travel date in DD/MM/YYYY format based on lead time."""
    target_date = datetime.now() + timedelta(days=lead_time_days)
    return target_date.strftime("%d/%m/%Y")

def main():
    logger.info("Starting flight scraping run.")
    
    # Load env vars
    load_dotenv()
    
    # Cleanup orphaned browsers from previous runs
    from scraper.cleanup import cleanup_orphaned_browsers
    cleanup_orphaned_browsers()
    
    # Initialize DB
    try:
        engine = get_engine()
        init_db(engine)
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        return

    # Load configuration
    try:
        config = load_config()
        routes = config.get("routes", [])
        lead_times = config.get("lead_time_days", [])
    except Exception as e:
        logger.critical(f"Failed to load config.yaml: {e}")
        return

    # Initialize scrapers
    scrapers = [YatraScraper(), ClearTripScraper()]
    
    total_inserted = 0

    import time
    import traceback

    for scraper in scrapers:
        scraper_name = scraper.__class__.__name__
        logger.info(f"Starting {scraper_name}")
        start_time = time.time()
        scraper_rows_returned = 0
        scraper_rows_inserted = 0
        
        try:
            for route in routes:
                origin = route.get("origin")
                destination = route.get("destination")
                
                for lead_time in lead_times:
                    travel_date = get_travel_date(lead_time)
                    
                    logger.info(f"Scraping {origin}-{destination} for {travel_date} (lead: {lead_time} days)")
                    
                    try:
                        flights = scraper.scrape(origin, destination, travel_date, lead_time)
                        if flights:
                            scraper_rows_returned += len(flights)
                            logger.info("--- DEBUG: First 5 extracted flights ---")
                            for f in flights[:5]:
                                logger.info(f"Flight: {f.get('flight_number')} | Dep: {f.get('departure_time')} | Scraped Hr: {f.get('scraped_hour')} | Fare: {f.get('total_fare')}")
                            logger.info("----------------------------------------")
                            
                            inserted = insert_flights(engine, flights)
                            scraper_rows_inserted += inserted
                            logger.info(f"Inserted {inserted} / {len(flights)} flights (deduplicated).")
                            total_inserted += inserted
                        else:
                            logger.info("No flights extracted.")
                    except Exception as e:
                        logger.error(f"Failed to scrape {origin}-{destination} for {travel_date}: {e}")
                    
                    # Jitter between requests to avoid rate limits
                    scraper.random_delay(30, 90)
        except Exception as e:
            logger.error(f"Uncaught exception in scraper {scraper_name}:\n{traceback.format_exc()}")
            
        elapsed_time = time.time() - start_time
        logger.info(f"Summary for {scraper_name}: Returned {scraper_rows_returned} rows, Inserted {scraper_rows_inserted} rows, Elapsed time {elapsed_time:.2f} seconds.")

    logger.info(f"Run completed. Total new flights inserted: {total_inserted}")

    pipeline_failed = False
    logger.info("Starting cleaning pipeline...")
    start_time_pipeline = time.time()
    try:
        run_pipeline()
        elapsed_time_pipeline = time.time() - start_time_pipeline
        logger.info(f"Cleaning pipeline completed successfully, Elapsed time {elapsed_time_pipeline:.2f} seconds.")
    except Exception as e:
        pipeline_failed = True
        logger.error(f"Cleaning pipeline failed:\n{traceback.format_exc()}")

    logger.info("Starting index calculation...")
    if pipeline_failed:
        logger.warning("Cleaning pipeline failed previously. Index calculation is running on potentially stale clean data.")
        
    start_time_index = time.time()
    try:
        calculate_index()
        elapsed_time_index = time.time() - start_time_index
        logger.info(f"Index calculation completed successfully, Elapsed time {elapsed_time_index:.2f} seconds.")
    except Exception as e:
        logger.error(f"Index calculation failed:\n{traceback.format_exc()}")

    current_hour = datetime.now().hour
    if current_hour == RETRAIN_AT_HOUR:
        logger.info("Starting price model retraining (scheduled daily at hour %d)...", RETRAIN_AT_HOUR)
        start_time_train = time.time()
        try:
            from ml.train import main as train_model
            metadata = train_model(prefer="db")
            elapsed_time_train = time.time() - start_time_train
            test_metrics = metadata.get("test_metrics", {})
            logger.info(
                "Price model retrained successfully in %.2f seconds. "
                "Rows: %s, test MAE: %.2f, test R2: %.3f",
                elapsed_time_train,
                metadata.get("n_rows_after_cleaning"),
                test_metrics.get("mae", float("nan")),
                test_metrics.get("r2", float("nan")),
            )
        except Exception:
            logger.error(f"Price model retraining failed:\n{traceback.format_exc()}")
    else:
        logger.info(
            "Skipping price model retraining (runs at hour %d, current hour is %d)",
            RETRAIN_AT_HOUR,
            current_hour,
        )

if __name__ == "__main__":
    main()
