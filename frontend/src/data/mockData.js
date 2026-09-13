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

// City coordinates used to plot the India route heatmap (SVG viewBox space,
// pre-projected for a 0-1000 x 0-1000 canvas roughly matching India's outline).
export const CITY_COORDS = {
  DEL: { name: "Delhi", x: 430, y: 190 },
  BOM: { name: "Mumbai", x: 260, y: 520 },
  BLR: { name: "Bengaluru", x: 400, y: 730 },
  MAA: { name: "Chennai", x: 470, y: 760 },
  CCU: { name: "Kolkata", x: 690, y: 400 },
  HYD: { name: "Hyderabad", x: 420, y: 610 },
  GOI: { name: "Goa", x: 300, y: 600 },
  PNQ: { name: "Pune", x: 300, y: 550 },
  COK: { name: "Kochi", x: 380, y: 830 },
  AMD: { name: "Ahmedabad", x: 260, y: 420 },
};

// Top 6 routes ranked by current Airfare Price Index (APIX).
// `change` is the % move vs. the previous period (positive = index up).
export const TOP_ROUTES = [
  { route: "DEL-BOM", origin: "Delhi", destination: "Mumbai", index: 142.8, change: 3.4 },
  { route: "BOM-BLR", origin: "Mumbai", destination: "Bengaluru", index: 128.5, change: -1.2 },
  { route: "DEL-BLR", origin: "Delhi", destination: "Bengaluru", index: 135.1, change: 2.1 },
  { route: "DEL-CCU", origin: "Delhi", destination: "Kolkata", index: 119.6, change: -0.6 },
  { route: "BLR-MAA", origin: "Bengaluru", destination: "Chennai", index: 108.9, change: 1.8 },
  { route: "BOM-GOI", origin: "Mumbai", destination: "Goa", index: 121.3, change: 4.7 },
];

// Detailed APIX numbers per route for the ApixPanel (labelled numeric display).
export const APIX_BY_ROUTE = {
  "DEL-BOM": { current: 142.8, baseline: 100, weeklyAvg: 139.2, monthlyAvg: 133.7 },
  "BOM-BLR": { current: 128.5, baseline: 100, weeklyAvg: 130.1, monthlyAvg: 126.4 },
  "DEL-BLR": { current: 135.1, baseline: 100, weeklyAvg: 132.0, monthlyAvg: 129.8 },
  "DEL-CCU": { current: 119.6, baseline: 100, weeklyAvg: 120.4, monthlyAvg: 117.9 },
  "BLR-MAA": { current: 108.9, baseline: 100, weeklyAvg: 106.5, monthlyAvg: 104.2 },
  "BOM-GOI": { current: 121.3, baseline: 100, weeklyAvg: 115.8, monthlyAvg: 112.0 },
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
  { from: "BOM", to: "BLR", intensity: 0.75, index: 128.5 },
  { from: "DEL", to: "BLR", intensity: 0.85, index: 135.1 },
  { from: "DEL", to: "CCU", intensity: 0.6, index: 119.6 },
  { from: "BLR", to: "MAA", intensity: 0.5, index: 108.9 },
  { from: "BOM", to: "GOI", intensity: 0.68, index: 121.3 },
  { from: "DEL", to: "AMD", intensity: 0.4, index: 102.5 },
  { from: "BOM", to: "PNQ", intensity: 0.3, index: 96.8 },
  { from: "BLR", to: "COK", intensity: 0.45, index: 105.2 },
  { from: "HYD", to: "DEL", intensity: 0.55, index: 111.4 },
];
