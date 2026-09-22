import json
import logging
from typing import List, Dict, Any
from datetime import datetime
from patchright.sync_api import sync_playwright
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_not_exception_type

from .base import BaseScraper
from scraper.cleanup import cleanup_orphaned_browsers

logger = logging.getLogger(__name__)


class CleartripBlocked(Exception):
    """Raised when Cleartrip/Akamai returns 403/429. Not retried: retrying a block makes it worse."""


def _dismiss_overlays(page, log_func):
    """Close Cleartrip's login/promo modal. Never removes DOM nodes (breaks React)."""
    for _ in range(2):
        try:
            overlay = page.query_selector("div.overlay-bg")
            if not overlay or not overlay.is_visible():
                return
            icon = page.query_selector("svg[data-testid='closeIcon']")
            if icon and icon.is_visible():
                icon.click(timeout=2000)
                page.wait_for_selector("div.overlay-bg", state="hidden", timeout=3000)
                log_func("Dismissal method: close-icon")
                return
        except Exception:
            pass

    try:
        overlay = page.query_selector("div.overlay-bg")
        if overlay and overlay.is_visible():
            page.evaluate(
                "() => { document.querySelectorAll('.overlay-bg').forEach(el => {"
                " el.style.display = 'none'; el.style.pointerEvents = 'none'; });"
                " document.body.style.overflow = ''; }"
            )
            log_func("Dismissal method: js-hide")
    except Exception:
        pass


def _read_calendar_months(page):
    """Parse the two visible DayPicker-Caption headers (left pane, right pane) into (year, month) tuples."""
    months = []
    for cap in page.query_selector_all("div.DayPicker-Caption"):
        try:
            dt = datetime.strptime(cap.inner_text().strip(), "%B %Y")
            months.append((dt.year, dt.month))
        except ValueError:
            continue
    return months


def _select_date(page, date_obj, log_func):
    """
    Read the two-pane calendar's month/year headers (div.DayPicker-Caption) and page
    forward (svg[data-testid='rightArrow']) or backward (.DayPicker-wrapper .ta-left svg,
    which has no data-testid and stays in the DOM but drops its 'c-pointer' class when
    disabled) until the target month is one of the two visible panes. The persistent
    browser profile can leave the calendar open on a later month than the target, so
    paging must be able to go either direction, not just forward.
    Only once the target month is in view is the day cell (div[aria-label*=...]) located,
    via a Locator re-queried immediately before the click (not a held ElementHandle) to
    avoid stale-element errors. False = ran out of arrow in that direction (beyond
    booking window going forward, or before the earliest selectable month going back).
    """
    formatted = date_obj.strftime("%a %b %d %Y")
    cell_selector = f"div[aria-label*='{formatted}']"
    target = (date_obj.year, date_obj.month)

    try:
        page.wait_for_selector("div.DayPicker-Caption", state="visible", timeout=5000)
    except Exception:
        pass

    months = _read_calendar_months(page)
    if not months:
        raise RuntimeError("Could not read calendar month header (no div.DayPicker-Caption found)")
    direction = "back" if target < months[0] else "fwd"

    for i in range(13):
        if target in months:
            break

        if direction == "fwd":
            arrow = page.query_selector("svg[data-testid='rightArrow']")
            arrow_ok = bool(arrow and arrow.is_visible())
        else:
            arrow = page.query_selector(".DayPicker-wrapper .ta-left svg")
            arrow_ok = bool(arrow and arrow.is_visible() and "c-pointer" in (arrow.get_attribute("class") or ""))

        if not arrow_ok:
            shown = ", ".join(f"{y}-{m:02d}" for y, m in months)
            log_func(f"Date {formatted} beyond booking window (arrow gone after {i} clicks, direction={direction}, shown={shown})")
            return False

        arrow.click()
        page.wait_for_timeout(600)
        months = _read_calendar_months(page)
        if not months:
            raise RuntimeError("Could not read calendar month header (no div.DayPicker-Caption found)")
    else:
        shown = ", ".join(f"{y}-{m:02d}" for y, m in months)
        raise RuntimeError(f"Date {formatted} not found after 13 {direction} clicks (shown={shown})")

    try:
        page.locator(cell_selector).first.click(timeout=3000)
    except Exception as e:
        raise RuntimeError(f"Day cell for {formatted} not clickable after target month matched: {e}")

    log_func(f"Date selected after {i} clicks (direction={direction})")
    return True


