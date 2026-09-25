import os
import sys
import time
import json
import psutil
import urllib.request
import subprocess
import re
import random
import base64
import gzip
import threading
from datetime import datetime, timedelta
from pathlib import Path

from patchright.sync_api import sync_playwright

# Do not import db, we are not inserting in this test script per instructions.
# In production, we would import from Backend.cleaning.pipeline

LOG_SINK = None  # set by run_makemytrip.py: callable(str) that writes into its rotating log file

def log(*args):
    msg = " ".join(str(a) for a in args)
    if LOG_SINK is not None: LOG_SINK(msg)
    else: print(datetime.now().strftime("%H:%M:%S"), msg, flush=True)

# journeyKey: DEP$ARR$YYYY-MM-DD HH:MM$FLIGHT  (connections are legs joined by '|', so those don't match)
KEY_RE = re.compile(r"^([A-Z]{3})\$([A-Z]{3})\$(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\$([A-Z0-9]{2}-\d+)$")
# search-stream-dt request param: it=DEL-BOM-20260928
IT_RE = re.compile(r"[?&]it=([A-Z]{3})-([A-Z]{3})-(\d{8})(?:&|$)")

# Explicit per-card product field: journeyMap[key].flightDetail.legListV2[].airlineInfo.farefamily
# (seen: SAVER, ECO VALUE, Value, SPICESAVER = economy; Stretch = IndiGo premium). Deliberately NOT a price filter.
NON_ECONOMY_RE = re.compile(r"stretch|business|premium|first|biz", re.I)  # "biz" = Air India Express Xpress Biz

def strip_html(s):
    return re.sub(r"<[^>]+>", "", str(s)).strip()

def fmt_counts(d):
    return ";".join(f"{k}:{v}" for k, v in sorted((d or {}).items())) or "-"

HOME = "https://www.makemytrip.com/"
ORIGIN_URL = "https://www.makemytrip.com"
CITY_NAMES = {"DEL": ("Delhi",), "BOM": ("Mumbai",), "BLR": ("Bengaluru", "Bangalore"), "CCU": ("Kolkata",),
              "MAA": ("Chennai",), "HYD": ("Hyderabad",)}
DIAG_DIR = Path(__file__).resolve().parent.parent / "scratch" / "mmt_diag"

# --- page-side helpers used only for diagnostics / read-back --------------------------------------------------------
ACTIVE_JS = r'''() => {
  const all = [...document.querySelectorAll('input.react-autosuggest__input')];
  const a = document.activeElement;
  return {
    active: a ? {tag: a.tagName, id: a.id, ph: a.placeholder, val: a.value, cls: String(a.className).slice(0, 50), idx_among_autosuggest_inputs: all.indexOf(a)} : null,
    autosuggest_inputs: all.map((i, n) => ({n, id: i.id, ph: i.placeholder, val: i.value, visible: !!(i.offsetWidth || i.offsetHeight)}))
  };
}'''
PICK_JS = r'''li => {
  const ul = li.closest('ul'); const kids = ul ? [...ul.children] : [];
  const panel = li.closest('[class*="fsw"], [class*="utosuggest"]');
  return {text: (li.innerText || '').replace(/\s+/g, ' ').slice(0, 70), idx: kids.indexOf(li), of: kids.length,
          ul_cls: ul ? String(ul.className).slice(0, 50) : null, ul_visible: ul ? !!(ul.offsetWidth || ul.offsetHeight) : null,
          panel_cls: panel ? String(panel.className).slice(0, 50) : null};
}'''
STATE_JS = r'''() => ({
  url: location.href,
  day_pickers: document.querySelectorAll('.DayPicker').length,
  search_btn: !!document.querySelector('.widgetSearchBtn'),
  errors: [...document.querySelectorAll('[class*="error" i], [role="alert"], .redText')]
            .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 5)
})'''
OVERLAY_JS = r'''() => {
  let selectors = ['.imageSlideContainer', '.webengage-data-wrapper', '[data-cy="outsideModal"]', '.loginModal', '.autopop'];
  selectors.forEach(sel => { document.querySelectorAll(sel).forEach(el => el.remove()); });
  document.querySelectorAll('img[src*="promos.makemytrip.com"]').forEach(el => {
      let parent = el.parentElement;
      if (parent && parent.tagName !== 'BODY' && parent.id !== 'SW') parent.remove();
      else el.remove();
  });
}'''

WD = {"fired": False}  # set by the watchdog timer thread

class FillError(Exception):
    """The form could not be filled correctly; the caller may redo the whole fill once."""

def is_target_closed(ex):
    return (WD["fired"] or "TargetClosed" in type(ex).__name__ or "has been closed" in str(ex)
            or "Connection closed" in str(ex) or "Target closed" in str(ex))

