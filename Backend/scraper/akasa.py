"""
Akasa Air scraper.

Drives the akasaair.com one-way search form and reads the availability
response the page itself requests. Parsing lives in scraper/akasa_parser.py.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from patchright.sync_api import sync_playwright
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

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

CITY_MAP = {
    "DEL": ["DELHI"],
    "BOM": ["MUMBAI"],
    "BLR": ["BENGALURU", "BANGALORE"],
    "CCU": ["KOLKATA"],
    "HYD": ["HYDERABAD"],
    "MAA": ["CHENNAI"]
}


def _ordinal_suffix(day: int) -> str:
    """1 -> 'st', 2 -> 'nd', 3 -> 'rd', 4..20 -> 'th', 21 -> 'st', etc."""
    if 4 <= day <= 20 or 24 <= day <= 30:
        return "th"
    return ["st", "nd", "rd"][day % 10 - 1]


def select_airport(page, field_selector: str, iata: str) -> None:
    """Select an origin/destination by clicking the field and choosing the
    matching option from the destinations list that's already rendered --
    no typing.

    Typing into these fields reliably breaks Akasa's dropdown: live network
    inspection showed no request backs the list (it's fetched once at page
    load from Storyblok's app-master-data and rendered fully client-side --
    all airports, including every IATA code this scraper needs, are already
    present as real li[id="{iata}"] elements as soon as the field is
    clicked), and typing empties #destinations client-side with no reliable
    recovery signal to wait on. So this selects directly from the full list
    instead of typing to filter it.

    The option locator is scoped to "#destinations:visible li[id=...]"
    rather than a bare "li:has-text(...)" or "#destinations li": the site's
    top nav (e.g. the Add-Ons menu's "Delayed or Lost Baggage" link)
    substring-matches a 3-letter code too, and both the From and To fields'
    dropdowns share the same "#destinations" id, so only the currently
    visible one should be searched.

    Raises RuntimeError if the field already has leftover text, the option
    never appears, or the post-click readback doesn't match. Callers are
    expected to recover via a page reload (typing isn't a valid recovery
    path here), not by retrying in place.
    """
    field = page.locator(field_selector).first
    option = page.locator(f'#destinations:visible li[id="{iata}"]').first
    valid_cities = CITY_MAP.get(iata, [])

    existing = field.input_value()
    if existing.strip() != "":
        raise RuntimeError(f"Field {field_selector!r} already has leftover text {existing!r} before selecting {iata!r}")

    field.click(timeout=10000)
    option.wait_for(state="visible", timeout=10000)
    option.scroll_into_view_if_needed(timeout=5000)
    option.click(timeout=5000)
    page.wait_for_timeout(300)

    value = field.input_value().upper()
    if iata in value or any(city in value for city in valid_cities):
        return

    raise RuntimeError(f"Could not select airport {iata!r} in {field_selector!r}: readback was {value!r}")


def _field_value_matches(page, field_selector: str, iata: str) -> bool:
    """True if the field's current value still reflects the selected airport."""
    try:
        value = page.locator(field_selector).first.input_value().upper()
    except Exception:
        return False
    valid_cities = CITY_MAP.get(iata, [])
    return iata in value or any(city in value for city in valid_cities)


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
    SUPPORTED_ROUTES = {("BOM", "BLR"), ("DEL", "BLR"), ("DEL", "BOM"), ("DEL", "CCU")}

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
        context_ref = None
        route = f"{origin}-{destination}"

        logger.info(f"[{self.source}] Scraping route {route} for {travel_date}")

        watchdog_triggered = False
        def watchdog():
            nonlocal watchdog_triggered
            watchdog_triggered = True
            logger.error(f"[{self.source}] Search exceeded 3 min, aborted")
            
            try:
                import psutil
                akasa_procs = []
                for proc in psutil.process_iter(["pid", "cmdline"]):
                    cmd = " ".join(proc.info.get("cmdline") or []).lower()
                    if "akasa_browser_profile" in cmd:
                        akasa_procs.append(proc)
                
                victims = []
                for p in akasa_procs:
                    victims.append(p)
                    try:
                        victims.extend(p.children(recursive=True))
                    except psutil.NoSuchProcess:
                        pass
                
                unique = list({v.pid: v for v in victims}.values())
                for v in unique:
                    try:
                        v.kill()
                    except psutil.NoSuchProcess:
                        pass
            except Exception as e:
                logger.warning(f"[{self.source}] Watchdog kill failed: {e}")

        # Timer for 3 mins (180 seconds)
        watchdog_timer = threading.Timer(180.0, watchdog)
        watchdog_timer.start()

        try:
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
                context_ref = context

                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    
                    context.add_init_script(
                        "try { window.localStorage.clear(); window.sessionStorage.clear(); } catch(e) {}"
                    )

                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(8000)
                    self._dismiss_popups(page)
                    
                    response = None
                    no_response_reason = None
                    try:
                        response = self._fill_search(page, origin, destination, date_obj)
                    except PlaywrightTimeoutError:
                        no_response_reason = "watchdog" if watchdog_triggered else "timeout"

                    if response is not None:
                        if response.status != 200:
                            no_response_reason = f"status_{response.status}"
                        else:
                            payload = response.json()
                            if _payload_matches(payload, expected_date):
                                captured = payload
                            else:
                                no_response_reason = "date_mismatch"
                    elif no_response_reason is None:
                        no_response_reason = "timeout"

                    if captured is None:
                        self._save_screenshot(page, origin, destination, travel_date)
                        logger.warning(f"[{self.source}] NO_RESPONSE {route} {travel_date} reason={no_response_reason}")
                        return []

                except Exception as e:
                    if watchdog_triggered:
                        logger.warning(f"[{self.source}] NO_RESPONSE {route} {travel_date} reason=watchdog")
                        return []
                    logger.error(f"[{self.source}] Error during search/interception: {e}")
                    if page is not None:
                        self._save_screenshot(page, origin, destination, travel_date)
                    raise  # trigger Tenacity retry
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        cleanup_orphaned_browsers()
                    except Exception as e:
                        logger.warning(f"[{self.source}] Orphaned browser cleanup failed: {e}")
        finally:
            watchdog_timer.cancel()

        self._dump_payload(captured, origin, destination, travel_date)

        results = parse_akasa_response(captured, origin, destination, lead_time_days, now)
        if results:
            logger.info(f"[{self.source}] OK {route} {travel_date} rows={len(results)}")
        else:
            logger.info(f"[{self.source}] NO_FLIGHTS {route} {travel_date}")
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

    def _fill_search(self, page, origin: str, destination: str, date_obj: datetime) -> Any:
        route = f"{origin}-{destination}"
        date_label = date_obj.strftime("%d/%m/%Y")
        max_reloads = 2
        reload_count = 0

        while True:
            try:
                page.locator("input#oneway").first.click(timeout=3000, force=True)
                page.wait_for_timeout(500)
            except Exception:
                pass

            bad_field = None
            try:
                select_airport(page, "input#To", destination)
                page.wait_for_timeout(1000)
            except Exception:
                bad_field = "To"

            if bad_field is None:
                try:
                    select_airport(page, "input#From", origin)
                    page.wait_for_timeout(1000)
                except Exception:
                    bad_field = "From"

            if bad_field is None:
                to_ok = _field_value_matches(page, "input#To", destination)
                from_ok = _field_value_matches(page, "input#From", origin)
                if not to_ok:
                    bad_field = "To"
                elif not from_ok:
                    bad_field = "From"

            if bad_field is None:
                break  # both fields filled and verified

            if reload_count >= max_reloads:
                from_val = self._safe_field_value(page, "input#From")
                to_val = self._safe_field_value(page, "input#To")
                logger.error(f"[{self.source}] FILL_FAILED {route} {date_label}: From='{from_val}' To='{to_val}'")
                self._save_screenshot(page, origin, destination, date_label)
                raise RuntimeError(f"FILL_FAILED {route} {date_label}: From='{from_val}' To='{to_val}'")

            reload_count += 1
            logger.warning(f"[{self.source}] FILL_RETRY_RELOAD {route} {date_label} field={bad_field}")
            try:
                with page.expect_response(
                    lambda r: "app-master-data" in r.url and r.status == 200,
                    timeout=20000,
                ):
                    page.reload(wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                logger.debug(f"[{self.source}] app-master-data response not observed within 20s after reload")
            self._dismiss_popups(page)
            self._wait_fields_ready(page)
            # loop back and refill both fields from scratch

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

        expected_date = date_obj.strftime("%Y-%m-%d")
        
        def response_predicate(response):
            if "availability/search" in response.url and response.request.method == "POST":
                post_data = response.request.post_data
                if post_data and expected_date in post_data:
                    return True
            return False

        with page.expect_response(response_predicate, timeout=30000) as response_info:
            page.locator("button:has-text('Search Flights')").first.click(timeout=10000)
            
        return response_info.value

    def _safe_field_value(self, page, selector: str) -> str:
        try:
            return page.locator(selector).first.input_value()
        except Exception:
            return "?"

    def _wait_fields_ready(self, page, timeout_ms: int = 15000) -> None:
        """Wait for input#To and input#From to be visible and interactable after a reload."""
        to_field = page.locator("input#To").first
        from_field = page.locator("input#From").first
        to_field.wait_for(state="visible", timeout=timeout_ms)
        from_field.wait_for(state="visible", timeout=timeout_ms)
        for _ in range(20):
            try:
                if to_field.is_enabled() and from_field.is_enabled():
                    return
            except Exception:
                pass
            page.wait_for_timeout(250)

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
