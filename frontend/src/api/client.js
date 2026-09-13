// ---------------------------------------------------------------------------
// API CLIENT — currently backed by mock data.
//
// Every function below returns a Promise so components don't need to change
// when these are swapped for real `fetch` calls against the backend. To wire
// up the real API, replace each function body with something like:
//
//   export async function getTopRoutes() {
//     const res = await fetch(`${API_BASE_URL}/api/routes/top`);
//     if (!res.ok) throw new Error("Failed to load top routes");
//     return res.json();
//   }
//
// and remove the mockData.js import once the backend is ready.
// ---------------------------------------------------------------------------
import {
  TOP_ROUTES,
  APIX_BY_ROUTE,
  TREND_DATA,
  HEATMAP_ROUTES,
  CITY_COORDS,
} from "../data/mockData";

// export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const MOCK_LATENCY_MS = 250;

function delay(value) {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_LATENCY_MS));
}

// GET /api/routes/top
export function getTopRoutes() {
  return delay(TOP_ROUTES);
}

// GET /api/index/:route
export function getApixForRoute(route) {
  return delay(APIX_BY_ROUTE[route] ?? null);
}

// GET /api/index (all routes at once, used by the ApixPanel)
export function getApixAll() {
  return delay(APIX_BY_ROUTE);
}

// GET /api/trends
export function getTrends() {
  return delay(TREND_DATA);
}

// GET /api/heatmap
export function getHeatmapRoutes() {
  return delay({ routes: HEATMAP_ROUTES, cities: CITY_COORDS });
}