def jdump(o, limit=700):
    s = json.dumps(o, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"

class StepTimer:
    """Wall time per fill step; accumulates across a from-scratch retry."""
    def __init__(self): self.t = time.time(); self.d = {}
    def mark(self, name):
        now = time.time(); self.d[name] = self.d.get(name, 0.0) + (now - self.t); self.t = now
    def __str__(self): return ";".join(f"{k}:{v:.1f}" for k, v in self.d.items())

def _option_texts(page):
    try: return [re.sub(r"\s+", " ", t).strip() for t in page.locator("li[role='option']").all_inner_texts()]
    except Exception as ex: return [f"<could not read options: {ex}>"]

def read_form(page):
    """The From / To / date text the form is actually displaying (the labels the fill clicks)."""
    out = {}
    for key, sel in (("from", "label[for='fromCity']"), ("to", "label[for='toCity']"), ("date", "label[for='departure']")):
        try: out[key] = re.sub(r"\s+", " ", page.locator(sel).first.inner_text(timeout=2000)).strip()[:90]
        except Exception as ex: out[key] = f"<unreadable: {type(ex).__name__}>"
    return out

def _shows_airport(text, code):
    return bool(re.search(rf"\b{code}\b", text)) or any(n.lower() in text.lower() for n in CITY_NAMES.get(code, ()))

def _shown_date(text):
    m = re.search(r"\b(\d{1,2})\s*([A-Za-z]{3})", text) or None
    if m: return int(m.group(1)), m.group(2).title()
    m = re.search(r"\b([A-Za-z]{3})\w*\s+(\d{1,2})\b", text)
    return (int(m.group(2)), m.group(1).title()) if m else None

def form_problems(form, origin, dest, target_date, which):
    p = []
    if "from" in which and not _shows_airport(form["from"], origin): p.append(f"From shows {form['from']!r}, expected {origin}")
    if "to" in which and not _shows_airport(form["to"], dest): p.append(f"To shows {form['to']!r}, expected {dest}")
    if "date" in which:
        shown = _shown_date(form["date"])
        if shown != (target_date.day, target_date.strftime("%b")):
            p.append(f"Date shows {form['date']!r} (parsed {shown}), expected {target_date.day} {target_date:%b}")
    return p

def verify_form(page, origin, dest, target_date, which, when, timeout_ms=3000):
    """Poll until the displayed form matches the request (or time out), log the read-back, raise FillError on mismatch."""
    deadline = time.time() + timeout_ms / 1000
    while True:
        form = read_form(page)
        problems = form_problems(form, origin, dest, target_date, which)
        if not problems or time.time() > deadline: break
        page.wait_for_timeout(200)
    log(f"READBACK {when}: from={form['from']!r} to={form['to']!r} date={form['date']!r} -> "
        f"{'OK' if not problems else 'MISMATCH: ' + '; '.join(problems)}")
    if problems: raise FillError(f"form read-back mismatch {when}: " + "; ".join(problems))

def dump_state(sess, tag, origin, dest, lead):
    """Screenshot + URL + field values, logged as one STATE line (used on fill failures and when no request is sent)."""
    page = sess.page
    try: info = page.evaluate(STATE_JS)
    except Exception as ex: info = {"state_error": f"{type(ex).__name__}"}
    try: inputs = page.evaluate(ACTIVE_JS)["autosuggest_inputs"]
    except Exception: inputs = "?"
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        shot = str(DIAG_DIR / f"{origin}-{dest}_T{lead}_{datetime.now():%H%M%S}_{tag}.png")
        page.screenshot(path=shot, timeout=10000)
    except Exception as ex: shot = f"<screenshot failed: {type(ex).__name__}>"
    log(f"STATE[{tag}] url={info.get('url')} form={jdump(read_form(page))} inputs={jdump(inputs)} "
        f"day_pickers={info.get('day_pickers')} search_btn={info.get('search_btn')} errors={info.get('errors')} screenshot={shot}")

def pick_airport(page, label_sel, code, required, field):
    """Type an IATA code into the open autosuggest and pick its option. Single attempt: on any problem the caller
    redoes the WHOLE fill once, because an in-place retry can 'recover' into the wrong field.
    Waits (max 10s) for an option containing the code; logs which input is typed into and which option/list is picked."""
    try: page.locator(label_sel).click(timeout=5000 if required else 2000, force=True)
    except Exception:
        if required: raise
    field_input = page.locator(f"input.react-autosuggest__input[placeholder='{field.title()}']").first
    try: field_input.wait_for(state="visible", timeout=5000 if required else 3000)
    except Exception:
        raise FillError(f"{field}: the {field.title()} autosuggest input did not open; page inputs: "
                        f"{jdump(page.evaluate(ACTIVE_JS).get('autosuggest_inputs'))}")
    field_input.evaluate("el => { el.focus(); el.value = ''; }")
    try: log(f"DIAG {field}: about to type {code!r}; page state: {jdump(page.evaluate(ACTIVE_JS))}")
    except Exception as ex: log(f"DIAG {field}: could not read active field: {type(ex).__name__}")
    page.keyboard.type(code, delay=150)
    opts = page.locator("li[role='option']")
    try:
        # has_text matches raw textContent, where the code runs straight into the next word ("DELNew Delhi..."),
        # so no \b: prefer an option that STARTS with the code, accept one that merely contains it.
        opts.filter(has_text=re.compile(code)).first.wait_for(state="attached", timeout=10000)
    except Exception as ex:
        texts = _option_texts(page)
        log(f"AUTOSUGGEST[{field}] no option matching {code}; error={type(ex).__name__}; options present: {texts}")
        raise FillError(f"{field}: no autosuggest option matching {code} within 10s; options={texts}")
    preferred = opts.filter(has_text=re.compile(rf"^{code}"))
    target = (preferred if preferred.count() else opts.filter(has_text=re.compile(code))).first
    try: picked = target.evaluate(PICK_JS)
    except Exception as ex: picked = f"<unreadable: {type(ex).__name__}>"
    target.click(force=True, timeout=5000)
    log(f"DIAG {field}: picked {jdump(picked)}")

CAPTIONS_JS = r'''() => [...document.querySelectorAll('.DayPicker-Month .DayPicker-Caption')].map(c => (c.innerText || '').trim())'''

# The "Sign up/Login now to" popup: found by its visible mobile-number input; its close (X) is the nearest visible
# element with "close" in its class / data-cy / aria-label. It is only ever CLICKED (or Escape pressed); nothing is typed.
MODAL_JS = r'''() => {
  // "shown" = on screen, not hidden, and topmost at its centre point. The page footer has a "send app link" mobile
  // input that has layout size but sits far below the viewport, so offsetWidth alone is not enough.
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return false;
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!t && (t === e || e.contains(t));
  };
  const inp = [...document.querySelectorAll('input')].find(i => /mobile/i.test((i.placeholder || '') + (i.name || '') + (i.id || '')) && vis(i));
  document.querySelectorAll('[data-mmt-close-target]').forEach(e => e.removeAttribute('data-mmt-close-target'));
  if (!inp) return {present: false};
  let root = inp, x = null;
  for (let k = 0; k < 14 && root.parentElement && !x; k++) {
    root = root.parentElement;
    x = [...root.querySelectorAll('[class*="close" i], [data-cy*="close" i], [aria-label*="close" i]')].find(vis) || null;
  }
  if (x) x.setAttribute('data-mmt-close-target', '1');
  return {present: true, x: x ? {tag: x.tagName, cls: String(x.className).slice(0, 60), dataCy: x.getAttribute('data-cy')} : null};
}'''

RUN_T0 = time.time()

def dismiss_login_modal(sess, when):
    """If the login/sign-up popup is showing, dismiss it like a user would: click its X, else press Escape.
    Never types into it. Logs every dismissal with session/run time and counters (time-based vs count-based)."""
    page = sess.page
    st = page.evaluate(MODAL_JS)
    if not st.get("present"): return False
    how = "Escape"
    if st.get("x"):
        try:
            page.locator('[data-mmt-close-target="1"]').first.click(timeout=2000, force=True)
            how = "X button"
        except Exception:
            page.keyboard.press("Escape")
    else:
        page.keyboard.press("Escape")
    gone = False
    for _ in range(10):
        page.wait_for_timeout(200)
        if not page.evaluate(MODAL_JS).get("present"): gone = True; break
    if not gone and how == "X button":  # X did not work: Escape as the fallback
        page.keyboard.press("Escape"); how = "X button then Escape"
        for _ in range(10):
            page.wait_for_timeout(200)
            if not page.evaluate(MODAL_JS).get("present"): gone = True; break
    log(f"MODAL {'dismissed' if gone else 'COULD NOT BE DISMISSED'} via {how} ({when}): "
        f"session_time={time.time() - sess.t0:.0f}s run_time={time.time() - RUN_T0:.0f}s search_no={sess.search_no} "
        f"homepage_loads={sess.homepage_loads} x={st.get('x')}")
    if not gone: raise FillError(f"login modal could not be dismissed ({when})")
    return True

def _visible_months(page):
    out = []
    for c in page.evaluate(CAPTIONS_JS):
        try: out.append(datetime.strptime(c, "%B %Y"))
        except ValueError: pass
    return [(m.year, m.month) for m in out]

def select_date(page, target_date):
    """Pick the date in the calendar, navigating in whichever direction reaches the target month. The calendar opens
    at the PRE-FILLED departure month, which may be after the target (e.g. T+1 right after T+45)."""
    tgt = (target_date.year, target_date.month)
    target_month_year = target_date.strftime("%B %Y")
    target_day = str(target_date.day)
    try: page.locator(".DayPicker-Month").first.wait_for(state="visible", timeout=1500)
    except Exception:
        try: page.locator("label[for='departure']").click(timeout=2000, force=True)
        except: pass
    seen = []
    for _ in range(16):
        keys = _visible_months(page)
        if not keys:
            page.wait_for_timeout(300)  # calendar not rendered yet
            continue
        seen.append(keys[0])
        if tgt in keys:
            clicked = page.evaluate(f'''() => {{
                let months = document.querySelectorAll(".DayPicker-Month");
                for (let m of months) {{
                    let caption = m.querySelector(".DayPicker-Caption");
                    if (caption && caption.innerText.includes("{target_month_year}")) {{
                        let days = m.querySelectorAll(".DayPicker-Day");
                        for (let d of days) {{
                            let p = d.querySelector(".dateInnerCell p");
                            if (p && p.innerText.trim() === "{target_day}") {{
                                d.click();
                                return true;
                            }}
                        }}
                    }}
                }}
                return false;
            }}''')
            if clicked: return
            raise FillError(f"calendar: {target_month_year} is showing but day {target_day} could not be clicked")
        direction = "prev" if tgt < min(keys) else "next"
        try: page.locator(f".DayPicker-NavButton--{direction}").click(timeout=2000, force=True)
        except Exception as ex:
            raise FillError(f"calendar: cannot click {direction} (showing {keys}, want {tgt}): {type(ex).__name__}")
        deadline = time.time() + 2  # wait for the captions to change (a condition, not a fixed delay)
        while time.time() < deadline:
            now = _visible_months(page)
            if now and now[0] != keys[0]: break
            page.wait_for_timeout(100)
    raise FillError(f"calendar: {target_month_year} not reached after 16 navigations; first visible months seen: {seen}")

def fill_form(sess, origin, dest, target_date, tm):
    """Fill From / To / date and click Search, reading the form back after every step and again just before Search.
    The login popup is checked for (and dismissed) before every step and right before Search."""
    page = sess.page
    dismiss_login_modal(sess, "before From pick")
    pick_airport(page, "label[for='fromCity']", origin, True, "from")
    verify_form(page, origin, dest, target_date, ("from",), "after From pick")
    tm.mark("origin")
    dismiss_login_modal(sess, "before To pick")
    pick_airport(page, "label[for='toCity']", dest, False, "to")
    verify_form(page, origin, dest, target_date, ("from", "to"), "after To pick")
    tm.mark("dest")
    dismiss_login_modal(sess, "before date pick")
    select_date(page, target_date)
    verify_form(page, origin, dest, target_date, ("from", "to", "date"), "after date pick")
    tm.mark("date")
    dismiss_login_modal(sess, "before Search")
    verify_form(page, origin, dest, target_date, ("from", "to", "date"), "immediately before Search")
    page.locator(".widgetSearchBtn").first.click(timeout=5000, force=True)
    tm.mark("click")

def parse_it(url):
    """Return (origin, dest, date) from a search-stream-dt request URL, or None."""
    m = IT_RE.search(url or "")
    if not m: return None
    try: return m.group(1), m.group(2), datetime.strptime(m.group(3), "%Y%m%d").date()
    except ValueError: return None

def kill_stale_chrome(profile_dir):
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['name'] and 'chrome' in p.info['name'].lower():
                cmd = ' '.join(p.info.get('cmdline') or [])
                if profile_dir in cmd:
                    for child in p.children(recursive=True):
                        try: child.kill()
                        except: pass
                    try: p.kill()
                    except: pass
        except:
            pass

def get_chrome_path():
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]
    for p in paths:
        if os.path.exists(p): return p
    return None

