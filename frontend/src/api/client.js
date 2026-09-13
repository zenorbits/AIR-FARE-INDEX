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

async function apiFetch(path) {
  const res = await fetch(`${API_BASE_URL}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(`${path} -> ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// APIx is a Jevons price index computed against this baseline (see
// Backend/index_calc/jevons.py) — not returned by the API itself.
const INDEX_BASELINE = 100;

async function fetchCurrentIndexRows() {
  const rows = await apiFetch("/index/current?frequency=daily&lead_time_days=overall");
  return rows.filter((r) => r.route !== "overall");
}

async function fetchRouteHistory(route) {
  return apiFetch(
    `/index/history?route=${encodeURIComponent(route)}&lead_time_days=overall&frequency=daily`,
  );
}

// GET /index/current (backend) -> top 6 routes by current index value
export async function getTopRoutes() {
  try {
    const rows = await fetchCurrentIndexRows();
    const top = rows.sort((a, b) => b.index_value - a.index_value).slice(0, 6);

    return await Promise.all(
      top.map(async (r) => {
        const history = await fetchRouteHistory(r.route);
        const change =
          history.length >= 2
            ? ((history[history.length - 1].index_value - history[history.length - 2].index_value) /
                history[history.length - 2].index_value) *
              100
            : 0;
        return { route: r.route, ...routeLabel(r.route), index: r.index_value, change };
      }),
    );
  } catch (err) {
    console.warn("[api/client] getTopRoutes: backend unreachable, using mock data.", err);
    return TOP_ROUTES;
  }
}

// GET /index/history (backend) -> current/weekly/monthly APIX for one route
export async function getApixForRoute(route) {
  try {
    const history = await fetchRouteHistory(route);
    if (history.length === 0) return null;

    const avg = (points) => points.reduce((sum, p) => sum + p.index_value, 0) / points.length;
    return {
      current: history[history.length - 1].index_value,
      baseline: INDEX_BASELINE,
      weeklyAvg: avg(history.slice(-7)),
      monthlyAvg: avg(history.slice(-30)),
    };
  } catch (err) {
    console.warn(`[api/client] getApixForRoute(${route}): backend unreachable, using mock data.`, err);
    return APIX_BY_ROUTE[route] ?? null;
  }
}

// APIX for every top route at once, used by ApixPanel.
export async function getApixAll() {
  const top = await getTopRoutes();
  const entries = await Promise.all(top.map(async (r) => [r.route, await getApixForRoute(r.route)]));
  return Object.fromEntries(entries);
}

// GET /index/history (backend, per top route) -> merged trend series
export async function getTrends() {
  try {
    const top = await getTopRoutes();
    const perRoute = await Promise.all(
      top.map(async (r) => ({ route: r.route, history: (await fetchRouteHistory(r.route)).slice(-14) })),
    );

    const byDate = new Map();
    perRoute.forEach(({ route, history }) => {
      history.forEach((point) => {
        const date = point.period_start.slice(5, 10); // MM-DD, matches mockData's format
        if (!byDate.has(date)) byDate.set(date, { date });
        byDate.get(date)[route] = point.index_value;
      });
    });

    return Array.from(byDate.values()).sort((a, b) => (a.date > b.date ? 1 : -1));
  } catch (err) {
    console.warn("[api/client] getTrends: backend unreachable, using mock data.", err);
    return TREND_DATA;
  }
}

// GET /index/current (backend) -> route intensities for the India heatmap.
// City geodata (CITY_COORDS) is frontend-only — the backend only knows
// route codes, not coordinates — so routes to/from a city we don't have
// coordinates for are dropped.
export async function getHeatmapRoutes() {
  try {
    const rows = (await fetchCurrentIndexRows()).filter((r) => r.route.includes("-"));
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

    return { routes, cities: CITY_COORDS };
  } catch (err) {
    console.warn("[api/client] getHeatmapRoutes: backend unreachable, using mock data.", err);
    return { routes: HEATMAP_ROUTES, cities: CITY_COORDS };
  }
}
