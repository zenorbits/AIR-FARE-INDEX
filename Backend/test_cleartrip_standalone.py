import logging
from scraper.cleartrip import ClearTripScraper

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

if __name__ == "__main__":
    scraper = ClearTripScraper()
    results = scraper.scrape(
        origin="DEL",
        destination="BOM",
        travel_date="20/09/2026",  # DD/MM/YYYY, pick a real near-future date
        lead_time_days=10
    )
    print(f"\n\nTOTAL RESULTS: {len(results)}")
    for r in results[:5]:
        print(r)