def extract_amount(html_str):
    if not html_str: return 0
    m = re.search(r'₹\s*([\d,]+)', str(html_str))
    if m: return int(m.group(1).replace(',', ''))
    return 0

def clean_time(time_str):
    # '17:00' -> '17:00:00'
    if not time_str: return "00:00:00"
    if len(time_str) == 5: return time_str + ":00"
    return time_str

def process_stream(body_bytes, req_origin, req_dest, req_date, lead_time_days, diag=None):
    """Always returns (rows, nearby_dropped). When rows is empty, diag['reason'] says why."""
    if diag is None: diag = {}
    rows = []
    messages = []
    payload_lines = 0
    decode_errors = 0

    for line in body_bytes.split(b'\n'):
        if line.startswith(b"data: H4sI"):
            payload_lines += 1
            payload = line[6:].strip()
            try:
                compressed = base64.b64decode(payload)
                decompressed = gzip.decompress(compressed)
                messages.append(json.loads(decompressed.decode('utf-8')))
            except Exception as e:
                decode_errors += 1
                log("Error decoding payload:", e)

    if not messages:
        diag["reason"] = (f"unparseable stream: {payload_lines} payload line(s), {decode_errors} decode error(s), "
                          f"{len(body_bytes)} bytes, head={body_bytes[:80]!r}")
        return rows, 0

    final_msg = None
    for m in reversed(messages):
        if m.get("cardList"):
            final_msg = m
            break
            
    if not final_msg:
        diag["reason"] = f"no cardList in any of {len(messages)} decoded message(s), {len(body_bytes)} bytes"
        return rows, 0

    card_list = final_msg.get("cardList", [])
    journey_map = final_msg.get("journeyMap", {})
    
    if not card_list or not card_list[0]:
        diag["reason"] = f"cardList empty ({len(messages)} message(s), {len(body_bytes)} bytes)"
        return rows, 0

    nearby_dropped = 0
    multistop_dropped = 0
    nokey_dropped = 0
    badkey_dropped = 0
    recon_dropped = 0
    discounted = 0
    family_dropped = {}
    families = {}
    now = datetime.now()
    scraped_hour = now.replace(minute=0, second=0, microsecond=0)
    
    # User requested: parse all clusters if they contain different flights
    all_cards = []
    for cluster in card_list:
        all_cards.extend(cluster)
        
    for card in all_cards:
        jkeys = card.get("journeyKeys", [])
        if not jkeys:
            nokey_dropped += 1
            continue

        exact_match = True
        total_stops = 0
        first_key = ""
        
        for jk in jkeys:
            jdata = journey_map.get(jk, {})
            dep_code = jdata.get("depCityCd", "")
            arr_code = jdata.get("arrCityCd", "")
            
            if dep_code != req_origin or arr_code != req_dest:
                exact_match = False
                
            total_stops += jdata.get("stops", 0)
            if not first_key: first_key = jk
            
        if not exact_match:
            nearby_dropped += 1
            continue
            
        if total_stops > 0:
            multistop_dropped += 1
            continue

        # Product filter on the explicit farefamily field (search is cc=E, but that still returns premium products).
        fams = [str(l.get("airlineInfo", {}).get("farefamily") or "")
                for jk in jkeys for l in journey_map.get(jk, {}).get("flightDetail", {}).get("legListV2", [])]
        fam_label = "/".join(fams) or "?"
        if any(NON_ECONOMY_RE.search(f) for f in fams):
            family_dropped[fam_label] = family_dropped.get(fam_label, 0) + 1
            log(f"FAMILY DROP {card.get('flightNumber')} family={fam_label} fare={card.get('fare')}")
            continue

        base_fare = 0.0
        taxes_fees = 0.0
        discount_amt = 0.0
        total_fare = float(card.get("fare", 0))  # what the traveller pays, i.e. AFTER any instant discount

        fare_breakup = card.get("fareBreakup", {}).get("fareBreakUpItems", [])
        for item in fare_breakup:
            text = item.get("text", "").lower()
            amt = float(extract_amount(item.get("amount", "")))
            # Discount items carry a negative amount ("-₹ 750", "Instant discount applied"); they are deductions.
            if strip_html(item.get("amount", "")).startswith(("-", "−")) or "discount" in text: discount_amt += amt
            elif "base" in text: base_fare = amt
            elif "surcharge" in text or "tax" in text: taxes_fees = amt

        if total_fare > 0 and base_fare == 0:
            base_fare = total_fare + discount_amt - taxes_fees

        flight_num = card.get("flightNumber", "")
        flight_num = flight_num.replace(" ", "-") if " " in flight_num else flight_num
        airline_code = flight_num.split("-")[0] if "-" in flight_num else flight_num[:2]
        
        km = KEY_RE.match(first_key)
        if not km:
            badkey_dropped += 1
            continue
        departure_time = datetime.strptime(km.group(3), "%Y-%m-%d %H:%M")

        # Reconciliation (discount-aware): base + taxes - discounts must equal the paid total, otherwise DROP the row
        # and log its full breakup.
        breakup = [(strip_html(i.get("text", "")), strip_html(i.get("amount", ""))) for i in fare_breakup]
        if abs((base_fare + taxes_fees - discount_amt) - total_fare) > 1.0:
            recon_dropped += 1
            log(f"RECON DROP {flight_num} dep={km.group(3)} family={fam_label} paid_total={total_fare} base={base_fare} "
                f"tax={taxes_fees} discount={discount_amt} gap={round(base_fare + taxes_fees - discount_amt - total_fare, 2)} "
                f"slashedFare={card.get('slashedFare')!r} finalFare={card.get('finalFare')!r} breakup={breakup}")
            continue
        if discount_amt > 0:
            # Store the pre-discount published fare so stored base + taxes == total; the discount is logged, not stored.
            discounted += 1
            log(f"DISCOUNT {flight_num} dep={km.group(3)} family={fam_label} discount={discount_amt} paid_total={total_fare} "
                f"stored_total={base_fare + taxes_fees} slashedFare={card.get('slashedFare')!r} breakup={breakup}")
            total_fare = base_fare + taxes_fees
        families[fam_label] = families.get(fam_label, 0) + 1

        row = {
            "source": "makemytrip",
            "route": f"{req_origin}-{req_dest}",
            "airline": airline_code,
            "flight_number": flight_num,
            "cabin_class": "Economy",
            "base_fare": base_fare,
            "taxes_fees": taxes_fees,
            "total_fare": total_fare,
            "stops": 0,
            "lead_time_days": lead_time_days,
            "departure_time": departure_time,
            "scraped_at": now,
            "scraped_hour": scraped_hour
        }
        rows.append(row)
        
    seen = set()
    deduped = []
    for r in rows:
        key = f"{r['flight_number']}_{r['departure_time']}"
        if key not in seen:
            seen.add(key)
            deduped.append(r)
            
    diag["recon_dropped"] = recon_dropped
    diag["discounted"] = discounted
    diag["family_dropped"] = family_dropped
    diag["families"] = families

    if not deduped:
        diag["reason"] = (f"0 rows after filtering {len(all_cards)} card(s): nearby={nearby_dropped}, "
                          f"multistop={multistop_dropped}, no_journey_keys={nokey_dropped}, unparseable_journey_key={badkey_dropped}, "
                          f"non_economy={sum(family_dropped.values())}, recon={recon_dropped}")

    return deduped, nearby_dropped