class ClearTripScraper(BaseScraper):
    """
    Scraper for ClearTrip using Playwright network interception.
    """

    def __init__(self):
        self.source = "ClearTrip"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_not_exception_type(CleartripBlocked),
        reraise=True
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrape ClearTrip flight results by driving the search form and intercepting the v2 search API.
        travel_date is expected in DD/MM/YYYY format.
        """
        results = []
        now = datetime.now()
        scraped_hour = now.replace(minute=0, second=0, microsecond=0)
        log = lambda m: logger.info(f"[{self.source}] {m}")

        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")

        captured_data = None
        blocked_status = None

        with sync_playwright() as p:
            # Persistent context keeps cookies/history to pass Akamai.
            # No custom user_agent / extra headers: they mismatch patchright's Chromium fingerprint.
            context = p.chromium.launch_persistent_context(
                user_data_dir="./cleartrip_browser_profile",
                headless=False,
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139},
                permissions=["geolocation"]
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()

                # Warm-up navigation
                logger.info(f"[{self.source}] Performing warm-up navigation...")
                home_resp = page.goto("https://www.cleartrip.com/", wait_until="domcontentloaded", timeout=30000)
                if home_resp is not None and home_resp.status in (403, 429):
                    raise CleartripBlocked(f"Homepage returned HTTP {home_resp.status}")
                page.wait_for_timeout(8000)

                # Clear stale SPA search state ONCE (not on every navigation)
                try:
                    page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
                except Exception:
                    pass

                _dismiss_overlays(page, log)

                abck_cookie = next((c for c in context.cookies() if c['name'] == '_abck'), None)
                if abck_cookie:
                    logger.info(f"[{self.source}] _abck cookie present: {abck_cookie['value'][:40]}")
                else:
                    logger.info(f"[{self.source}] _abck cookie not found")

                def handle_response(response):
                    nonlocal captured_data, blocked_status
                    if "cleartrip.com/flight/search/v2" in response.url:
                        try:
                            logger.debug(f"[{self.source}] Intercepted response. Status: {response.status}")
                            if response.status == 200:
                                raw_data = response.json()
                                parsed = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                                if parsed.get("fares"):
                                    captured_data = parsed
                                    logger.debug(f"[{self.source}] Captured response WITH fare data.")
                                else:
                                    logger.debug(f"[{self.source}] Captured response but 'fares' empty — waiting for next one.")
                            else:
                                logger.warning(f"[{self.source}] API returned status {response.status}.")
                                if response.status in (403, 429):
                                    blocked_status = response.status
                        except Exception as e:
                            logger.error(f"[{self.source}] Error parsing JSON: {e}")

                page.on("response", handle_response)

                # --- Origin ---
                logger.info(f"[{self.source}] Filling origin: {origin}")
                page.evaluate("window.scrollTo(0, 0)")
                _dismiss_overlays(page, log)
                page.click("input[placeholder='Where from?']")
                page.fill("input[placeholder='Where from?']", "")
                page.type("input[placeholder='Where from?']", origin, delay=120)
                page.wait_for_timeout(2000)
                selector = f"ul:visible li:has-text('{origin}'):not(:has-text('Anywhere'))"
                try:
                    page.wait_for_selector(selector, state="visible", timeout=8000)
                except Exception:
                    lis = page.query_selector_all("ul:visible li")
                    li_texts = [li.inner_text().strip() for li in lis if li.is_visible()]
                    raise RuntimeError(f"Origin autocomplete option not found for {origin}. Visible options: {li_texts}")
                page.locator(selector).first.click()

                # --- Destination ---
                logger.info(f"[{self.source}] Filling destination: {destination}")
                _dismiss_overlays(page, log)
                page.click("input[placeholder='Where to?']")
                page.fill("input[placeholder='Where to?']", "")
                page.type("input[placeholder='Where to?']", destination, delay=120)
                page.wait_for_timeout(2000)
                selector = f"ul:visible li:has-text('{destination}'):not(:has-text('Anywhere'))"
                try:
                    page.wait_for_selector(selector, state="visible", timeout=8000)
                except Exception:
                    lis = page.query_selector_all("ul:visible li")
                    li_texts = [li.inner_text().strip() for li in lis if li.is_visible()]
                    raise RuntimeError(f"Destination autocomplete option not found for {destination}. Visible options: {li_texts}")
                page.locator(selector).first.click()

                try:
                    val_from = page.locator("input[placeholder='Where from?']").input_value()
                    val_to = page.locator("input[placeholder='Where to?']").input_value()
                    logger.info(f"[{self.source}] Autocomplete resolved -> From: '{val_from}', To: '{val_to}'")
                except Exception as e:
                    logger.info(f"[{self.source}] Could not log input values: {e}")

                # --- Date ---
                logger.info(f"[{self.source}] Selecting travel date: {travel_date}")
                _dismiss_overlays(page, log)
                page.click("div[data-testid='dateSelectOnward']")
                page.wait_for_timeout(1000)
                date_obj = datetime.strptime(travel_date, "%d/%m/%Y")
                if not _select_date(page, date_obj, log):
                    logger.warning(f"[{self.source}] Date {travel_date} beyond booking window.")
                    return []

                # --- Submit ---
                logger.info(f"[{self.source}] Submitting search form")
                _dismiss_overlays(page, log)
                enabled_submit_selector = "button:has-text('Search Flights'):not([disabled]), button:has-text('Search'):not([disabled])"
                try:
                    page.wait_for_selector(enabled_submit_selector, state="visible", timeout=10000)
                except Exception:
                    logger.warning(f"[{self.source}] Submit button remains disabled or not found after 10 seconds.")

                search_flights_btn = page.query_selector("button:has-text('Search Flights')")
                if search_flights_btn and search_flights_btn.is_visible():
                    search_flights_btn.click()
                else:
                    page.click("button:has-text('Search')", timeout=5000)

                page.wait_for_timeout(3000)
                logger.info(f"[{self.source}] Navigation URL after submit: {page.url}")

                # Wait up to 30s for fare data
                for _ in range(30):
                    if captured_data is not None or blocked_status is not None:
                        break
                    page.wait_for_timeout(1000)

                if not captured_data and blocked_status is not None:
                    raise CleartripBlocked(f"Search API returned HTTP {blocked_status} for {origin}-{destination} on {travel_date}")
                if not captured_data:
                    raise RuntimeError(f"Search response never fired or had no fares for {origin}-{destination} on {travel_date}")

            except Exception as e:
                logger.error(f"[{self.source}] Error during navigation/interception: {e}")
                raise  # Reraise to trigger Tenacity retry
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

                    airline_name = airline_code  # Fallback to code if full name not found easily
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
