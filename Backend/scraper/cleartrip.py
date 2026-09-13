import json
import logging
import urllib.parse
from typing import List, Dict, Any
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import BaseScraper
from scraper.cleanup import cleanup_orphaned_browsers

logger = logging.getLogger(__name__)

class ClearTripScraper(BaseScraper):
    """
    Scraper for ClearTrip using Playwright network interception.
    """
    
    def __init__(self):
        self.source = "ClearTrip"
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrape ClearTrip flight results by intercepting the v2 search API.
        travel_date is expected in DD/MM/YYYY format.
        """
        results = []
        now = datetime.now()
        scraped_hour = now.replace(minute=0, second=0, microsecond=0)
        
        # URL encoding
        date_encoded = urllib.parse.quote(travel_date, safe="")
        
        url = (f"https://www.cleartrip.com/flight/search/v2?"
               f"from={origin}&source_header={origin}&to={destination}&destination_header={destination}&"
               f"depart_date={date_encoded}&class=Economy&adults=1&childs=0&infants=0&mobileApp=true&"
               f"intl=n&responseType=jsonV3&source=DESKTOP&utm_currency=INR&return_date=&carrier=&"
               f"cfw=false&multiFare=true&isFFSC=true&trafficSource=&utmCustom=&filterVersion=v2&isWP=true")
        
        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")
        
        captured_data = None
        
        with sync_playwright() as p:
            # Use persistent context to build cookies/history and pass Bot Managers
            context = p.chromium.launch_persistent_context(
                user_data_dir="./cleartrip_browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139},
                permissions=["geolocation"]
            )
            
            try:
                # Disable HTTP caching for this context
                context.set_extra_http_headers({
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                })
                
                page = context.pages[0] if context.pages else context.new_page()
                stealth_sync(page)
                
                # Clear localStorage and sessionStorage before navigating to prevent SPA from restoring previous search state
                try:
                    page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
                except Exception:
                    pass
                context.add_init_script("try { window.localStorage.clear(); window.sessionStorage.clear(); } catch(e) {}")
                
                def handle_response(response):
                    nonlocal captured_data
                    if "cleartrip.com/flight/search/v2" in response.url:
                        try:
                            logger.debug(f"[{self.source}] Intercepted response. Status: {response.status}")
                            if response.status == 200:
                                raw_data = response.json()
                                if isinstance(raw_data, str):
                                    parsed = json.loads(raw_data)
                                else:
                                    parsed = raw_data
                                if parsed.get("fares"):
                                    captured_data = parsed
                                    logger.debug(f"[{self.source}] Captured response WITH fare data.")
                                else:
                                    logger.debug(f"[{self.source}] Captured response but 'fares' empty — waiting for next one.")
                            else:
                                logger.warning(f"[{self.source}] API returned status {response.status}. Headers: {response.headers}")
                        except Exception as e:
                            logger.error(f"[{self.source}] Error parsing JSON: {e}")
    
                page.on("response", handle_response)
                
                # Load the page and wait for network idle to give the XHR time to fire
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                # Wait up to 30s specifically for our data to be populated
                for _ in range(30):
                    if captured_data is not None:
                        break
                    page.wait_for_timeout(1000)
                
                if not captured_data:
                    logger.warning(f"[{self.source}] response never fired or failed for {origin}-{destination} on {travel_date}")
                    return []
                    
            except Exception as e:
                logger.error(f"[{self.source}] Error during navigation/interception: {e}")
                raise # Reraise to trigger Tenacity retry
            finally:
                context.close()
                try:
                    cleanup_orphaned_browsers()
                except Exception as e:
                    logger.warning(f"[{self.source}] Orphaned browser cleanup failed: {e}")
                
        # Parse the captured JSON payload
        try:
            cards = captured_data.get("cards", {}).get("J1", [])
            sub_options = captured_data.get("subTravelOptions", {})
            fares = captured_data.get("fares", {})
            
            for card in cards:
                try:
                    travel_option_id = card.get("travelOptionId", "")
                    if not travel_option_id:
                        continue
                        
                    leg_ids = travel_option_id.split("__")
                    
                    # ONLY process entries where there is exactly 1 leg after splitting, i.e. non-stop flights
                    if len(leg_ids) != 1:
                        continue
                        
                    leg_id = leg_ids[0]
                    
                    sub_option = sub_options.get(leg_id)
                    if not sub_option:
                        logger.debug(f"[{self.source}] leg ID {leg_id} not found in subTravelOptions")
                        continue
                        
                    cheapest_fare_id = sub_option.get("cheapestFareId")
                    if not cheapest_fare_id:
                        logger.debug(f"[{self.source}] cheapestFareId not found for leg {leg_id}")
                        continue
                        
                    fare_obj = fares.get(cheapest_fare_id)
                    if not fare_obj:
                        logger.debug(f"[{self.source}] cheapestFareId {cheapest_fare_id} not found in fares")
                        continue
                    pricing = fare_obj.get("pricing", {}).get("totalPricing", {})
                    
                    # leg_id format: "6E-303-DEL-BOM-1789050600"
                    leg_parts = leg_id.split("-")
                    if len(leg_parts) < 5:
                        logger.debug(f"[{self.source}] leg ID format unexpected: {leg_id}")
                        continue
                        
                    airline_code = leg_parts[0]
                    flight_num = leg_parts[1]
                    timestamp_str = leg_parts[-1]
                    
                    try:
                        departure_time = datetime.fromtimestamp(int(timestamp_str))
                    except Exception as e:
                        logger.debug(f"[{self.source}] Failed to parse timestamp {timestamp_str}: {e}")
                        departure_time = now
                        
                    airline_name = airline_code # Fallback to code if full name not found easily
                    flight_number = f"{airline_code}-{flight_num}"
                    cabin_class = fare_obj.get("cabinType", "ECONOMY")
                    base_fare = float(pricing.get("totalBaseFare", 0))
                    taxes_fees = float(pricing.get("totalTax", 0))
                    total_fare = float(pricing.get("totalPrice", 0))
                    stops = 0
                    
                    results.append({
                        "source": "cleartrip",
                        "route": f"{origin}-{destination}",
                        "airline": airline_name,
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
                    logger.debug(f"[{self.source}] Error parsing card {card.get('travelOptionId')}: {card_e}")
                    
        except Exception as e:
            logger.error(f"[{self.source}] Error mapping JSON fields: {e}")

        logger.info(f"[{self.source}] Extracted {len(results)} non-stop flights for {origin}-{destination}")
        
        if results:
            logger.debug("--- DEBUG: First 5 extracted flights ---")
            for f in results[:5]:
                logger.debug(f"Flight: {f.get('flight_number')} | Dep: {f.get('departure_time')} | Scraped Hr: {f.get('scraped_hour')} | Fare: {f.get('total_fare')}")
            logger.debug("----------------------------------------")
            
        return results