class Session:
    """One CDP connection to the MMT Chrome: page, CDP session and the Fetch-interception buffers."""
    def __init__(self, pw):
        self.pw = pw
        self.t0 = time.time()
        self.search_no = 0
        self.homepage_loads = 0
        self.event_queue = []
        self.search_urls = []
        self.browser = self.page = self.cdp = None

    def _on_paused(self, e):
        url = e.get("request", {}).get("url", "")
        if "search-stream-dt" in url: self.search_urls.append(url)
        self.event_queue.append(("requestPaused", e))

    def open(self):
        self.browser = self.pw.chromium.connect_over_cdp("http://localhost:9334")
        context = self.browser.contexts[0]
        self.page = context.pages[0] if context.pages else context.new_page()
        self.cdp = context.new_cdp_session(self.page)
        self.cdp.send("Network.enable")
        self.cdp.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        self.cdp.send("Storage.clearDataForOrigin", {"origin": ORIGIN_URL, "storageTypes": "all"})
        self.cdp.on("Fetch.requestPaused", self._on_paused)  # registered ONCE per session
        self.t0 = time.time()
        self.page.goto(HOME, wait_until="domcontentloaded")
        self.homepage_loads += 1
        log("Warm-up: Idling 30s...")
        self.page.wait_for_timeout(30000)

    def goto_home(self):
        """Back to a fresh homepage, like a user starting a new search (the results page has no search form)."""
        self.page.goto(HOME, wait_until="domcontentloaded")
        self.homepage_loads += 1
        self.page.locator("label[for='fromCity']").wait_for(state="visible", timeout=20000)
        try: self.page.wait_for_load_state("load", timeout=10000)   # scripts/handlers attached
        except Exception: pass
        self.page.locator(".widgetSearchBtn").first.wait_for(state="visible", timeout=10000)

    def release_paused(self):
        """Every paused request must be fulfilled or continued: continue any that were queued but never handled."""
        try: self.page.wait_for_timeout(200)  # let any in-flight requestPaused events reach the queue
        except Exception: pass
        while self.event_queue:
            _, ev = self.event_queue.pop(0)
            try: self.cdp.send("Fetch.continueRequest", {"requestId": ev.get("requestId")})
            except Exception as ex: log("  release_paused: continueRequest failed:", ex)

