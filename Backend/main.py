import os
import yaml
import logging
from pathlib import Path
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
        logging.FileHandler(Path(__file__).resolve().parent / "scraper.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
    force=True
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

    rows_returned = {scraper.__class__.__name__: 0 for scraper in scrapers}
    rows_inserted = {scraper.__class__.__name__: 0 for scraper in scrapers}
    elapsed = {scraper.__class__.__name__: 0 for scraper in scrapers}
    failures = {scraper.__class__.__name__: 0 for scraper in scrapers}

    run_start = time.time()
    total_pairs = len(routes) * len(lead_times)
    total_scrapers = len(scrapers)
    pair_count = 0

    for route in routes:
        origin = route.get("origin")
        destination = route.get("destination")
        
        for lead_time in lead_times:
            travel_date = get_travel_date(lead_time)
            pair_count += 1
            
            scraper_idx = 0
            for scraper in scrapers:
                scraper_idx += 1
                scraper_name = scraper.__class__.__name__
                
                logger.info(f"[{scraper_name}] Scraping {origin}-{destination} for {travel_date} (lead: {lead_time} days)")
                
                start_time = time.time()
                n = 0
                
                try:
                    flights = scraper.scrape(origin, destination, travel_date, lead_time)
                    if flights:
                        n = len(flights)
                        rows_returned[scraper_name] += n
                        logger.info("--- DEBUG: First 5 extracted flights ---")
                        for f in flights[:5]:
                            logger.info(f"Flight: {f.get('flight_number')} | Dep: {f.get('departure_time')} | Scraped Hr: {f.get('scraped_hour')} | Fare: {f.get('total_fare')}")
                        logger.info("----------------------------------------")
                        
                        inserted = insert_flights(engine, flights)
                        rows_inserted[scraper_name] += inserted
                        logger.info(f"Inserted {inserted} / {n} flights (deduplicated).")
                        total_inserted += inserted
                    else:
                        logger.info("No flights extracted.")
                except Exception as e:
                    logger.exception(f"[{scraper_name}] Failed to scrape {origin}-{destination} for {travel_date} (lead: {lead_time} days): {e}")
                    failures[scraper_name] += 1
                
                secs = time.time() - start_time
                elapsed[scraper_name] += secs
                
                logger.info(f"[{scraper_name}] {origin}-{destination} lead={lead_time}: {n} rows in {secs:.1f}s")
                
                is_last_call = (pair_count == total_pairs) and (scraper_idx == total_scrapers)
                if not is_last_call:
                    scraper.random_delay(10, 25)

    for scraper in scrapers:
        scraper_name = scraper.__class__.__name__
        logger.info(f"Summary for {scraper_name}: Returned {rows_returned[scraper_name]} rows, Inserted {rows_inserted[scraper_name]} rows, Failures {failures[scraper_name]}, Scrape time (excl. delays) {elapsed[scraper_name]:.2f} seconds.")

    logger.info(f"Run completed. Total new flights inserted: {total_inserted}")
    total_wall_clock = time.time() - run_start
    logger.info(f"Total cycle wall-clock time: {total_wall_clock:.2f} seconds")

    pipeline_failed = False
    logger.info("Starting cleaning pipeline...")
    start_time_pipeline = time.time()
    try:
        run_pipeline()
        elapsed_time_pipeline = time.time() - start_time_pipeline
        logger.info(f"Cleaning pipeline completed successfully, Elapsed time {elapsed_time_pipeline:.2f} seconds.")
    except Exception:
        pipeline_failed = True
        logger.exception("Cleaning pipeline failed")

    logger.info("Starting index calculation...")
    if pipeline_failed:
        logger.warning("Cleaning pipeline failed previously. Index calculation is running on potentially stale clean data.")
        
    start_time_index = time.time()
    try:
        calculate_index()
        elapsed_time_index = time.time() - start_time_index
        logger.info(f"Index calculation completed successfully, Elapsed time {elapsed_time_index:.2f} seconds.")
    except Exception:
        logger.exception("Index calculation failed")

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
