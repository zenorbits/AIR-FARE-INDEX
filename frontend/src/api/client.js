// ---------------------------------------------------------------------------
// API CLIENT — wired to the real backend (Backend/api/main.py), with a
// fallback to mockData.js so the dashboard still renders if the backend is
// unreachable (not running, wrong URL/key, CORS, etc).
//
// The backend doesn't expose "top routes" or "heatmap" endpoints directly —
// it exposes /index/current, /index/history, and /routes (see
// Backend/api/main.py). Everything here is derived from those:
//   - getTopRoutes(): calls /index/current for every route, sorts by index.
//   - getApixForRoute()/getApixAll(): calls /index/history per route and
//     averages the recent points.
//   - getTrends(): calls /index/history per top route and merges by date.
//   - getHeatmapRoutes(): reuses /index/current, normalizes index_value into
//     a 0-1 intensity, and pairs it with the static city coordinates in
//     mockData.js (the backend has no city geodata).
//   - getAssistantAnswer(): calls the RAG-backed POST /assistant/ask
//     (Backend/api/assistant.py) directly.
// ---------------------------------------------------------------------------
import {
  TOP_ROUTES,
  APIX_BY_ROUTE,
  TREND_DATA,
  HEATMAP_ROUTES,
  CITY_COORDS,
  routeLabel,
} from "../data/mockData";

// Base URL of the FastAPI backend. Matches the uvicorn dev default
// (`uvicorn api.main:app --reload` binds :8000). Override in frontend/.env:
//   VITE_API_BASE_URL=http://localhost:8000
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Every data endpoint in Backend/api/main.py requires an `X-API-Key` header
// (see verify_api_key there). For local development, set VITE_API_KEY in
// frontend/.env to match the backend's own API_KEY.
//
// WARNING: anything in a Vite `VITE_*` env var ships in the browser bundle —
// visible to any user via devtools. That's fine for local development, but
// never put a real/production API key here. For production, put an
// authenticating proxy (or a separate public, read-only endpoint) in front
// of the backend instead of shipping its key to the browser.
const API_KEY = import.meta.env.VITE_API_KEY;

