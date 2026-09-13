import { useEffect, useState } from "react";
import { getHeatmapRoutes } from "../api/client";

// Simplified silhouette of India, hand-traced to a 0-800 x 0-950 viewBox.
// This keeps the component dependency-free (no GeoJSON/topojson fetch) while
// still giving recognizable geography for the route arcs to sit on top of.
// Swap for a proper GeoJSON/TopoJSON-driven map later if more precision is needed.
const INDIA_OUTLINE = `
M 420 20
L 460 40 L 500 70 L 520 110 L 560 130 L 610 150
L 650 180 L 700 190 L 750 230 L 730 270 L 690 260
L 660 290 L 690 330 L 670 380 L 630 420 L 600 460
L 580 510 L 560 560 L 540 610 L 510 660 L 480 710
L 450 760 L 420 810 L 400 860 L 385 905 L 370 860
L 350 810 L 330 760 L 300 720 L 290 680 L 270 630
L 250 580 L 235 530 L 225 480 L 220 430 L 215 380
L 220 330 L 200 290 L 210 250 L 230 210 L 250 170
L 270 130 L 300 100 L 330 70 L 360 45 Z
`;

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
  top: { dx: 0, dy: -14, anchor: "middle" },
  bottom: { dx: 0, dy: 22, anchor: "middle" },
  left: { dx: -12, dy: 4, anchor: "end" },
  right: { dx: 12, dy: 4, anchor: "start" },
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
          <svg viewBox="180 0 600 930" className="w-full max-w-sm h-[440px]">
            <path
              d={INDIA_OUTLINE}
              fill="rgba(255,255,255,0.06)"
              stroke="rgba(255,255,255,0.25)"
              strokeWidth="2"
            />

            {routes.map((r) => {
              const from = cities[r.from];
              const to = cities[r.to];
              if (!from || !to) return null;

              const mx = (from.x + to.x) / 2;
              const my = (from.y + to.y) / 2 - 40; // arc bulge
              const key = `${r.from}-${r.to}`;
              const isHovered = hovered === key;

              return (
                <path
                  key={key}
                  d={`M ${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`}
                  fill="none"
                  stroke={intensityColor(r.intensity)}
                  strokeWidth={2 + r.intensity * 8}
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
              const { dx, dy, anchor } = LABEL_OFFSET[c.labelPos] ?? LABEL_OFFSET.top;
              return (
                <g key={code}>
                  <circle cx={c.x} cy={c.y} r={7} fill="#0b1020" stroke="#e5e7eb" strokeWidth="2" />
                  <text
                    x={c.x + dx}
                    y={c.y + dy}
                    textAnchor={anchor}
                    fontSize="17"
                    fontWeight="600"
                    fill="rgba(255,255,255,0.9)"
                    stroke="#0b1020"
                    strokeWidth="3"
                    paintOrder="stroke"
                  >
                    {code}
                  </text>
                </g>
              );
            })}
          </svg>

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
