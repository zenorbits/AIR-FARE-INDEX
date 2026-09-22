import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import time
from datetime import datetime

from dotenv import load_dotenv

from scraper.indigo import IndigoScraper
from db.database import get_engine, init_db, insert_flights
from main import load_config
from pathlib import Path

log_dir = Path(__file__).resolve().parent / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("run_indigo")
logger.setLevel(logging.INFO)

log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

# Rotating log: 5MB * 3 backups
file_handler = RotatingFileHandler(
    log_dir / "indigo.log", 
    maxBytes=5*1024*1024, 
    backupCount=3, 
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# Also console
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)


def build_jobs(routes, lead_times):
    return [(r[0], r[1], lt) for r in routes for lt in lead_times]

def main():
    try:
        _run()
    except Exception:
        logger.exception("run_indigo crashed")
        raise


def _run():
    logger.info("Starting run_indigo standalone entry point.")
    load_dotenv()
    
    quick_mode = "--quick" in sys.argv
    
    try:
        engine = get_engine()
        init_db(engine)
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        return

    try:
        config = load_config()
        routes = [(r["origin"], r["destination"]) for r in config.get("routes", [])]
        lead_times = config.get("lead_time_days", [])
    except Exception as e:
        logger.critical(f"Failed to load config.yaml: {e}")
        return

    if quick_mode:
        # Override for quick test
        routes = [("DEL", "BOM"), ("DEL", "BLR")]
        lead_times = [7, 14]

    jobs = build_jobs(routes, lead_times)

    scraper = IndigoScraper()
    if quick_mode:
        scraper.quick_mode = True

    start_time = time.time()
    logger.info(f"Indigo batch scraping {len(jobs)} jobs...")
    
    try:
        # returns all_results, attempts, successes, blocked_count, row_counts, search_outcomes
        all_results, attempts, successes, blocked_count, row_counts, search_outcomes = scraper.scrape_batch(jobs)
    except Exception as e:
        import traceback
        logger.error(f"Indigo batch failed with exception:\n{traceback.format_exc()}")
        return

    elapsed_time = time.time() - start_time
    
    timeouts = sum(1 for o in search_outcomes if o["outcome"] == "TIMEOUT-other")
    
    # Summary line
    logger.info(
        f"[indigo] summary: ok={successes} blocked={blocked_count} timeout={timeouts} "
        f"rows={len(all_results)} runtime={elapsed_time:.2f}s"
    )

    if all_results:
        try:
            inserted = insert_flights(engine, all_results)
            logger.info(f"Inserted {inserted} / {len(all_results)} flights (deduplicated).")
        except Exception as e:
            logger.error(f"Failed to insert flights: {e}")

if __name__ == "__main__":
    main()