function authHeaders() {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

// ---------------------------------------------------------------------------
// Backend reachability status — every function below falls back to mock data
// on any fetch failure so the dashboard still renders something, but that
// fallback used to be silent (only a console.warn). This tiny pub/sub lets
// any component (see BackendStatusBanner) show a visible warning instead of
// quietly passing off sample data as if it were live.
// ---------------------------------------------------------------------------
let usingMockData = false;
const statusListeners = new Set();

function setBackendStatus(isMock) {
  if (isMock === usingMockData) return;
  usingMockData = isMock;
  statusListeners.forEach((cb) => cb(usingMockData));
}

export function isUsingMockData() {
  return usingMockData;
}

export function subscribeBackendStatus(cb) {
  statusListeners.add(cb);
  return () => statusListeners.delete(cb);
}

async function apiFetch(path, options) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers: { ...authHeaders(), ...(options?.headers ?? {}) } });
  } catch (err) {
    throw new Error(`${path} -> network error (${err.message})`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      // response wasn't JSON; keep statusText
    }
    throw new Error(`${path} -> ${res.status} ${detail}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Multiple panels independently call getTopRoutes()/fetchRouteHistory() for
// the same routes on every render (no shared state between components), so
// a single page load can fire the same GET dozens of times over. Rather than
// restructure that data flow, share in-flight/short-lived requests across
// callers here -- same params within the TTL reuse one network call.
const GET_CACHE_TTL_MS = 15_000;
const _getCache = new Map(); // path -> { promise, expires }

function cachedApiFetch(path) {
  const now = Date.now();
  const hit = _getCache.get(path);
  if (hit && hit.expires > now) return hit.promise;

  const promise = apiFetch(path).catch((err) => {
    _getCache.delete(path); // don't cache failures
    throw err;
  });
  _getCache.set(path, { promise, expires: now + GET_CACHE_TTL_MS });
  return promise;
}

// APIx is a Jevons price index computed against this baseline (see
// Backend/index_calc/jevons.py) — not returned by the API itself.
const INDEX_BASELINE = 100;

// Lead times the backend actually has index rows for (see Backend/config.yaml).
export const AVAILABLE_LEAD_TIMES = [1, 7, 15, 30, 45];

async function fetchCurrentIndexRows(leadTimeDays = 30) {
  // The backend does not currently write per-route rows with a NULL (overall) lead time,
  // so we use a specific advance-purchase window as a representative view.
  // This should change to 'overall' once per-route blended rows are written.
  const rows = await cachedApiFetch(`/index/current?frequency=daily&lead_time_days=${leadTimeDays}`);
  return rows.filter((r) => r.route !== "overall");
}

async function fetchRouteHistory(route, leadTimeDays = 30) {
  return cachedApiFetch(
    `/index/history?route=${encodeURIComponent(route)}&lead_time_days=${leadTimeDays}&frequency=daily`,
  );
}

// GET /index/current (backend) -> top 6 routes by current index value
export async function getTopRoutes(leadTimeDays = 30) {
  try {
    const rows = await fetchCurrentIndexRows(leadTimeDays);
    const top = rows.sort((a, b) => b.index_value - a.index_value).slice(0, 6);

    const result = await Promise.all(
      top.map(async (r) => {
        const history = await fetchRouteHistory(r.route, leadTimeDays);
        const change =
          history.length >= 2
            ? ((history[history.length - 1].index_value - history[history.length - 2].index_value) /
                history[history.length - 2].index_value) *
              100
            : 0;
        return { route: r.route, ...routeLabel(r.route), index: r.index_value, change };
      }),
    );
    setBackendStatus(false);
    return result;
  } catch (err) {
    console.warn("[api/client] getTopRoutes: backend unreachable, using mock data.", err);
    setBackendStatus(true);
    return TOP_ROUTES;
  }
}

// GET /index/history (backend) -> current/weekly/monthly APIX for one route
export async function getApixForRoute(route, leadTimeDays = 30) {
  try {
    const history = await fetchRouteHistory(route, leadTimeDays);
    if (history.length === 0) return null;

    const avg = (points) => points.reduce((sum, p) => sum + p.index_value, 0) / points.length;
    const result = {
      current: history[history.length - 1].index_value,
      baseline: INDEX_BASELINE,
      weeklyAvg: avg(history.slice(-7)),
      monthlyAvg: avg(history.slice(-30)),
    };
    setBackendStatus(false);
    return result;
  } catch (err) {
    console.warn(`[api/client] getApixForRoute(${route}): backend unreachable, using mock data.`, err);
    setBackendStatus(true);
    return APIX_BY_ROUTE[route] ?? null;
  }
}

// APIX for every top route at once, used by ApixPanel.
export async function getApixAll(leadTimeDays = 30) {
  const top = await getTopRoutes(leadTimeDays);
  const entries = await Promise.all(
    top.map(async (r) => [r.route, await getApixForRoute(r.route, leadTimeDays)]),
  );
  return Object.fromEntries(entries);
}

// GET /index/history (backend, per top route) -> merged trend series
export async function getTrends(leadTimeDays = 30) {
  try {
    const top = await getTopRoutes(leadTimeDays);
    const perRoute = await Promise.all(
      top.map(async (r) => ({
        route: r.route,
        history: (await fetchRouteHistory(r.route, leadTimeDays)).slice(-14),
      })),
    );

    const byDate = new Map();
    perRoute.forEach(({ route, history }) => {
      history.forEach((point) => {
        const date = point.period_start.slice(5, 10); // MM-DD, matches mockData's format
        if (!byDate.has(date)) byDate.set(date, { date });
        byDate.get(date)[route] = point.index_value;
      });
    });

    const result = Array.from(byDate.values()).sort((a, b) => (a.date > b.date ? 1 : -1));
    setBackendStatus(false);
    return result;
  } catch (err) {
    console.warn("[api/client] getTrends: backend unreachable, using mock data.", err);
    setBackendStatus(true);
    return TREND_DATA;
  }
}

// GET /index/current (backend) -> route intensities for the India heatmap.
// City geodata (CITY_COORDS) is frontend-only — the backend only knows
// route codes, not coordinates — so routes to/from a city we don't have
// coordinates for are dropped.
export async function getHeatmapRoutes(leadTimeDays = 30) {
  try {
    const rows = (await fetchCurrentIndexRows(leadTimeDays)).filter((r) => r.route.includes("-"));
    const values = rows.map((r) => r.index_value);
    const min = Math.min(...values);
    const range = Math.max(...values) - min || 1;

    const routes = rows
      .map((r) => {
        const [from, to] = r.route.split("-");
        if (!CITY_COORDS[from] || !CITY_COORDS[to]) return null;
        return { from, to, index: r.index_value, intensity: (r.index_value - min) / range };
      })
      .filter(Boolean);

    setBackendStatus(false);
    return { routes, cities: CITY_COORDS };
  } catch (err) {
    console.warn("[api/client] getHeatmapRoutes: backend unreachable, using mock data.", err);
    setBackendStatus(true);
    return { routes: HEATMAP_ROUTES, cities: CITY_COORDS };
  }
}

// POST /assistant/ask (backend) -> a RAG-grounded natural-language answer.
// See Backend/api/assistant.py: the answer is generated from the route's
// real recent index history plus the actual Jevons/DGCA-weighting
// methodology, never invented. The mock fallback below mimics that same
// grounded shape (referencing real mock numbers) so the UI behaves
// identically offline, but it is NOT a language model — just a template.
export async function getAssistantAnswer({ route, question }) {
  try {
    return await apiPost("/assistant/ask", { route, question });
  } catch (err) {
    console.warn("[api/client] getAssistantAnswer: backend unreachable, using mock data.", err);
    return mockAssistantAnswer({ route, question });
  }
}

function mockAssistantAnswer({ route, question }) {
  const detail = route ? APIX_BY_ROUTE[route] : null;
  const sources = [
    {
      label: "Index methodology",
      detail: "Jevons formula, DGCA route weights, outlier handling (mock — backend unreachable)",
    },
  ];

  if (!detail) {
    return {
      answer:
        "The Airfare Price Index (APIX) is a Jevons index: it tracks the geometric mean of fares for each route against a base period, so it reflects genuine price movement rather than just which flights happened to be scraped. Select a route from Top 6 Routes for numbers specific to it. (This is a offline mock answer — the backend isn't reachable right now.)",
      sources,
    };
  }

  const { origin, destination } = routeLabel(route);
  sources.push({
    label: `${route} index history`,
    detail: `current ${detail.current.toFixed(1)}, weekly avg ${detail.weeklyAvg.toFixed(1)} (mock data)`,
  });

  const direction = detail.current >= detail.weeklyAvg ? "above" : "below";
  return {
    answer: `${origin} → ${destination} (${route}) is currently at an APIX of ${detail.current.toFixed(1)}, ${direction} its weekly average of ${detail.weeklyAvg.toFixed(1)} and its monthly average of ${detail.monthlyAvg.toFixed(1)}. Values are indexed to a baseline of ${detail.baseline}, so ${detail.current.toFixed(1)} means fares are roughly ${(detail.current - detail.baseline).toFixed(0)}% above the base period. (This is an offline mock answer for "${question}" — the backend isn't reachable right now, so this isn't a real generated response.)`,
    sources,
  };
}

// GET /routes -> every route the backend has clean data for.
export async function getRoutesList() {
  return apiFetch("/routes");
}

// GET /sources -> every data source (e.g. yatra, cleartrip). Akasa is scraped
// directly but excluded from the index/clean tables (fare-decomposition/ML
// only) -- see Backend/index_calc/jevons.py -- so it will not appear here
// until that changes.
export async function getSources() {
  return apiFetch("/sources");
}

// GET /fares/raw -> individual (non-aggregated) fare rows, for fare
// decomposition (base vs taxes) and per-source comparisons.
export async function getFareBreakdown({ route, leadTimeDays, source, limit = 500 } = {}) {
  const params = new URLSearchParams();
  if (route) params.set("route", route);
  if (leadTimeDays != null) params.set("lead_time_days", leadTimeDays);
  if (source) params.set("source", source);
  params.set("limit", limit);
  return apiFetch(`/fares/raw?${params.toString()}`);
}

// POST /predict-price -> single predicted fare for one flight.
export async function predictPrice(payload) {
  return apiPost("/predict-price", payload);
}

// GET /predict-price/curve -> predicted fare at every trained lead time for
// the same flight, i.e. the lead-time elasticity curve.
export async function predictPriceCurve({ route, departureDate, departureHour, airline, cabinClass = "ECONOMY", stops = 0 }) {
  const params = new URLSearchParams({
    route,
    departure_date: departureDate,
    departure_hour: departureHour,
    airline,
    cabin_class: cabinClass,
    stops,
  });
  return apiFetch(`/predict-price/curve?${params.toString()}`);
}

// GET /backtest/cpi-comparison -> APIx vs the official CPI Airfare index.
export async function getBacktestComparison() {
  return apiFetch("/backtest/cpi-comparison");
}
