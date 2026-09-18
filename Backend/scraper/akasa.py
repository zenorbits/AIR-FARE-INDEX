"""
Akasa Air scraper.

Drives the akasaair.com one-way search form and reads the availability
response the page itself requests. Parsing lives in scraper/akasa_parser.py.
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from patchright.sync_api import sync_playwright

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from .base import BaseScraper
from scraper.akasa_parser import parse_akasa_response
from scraper.cleanup import cleanup_orphaned_browsers

logger = logging.getLogger(__name__)

HOME_URL = "https://www.akasaair.com/"

POPUP_CLOSE_SELECTORS = [
    "[aria-label*='close' i]",
    "button:has-text('✕')",
    "button:has-text('Accept')",
]


def _ordinal_suffix(day: int) -> str:
    """1 -> 'st', 2 -> 'nd', 3 -> 'rd', 4..20 -> 'th', 21 -> 'st', etc."""
    if 4 <= day <= 20 or 24 <= day <= 30:
        return "th"
    return ["st", "nd", "rd"][day % 10 - 1]


def select_airport(page, field_selector: str, iata: str) -> None:
    """Fill an origin/destination autocomplete field and select the matching airport.

    The option locator is scoped to "#destinations li" rather than a bare
    "li:has-text(...)": the site's top nav (e.g. the Add-Ons menu's "Delayed
    or Lost Baggage" link) sits earlier in the DOM and substring-matches a
    3-letter code too, so an unscoped locator clicks that nav item instead
    of the real suggestion -- which is what was navigating the scraper away
    to the Add-Ons page mid-search. Retries once if the selection can't be
    confirmed afterwards.
    """
    field = page.locator(field_selector).first
    # The code and city name run together with no separator in the option's
    # text ("DELDelhiIndira Gandhi International Airport"), so a \b-bounded
    # regex never matches -- anchor on the start instead, where the code
    # always appears.
    option = page.locator("#destinations li").filter(has_text=re.compile(rf"^{re.escape(iata)}"))

    city_map = {
        "DEL": ["DELHI"],
        "BOM": ["MUMBAI"],
        "BLR": ["BENGALURU", "BANGALORE"],
        "CCU": ["KOLKATA"],
        "HYD": ["HYDERABAD"],
        "MAA": ["CHENNAI"]
    }
    valid_cities = city_map.get(iata, [])

    last_error = "unknown error"
    for _attempt in range(2):
        try:
            field.click(timeout=10000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            if field.input_value() != "":
                field.fill("")
                
            if field.input_value() != "":
                raise RuntimeError("Field is not empty after clearing")

            field.press_sequentially(iata, delay=150)
            
            try:
                option.first.wait_for(state="visible", timeout=10000)
            except Exception as e:
                import os
                os.makedirs("scratch", exist_ok=True)
                page.screenshot(path=f"scratch/akasa_{iata}_fail.png")
                container = page.locator("#destinations").first
                if container.is_visible():
                    html = container.inner_html()
                    logger.warning(f"Dropdown HTML: {html[:500]}")
                raise e

            option.first.click(timeout=5000)
            page.wait_for_timeout(300)

            value = field.input_value().upper()
            if iata in value or any(city in value for city in valid_cities):
                return
            last_error = f"field value after selection was {value!r}, expected to contain {iata!r} or city name"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"Could not select airport {iata!r} in {field_selector!r}: {last_error}")


def _payload_matches(payload: Any, expected_date: str) -> bool:
    """True if the availability payload is for the requested travel date."""
    try:
        for result in payload["data"]["results"]:
            for trip in result.get("trips", []):
                if str(trip.get("date", ""))[:10] == expected_date:
                    return True
    except (KeyError, TypeError):
        return False
    return False


class AkasaScraper(BaseScraper):
    """Scraper for Akasa Air using the page's own availability response."""
    SUPPORTED_ROUTES = {("BOM", "BLR"), ("DEL", "BLR"), ("DEL", "BOM"), ("DEL", "CCU"), ("BLR", "HYD")}

    def __init__(self):
        self.source = "akasa"

    @retry(
        stop=stop_after_attempt(2),  # 1 retry
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """travel_date is expected in DD/MM/YYYY format (same as YatraScraper)."""

        now = datetime.now()
        date_obj = datetime.strptime(travel_date, "%d/%m/%Y")
        expected_date = date_obj.strftime("%Y-%m-%d")
        captured: Optional[dict] = None
        page = None

        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir="./akasa_browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139}
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()
                
                context.add_init_script(
                    "try { window.localStorage.clear(); window.sessionStorage.clear(); } catch(e) {}"
                )

                def handle_response(response):
                    nonlocal captured
                    try:
                        if "availability/search" not in response.url:
                            return
                        if response.request.method != "POST":
                            return
                        if response.status != 200:
                            logger.warning(f"[{self.source}] availability/search returned {response.status}")
                            return
                        payload = response.json()
                        if _payload_matches(payload, expected_date):
                            captured = payload
                        else:
                            logger.debug(f"[{self.source}] Ignoring search response for a different date")
                    except Exception as e:
                        logger.error(f"[{self.source}] Error reading availability JSON: {e}")

                page.on("response", handle_response)

                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                self._dismiss_popups(page)
                self._fill_search(page, origin, destination, date_obj)

                for _ in range(30):
                    if captured is not None:
                        break
                    page.wait_for_timeout(1000)

                if captured is None:
                    self._save_screenshot(page, origin, destination, travel_date)
                    logger.warning(f"[{self.source}] No availability response for {origin}-{destination} on {travel_date}")
                    return []

            except Exception as e:
                logger.error(f"[{self.source}] Error during search/interception: {e}")
                if page is not None:
                    self._save_screenshot(page, origin, destination, travel_date)
                raise  # trigger Tenacity retry
            finally:
                context.close()
                try:
                    cleanup_orphaned_browsers()
                except Exception as e:
                    logger.warning(f"[{self.source}] Orphaned browser cleanup failed: {e}")

        self._dump_payload(captured, origin, destination, travel_date)

        results = parse_akasa_response(captured, origin, destination, lead_time_days, now)
        logger.info(f"[{self.source}] Extracted {len(results)} flights for {origin}-{destination}")
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _dismiss_popups(self, page) -> None:
        for sel in POPUP_CLOSE_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1000):
                    loc.click(timeout=2000)
                    page.wait_for_timeout(500)
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    def _fill_search(self, page, origin: str, destination: str, date_obj: datetime) -> None:
        try:
            page.locator("input#oneway").first.click(timeout=3000, force=True)
            page.wait_for_timeout(500)
        except Exception:
            pass

        select_airport(page, "input#From", origin)
        page.wait_for_timeout(1000)
        select_airport(page, "input#To", destination)
        page.wait_for_timeout(1000)

        # Date
        page.locator("input[name='DepartureDate']").first.click(timeout=10000)

        day = date_obj.day
        date_str = f"{date_obj.strftime('%B')} {day}{_ordinal_suffix(day)}, {date_obj.strftime('%Y')}"
        cell = page.locator(f"div[aria-label*='{date_str}']").first
        
        for _ in range(3):
            if cell.is_visible():
                break
            page.keyboard.press("ArrowRight")
            try:
                page.locator(".react-datepicker__navigation--next").first.click(timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            
        try:
            cell.click(timeout=10000)
        except Exception:
            page.keyboard.press("Enter")
        page.wait_for_timeout(500)

        page.locator("button:has-text('Search Flights')").first.click(timeout=10000)

    def _save_screenshot(self, page, origin: str, destination: str, travel_date: str) -> None:
        try:
            os.makedirs("debug_dumps", exist_ok=True)
            safe_date = travel_date.replace("/", "-")
            page.screenshot(path=f"debug_dumps/akasa_{origin}_{destination}_{safe_date}_{int(datetime.now().timestamp())}.png")
        except Exception as e:
            logger.warning(f"[{self.source}] Screenshot failed: {e}")

    def _dump_payload(self, payload: dict, origin: str, destination: str, travel_date: str) -> None:
        try:
            os.makedirs("debug_dumps", exist_ok=True)
            safe_date = travel_date.replace("/", "-")
            path = f"debug_dumps/akasa_{origin}_{destination}_{safe_date}_{int(datetime.now().timestamp())}.json"
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"[{self.source}] Failed to write debug dump: {e}")