def capture_stream(sess):
    """After Search is clicked: intercept search-stream-dt, buffer it, fulfil it, until 'id: END' or 45s."""
    page, cdp = sess.page, sess.cdp
    wait_start = time.time()
    body_chunks = []
    fulfilled = False
    req_status = None
    modal_seen_at = None
    next_modal_check = time.time() + 1.5
    while time.time() - wait_start < 45:
        # The login popup can appear right after Search is clicked and swallow the search. While no request has been
        # seen, look for it every ~1.5s and dismiss it; if the search still does not start, give up early (caller retries).
        if not fulfilled and not sess.event_queue and not sess.search_urls and time.time() >= next_modal_check:
            next_modal_check = time.time() + 1.5
            try:
                if page.evaluate(MODAL_JS).get("present"):
                    if modal_seen_at is None: modal_seen_at = time.time()
                    dismiss_login_modal(sess, "during Search wait")
            except Exception as ex:
                if is_target_closed(ex): raise
                log(f"  popup check during Search wait failed: {type(ex).__name__}: {str(ex).splitlines()[0][:120]}")
        if modal_seen_at and not fulfilled and not sess.event_queue and not sess.search_urls and time.time() - modal_seen_at > 10:
            break
        if sess.event_queue:
            ev_type, e = sess.event_queue.pop(0)
            if ev_type == "requestPaused":
                req_id = e.get("requestId")
                # The CORS preflight (OPTIONS) is paused too. Fulfil each request with ITS OWN status
                # and body; only the real GET's status is reported as the search status.
                resp_code = e.get("responseStatusCode")
                if e.get("request", {}).get("method") != "OPTIONS":
                    req_status = resp_code
                headers_list = e.get("responseHeaders", [])
                req_chunks = []
                taken = False
                try:
                    stream_res = cdp.send("Fetch.takeResponseBodyAsStream", {"requestId": req_id})
                    stream_handle = stream_res.get("stream")
                    taken = True
                    if stream_handle:
                        eof = False
                        while not eof:
                            read_res = cdp.send("IO.read", {"handle": stream_handle})
                            data = read_res.get("data", "")
                            if read_res.get("base64Encoded"): req_chunks.append(base64.b64decode(data))
                            else: req_chunks.append(data.encode('utf-8'))
                            eof = read_res.get("eof", False)
                        cdp.send("IO.close", {"handle": stream_handle})
                    body_chunks.extend(req_chunks)
                    new_headers = [h for h in headers_list if h.get("name", "").lower() not in ["content-encoding", "content-length"]]
                    cdp.send("Fetch.fulfillRequest", {
                        "requestId": req_id,
                        "responseCode": resp_code,
                        "responseHeaders": new_headers,
                        "body": base64.b64encode(b"".join(req_chunks)).decode('utf-8')
                    })
                    fulfilled = True
                except Exception as ex:
                    if is_target_closed(ex): raise
                    log("ERROR fulfilling paused request:", ex)
                    # Never leave the request paused. Once the body is taken it cannot be continued as-is,
                    # so fail it explicitly; before that, continue it untouched.
                    try:
                        if taken: cdp.send("Fetch.failRequest", {"requestId": req_id, "errorReason": "Failed"})
                        else: cdp.send("Fetch.continueRequest", {"requestId": req_id})
                    except Exception as ex2: log("  could not resolve paused request:", ex2)
        # The stream's final line is "id: END"; the partialResponse flag is inside the gzip payload.
        if fulfilled and b"id: END" in b"".join(body_chunks):
            break
        page.wait_for_timeout(100)
    return b"".join(body_chunks), fulfilled, req_status, time.time() - wait_start, modal_seen_at is not None

