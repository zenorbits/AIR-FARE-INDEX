import { useEffect, useState } from "react";
import { getHeatmapRoutes } from "../api/client";

// Simplified silhouette of India, hand-traced to match real geography:
// the Kashmir tip in the north, the northeast states reached through the
// narrow Siliguri corridor, the Kutch/Gujarat bulge in the west, and the
// coastline tapering south to Kanyakumari. Dependency-free (no GeoJSON/
// topojson fetch needed) — swap for a proper GeoJSON-driven map later if
// more precision is required.
const INDIA_OUTLINE = `
M 240 20 L 280 45 L 260 90 L 300 110 L 360 130
L 410 150 L 430 165 L 460 155 L 500 140 L 545 160
L 560 200 L 530 230 L 500 260 L 470 240 L 445 210
L 420 230 L 405 270 L 385 320 L 370 370 L 355 420
L 335 470 L 310 520 L 290 570 L 300 600 L 270 580
L 245 545 L 225 500 L 210 460 L 195 420 L 180 380
L 165 340 L 150 300 L 120 270 L 85 250 L 70 220
L 100 195 L 115 160 L 130 120 L 160 80 L 200 50 Z
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
          <svg viewBox="40 0 550 630" className="w-full max-w-sm h-[440px]">
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
