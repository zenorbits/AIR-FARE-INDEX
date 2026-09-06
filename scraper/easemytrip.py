import logging
from typing import List, Dict, Any
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_sync
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import BaseScraper

logger = logging.getLogger(__name__)

class EaseMyTripScraper(BaseScraper):
    """
    Scraper for EaseMyTrip using Playwright.
    """
    
    def __init__(self):
        self.source = "EaseMyTrip"
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrape EaseMyTrip flight results. travel_date should be DD/MM/YYYY.
        """
        results = []
        now = datetime.utcnow()
        scraped_hour = now.replace(minute=0, second=0, microsecond=0)
        
        city_map = {
            "DEL": "Delhi-India",
            "BOM": "Mumbai-India",
            "BLR": "Bangalore-India",
            "CCU": "Kolkata-India",
            "HYD": "Hyderabad-India",
            "MAA": "Chennai-India"
        }
        origin_str = f"{origin}-{city_map.get(origin, origin + '-India')}"
        dest_str = f"{destination}-{city_map.get(destination, destination + '-India')}"
        url = f"https://www.easemytrip.com/flight-search/listing?srch={origin_str}|{dest_str}|{travel_date}&px=1-0-0&cbn=0"
        
        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")
        
        with sync_playwright() as p:
            # Use persistent context to build cookies/history and pass anti-bot checks
            context = p.chromium.launch_persistent_context(
                user_data_dir="./browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139},
                permissions=["geolocation"]
            )
            
            # launch_persistent_context creates a default initial page
            page = context.pages[0] if context.pages else context.new_page()
            stealth_sync(page)
            
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                try:
                    # Wait explicitly for the EaseMyTrip spinner container to be hidden
                    page.wait_for_selector("#Loader", state="hidden", timeout=60000)
                except PlaywrightTimeoutError:
                    logger.warning(f"[{self.source}] Timeout waiting for #Loader spinner to disappear. Proceeding anyway.")
                
                # Wait up to 20 seconds for flight cards to appear
                try:
                    page.wait_for_selector(".flt-container", state="attached", timeout=20000)
                except PlaywrightTimeoutError:
                    logger.warning(f"[{self.source}] Timeout waiting for .flt-container to appear.")
                
                # Check if flight cards actually exist
                flight_cards = page.query_selector_all(".flt-container")
                
                if not flight_cards:
                    # No flights found, let's check why and take a screenshot
                    screenshot_path = f"debug_zeroflights_{origin}_{destination}_{travel_date.replace('/','')}.png"
                    page.screenshot(path=screenshot_path)
                    
                    page_text = page.locator("body").inner_text()
                    if "Oops! No flights found" in page_text or "No flights found" in page_text:
                        logger.info(f"[{self.source}] Zero flights found for {origin}-{destination} on {travel_date} (Oops! No flights found). Screenshot: {screenshot_path}. Skipping.")
                    else:
                        logger.warning(f"[{self.source}] No flight cards found, but 'No flights found' text was missing. DOM may be different. Screenshot: {screenshot_path}. Skipping.")
                    
                    return []
                
                for card in flight_cards:
                    try:
                        # Extract data. We use fallback values if elements aren't found.
                        airline_el = card.query_selector(".air-line-name, .al-name")
                        airline = airline_el.inner_text().strip() if airline_el else "Unknown"
                        
                        flight_num_el = card.query_selector(".flt-num, .f-num")
                        flight_number = flight_num_el.inner_text().strip() if flight_num_el else "Unknown"
                        
                        dep_el = card.query_selector(".dep-time, .d-time")
                        dep_time_str = dep_el.inner_text().strip() if dep_el else "00:00"
                        
                        # We parse the time, and attach it to the travel date.
                        # Assuming travel_date is 'DD/MM/YYYY' and time is 'HH:mm'
                        try:
                            # Note: This simple parsing assumes the flight departs on the searched date.
                            dep_dt_str = f"{travel_date} {dep_time_str}"
                            departure_time = datetime.strptime(dep_dt_str, "%d/%m/%Y %H:%M")
                        except ValueError:
                            departure_time = now # Fallback
                        
                        arr_el = card.query_selector(".arr-time, .a-time")
                        arrival_time = arr_el.inner_text().strip() if arr_el else "00:00"
                        
                        stops_el = card.query_selector(".stops, .stps")
                        stops_str = stops_el.inner_text().strip().lower() if stops_el else "0"
                        stops = 0 if "non" in stops_str or stops_str == "0" else 1 # simplified
                        
                        fare_el = card.query_selector(".fare, .price")
                        fare_str = fare_el.inner_text().strip() if fare_el else "0"
                        # Clean currency symbols and commas
                        total_fare = float(''.join(filter(str.isdigit, fare_str)))
                        
                        # For base_fare and taxes, we'll just set them as total_fare / 0 if not explicitly separable
                        base_fare = total_fare
                        taxes_fees = 0.0
                        
                        cabin_class = "Economy" # Default for basic search
                        
                        results.append({
                            "source": self.source,
                            "route": f"{origin}-{destination}",
                            "airline": airline,
                            "flight_number": flight_number,
                            "cabin_class": cabin_class,
                            "base_fare": base_fare,
                            "taxes_fees": taxes_fees,
                            "total_fare": total_fare,
                            "stops": stops,
                            "lead_time_days": lead_time_days,
                            "departure_time": departure_time,
                            "scraped_at": now,
                            "scraped_hour": scraped_hour
                        })
                    except Exception as card_e:
                        logger.debug(f"[{self.source}] Skipping malformed flight card: {card_e}")
                        continue
                        
            except Exception as e:
                logger.error(f"[{self.source}] Error during scrape: {e}")
                raise # Reraise to trigger tenacity retry
            finally:
                context.close()
                
        logger.info(f"[{self.source}] Extracted {len(results)} flights for {origin}-{destination}")
        return results