def search_once(sess, origin, dest, lead, idx, target_date):
    """One search: fill (with read-back; one from-scratch retry), click Search, capture and parse the stream.
    Returns the outcome dict for record()."""
    page, cdp = sess.page, sess.cdp
    tm = StepTimer()
    fill_retries = 0
    fill_errors = []
    filled = False
    full_body, fulfilled, req_status, waited, modal_seen = b"", False, None, 0.0, False
    for attempt in (1, 2):
        filled = False
        try:
            # After a search the page sits on the results listing, which has no search form.
            # Return to the homepage before every search but the first, and before a from-scratch retry.
            if idx > 0 or attempt == 2:
                sess.goto_home()
            tm.mark("goto")
            # Clear Service Workers to ensure search-stream-dt goes over Fetch native
            cdp.send("Storage.clearDataForOrigin", {"origin": ORIGIN_URL, "storageTypes": "service_workers"})
            page.evaluate(OVERLAY_JS)
            try:
                if page.locator(".close").is_visible(timeout=500): page.locator(".close").click(timeout=1000, force=True)
            except: pass
            tm.mark("overlay")
            cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*search-stream-dt*", "requestStage": "Response"}]})
            sess.event_queue.clear()
            sess.search_urls.clear()
            fill_form(sess, origin, dest, target_date, tm)
            filled = True
        except Exception as ex:
            if is_target_closed(ex): raise
            msg = str(ex) if isinstance(ex, FillError) else f"{type(ex).__name__}: {str(ex).splitlines()[0][:300]}"
            log(f"FILL FAILED (attempt {attempt}/2): {msg}")
            fill_errors.append(msg)
            dump_state(sess, f"fillfail{attempt}", origin, dest, lead)
            try: sess.release_paused()
            except Exception: pass
            try: cdp.send("Fetch.disable")
            except Exception: pass
            if attempt == 1:
                fill_retries = 1
                log("Retrying the whole fill from scratch (fresh homepage), once")
        else:
            full_body, fulfilled, req_status, waited, modal_seen = capture_stream(sess)
            tm.mark("stream")
            if not full_body and modal_seen and attempt == 1:
                log("Search was swallowed by the login popup (no request); retrying the whole fill from scratch, once")
                fill_retries = 1
                try: sess.release_paused()
                except Exception: pass
                try: cdp.send("Fetch.disable")
                except Exception: pass
                continue
            break

    if not filled:
        return dict(rows=[], nearby=0, nbytes=0, status=None, complete=False, url_it="-", url_dates=[],
                    failure="fill failed after from-scratch retry: " + " || ".join(fill_errors),
                    extra=f"fill_retries={fill_retries} timing={tm}")

    dump_dir = os.environ.get("MMT_DUMP_DIR")  # opt-in: keep raw streams for offline fare analysis
    if dump_dir and full_body:
        os.makedirs(dump_dir, exist_ok=True)
        with open(os.path.join(dump_dir, f"{origin}-{dest}_T{lead}_{datetime.now():%H%M%S}.sse"), "wb") as fh:
            fh.write(full_body)
    sess.release_paused()
    cdp.send("Fetch.disable")

    complete = b"id: END" in full_body
    diag = {}
    rows, nearby_dropped = process_stream(full_body, origin, dest, target_date, lead, diag)
    its = [x for x in (parse_it(u) for u in sess.search_urls) if x]
    url_it = ",".join(sorted({f"{x[0]}-{x[1]}-{x[2]:%Y%m%d}" for x in its})) or "-"
    failure = None
    if not full_body:
        failure = (f"no bytes captured (status={req_status}, fulfilled={fulfilled}, "
                   f"waited {round(waited)}s for search-stream-dt; login popup seen={modal_seen})")
        dump_state(sess, "noreq", origin, dest, lead)
    elif req_status != 200:
        failure = f"HTTP status {req_status} ({len(full_body)} bytes)"
    elif not rows:
        failure = diag.get("reason") or "0 rows (no reason recorded)"
    elif its and {f"{x[0]}-{x[1]}" for x in its} != {f"{origin}-{dest}"}:
        failure = f"searched route {url_it} != requested {origin}-{dest}"
    extra = (f"fill_retries={fill_retries} recon_dropped={diag.get('recon_dropped', 0)} "
             f"discounted={diag.get('discounted', 0)} "
             f"nonecon_dropped={fmt_counts(diag.get('family_dropped'))} families={fmt_counts(diag.get('families'))} "
             f"timing={tm}")
    return dict(rows=rows, nearby=nearby_dropped, nbytes=len(full_body), status=req_status, complete=complete,
                url_it=url_it, url_dates=sorted({x[2] for x in its}), failure=failure, extra=extra)

def parse_search_matrix():
    routes = [("DEL", "BOM"), ("DEL", "BLR"), ("BOM", "BLR"), ("DEL", "CCU"), ("MAA", "DEL"), ("BLR", "HYD")]
    lead_times = [1, 7, 15, 30, 45]
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--seq":  # sequence test: python makemytrip.py --seq DEL-BOM:1,DEL-BLR:1
        seq = []
        for item in args[1].split(","):
            route, lead = item.split(":")
            o, d = route.upper().split("-")
            seq.append((o, d, int(lead)))
        return seq
    if len(args) == 2:  # single-search test: python makemytrip.py DEL-BLR 7
        o, d = args[0].upper().split("-")
        return [(o, d, int(args[1]))]
    return [(origin, dest, lead) for origin, dest in routes for lead in lead_times]

SEARCH_DEADLINE = 150.0          # per-search hard timeout (seconds)
RUN_DEADLINE = 75 * 60.0         # whole-run deadman (seconds)
MAX_CONSECUTIVE_KILLS = 3

