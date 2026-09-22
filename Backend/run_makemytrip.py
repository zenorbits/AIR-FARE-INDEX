import sys
import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Task Scheduler may start us from any working directory: anchor everything on this file's location.
BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from scraper import makemytrip  # noqa: E402

log_dir = BACKEND / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("run_makemytrip")
logger.setLevel(logging.INFO)

log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

# Rotating log: 5MB * 3 backups
file_handler = RotatingFileHandler(
    log_dir / "makemytrip.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# Also console
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# Everything the scraper logs goes through the same rotating log.
makemytrip.LOG_SINK = logger.info


def build_jobs(routes, lead_times):
    return [(r[0], r[1], lt) for r in routes for lt in lead_times]


def parse_only(spec):
    """--only DEL-BLR:7[,DEL-BOM:1,...] -> [("DEL", "BLR", 7), ...]"""
    jobs = []
    for item in spec.split(","):
        route, lead = item.strip().split(":")
        origin, dest = route.upper().split("-")
        jobs.append((origin, dest, int(lead)))
    return jobs


def main():
    logger.info("Starting run_makemytrip standalone entry point.")
    load_dotenv(BACKEND / ".env")

    try:
        with open(BACKEND / "config.yaml", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
        routes = [(r["origin"], r["destination"]) for r in config.get("routes", [])]
        lead_times = config.get("lead_time_days", [])
    except Exception as e:
        logger.critical(f"Failed to load config.yaml: {e}")
        return 1

    if "--only" in sys.argv:
        try:
            jobs = parse_only(sys.argv[sys.argv.index("--only") + 1])
        except Exception as e:
            logger.critical(f"Bad --only value (expected e.g. DEL-BLR:7): {e}")
            return 1
    else:
        jobs = build_jobs(routes, lead_times)

    logger.info(f"MakeMyTrip batch scraping {len(jobs)} jobs...")
    try:
        # insert=True: clean results go to flight_prices via db.database.insert_flights (source='makemytrip')
        aborted = makemytrip.run(jobs, insert=True)
    except Exception:
        logger.error(f"MakeMyTrip run failed with exception:\n{traceback.format_exc()}")
        return 1

    logger.info(f"[makemytrip] run finished, aborted={aborted}")
    return 2 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
