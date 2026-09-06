import os
import yaml
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from scraper.yatra import YatraScraper
from db.database import get_engine, init_db, insert_flights

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
    scrapers = [YatraScraper()]
    
    total_inserted = 0

    for scraper in scrapers:
        for route in routes:
            origin = route.get("origin")
            destination = route.get("destination")
            
            for lead_time in lead_times:
                travel_date = get_travel_date(lead_time)
                
                logger.info(f"Scraping {origin}-{destination} for {travel_date} (lead: {lead_time} days)")
                
                try:
                    flights = scraper.scrape(origin, destination, travel_date, lead_time)
                    if flights:
                        logger.info("--- DEBUG: First 5 extracted flights ---")
                        for f in flights[:5]:
                            logger.info(f"Flight: {f.get('flight_number')} | Dep: {f.get('departure_time')} | Scraped Hr: {f.get('scraped_hour')} | Fare: {f.get('total_fare')}")
                        logger.info("----------------------------------------")
                        
                        inserted = insert_flights(engine, flights)
                        logger.info(f"Inserted {inserted} / {len(flights)} flights (deduplicated).")
                        total_inserted += inserted
                    else:
                        logger.info("No flights extracted.")
                except Exception as e:
                    logger.error(f"Failed to scrape {origin}-{destination} for {travel_date}: {e}")
                
                # Jitter between requests to avoid rate limits
                scraper.random_delay(30, 90)

    logger.info(f"Run completed. Total new flights inserted: {total_inserted}")

if __name__ == "__main__":
    main()