class ChromeProc:
    """The Chrome we launched for MMT (remote-debugging port 9334, profile mmt_cdp)."""
    def __init__(self, profile_dir):
        self.profile_dir = profile_dir
        self.proc = None

    def start(self):
        kill_stale_chrome(self.profile_dir)
        time.sleep(1)
        self.proc = subprocess.Popen([get_chrome_path(), "--remote-debugging-port=9334", f"--user-data-dir={self.profile_dir}",
                                      "--no-first-run", "--no-default-browser-check",
                                      "--hide-crash-restore-bubble"])
        for _ in range(40):
            try:
                if urllib.request.urlopen("http://localhost:9334/json/version", timeout=1).getcode() == 200: return
            except Exception: time.sleep(0.5)
        raise RuntimeError("Chrome did not open the remote-debugging port 9334")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        try:
            pr = psutil.Process(self.proc.pid)
            for child in pr.children(recursive=True): child.kill()
            pr.kill()
        except Exception: pass
        kill_stale_chrome(self.profile_dir)

class Watchdog:
    """Per-search hard timeout. On expiry it kills Chrome, which makes any blocked sync call (IO.read, cdp.send, page.*)
    raise, so a hung search can never hang the run."""
    def __init__(self, chrome, seconds, label):
        self.chrome, self.seconds, self.label = chrome, seconds, label
        self.timer = None
    @property
    def fired(self): return WD["fired"]
    def start(self):
        WD["fired"] = False
        self.timer = threading.Timer(self.seconds, self._fire)
        self.timer.daemon = True
        self.timer.start()
    def _fire(self):
        WD["fired"] = True
        log(f"WATCHDOG: {self.label} exceeded {self.seconds:.0f}s - killing Chrome to unblock the run")
        self.chrome.stop()
    def cancel(self):
        if self.timer: self.timer.cancel()

class KillBreaker:
    """Abort the run after N consecutive kills, so a persistently broken page is not hammered."""
    def __init__(self, limit=MAX_CONSECUTIVE_KILLS): self.n, self.limit = 0, limit
    def kill(self):
        self.n += 1
        return self.n >= self.limit   # True => abort
    def ok(self): self.n = 0

def deadline_for(attempt):
    # MMT_TEST_FIRST_ATTEMPT_DEADLINE: test-only way to force one watchdog trigger on attempt 1 (the retry keeps the real timer).
    forced = os.environ.get("MMT_TEST_FIRST_ATTEMPT_DEADLINE")
    if attempt == 1 and forced: return float(forced)
    return SEARCH_DEADLINE

def make_db():
    """(engine, insert_flights) from db.database - the same insert path run_indigo.py / main.py use."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Backend/, so `db` imports when run from anywhere
    from db.database import get_engine, init_db, insert_flights
    engine = get_engine()
    init_db(engine)
    return engine, insert_flights

def insert_rows(db, out, requested):
    """Store a search's rows via insert_flights (ON CONFLICT DO NOTHING on source/flight/departure/scraped_hour).
    Only clean searches are stored: no failure, and the searched date == requested date == every row's date."""
    rows = out["rows"]
    if db is None or not rows or out["failure"]: return 0
    dep_dates = sorted({r["departure_time"].date() for r in rows})
    if out["url_dates"] != [requested] or dep_dates != [requested]:
        log(f"DB INSERT SKIPPED: date mismatch (requested {requested}, request URL {out['url_dates']}, rows {dep_dates})")
        out["extra"] = (out["extra"] + " inserted=0(date-mismatch)").strip()
        return 0
    engine, insert_fn = db
    try:
        n = insert_fn(engine, rows)
    except Exception as ex:
        log(f"DB INSERT FAILED: {type(ex).__name__}: {ex}")
        out["extra"] = (out["extra"] + " inserted=ERR").strip()
        return 0
    out["extra"] = (out["extra"] + f" inserted={n}/{len(rows)}").strip()
    return n

def failure_outcome(reason, extra=""):
    return dict(rows=[], nearby=0, nbytes=0, status=None, complete=False, url_it="-", url_dates=[], failure=reason, extra=extra)

RUN_STATS = {"attempted": 0, "inserted": 0, "rows": 0}   # filled by run_matrix; read by in-process callers (main.py)

def run(jobs=None, insert=False, standalone=True, engine=None):
    """Start the MMT Chrome, run the searches (jobs = [(origin, dest, lead), ...] or the built-in matrix), tear down.
    insert=True stores clean results in flight_prices. Returns True if the run was aborted.
    standalone=False (embedded in main.py): the run deadline aborts the run instead of os._exit()-ing the host process.
    engine: reuse the caller's SQLAlchemy engine instead of building a second one."""
    if insert and engine is not None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from db.database import insert_flights
        db = (engine, insert_flights)
    else:
        db = make_db() if insert else None
    profile_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".profiles", "mmt_cdp"))
    os.makedirs(profile_dir, exist_ok=True)
    chrome = ChromeProc(profile_dir)
    chrome.start()
    try:
        return run_matrix(chrome, jobs, db, standalone=standalone)
    finally:
        # Always tear down the Chrome we started, even if the matrix crashed mid-run.
        chrome.stop()

def main():
    insert = "--insert" in sys.argv
    sys.argv = [a for a in sys.argv if a != "--insert"]
    if run(None, insert): sys.exit(2)

