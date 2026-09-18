import os
import math
import time
import random
import traceback
import yaml
import logging
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

from scraper.yatra import YatraScraper
from scraper.cleartrip import ClearTripScraper, CleartripBlocked
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

# --- Cleartrip anti-block settings -------------------------------------------
# Task Scheduler runs every 2 hours. Cleartrip only scrapes a rotating slice of
# the route x lead-time grid per run, so every cell is still covered several
# times a day while keeping per-IP search volume low.
RUN_INTERVAL_HOURS = 2
DEFAULT_CLEARTRIP_SEARCHES_PER_RUN = 10   # override with `cleartrip_searches_per_run` in config.yaml
CLEARTRIP_DELAY_SECONDS = (60, 120)        # random pause between Cleartrip searches
CLEARTRIP_MAX_CONSECUTIVE_FAILURES = 2     # stop Cleartrip for this run after this many failures in a row
YATRA_DELAY_SECONDS = (30, 90)             # unchanged from before


def load_config(config_path="config.yaml"):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_travel_date(lead_time_days: int) -> str:
    """Returns travel date in DD/MM/YYYY format based on lead time."""
    target_date = datetime.now() + timedelta(days=lead_time_days)
    return target_date.strftime("%d/%m/%Y")


def build_jobs(routes, lead_times):
    """All (origin, destination, lead_time) combinations, in config order."""
    return [
        (r.get("origin"), r.get("destination"), lt)
        for r in routes
        for lt in lead_times
    ]


def pick_rotating_batch(jobs, per_run, now=None):
    """
    Split jobs into ceil(len/per_run) interleaved batches and pick one based on the
    current 2-hour slot, so consecutive runs cycle through all batches.
    Returns (shuffled_batch, batch_number, total_batches).
    """
    if not jobs:
        return [], 0, 0
    per_run = max(1, int(per_run))
    n_batches = max(1, math.ceil(len(jobs) / per_run))
    now = now or datetime.now()
    slot = int(now.timestamp() // 3600) // RUN_INTERVAL_HOURS
    batch_idx = slot % n_batches
    batch = [job for i, job in enumerate(jobs) if i % n_batches == batch_idx]
    random.shuffle(batch)
    return batch, batch_idx + 1, n_batches


def run_scraper(scraper, jobs, engine, delay_range, max_consecutive_failures=None):
    """Scrape each job, insert results, pause between jobs. Returns (rows_returned, rows_inserted)."""
    name = scraper.__class__.__name__
    rows_returned = 0
    rows_inserted = 0
    consecutive_failures = 0

    for idx, (origin, destination, lead_time) in enumerate(jobs):
        travel_date = get_travel_date(lead_time)
        remaining = len(jobs) - idx - 1
        logger.info(f"[{name}] ({idx + 1}/{len(jobs)}) Scraping {origin}-{destination} for {travel_date} (lead: {lead_time} days)")

        import concurrent.futures
        from scraper.cleanup import cleanup_orphaned_browsers
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(scraper.scrape, origin, destination, travel_date, lead_time)
                flights = future.result(timeout=600)
            consecutive_failures = 0
            if flights:
                rows_returned += len(flights)
                logger.info("--- DEBUG: First 5 extracted flights ---")
                for f in flights[:5]:
                    logger.info(f"Flight: {f.get('flight_number')} | Dep: {f.get('departure_time')} | Scraped Hr: {f.get('scraped_hour')} | Fare: {f.get('total_fare')}")
                logger.info("----------------------------------------")

                inserted = insert_flights(engine, flights)
                rows_inserted += inserted
                logger.info(f"Inserted {inserted} / {len(flights)} flights (deduplicated).")
            else:
                logger.info("No flights extracted.")
        except CleartripBlocked as e:
            logger.warning(f"[{name}] Blocked ({e}). Skipping remaining {remaining} searches for this run.")
            break
        except concurrent.futures.TimeoutError:
            consecutive_failures += 1
            logger.error(f"[{name}] Timed out after 600s scraping {origin}-{destination} for {travel_date}")
            cleanup_orphaned_browsers()
            if max_consecutive_failures and consecutive_failures >= max_consecutive_failures:
                logger.warning(f"[{name}] {consecutive_failures} failures in a row. Skipping remaining {remaining} searches for this run.")
                break
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Failed to scrape {origin}-{destination} for {travel_date}: {e}")
            if max_consecutive_failures and consecutive_failures >= max_consecutive_failures:
                logger.warning(f"[{name}] {consecutive_failures} failures in a row. Skipping remaining {remaining} searches for this run.")
                break

        if remaining > 0:
            scraper.random_delay(*delay_range)

    return rows_returned, rows_inserted


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
        cleartrip_per_run = config.get("cleartrip_searches_per_run", DEFAULT_CLEARTRIP_SEARCHES_PER_RUN)
    except Exception as e:
        logger.critical(f"Failed to load config.yaml: {e}")
        return

    all_jobs = build_jobs(routes, lead_times)

    # Yatra: full grid, config order (unchanged behaviour)
    # Cleartrip: rotating, shuffled slice with longer gaps and stop-on-block
    ct_jobs, batch_no, n_batches = pick_rotating_batch(all_jobs, cleartrip_per_run)
    logger.info(f"Cleartrip batch {batch_no}/{n_batches}: {len(ct_jobs)} of {len(all_jobs)} searches this run.")

    plan = [
        (YatraScraper(), all_jobs, YATRA_DELAY_SECONDS, None),
        (ClearTripScraper(), ct_jobs, CLEARTRIP_DELAY_SECONDS, CLEARTRIP_MAX_CONSECUTIVE_FAILURES),
    ]

    total_inserted = 0

    for scraper, jobs, delay_range, max_failures in plan:
        scraper_name = scraper.__class__.__name__
        logger.info(f"Starting {scraper_name}")
        start_time = time.time()
        scraper_rows_returned = 0
        scraper_rows_inserted = 0

        try:
            scraper_rows_returned, scraper_rows_inserted = run_scraper(
                scraper, jobs, engine, delay_range, max_failures
            )
            total_inserted += scraper_rows_inserted
        except Exception:
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
