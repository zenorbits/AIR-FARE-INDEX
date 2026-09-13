import { useEffect, useMemo, useState } from "react";
import { ComposableMap, Geographies, Geography } from "react-simple-maps";
import { geoMercator } from "d3-geo";
import indiaGeo from "../data/indiaGeo.json";
import { getHeatmapRoutes } from "../api/client";

// India's real coastline as GeoJSON (Natural Earth, via react-simple-maps +
// d3-geo) instead of a hand-drawn SVG silhouette — see
// scripts/extract-india-geo.js for how src/data/indiaGeo.json was generated.
const MAP_WIDTH = 480;
const MAP_HEIGHT = 560;

// Sequential single-hue (blue) ramp — magnitude should read as one hue from
// dim to bright, never a rainbow. Values are the validated palette's
// sequential blue steps (see the dataviz skill's reference palette).
// Because the dashboard is dark-themed, low intensity recedes toward the
// dark surface (darkest step) and high intensity pops brighter (lightest step).
const SEQUENTIAL_BLUE = [
  "#0d366b", // step 700 — recedes into the dark surface (low demand)
  "#184f95", // step 600
  "#1c5cab", // step 550
  "#256abf", // step 500
  "#2a78d6", // step 450
  "#3987e5", // step 400
  "#5598e7", // step 350
  "#6da7ec", // step 300
  "#86b6ef", // step 250 — pops against the dark surface (high demand)
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
  top: { dx: 0, dy: -10, anchor: "middle" },
  bottom: { dx: 0, dy: 16, anchor: "middle" },
  left: { dx: -9, dy: 3, anchor: "end" },
  right: { dx: 9, dy: 3, anchor: "start" },
};

export default function RouteHeatmap() {
  const [routes, setRoutes] = useState([]);
  const [cities, setCities] = useState({});
  const [hovered, setHovered] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Swap for: fetch(`${API_BASE_URL}/api/heatmap`)
    getHeatmapRoutes().then(({ routes, cities }) => {
      setRoutes(routes);
      setCities(cities);
      setLoading(false);
    });
  }, []);

  const projection = useMemo(
    () => geoMercator().center([82.8, 22.5]).scale(880).translate([MAP_WIDTH / 2, MAP_HEIGHT / 2]),
    [],
  );

  const projected = useMemo(() => {
    const map = {};
    Object.entries(cities).forEach(([code, c]) => {
      map[code] = projection(c.coordinates);
    });
    return map;
  }, [cities, projection]);

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
        <div className="relative flex justify-center">
          <ComposableMap
            projection={projection}
            width={MAP_WIDTH}
            height={MAP_HEIGHT}
            style={{ width: "100%", maxWidth: 380, height: "auto" }}
          >
            <Geographies geography={indiaGeo}>
              {({ geographies }) =>
                geographies.map((geo) => (
                  <Geography
                    key={geo.rsmKey}
                    geography={geo}
                    fill="rgba(255,255,255,0.06)"
                    stroke="rgba(255,255,255,0.3)"
                    strokeWidth={1}
                    style={{
                      default: { outline: "none" },
                      hover: { outline: "none" },
                      pressed: { outline: "none" },
                    }}
                  />
                ))
              }
            </Geographies>

            {routes.map((r) => {
              const from = projected[r.from];
              const to = projected[r.to];
              if (!from || !to) return null;

              const [x1, y1] = from;
              const [x2, y2] = to;
              const mx = (x1 + x2) / 2;
              const my = (y1 + y2) / 2 - 26; // arc bulge
              const key = `${r.from}-${r.to}`;
              const isHovered = hovered === key;

              return (
                <path
                  key={key}
                  d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`}
                  fill="none"
                  stroke={intensityColor(r.intensity)}
                  strokeWidth={1.5 + r.intensity * 6}
                  strokeLinecap="round"
                  opacity={isHovered ? 1 : 0.7 + r.intensity * 0.25}
                  onMouseEnter={() => setHovered(key)}
                  onMouseLeave={() => setHovered(null)}
                  style={{ cursor: "pointer", transition: "opacity 0.15s" }}
                >
                  <title>
                    {r.from} → {r.to} · index {r.index.toFixed(1)}
                  </title>
                </path>
              );
            })}

            {Object.entries(cities).map(([code, c]) => {
              const pos = projected[code];
              if (!pos) return null;
              const [x, y] = pos;
              const { dx, dy, anchor } = LABEL_OFFSET[c.labelPos] ?? LABEL_OFFSET.top;

              return (
                <g key={code}>
                  <circle cx={x} cy={y} r={4} fill="#0b1020" stroke="#e5e7eb" strokeWidth={1.5} />
                  <text
                    x={x + dx}
                    y={y + dy}
                    textAnchor={anchor}
                    fontSize="11"
                    fontWeight="600"
                    fill="rgba(255,255,255,0.9)"
                    stroke="#0b1020"
                    strokeWidth="2.5"
                    paintOrder="stroke"
                  >
                    {code}
                  </text>
                </g>
              );
            })}
          </ComposableMap>

          {hovered && (
            <div className="absolute top-2 right-2 glass px-3 py-2 rounded-lg text-xs text-white/85 border border-white/15">
              {(() => {
                const r = routes.find((r) => `${r.from}-${r.to}` === hovered);
                if (!r) return null;
                return (
                  <>
                    <p className="font-semibold">
                      {r.from} → {r.to}
                    </p>
                    <p className="text-white/50">Index: {r.index.toFixed(1)}</p>
                  </>
                );
              })()}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
