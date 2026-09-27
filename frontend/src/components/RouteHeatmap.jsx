import { useMemo, useState, useEffect } from "react";
import indiaStatesPng from "../assets/india-states.png";
import { getHeatmapRoutes } from "../api/client";

// Transparent India-with-state-borders PNG, with routes/cities drawn as an
// SVG overlay on top of it (rather than rendering the shape live from
// GeoJSON) — positioned via a lon/lat -> pixel calibration fit to this
// specific image's own dimensions.
//
// Calibration: the image is a tightly-cropped (no padding) trace of India's
// mainland, so its pixel bounding box is assumed to correspond directly to
// the mainland's real geographic bounding box. That geographic box was
// computed once from the verified India GeoJSON (src/data/indiaGeo.json,
// itself from Natural Earth -- see scripts/extract-india-geo.js) via:
//   node --input-type=module -e "
//     import { geoBounds } from 'd3-geo'; import fs from 'fs';
//     const g = JSON.parse(fs.readFileSync('src/data/indiaGeo.json'));
//     // geometry.coordinates[0] is the mainland ring -- by far the
//     // largest of its 14 polygons (the other 13 are small islands,
//     // e.g. Andaman & Nicobar, which this clipart doesn't depict);
//     // geoBounds on just that ring gives the mainland-only box below.
//     console.log(geoBounds({ type: 'Feature', geometry: {
//       type: 'Polygon', coordinates: g.features[0].geometry.coordinates[0] } }));
//   "
// Verified by projecting all 10 known cities onto the image and confirming
// each lands in its correct real-world position relative to the drawn
// state borders (Delhi north, Mumbai/Pune/Goa along the west coast,
// Chennai/Kochi at the southern tips, Kolkata in the east, etc).
const IMAGE_WIDTH = 315;
const IMAGE_HEIGHT = 350;
const LON_MIN = 68.1648816488165;
const LON_MAX = 97.34317343173433;
const LAT_MIN = 8.078251125011263;
const LAT_MAX = 35.49668967408425;

function project([lon, lat]) {
  const x = (IMAGE_WIDTH * (lon - LON_MIN)) / (LON_MAX - LON_MIN);
  const y = (IMAGE_HEIGHT * (LAT_MAX - lat)) / (LAT_MAX - LAT_MIN);
  return [x, y];
}

// Sequential single-hue (blue) ramp — magnitude reads as one hue from
// dim to bright, never a rainbow. Low intensity recedes into the dark
// surface; high intensity pops bright blue.
const SEQUENTIAL_BLUE = [
  "#0d366b", // recedes into the dark surface (low demand)
  "#184f95",
  "#1c5cab",
  "#256abf",
  "#2a78d6",
  "#3987e5",
  "#5598e7",
  "#6da7ec",
  "#86b6ef", // pops against the dark surface (high demand)
];

