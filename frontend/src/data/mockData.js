// ---------------------------------------------------------------------------
// MOCK DATA — placeholder for the real backend API.
//
// This file exists purely so the dashboard has something to render during
// development. Every export here mirrors the shape of the REST endpoints the
// backend already exposes (see Backend/api/main.py), so swapping mock data
// for real network calls should only require changing the fetch layer in
// `src/api/client.js`, not the components themselves.
//
// Real endpoints to wire up later:
//   GET /api/routes/top      -> TOP_ROUTES
//   GET /api/index/:route    -> APIX_BY_ROUTE
//   GET /api/trends          -> TREND_DATA
//   GET /api/heatmap         -> HEATMAP_ROUTES
// ---------------------------------------------------------------------------

// Real city coordinates ([longitude, latitude], GeoJSON order) used to plot
// the India route heatmap on a real Leaflet/CARTO basemap in RouteHeatmap.jsx
// (converted to [latitude, longitude] there, which is what Leaflet expects).
export const CITY_COORDS = {
  DEL: { name: "Delhi", coordinates: [77.1025, 28.7041] },
  AMD: { name: "Ahmedabad", coordinates: [72.5714, 23.0225] },
  BOM: { name: "Mumbai", coordinates: [72.8777, 19.076] },
  PNQ: { name: "Pune", coordinates: [73.8567, 18.5204] },
  GOI: { name: "Goa", coordinates: [73.8278, 15.4909] },
  HYD: { name: "Hyderabad", coordinates: [78.4867, 17.385] },
  BLR: { name: "Bengaluru", coordinates: [77.5946, 12.9716] },
  MAA: { name: "Chennai", coordinates: [80.2707, 13.0827] },
  COK: { name: "Kochi", coordinates: [76.2673, 9.9312] },
  CCU: { name: "Kolkata", coordinates: [88.3639, 22.5726] },
};

// Full city name for a code, e.g. "DEL" -> "Delhi". Falls back to the code
// itself for anything not in CITY_COORDS. Used to build hover tooltips
// wherever a route/city code is shown in its short form.
export function cityName(code) {
  return CITY_COORDS[code]?.name ?? code;
}

// "DEL-BOM" -> { origin: "Delhi", destination: "Mumbai" }
export function routeLabel(route) {
  const [origin, destination] = route.split("-");
  return { origin: cityName(origin), destination: cityName(destination) };
}

// Top 6 routes ranked by current Airfare Price Index (APIX).
// `change` is the % move vs. the previous period (positive = index up).
export const TOP_ROUTES = [
  { route: "DEL-BOM", origin: "Delhi", destination: "Mumbai", index: 142.8, change: 3.4 },
  { route: "DEL-BLR", origin: "Delhi", destination: "Bengaluru", index: 135.1, change: 2.1 },
  { route: "BOM-BLR", origin: "Mumbai", destination: "Bengaluru", index: 128.5, change: -1.2 },
  { route: "DEL-CCU", origin: "Delhi", destination: "Kolkata", index: 119.6, change: -0.6 },
  { route: "MAA-DEL", origin: "Chennai", destination: "Delhi", index: 118.4, change: -0.9 },
  { route: "BLR-HYD", origin: "Bengaluru", destination: "Hyderabad", index: 99.7, change: 2.3 },
];

// Detailed APIX numbers per route for the ApixPanel (labelled numeric display).
export const APIX_BY_ROUTE = {
  "DEL-BOM": { current: 142.8, baseline: 100, weeklyAvg: 139.2, monthlyAvg: 133.7 },
  "DEL-BLR": { current: 135.1, baseline: 100, weeklyAvg: 132.0, monthlyAvg: 129.8 },
  "BOM-BLR": { current: 128.5, baseline: 100, weeklyAvg: 130.1, monthlyAvg: 126.4 },
  "DEL-CCU": { current: 119.6, baseline: 100, weeklyAvg: 120.4, monthlyAvg: 117.9 },
  "MAA-DEL": { current: 118.4, baseline: 100, weeklyAvg: 116.0, monthlyAvg: 113.2 },
  "BLR-HYD": { current: 99.7, baseline: 100, weeklyAvg: 97.5, monthlyAvg: 95.0 },
};

// Trend series: one index value per route per date, for the last 14 days.
// Shaped as an array of { date, <routeCode>: value, ... } so Recharts can
// plot multiple <Line> series straight off a single dataset.
function buildTrendData() {
  const days = 14;
  const routes = TOP_ROUTES.map((r) => r.route);
  const today = new Date();
  const series = [];

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const point = {
      date: d.toISOString().slice(5, 10), // MM-DD
    };
    routes.forEach((route) => {
      const base = APIX_BY_ROUTE[route].baseline + (APIX_BY_ROUTE[route].current - 100) * ((days - i) / days);
      const noise = Math.sin(i * 1.3 + route.length) * 4 + Math.random() * 3;
      point[route] = Math.round((base + noise) * 10) / 10;
    });
    series.push(point);
  }
  return series;
}

export const TREND_DATA = buildTrendData();

// Routes for the India heatmap: origin/destination city codes + intensity
// (0-1, derived from index/demand) driving line thickness & color.
export const HEATMAP_ROUTES = [
  { from: "DEL", to: "BOM", intensity: 0.95, index: 142.8 },
  { from: "DEL", to: "BLR", intensity: 0.85, index: 135.1 },
  { from: "BOM", to: "BLR", intensity: 0.75, index: 128.5 },
  { from: "DEL", to: "CCU", intensity: 0.6, index: 119.6 },
  { from: "MAA", to: "DEL", intensity: 0.58, index: 118.4 },
  { from: "BLR", to: "HYD", intensity: 0.35, index: 99.7 },
  { from: "DEL", to: "AMD", intensity: 0.4, index: 102.5 },
  { from: "BOM", to: "PNQ", intensity: 0.3, index: 96.8 },
  { from: "BLR", to: "COK", intensity: 0.45, index: 105.2 },
  { from: "HYD", to: "DEL", intensity: 0.55, index: 111.4 },
];
