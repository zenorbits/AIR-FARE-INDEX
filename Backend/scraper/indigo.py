"""
IndiGo (6E) direct scraper.

Drives the goindigo.in one-way search form and reads the availability response
the page itself requests (POST .../v2/flight/search). The results URL carries no
route/date, so the form has to be driven; deep links don't work.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from patchright.sync_api import sync_playwright
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_not_exception_type

from .base import BaseScraper
from scraper.cleanup import cleanup_orphaned_browsers

logger = logging.getLogger(__name__)

HOME_URL = "https://www.goindigo.in/"
SEARCH_URL_FRAGMENT = "/v2/flight/search"

# Booking widget wrappers (stable class names on the homepage).
FROM_WRAPPER = ".search-widget-form-body__from"
TO_WRAPPER = ".search-widget-form-body__to"
DEPARTURE_WRAPPER = ".search-widget-form-body__departure"
PAX_WRAPPER = ".search-widget-form-body__pax-fare-selection"
AIRPORT_INPUT = "input[role='combobox']"
SEARCH_BUTTON = (
    "[aria-label='Booking Widget'] "
    "button.skyplus-button--filled-primary:has-text('Search')"
)

# Cookie banner: choose the most privacy-preserving option first.
POPUP_CLOSE_SELECTORS = [
    "a.cc-dismiss",                # "Accept Essential Only"
    "span.cc-close-banner-btn",    # "Close banner"
]


class IndigoBlocked(Exception):
    """Raised when goindigo.in / its search API blocks us (403/429 or page won't load). Not retried."""


def _nearly_equal(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def parse_indigo_response(
    payload: dict,
    origin: str,
    destination: str,
    lead_time: int,
    scraped_at: datetime,
    fare_classes_out: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse the /v2/flight/search payload offline.

    Rows are insert-compatible with FlightPrice, so they carry no fare_class.
    If `fare_classes_out` is given, the chosen fare's name (Lite/Saver/...) is
    appended to it once per returned row, in the same order.
    """
    results: List[Dict[str, Any]] = []
    scraped_hour = scraped_at.replace(minute=0, second=0, microsecond=0)

    try:
        data = payload.get("data") or {}
        trips = data.get("trips") or []
        if not trips:
            logger.info("IndiGo payload has no trips.")
            return []
        journeys = trips[0].get("journeysAvailable") or []

        fare_config = (data.get("configSettings") or {}).get("fareConfig") or []
        fare_names = {c.get("productClass"): c.get("fareType") for c in fare_config}

        for journey in journeys:
            try:
                if journey.get("stops") != 0:
                    continue
                if journey.get("isSold"):
                    continue

                designator = journey.get("designator") or {}
                # Exact match only: results include nearby airports (NMI, DXN).
                if designator.get("origin") != origin or designator.get("destination") != destination:
                    continue

                segments = journey.get("segments") or []
                if not segments:
                    continue
                ident = segments[0].get("identifier") or {}
                carrier = ident.get("carrierCode")
                number = ident.get("identifier")
                if not carrier or not number:
                    logger.debug(f"Journey without flight identifier: {ident}")
                    continue
                flight_number = f"{carrier}-{number}"

                try:
                    dep_time = datetime.fromisoformat(designator.get("departure", ""))
                except (ValueError, TypeError):
                    logger.warning(f"Failed to parse departure time for {flight_number}: {designator.get('departure')!r}")
                    continue

                economy = [
                    pf for pf in (journey.get("passengerFares") or [])
                    if pf.get("isActive")
                    and str(pf.get("FareClass", "")).strip().lower() == "economy"
                    and pf.get("totalFareAmount") is not None
                ]
                if not economy:
                    logger.debug(f"{flight_number}: no active economy fare, skipping")
                    continue

                best = min(economy, key=lambda pf: float(pf["totalFareAmount"]))
                total = float(best["totalFareAmount"])
                base = float(best.get("totalPublishFare") or 0)
                taxes = float(best.get("totalTax") or 0)
                product_class = best.get("productClass")
                fare_class = fare_names.get(product_class, product_class)

                if not _nearly_equal(base + taxes, total):
                    logger.warning(
                        f"{flight_number}: base {base} + taxes {taxes} != total {total}"
                    )

                results.append({
                    "source": "indigo",
                    "route": f"{origin}-{destination}",
                    "airline": carrier,
                    "flight_number": flight_number,
                    "cabin_class": "ECONOMY",
                    "base_fare": base,
                    "taxes_fees": taxes,
                    "total_fare": total,
                    "stops": 0,
                    "lead_time_days": lead_time,
                    "departure_time": dep_time,
                    "scraped_at": scraped_at,
                    "scraped_hour": scraped_hour,
                })
                if fare_classes_out is not None:
                    fare_classes_out.append(fare_class)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Error parsing IndiGo journey: {e}")
                continue
    except Exception:
        logger.exception("Failed to parse IndiGo payload")
        return []

    if not results:
        logger.info(f"No non-stop economy flights found for {origin}-{destination}.")
    return results


def _attach_network_logging(page) -> None:
    """Diagnostics only: log goindigo.in traffic from submit onwards. Does not affect capture."""
    def on_request(request):
        try:
            if "flight/search" in request.url:
                logger.info(f"[net] request {request.method} {request.url}")
        except Exception as e:
            logger.warning(f"[net] request log error: {e}")

    def on_response(response):
        try:
            if "flight/search" in response.url:
                req = response.request
                ctype = response.headers.get("content-type")
                try:
                    body = response.text()[:300]
                except Exception as e:
                    body = f"<body unavailable: {e}>"
                logger.info(f"[net] flight/search response: method={req.method} status={response.status} content-type={ctype!r}")
                logger.debug(f"[net] flight/search body preview: {body!r}")
        except Exception as e:
            logger.warning(f"[net] response log error: {e}")

    def on_failed(request):
        try:
            if "flight/search" in request.url:
                logger.info(f"[net] requestfailed {request.method} {request.url} | reason: {request.failure}")
        except Exception as e:
            logger.warning(f"[net] requestfailed log error: {e}")

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)


def _dismiss_popups(page) -> None:
    for sel in POPUP_CLOSE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(500)
                logger.info(f"[indigo] Dismissed popup via {sel}")
                return
        except Exception:
            pass


def _wrapper_text(page, wrapper_sel: str) -> str:
    try:
        return page.locator(wrapper_sel).first.inner_text(timeout=3000)
    except Exception:
        return ""


def _field_value(page, wrapper_sel: str) -> str:
    """Committed value shown in a From/To field (e.g. 'Mumbai, BOM'), or 'Going to?' when empty.

    Reads only the field's own label: the wrapper's inner_text also contains the open
    suggestion list, which made an unselected field look selected.
    """
    try:
        return page.locator(wrapper_sel).first.locator(".value-wrapper").first.inner_text(timeout=3000)
    except Exception:
        return ""


def _dump_dom(page, tag: str) -> None:
    try:
        os.makedirs("debug_dumps", exist_ok=True)
        stem = f"debug_dumps/indigo_{tag}_{int(datetime.now().timestamp())}"
        page.screenshot(path=f"{stem}.png")
        with open(f"{stem}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        logger.info(f"[indigo] DOM dump saved to {stem}.png/.html")
    except Exception as e:
        logger.warning(f"[indigo] DOM dump failed: {e}")


# Finds the suggestion row for an airport code in the open dropdown. The dropdown is rendered
# lazily and its markup is not known ahead of time, so this looks for the deepest visible
# element (outside the header/nav/footer and outside the field itself, below the field) whose
# text contains the code, then climbs to the nearest clickable row. Returns
# [row, container, info] (elements or null) without mutating the page.
FIND_OPTION_JS = r"""
(args) => {
  const [code, wrapperSel] = args;
  const re = new RegExp('\\b' + code + '\\b');
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const wrapper = document.querySelector(wrapperSel);
  const fieldTop = wrapper ? wrapper.getBoundingClientRect().top : 0;
  const cands = Array.from(document.querySelectorAll('body *')).filter(el => {
    if (['SCRIPT', 'STYLE', 'INPUT'].includes(el.tagName)) return false;
    if ((el.textContent || '').length > 300) return false;
    if (el.closest('header, nav, footer, .booking-widget-field')) return false;
    if (!vis(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.top < fieldTop - 5 || r.top > fieldTop + 700 || r.top > window.innerHeight) return false;
    return re.test(el.innerText || '');
  });
  const set = new Set(cands);
  const leaves = cands.filter(el => !Array.from(el.children).some(c => set.has(c)));
  if (!leaves.length) return [null, null, {leaves: 0}];
  const leaf = leaves[0];
  const rowSel = 'li, [role=option], [role=button], a, button, [tabindex]';
  let row = null, e = leaf;
  for (let i = 0; e && i < 6; i++, e = e.parentElement) {
    if (e.classList.contains('booking-widget-field') || e.querySelector('.booking-widget-field')) break;
    if (e.matches(rowSel)) { row = e; break; }
  }
  if (!row) row = leaf;
  let container = null;
  e = row.parentElement;
  for (let i = 0; e && i < 6; i++, e = e.parentElement) {
    if (e.matches('ul, ol, [role=listbox]')) { container = e; break; }
  }
  if (!container) container = (row.parentElement && row.parentElement.parentElement) || row.parentElement;
  return [row, container, {leaves: leaves.length, rowTag: row.tagName, rowClass: String(row.className).slice(0, 120),
                           rowText: (row.innerText || '').trim().slice(0, 100)}];
}
"""


def _find_option(page, wrapper_sel: str, iata: str):
    """(row_handle, container_handle, info) for the airport suggestion, or (None, None, info)."""
    h = page.evaluate_handle(FIND_OPTION_JS, [iata, wrapper_sel])
    try:
        row = h.get_property("0").as_element()
        container = h.get_property("1").as_element()
        info = h.get_property("2").json_value()
        return row, container, info
    finally:
        h.dispose()


def _dump_dropdown(page, wrapper_sel: str, iata: str, container, info) -> None:
    """Save the open dropdown's outerHTML, the field wrapper's outerHTML and a screenshot."""
    try:
        os.makedirs("debug_dumps", exist_ok=True)
        stem = f"debug_dumps/indigo_dropdown_{iata}_{int(datetime.now().timestamp())}"
        page.screenshot(path=f"{stem}.png")
        with open(f"{stem}_container.html", "w", encoding="utf-8") as f:
            f.write(container.evaluate("el => el.outerHTML") if container is not None else "<!-- no container -->")
        with open(f"{stem}_wrapper.html", "w", encoding="utf-8") as f:
            f.write(page.locator(wrapper_sel).first.evaluate("el => el.outerHTML"))
        with open(f"{stem}_page.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        logger.info(f"[indigo] Dropdown dump saved to {stem}.png / _container.html / _wrapper.html / _page.html; row info: {info}")
    except Exception as e:
        logger.warning(f"[indigo] Dropdown dump failed: {e}")


def _select_airport(page, wrapper_sel: str, iata: str) -> None:
    """Pick `iata` in the From/To field. Skips if the field already shows it (From is pre-filled from geolocation).

    Click the field, type the code slowly, wait for a visible suggestion containing the code,
    click that element, then verify only the field's .value-wrapper (not the open list).
    """
    code = re.compile(rf"\b{re.escape(iata)}\b")
    current = _field_value(page, wrapper_sel)
    if code.search(current):
        logger.info(f"[indigo] {wrapper_sel} already shows {iata!r} ({current!r})")
        return

    wrapper = page.locator(wrapper_sel).first
    field = wrapper.locator(AIRPORT_INPUT).first

    last_error = "unknown error"
    for attempt in range(2):
        try:
            logger.info(f"[indigo] {wrapper_sel}: attempt {attempt + 1}, clicking field (value now {_field_value(page, wrapper_sel)!r})")
            wrapper.click(timeout=10000)
            page.wait_for_timeout(700)
            if field.is_visible():
                field.click(timeout=3000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                field.press_sequentially(iata, delay=250)
            else:
                logger.info(f"[indigo] {wrapper_sel}: input not visible, typing via keyboard")
                page.keyboard.type(iata, delay=250)

            row = container = None
            info = None
            for _ in range(20):
                page.wait_for_timeout(500)
                row, container, info = _find_option(page, wrapper_sel, iata)
                if row is not None:
                    break
            if row is None:
                logger.warning(f"[indigo] {wrapper_sel}: no visible option containing {iata!r} ({info})")
                _dump_dropdown(page, wrapper_sel, iata + "_nooption", None, info)
                last_error = f"no visible option containing {iata!r}"
                continue

            # Capture the real markup now, while the options are visible (success or not).
            _dump_dropdown(page, wrapper_sel, iata, container, info)
            logger.info(f"[indigo] {wrapper_sel}: clicking option {info}")
            row.scroll_into_view_if_needed(timeout=3000)
            row.click(timeout=5000)
            page.wait_for_timeout(1000)

            value = _field_value(page, wrapper_sel)
            logger.info(f"[indigo] {wrapper_sel}: .value-wrapper after click = {value!r}")
            if code.search(value):
                return
            last_error = f".value-wrapper after clicking option was {value!r}"
            _dump_dom(page, f"verifyfail_{iata}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[indigo] {wrapper_sel}: attempt {attempt + 1} error: {e}")
    raise RuntimeError(f"Could not select airport {iata!r} in {wrapper_sel!r}: {last_error}")


def _select_date(page, date_obj: datetime) -> None:
    """Open the departure calendar and click the day, paging forward as needed."""
    month = date_obj.strftime("%B")
    day, year = date_obj.day, date_obj.year
    labels = [f"{day} {month} {year}", f"{month} {day}, {year}"]
    parts = []
    for lab in labels:
        # ' ' prefix / '^' anchor stop "6 September" from matching "16 September".
        parts.append(f"[aria-label^='{lab}']:not(.booking-widget-field)")
        parts.append(f"[aria-label*=' {lab}']:not(.booking-widget-field)")
    cell = page.locator(", ".join(parts)).locator("visible=true").first

    dep_field = page.locator(f"{DEPARTURE_WRAPPER} .booking-widget-field").first
    # Selecting the destination may auto-open the calendar; clicking again would close it.
    if dep_field.get_attribute("aria-expanded") == "true":
        logger.info("[indigo] Calendar already open")
    else:
        page.locator(DEPARTURE_WRAPPER).first.click(timeout=10000)
    page.wait_for_timeout(1000)

    next_arrow = page.locator("[aria-label*='next' i]:not([aria-label*='offer' i]):visible").first
    for _ in range(12):
        if cell.count() and cell.is_visible():
            break
        try:
            next_arrow.click(timeout=2000)
        except Exception:
            break
        page.wait_for_timeout(600)

    cell.click(timeout=10000)
    page.wait_for_timeout(600)

    aria = page.locator(f"{DEPARTURE_WRAPPER} .booking-widget-field").first.get_attribute("aria-label") or ""
    if labels[0] not in aria:
        raise RuntimeError(f"Departure date not applied: field aria-label is {aria!r}, expected {labels[0]!r}")


class IndigoScraper(BaseScraper):
    """Scraper for IndiGo using the page's own /v2/flight/search response."""

    def __init__(self):
        self.source = "indigo"
        # Diagnostics for the last scrape() attempt (read by __main__).
        self.last_home_status: Optional[int] = None
        self.last_payload: Optional[dict] = None
        self.last_fare_classes: List[str] = []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_not_exception_type(IndigoBlocked),
        reraise=True,
    )
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """travel_date is expected in DD/MM/YYYY format (same as the other scrapers)."""
        now = datetime.now()
        date_obj = datetime.strptime(travel_date, "%d/%m/%Y")
        captured: Optional[dict] = None
        blocked_status: Optional[int] = None
        page = None

        self.last_home_status = None
        self.last_payload = None
        self.last_fare_classes = []

        logger.info(f"[{self.source}] Scraping route {origin}-{destination} for {travel_date}")

        with sync_playwright() as p:
            # Persistent profile keeps cookies/history. No custom user_agent / automation flag:
            # they mismatch patchright's Chromium fingerprint.
            context = p.chromium.launch_persistent_context(
                user_data_dir="./indigo_browser_profile",
                headless=False,
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                geolocation={"longitude": 77.2090, "latitude": 28.6139},
                permissions=["geolocation"],
            )

            try:
                page = context.pages[0] if context.pages else context.new_page()

                # Warm-up: homepage
                try:
                    home_resp = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    raise IndigoBlocked(f"Homepage failed to load: {e}") from e
                home_status = home_resp.status if home_resp is not None else None
                self.last_home_status = home_status
                logger.info(f"[{self.source}] Homepage response: {home_status}, final URL: {page.url}")
                if home_status is None or home_status in (403, 429):
                    self._save_screenshot(page, "indigo_1_home.png")
                    raise IndigoBlocked(f"Homepage returned HTTP {home_status}")

                page.wait_for_timeout(8000)
                _dismiss_popups(page)
                self._save_screenshot(page, "indigo_1_home.png")

                def handle_response(response):
                    nonlocal captured, blocked_status
                    try:
                        if SEARCH_URL_FRAGMENT not in response.url:
                            return
                        if response.request.method != "POST":
                            return
                        logger.info(f"[{self.source}] Search response status: {response.status}")
                        if response.status in (403, 429):
                            blocked_status = response.status
                            return
                        if response.status != 200:
                            logger.warning(f"[{self.source}] search returned {response.status}")
                            return
                        # Content-Type is text/plain but the body is JSON.
                        payload = json.loads(response.text())
                        trips = (payload.get("data") or {}).get("trips")
                        if trips:
                            captured = payload
                        else:
                            logger.warning(f"[{self.source}] Search response had no trips; waiting for another")
                    except Exception as e:
                        logger.error(f"[{self.source}] Error reading search response: {e}")

                page.on("response", handle_response)

                self._fill_search(page, origin, destination, date_obj)

                for _ in range(45):
                    if captured is not None or blocked_status is not None:
                        break
                    page.wait_for_timeout(1000)

                self._save_screenshot(page, "indigo_2_results.png")

                if captured is None and blocked_status is not None:
                    raise IndigoBlocked(f"Search API returned HTTP {blocked_status} for {origin}-{destination} on {travel_date}")
                if captured is None:
                    raise RuntimeError(f"Search response never captured for {origin}-{destination} on {travel_date}")

            except Exception as e:
                logger.error(f"[{self.source}] Error during search/interception: {e}")
                if page is not None:
                    self._save_debug(page, origin, destination, travel_date)
                raise  # IndigoBlocked skips retry; anything else triggers Tenacity
            finally:
                context.close()
                try:
                    cleanup_orphaned_browsers()
                except Exception as e:
                    logger.warning(f"[{self.source}] Orphaned browser cleanup failed: {e}")

        self.last_payload = captured
        self._dump_payload(captured, origin, destination, travel_date)

        fare_classes: List[str] = []
        results = parse_indigo_response(captured, origin, destination, lead_time_days, now, fare_classes)
        self.last_fare_classes = fare_classes
        logger.info(f"[{self.source}] Extracted {len(results)} flights for {origin}-{destination}")
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def scrape_batch(self, jobs: List[tuple]) -> List[Dict[str, Any]]:
        import subprocess
        import socket
        import psutil
        import time
        from pathlib import Path

        # C. Find and kill existing chrome with this profile
        profile_dir = str(Path(__file__).resolve().parents[1] / ".profiles" / "indigo_cdp")
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if p.info['name'] and 'chrome' in p.info['name'].lower():
                    cmdline = p.info.get('cmdline') or []
                    cmd_str = ' '.join(cmdline)
                    if profile_dir in cmd_str:
                        logger.info(f"[{self.source}] Killing existing Chrome tree {p.pid} for profile {profile_dir}")
                        for child in p.children(recursive=True):
                            try:
                                child.kill()
                            except psutil.NoSuchProcess:
                                pass
                        p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        # Give it a second to shut down
        time.sleep(1)

        # Check if port 9333 is in use
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', 9333)) == 0:
                raise RuntimeError("Port 9333 is already in use after cleanup!")

        # Resolve chrome path
        chrome_path = None
        for path in [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]:
            if os.path.exists(path):
                chrome_path = path
                break
        
        if not chrome_path:
            raise RuntimeError("Could not find chrome.exe")

        logger.info(f"[{self.source}] Starting chrome via subprocess...")
        chrome_proc = subprocess.Popen(
            [
                chrome_path,
                "--remote-debugging-port=9333",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Poll for CDP readiness
        import urllib.request
        ready = False
        for _ in range(40):
            try:
                resp = urllib.request.urlopen("http://localhost:9333/json/version", timeout=1)
                if resp.getcode() == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        
        if not ready:
            chrome_proc.kill()
            raise RuntimeError("Chrome CDP did not become ready in 20s")

        all_results = []
        now = datetime.now()
        attempts = 0
        successes = 0
        blocked_count = 0
        row_counts = {}

        # Remove Warm-up outside loop, move it inside
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9333")
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                
                quick_mode = getattr(self, 'quick_mode', False)
                
                # To track search outcomes for the report
                search_outcomes = []
                from collections import deque
                jobs_queue = deque([(j, 0) for j in jobs])
                idx = 0

                while jobs_queue:
                    (origin, destination, lead_time_days), attempt_num = jobs_queue.popleft()
                    idx += 1
                    attempts += 1
                    travel_date = (now + timedelta(days=lead_time_days)).strftime("%d/%m/%Y")
                    date_obj = now + timedelta(days=lead_time_days)
                    captured = None
                    blocked_status = None
                    search_failed = False
                    
                    # Store mapping of requestId -> URL for this search
                    request_urls = {}
                    
                    def on_request_will_be_sent(params):
                        req = params.get("request", {})
                        url = req.get("url", "")
                        if "flight/search" in url:
                            request_urls[params.get("requestId")] = url
                            
                    def on_response_extra_info(params):
                        nonlocal blocked_status
                        req_id = params.get("requestId")
                        if req_id in request_urls:
                            statusCode = params.get("statusCode")
                            logger.info(f"[{self.source}] ExtraInfo {req_id}: statusCode={statusCode}")
                            if statusCode in (403, 429):
                                blocked_status = statusCode
                                
                    def on_loading_failed(params):
                        nonlocal blocked_status
                        req_id = params.get("requestId")
                        if req_id in request_urls:
                            logger.info(f"[{self.source}] loadingFailed {req_id}: {params}")
                            if "corsErrorStatus" in params:
                                blocked_status = "cors_error"
                                
                    def on_request_failed(request):
                        nonlocal blocked_status
                        if "flight/search" in request.url:
                            logger.error(f"[{self.source}] requestfailed: {request.url} - {request.failure}")
                            if "net::ERR_FAILED" in str(request.failure) or "net::ERR_CONNECTION_REFUSED" in str(request.failure):
                                # If blocked_status is already set via CDP, let it be. Else set to cors_error.
                                if not blocked_status:
                                    blocked_status = "cors_error"

                    cdp.on("Network.requestWillBeSent", on_request_will_be_sent)
                    cdp.on("Network.responseReceivedExtraInfo", on_response_extra_info)
                    cdp.on("Network.loadingFailed", on_loading_failed)
                    page.on("requestfailed", on_request_failed)
                    
                    logger.info(f"[{self.source}] Navigating to HOME_URL for {origin}-{destination} {travel_date}")
                    try:
                        home_resp = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                        if idx == 1:
                            logger.info(f"[{self.source}] First search warm-up: waiting 5s")
                            page.wait_for_timeout(5000)
                    except Exception as e:
                        logger.error(f"[{self.source}] Homepage failed to load: {e}")
                        search_failed = True
                    
                    if not search_failed:
                        self.last_home_status = home_resp.status if home_resp else None
                        if self.last_home_status in (403, 429):
                            logger.error(f"[{self.source}] Homepage returned HTTP {self.last_home_status}")
                            search_failed = True
                            blocked_status = self.last_home_status
                    
                    if not search_failed:
                        page.wait_for_timeout(3000)
                        _dismiss_popups(page)
                        try:
                            page.wait_for_selector(".search-widget-form-body__from", state="visible", timeout=20000)
                        except Exception as e:
                            logger.error(f"[{self.source}] form not visible: {e}")
                            search_failed = True

                    def handle_response(response):
                        nonlocal captured, blocked_status
                        try:
                            if SEARCH_URL_FRAGMENT not in response.url:
                                return
                            if response.request.method != "POST":
                                return
                            if response.status in (403, 429):
                                blocked_status = response.status
                                return
                            if response.status != 200:
                                return
                            payload = json.loads(response.text())
                            if (payload.get("data") or {}).get("trips"):
                                captured = payload
                        except Exception:
                            pass
                    
                    outcome = "TIMEOUT-other"
                    if not search_failed:
                        page.on("response", handle_response)
                        try:
                            self._fill_search(page, origin, destination, date_obj)
                            
                            for _ in range(45):
                                if captured is not None or blocked_status is not None:
                                    break
                                page.wait_for_timeout(1000)
                            
                            if captured is not None:
                                self._dump_payload(captured, origin, destination, travel_date)
                                fare_classes = []
                                results = parse_indigo_response(captured, origin, destination, lead_time_days, now, fare_classes)
                                all_results.extend(results)
                                successes += 1
                                row_counts[f"{origin}-{destination} {travel_date}"] = len(results)
                                logger.info(f"[{self.source}] Extracted {len(results)} flights for {origin}-{destination} on {travel_date}")
                                outcome = "OK"
                            elif blocked_status:
                                blocked_count += 1
                                logger.error(f"[{self.source}] Blocked ({blocked_status}) for {origin}-{destination} on {travel_date}")
                                search_failed = True
                                outcome = f"BLOCKED {blocked_status}"
                            else:
                                logger.error(f"[{self.source}] Timeout capturing response for {origin}-{destination} on {travel_date}")
                                search_failed = True
                                outcome = "TIMEOUT-other"
                        except Exception as e:
                            logger.error(f"[{self.source}] Failed on {origin}-{destination} {travel_date}: {e}")
                            search_failed = True
                            outcome = "TIMEOUT-other"
                        finally:
                            page.remove_listener("response", handle_response)
                    
                    search_outcomes.append({
                        "index": idx,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "route_lead": f"{origin}-{destination} T+{lead_time_days}",
                        "outcome": outcome
                    })

                    cdp.remove_listener("Network.requestWillBeSent", on_request_will_be_sent)
                    cdp.remove_listener("Network.responseReceivedExtraInfo", on_response_extra_info)
                    cdp.remove_listener("Network.loadingFailed", on_loading_failed)
                    page.remove_listener("requestfailed", on_request_failed)
                            
                    if search_failed:
                        try:
                            os.makedirs("Backend/logs", exist_ok=True)
                            fail_path = f"Backend/logs/indigo_fail_{origin}_{destination}_{lead_time_days}.png"
                            page.screenshot(path=fail_path)
                            logger.error(f"[{self.source}] Search failed. URL: {page.url} | Screenshot: {fail_path}")
                        except Exception as e:
                            logger.error(f"[{self.source}] Failed to take screenshot: {e}")
                            
                        if outcome == "TIMEOUT-other" and attempt_num == 0:
                            logger.info(f"[{self.source}] Queuing {origin}-{destination} T+{lead_time_days} for retry")
                            jobs_queue.append(((origin, destination, lead_time_days), 1))
                            
                        logger.info(f"[{self.source}] Delaying 5s after failure...")
                        time.sleep(5)
                    else:
                        if quick_mode:
                            self.random_delay(20.0, 30.0)
                        else:
                            self.random_delay(30.0, 90.0)

            finally:
                try:
                    if 'browser' in locals():
                        browser.close()
                except:
                    pass
                try:
                    parent = psutil.Process(chrome_proc.pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    parent.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        
        return all_results, attempts, successes, blocked_count, row_counts, search_outcomes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fill_search(self, page, origin: str, destination: str, date_obj: datetime) -> None:
        # One Way is the default; make sure.
        try:
            one_way = page.locator("input#radio-input-triptype-oneWay").first
            if not one_way.is_checked():
                page.locator("span.custom-radio[aria-label='oneWay']").first.click(timeout=3000)
        except Exception as e:
            logger.warning(f"[{self.source}] Could not confirm One Way: {e}")

        _select_airport(page, FROM_WRAPPER, origin)
        page.wait_for_timeout(1000)
        _select_airport(page, TO_WRAPPER, destination)
        page.wait_for_timeout(1000)

        _select_date(page, date_obj)

        pax = _wrapper_text(page, PAX_WRAPPER)
        if "1 Passenger" not in pax:
            logger.warning(f"[{self.source}] Unexpected passenger selection: {pax!r}")

        submit = page.locator(SEARCH_BUTTON).first
        for _ in range(10):
            cls = submit.get_attribute("class") or ""
            if "skyplus-button--disabled" not in cls:
                break
            page.wait_for_timeout(1000)
        _attach_network_logging(page)
        submit.click(timeout=10000)

    def _save_screenshot(self, page, path: str) -> None:
        try:
            page.screenshot(path=path)
        except Exception as e:
            logger.warning(f"[{self.source}] Screenshot {path} failed: {e}")

    def _save_debug(self, page, origin: str, destination: str, travel_date: str) -> None:
        """Screenshot + full DOM, so a selector miss can be diagnosed without another blind run."""
        try:
            os.makedirs("debug_dumps", exist_ok=True)
            stem = f"debug_dumps/indigo_fail_{origin}_{destination}_{travel_date.replace('/', '-')}_{int(datetime.now().timestamp())}"
            page.screenshot(path=f"{stem}.png")
            with open(f"{stem}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.info(f"[{self.source}] Failure debug saved to {stem}.png/.html")
        except Exception as e:
            logger.warning(f"[{self.source}] Failure debug dump failed: {e}")

    def _dump_payload(self, payload: dict, origin: str, destination: str, travel_date: str) -> None:
        try:
            os.makedirs("debug_dumps", exist_ok=True)
            safe_date = travel_date.replace("/", "-")
            path = f"debug_dumps/indigo_{origin}_{destination}_{safe_date}_{int(datetime.now().timestamp())}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"[{self.source}] Failed to write debug dump: {e}")


if __name__ == "__main__":
    import sys
    os.makedirs("debug_dumps", exist_ok=True)
    log_path = f"debug_dumps/indigo_run_{int(datetime.now().timestamp())}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )
    logger.info(f"Full run log: {log_path}")

    quick_mode = "--quick" in sys.argv
    if quick_mode:
        routes = [("DEL", "BOM"), ("DEL", "BLR")]
        lead_times = [7, 14]
    else:
        import yaml
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml"), encoding="utf-8") as fh:
            _cfg = yaml.safe_load(fh)
        routes = [(r["origin"], r["destination"]) for r in _cfg["routes"]]
        lead_times = _cfg["lead_time_days"]

    jobs = [(r[0], r[1], lt) for r in routes for lt in lead_times]

    scraper = IndigoScraper()
    if quick_mode:
        scraper.quick_mode = True
        
    import time
    start_time = time.time()
    
    try:
        rows, attempts, successes, blocked, row_counts, outcomes = scraper.scrape_batch(jobs)
        
        print("\n--- Indigo Attach Mode Standalone Run Report ---")
        print(f"Searches Attempted:  {attempts}")
        print(f"Searches Succeeded:  {successes}")
        print(f"Searches 403 Blocked:{blocked}")
        
        print("\nPer-Search Outcomes:")
        print(f"{'Idx':<4} | {'Time':<8} | {'Route/Lead':<18} | {'Outcome'}")
        print("-" * 55)
        for o in outcomes:
            print(f"{o['index']:<4} | {o['timestamp']:<8} | {o['route_lead']:<18} | {o['outcome']}")
        
        timeouts = sum(1 for o in outcomes if o["outcome"] == "TIMEOUT-other")
        print("\nRow counts per route/window:")
        for job_str, count in row_counts.items():
            print(f"  {job_str}: {count} rows")
            
        print("\n3 Sample Rows:")
        for row in rows[:3]:
            print(f"  {row['route']} | {row['flight_number']} dep={row['departure_time']} total={row['total_fare']} base={row['base_fare']} taxes={row['taxes_fees']} cabin={row['cabin_class']}")
            
        runtime = time.time() - start_time
        logger.info(
            f"[indigo] summary: ok={successes} blocked={blocked} timeout={timeouts} "
            f"rows={len(rows)} runtime={runtime:.2f}s"
        )
            
    except Exception as e:
        import traceback
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