function lerpColor(a, b, t) {
  const ah = parseInt(a.slice(1), 16);
  const bh = parseInt(b.slice(1), 16);
  const ar = (ah >> 16) & 0xff,
    ag = (ah >> 8) & 0xff,
    ab = ah & 0xff;
  const br = (bh >> 16) & 0xff,
    bg = (bh >> 8) & 0xff,
    bb = bh & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

function intensityColor(intensity) {
  const clamped = Math.min(1, Math.max(0, intensity));
  const steps = SEQUENTIAL_BLUE.length - 1;
  const pos = clamped * steps;
  const i = Math.min(steps - 1, Math.floor(pos));
  return lerpColor(SEQUENTIAL_BLUE[i], SEQUENTIAL_BLUE[i + 1], pos - i);
}

const LABEL_OFFSET = {
  top: { dx: 0, dy: -7, anchor: "middle" },
  bottom: { dx: 0, dy: 11, anchor: "middle" },
  left: { dx: -6, dy: 2, anchor: "end" },
  right: { dx: 6, dy: 2, anchor: "start" },
};

export default function RouteHeatmap({ leadTimeDays = 30 }) {
  const [routes, setRoutes] = useState([]);
  const [cities, setCities] = useState({});
  const [hoveredRoute, setHoveredRoute] = useState(null);
  const [hoveredCity, setHoveredCity] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getHeatmapRoutes(leadTimeDays).then(({ routes, cities }) => {
      setRoutes(routes);
      setCities(cities);
      setLoading(false);
    });
  }, [leadTimeDays]);

  const projected = useMemo(() => {
    const map = {};
    Object.entries(cities).forEach(([code, c]) => {
      map[code] = project(c.coordinates);
    });
    return map;
  }, [cities]);

  // CITY_COORDS lists every city this dashboard knows about, but not
  // every one of them necessarily has a route in the current data (e.g.
  // Goa/Kochi aren't among the routes currently scraped) -- only show a
  // marker for cities that actually appear in at least one real route,
  // rather than every city the frontend happens to have coordinates for.
  const citiesWithRoutes = useMemo(() => {
    const codes = new Set();
    routes.forEach((r) => {
      codes.add(r.from);
      codes.add(r.to);
    });
    return Object.fromEntries(Object.entries(cities).filter(([code]) => codes.has(code)));
  }, [cities, routes]);

  const hoveredRouteData = hoveredRoute
    ? routes.find((r) => `${r.from}-${r.to}` === hoveredRoute)
    : null;

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">India Route Heatmap</h2>
        <div className="flex items-center gap-2 text-xs text-white/45">
          <span
            className="inline-block h-2 w-16 rounded-full"
            style={{ background: `linear-gradient(90deg, ${SEQUENTIAL_BLUE[0]}, ${SEQUENTIAL_BLUE[SEQUENTIAL_BLUE.length - 1]})` }}
          />
          low → high demand
        </div>
      </div>

      {loading ? (
        <div className="h-[420px] rounded-xl bg-white/5 animate-pulse" />
      ) : (
        <div className="relative mx-auto" style={{ maxWidth: 380 }}>
          <img src={indiaStatesPng} alt="" className="w-full h-auto select-none pointer-events-none" draggable={false} />

          <svg
            viewBox={`0 0 ${IMAGE_WIDTH} ${IMAGE_HEIGHT}`}
            className="absolute inset-0 w-full h-full"
          >
            {routes.map((r) => {
              const from = projected[r.from];
              const to = projected[r.to];
              if (!from || !to) return null;

              const [x1, y1] = from;
              const [x2, y2] = to;
              const mx = (x1 + x2) / 2;
              const my = (y1 + y2) / 2 - 17; // arc bulge
              const key = `${r.from}-${r.to}`;
              const isHovered = hoveredRoute === key;

              return (
                <path
                  key={key}
                  d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`}
                  fill="none"
                  stroke={intensityColor(r.intensity)}
                  strokeWidth={1 + r.intensity * 4}
                  strokeLinecap="round"
                  opacity={isHovered ? 1 : 0.7 + r.intensity * 0.25}
                  onMouseEnter={() => setHoveredRoute(key)}
                  onMouseLeave={() => setHoveredRoute(null)}
                  style={{ cursor: "pointer", transition: "opacity 0.15s" }}
                />
              );
            })}

            {Object.entries(citiesWithRoutes).map(([code, c]) => {
              const pos = projected[code];
              if (!pos) return null;
              const [x, y] = pos;
              const { dx, dy, anchor } = LABEL_OFFSET[c.labelPos] ?? LABEL_OFFSET.top;

              return (
                <g
                  key={code}
                  onMouseEnter={() => setHoveredCity(code)}
                  onMouseLeave={() => setHoveredCity(null)}
                  style={{ cursor: "pointer" }}
                >
                  <circle cx={x} cy={y} r={3} fill="#060810" stroke="#93c5fd" strokeWidth={1.2} />
                  <text
                    x={x + dx}
                    y={y + dy}
                    textAnchor={anchor}
                    fontSize="8"
                    fontWeight="600"
                    fill="rgba(255,255,255,0.9)"
                    stroke="#060810"
                    strokeWidth="2"
                    paintOrder="stroke"
                  >
                    {code}
                  </text>
                </g>
              );
            })}
          </svg>

          {(hoveredRouteData || hoveredCity) && (
            <div className="absolute top-2 right-2 glass px-3 py-2 rounded-lg text-xs text-white/85 border border-blue-400/30 pointer-events-none">
              {hoveredCity ? (
                <p className="font-semibold">{cities[hoveredCity]?.name ?? hoveredCity}</p>
              ) : (
                <>
                  <p className="font-semibold">
                    {cities[hoveredRouteData.from]?.name ?? hoveredRouteData.from} →{" "}
                    {cities[hoveredRouteData.to]?.name ?? hoveredRouteData.to}
                  </p>
                  <p className="text-white/50">Index: {hoveredRouteData.index.toFixed(1)}</p>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
