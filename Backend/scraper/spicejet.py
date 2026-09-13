import logging
from typing import List, Dict, Any
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_sync
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import BaseScraper

logger = logging.getLogger(__name__)

class SpiceJetScraper(BaseScraper):
    """
    Scraper for SpiceJet direct site using Playwright.
    """
    
    def __init__(self):
        self.source = "SpiceJet"
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrape SpiceJet flight results. travel_date is expected in DD/MM/YYYY, so we convert to YYYY-MM-DD.
        """
        results = []
        now = datetime.utcnow()
        scraped_hour = now.replace(minute=0, second=0, microsecond=0)
        
        # Convert DD/MM/YYYY to YYYY-MM-DD
        dt_obj = datetime.strptime(travel_date, "%d/%m/%Y")
        sj_date = dt_obj.strftime("%Y-%m-%d")
        
        # SpiceJet direct search URL pattern (React PWA router)
        url = f"https://www.spicejet.com/search?from={origin}&to={destination}&tripType=1&departure={sj_date}&adult=1&currency=INR"
        
        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")
        
        with sync_playwright() as p:
            # Use persistent context to build cookies/history and pass anti-bot checks
            context = p.chromium.launch_persistent_context(
                user_data_dir="./spicejet_browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139}, # Delhi default
                permissions=["geolocation"]
            )
            
            page = context.pages[0] if context.pages else context.new_page()
            stealth_sync(page)
            
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                # Wait for either flight cards to appear or no-flights text
                # NOTE: You mentioned "inspect the actual live page structure for flight result selectors".
                # Since the site dynamically loads results and we don't have the exact DOM structure here,
                # we'll use a placeholder CSS selector.
                # TODO: Replace '.spicejet-flight-card-placeholder' with the actual flight card selector.
                FLIGHT_CARD_SELECTOR = ".css-1dbjc4n.r-1awozwy.r-19m6qjp.r-z2wwpe.r-1phboty.r-rs99b7" # This is a guess based on their React Native Web components
                
                try:
                    # Wait up to 20s for flight cards to attach
                    page.wait_for_selector(FLIGHT_CARD_SELECTOR, state="attached", timeout=20000)
                except PlaywrightTimeoutError:
                    logger.warning(f"[{self.source}] Timeout waiting for flight cards to appear. Taking screenshot...")
                
                flight_cards = page.query_selector_all(FLIGHT_CARD_SELECTOR)
                
                if not flight_cards:
                    screenshot_path = f"debug_zeroflights_spicejet_{origin}_{destination}_{travel_date.replace('/','')}.png"
                    page.screenshot(path=screenshot_path)
                    
                    page_text = page.locator("body").inner_text()
                    # TODO: Update the 'No flights' text based on what SpiceJet actually says
                    if "No flights" in page_text or "Oops" in page_text:
                        logger.info(f"[{self.source}] Zero flights found for {origin}-{destination} on {travel_date}. Screenshot: {screenshot_path}. Skipping.")
                    else:
                        logger.warning(f"[{self.source}] No flight cards found, and no explicit empty message. DOM may be different. Screenshot: {screenshot_path}. Skipping.")
                    
                    return []
                
                for card in flight_cards:
                    try:
                        # Extract data. We use fallback values since we don't have exact selectors yet.
                        # TODO: Update these internal selectors based on actual DevTools inspection
                        airline = "SpiceJet"
                        
                        # Just placeholder parsing logic
                        total_fare = 0.0
                        
                        results.append({
                            "source": self.source,
                            "route": f"{origin}-{destination}",
                            "airline": airline,
                            "flight_number": "SG-XXXX",
                            "cabin_class": "Economy",
                            "base_fare": total_fare,
                            "taxes_fees": 0.0,
                            "total_fare": total_fare,
                            "stops": 0,
                            "lead_time_days": lead_time_days,
                            "departure_time": now,
                            "scraped_at": now,
                            "scraped_hour": scraped_hour
                        })
                    except Exception as card_e:
                        logger.debug(f"[{self.source}] Skipping malformed flight card: {card_e}")
                        continue
                        
            except Exception as e:
                logger.error(f"[{self.source}] Error during scrape: {e}")
                raise
            finally:
                context.close()
                
        logger.info(f"[{self.source}] Extracted {len(results)} flights for {origin}-{destination}")
        return results
