import json
import logging
import urllib.parse
import os
from typing import List, Dict, Any
from datetime import datetime
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import BaseScraper

logger = logging.getLogger(__name__)

class YatraScraper(BaseScraper):
    """
    Scraper for Yatra using Playwright network interception to capture the lowest-fare JSON API.
    """
    
    def __init__(self):
        self.source = "Yatra"
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrape Yatra flight results by intercepting the get-fare API.
        travel_date is expected in DD/MM/YYYY format.
        """
        results = []
        now = datetime.now()
        scraped_hour = now.replace(minute=0, second=0, microsecond=0)
        
        # Yatra URL encoding
        date_encoded = urllib.parse.quote(travel_date, safe="")
        
        # Note: We provide a default return date that is same as depart date for one-way to satisfy any internal constraints, though 'type=O' handles one-way.
        url = (f"https://flight.yatra.com/air-search-ui/dom2/trigger?"
               f"flex=0&viewName=normal&source=fresco-flights&type=O&class=Economy&"
               f"ADT=1&CHD=0&INF=0&noOfSegments=1&"
               f"origin={origin}&originCountry=IN&"
               f"destination={destination}&destinationCountry=IN&"
               f"flight_depart_date={date_encoded}&_cb={int(datetime.now().timestamp())}")
        
        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")
        
        captured_data = None
        
        with sync_playwright() as p:
            # Use persistent context to build cookies/history and pass Akamai Bot Manager
            context = p.chromium.launch_persistent_context(
                user_data_dir="./yatra_browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139},
                permissions=["geolocation"]
            )
            
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
                if "lowest-fare-service/dom2/get-fare" in response.url:
                    try:
                        logger.debug(f"[{self.source}] Intercepted get-fare response. Status: {response.status}")
                        if response.status == 200:
                            # Yatra often returns the JSON as a string inside the response, 
                            # or directly as JSON. We handle both.
                            raw_data = response.json()
                            if isinstance(raw_data, str):
                                captured_data = json.loads(raw_data)
                            else:
                                captured_data = raw_data
                        else:
                            logger.warning(f"[{self.source}] get-fare returned status {response.status}. Headers: {response.headers}")
                    except Exception as e:
                        logger.error(f"[{self.source}] Error parsing get-fare JSON: {e}")

            page.on("response", handle_response)
            
            try:
                # Load the page and wait for network idle to give the XHR time to fire
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
                # Wait up to 30s specifically for our data to be populated
                # We do this via a manual polling loop rather than blindly sleeping
                for _ in range(30):
                    if captured_data is not None:
                        break
                    page.wait_for_timeout(1000)
                
                if not captured_data:
                    logger.warning(f"[{self.source}] get-fare response never fired or failed for {origin}-{destination} on {travel_date}")
                    return []
                
            except Exception as e:
                logger.error(f"[{self.source}] Error during navigation/interception: {e}")
                raise # Reraise to trigger Tenacity retry
            finally:
                context.close()
                
        try:
            os.makedirs("debug_dumps", exist_ok=True)
            safe_date = travel_date.replace('/', '-')
            dump_filename = f"debug_dumps/{origin}_{destination}_{safe_date}_{int(datetime.now().timestamp())}.json"
            with open(dump_filename, "w") as f:
                json.dump(captured_data, f, indent=2)
        except Exception as e:
            logger.warning(f"[{self.source}] Failed to write debug dump: {e}")
            
        # Parse the captured JSON payload
        # Structure: {"day": {"YYYY-MM-DD": {"af": {"AI": {"tf": 7000, "bf": 5493, "ow": [...]}}}}}
        try:
            expected_date_key = datetime.strptime(travel_date, "%d/%m/%Y").strftime("%Y-%m-%d")
            day_data = captured_data.get("day", {})
            date_info = day_data.get(expected_date_key)
            
            if date_info is None:
                logger.warning(f"[{self.source}] Requested date {expected_date_key} not found in calendar response for {origin}-{destination}. Available dates: {list(day_data.keys())}")
                return []
                
            airline_fares = date_info.get("af", {})
            
            for airline_code, fare_info in airline_fares.items():
                # Total fare (tf) and Base fare (bf)
                total_fare = float(fare_info.get("tf", 0))
                base_fare = float(fare_info.get("bf", 0))
                taxes_fees = total_fare - base_fare
                
                # Segments (ow = outbound way)
                segments = fare_info.get("ow", [])
                if not segments:
                    continue
                    
                first_segment = segments[0]
                last_segment = segments[-1]
                
                airline_name = first_segment.get("an", airline_code)
                
                # Combine flight numbers if multiple segments
                flight_number = f"{airline_code}-{first_segment.get('fl', 'UNK')}"
                if len(segments) > 1:
                    flight_number += f" (Multi-leg)"
                    
                cabin_class = first_segment.get("cabin", "Economy")
                stops = len(segments) - 1
                
                # Departure time (ddt) from first segment
                dep_time_str = first_segment.get("ddt") # e.g. "2026-10-05 19:55"
                if dep_time_str:
                    departure_time = datetime.strptime(dep_time_str, "%Y-%m-%d %H:%M")
                else:
                    departure_time = now # Fallback
                    
                results.append({
                    "source": "yatra",
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
        except Exception as e:
            logger.error(f"[{self.source}] Error mapping JSON fields: {e}")
            logger.debug(f"[{self.source}] Raw JSON: {json.dumps(captured_data)[:1000]}")
            
        logger.info(f"[{self.source}] Extracted {len(results)} lowest-fare flights for {origin}-{destination}")
        return results