def run_matrix(chrome, search_matrix=None, db=None, standalone=True):
    """Returns True if the run was aborted (3 consecutive kills or an unrecoverable restart failure)."""
    global RUN_T0
    RUN_T0 = time.time()
    inserted_total = 0
    search_matrix = search_matrix or parse_search_matrix()
    results = []
    start_time = time.time()
    run_deadline = float(os.environ.get("MMT_RUN_DEADLINE", RUN_DEADLINE))

    deadline_hit = threading.Event()

    def deadman():
        if standalone:
            log(f"DEADMAN: run exceeded {run_deadline:.0f}s - killing Chrome and exiting so a scheduled run cannot hang forever")
            chrome.stop()
            os._exit(3)
        # Embedded in main.py: never take the host process down. Kill Chrome to unblock any hung call; the loop sees the flag and aborts.
        log(f"DEADMAN: run exceeded {run_deadline:.0f}s - killing Chrome and aborting this source only")
        deadline_hit.set()
        chrome.stop()
    deadman_timer = threading.Timer(run_deadline, deadman)
    deadman_timer.daemon = True
    deadman_timer.start()

    def record(origin, dest, lead, requested, out):
        rows = out["rows"]
        dep_dates = sorted({r["departure_time"].date() for r in rows})
        dmin = dep_dates[0].isoformat() if dep_dates else "-"
        dmax = dep_dates[-1].isoformat() if dep_dates else "-"
        # Both the date the page actually searched (request URL) and every returned row must equal the requested date.
        date_ok = bool(rows) and out["url_dates"] == [requested] and dep_dates == [requested]
        results.append({"cell": f"{origin}-{dest} T+{lead}", "requested": requested.isoformat(), "url_it": out["url_it"],
                        "dmin": dmin, "dmax": dmax, "date_ok": date_ok, "rows": len(rows), "nearby": out["nearby"],
                        "bytes": out["nbytes"], "status": out["status"], "complete": out["complete"],
                        "failure": out["failure"], "extra": out["extra"]})
        log(f"RESULT {origin}-{dest} T+{lead} requested={requested.isoformat()} url_it={out['url_it']} "
            f"dep_dates={dmin}..{dmax} date_ok={date_ok} rows={len(rows)} nearby_drops={out['nearby']} bytes={out['nbytes']} "
            f"status={out['status']} complete={out['complete']} {out['extra']} failure={out['failure'] or '-'}")

    aborted = False
    with sync_playwright() as p:
        sess = Session(p)
        sess.open()
        breaker = KillBreaker()

        def restart():
            """Fresh Chrome + fresh CDP session (handlers registered once for the new session)."""
            nonlocal sess
            chrome.stop()
            chrome.start()
            sess = Session(p)
            sess.open()

        for idx, (origin, dest, lead) in enumerate(search_matrix):
            if deadline_hit.is_set():
                aborted = True
                log(f"ABORTING: run deadline hit before search {idx + 1} of {len(search_matrix)}")
                break
            log(f"--- Searching {origin}-{dest} T+{lead} ({idx + 1}/{len(search_matrix)}) ---")
            target_date = datetime.now() + timedelta(days=lead)
            kills = 0
            out = None
            for attempt in (1, 2):
                sess.search_no = idx + 1
                seconds = deadline_for(attempt)
                wd = Watchdog(chrome, seconds, f"search {origin}-{dest} T+{lead} (attempt {attempt})")
                wd.start()
                err = None
                try:
                    out = search_once(sess, origin, dest, lead, idx, target_date)
                except Exception as ex:
                    err = ex
                wd.cancel()
                closed = wd.fired or (err is not None and is_target_closed(err)) or not chrome.alive()
                if not closed:
                    if err is not None:
                        try: sess.release_paused()
                        except Exception: pass
                        try: sess.cdp.send("Fetch.disable")
                        except Exception: pass
                        out = failure_outcome(f"exception: {type(err).__name__}: {err}")
                    breaker.ok()
                    break
                kills += 1
                reason = "watchdog timeout" if wd.fired else "browser closed/crashed"
                log(f"KILL #{kills} ({reason}) during {origin}-{dest} T+{lead} attempt {attempt}; "
                    f"error={type(err).__name__ + ': ' + str(err).splitlines()[0][:120] if err else 'none (search had finished)'}")
                if breaker.kill():
                    aborted = True
                    out = failure_outcome(f"ABORTED: {MAX_CONSECUTIVE_KILLS} consecutive Chrome kills ({reason})", f"wd_kills={kills}")
                    break
                try: restart()
                except Exception as rex:
                    aborted = True
                    out = failure_outcome(f"ABORTED: Chrome restart failed: {type(rex).__name__}: {rex}", f"wd_kills={kills}")
                    break
                if err is None:  # the search had finished when the timer fired: keep its result, Chrome is fresh again
                    breaker.ok()
                    break
                if attempt == 1:
                    log("Retrying this search once on the fresh Chrome")
                    continue
                out = failure_outcome(f"killed twice ({reason}); giving up on this search")
            if out is not None and kills: out["extra"] = (out["extra"] + f" wd_kills={kills}").strip()
            inserted_total += insert_rows(db, out, target_date.date())
            record(origin, dest, lead, target_date.date(), out)
            if aborted:
                log(f"ABORTING the run after {idx + 1} of {len(search_matrix)} searches")
                break

            if idx < len(search_matrix) - 1:
                delay = random.randint(15, 40)
                log(f"Waiting {delay}s before next search...")
                try:
                    sess.page.wait_for_timeout(delay * 1000)
                except Exception as ex:
                    if not (is_target_closed(ex) or not chrome.alive()): raise
                    log(f"Chrome went away during the gap ({type(ex).__name__}); restarting it")
                    if breaker.kill():
                        aborted = True
                        log("ABORTING: too many consecutive Chrome kills")
                        break
                    try: restart()
                    except Exception as rex:
                        aborted = True
                        log(f"ABORTING: Chrome restart failed: {type(rex).__name__}: {rex}")
                        break

        try: sess.browser.close()
        except Exception: pass

    deadman_timer.cancel()
    if deadline_hit.is_set(): aborted = True
    RUN_STATS.update(attempted=len(results), inserted=inserted_total, rows=sum(r["rows"] for r in results))
    failed = [r for r in results if r["failure"]]
    log("=" * 40)
    log("MATRIX RUN SUMMARY")
    log("=" * 40)
    log(f"Runtime: {round(time.time() - start_time, 1)}s")
    log(f"Attempted: {len(results)}")
    log(f"Succeeded: {len(results) - len(failed)}")
    log(f"Failed: {len(failed)}")
    if db is not None: log(f"Rows inserted into flight_prices: {inserted_total}")
    for r in failed: log(f"  FAIL {r['cell']}: {r['failure'][:400]}")
    bad_dates = [r for r in results if not r["date_ok"]]
    log(f"Date mismatches (URL date or row dates != requested): {len(bad_dates)}")
    for r in bad_dates: log(f"  DATE {r['cell']}: requested {r['requested']}, url {r['url_it']}, rows {r['dmin']}..{r['dmax']}")
    incomplete = [r for r in results if not r["complete"]]
    log(f"Streams without id: END marker: {len(incomplete)}")
    for r in incomplete: log(f"  INCOMPLETE {r['cell']}")
    log("Row counts:")
    for r in results: log(f"  {r['cell']}: {r['rows']} rows, nearby_drops={r['nearby']}, dates {r['dmin']}..{r['dmax']}")
    log("=" * 40)
    log("MATRIX ABORTED" if aborted else "MATRIX DONE")
    return aborted

if __name__ == "__main__":
    main()
