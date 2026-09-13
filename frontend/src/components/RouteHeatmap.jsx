import { useEffect, useState } from "react";
import { getHeatmapRoutes } from "../api/client";

// Simplified silhouette of India, hand-traced to a 0-1000 x 0-1000 viewBox.
// This keeps the component dependency-free (no GeoJSON/topojson fetch) while
// still giving recognizable geography for the route arcs to sit on top of.
// Swap for a proper GeoJSON/TopoJSON-driven map later if more precision is needed.
const INDIA_OUTLINE = `
M 430 40
L 470 60 L 500 90 L 560 110 L 610 140 L 640 190 L 660 230
L 690 260 L 720 300 L 700 340 L 730 380 L 710 420 L 690 400
L 660 440 L 630 470 L 600 500 L 580 540 L 560 580 L 520 600
L 500 650 L 480 700 L 460 750 L 440 800 L 420 850 L 400 890
L 385 850 L 375 800 L 360 830 L 340 800 L 330 750 L 310 720
L 290 680 L 270 640 L 260 600 L 240 560 L 230 520 L 220 480
L 210 440 L 200 400 L 220 360 L 210 320 L 230 280 L 250 250
L 240 210 L 260 180 L 290 160 L 310 130 L 340 110 L 360 80
L 390 60 Z
`;

function intensityColor(intensity) {
  // Interpolate from cool (low demand) to hot (high demand).
  const hue = 260 - intensity * 220; // 260 (indigo) -> ~40 (amber/red)
  return `hsl(${hue}, 90%, ${60 - intensity * 10}%)`;
}

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
          <span className="inline-block h-2 w-6 rounded-full" style={{ background: "linear-gradient(90deg, hsl(260,90%,60%), hsl(40,90%,50%))" }} />
          low → high demand
        </div>
      </div>

      {loading ? (
        <div className="h-[420px] rounded-xl bg-white/5 animate-pulse" />
      ) : (
        <div className="relative flex justify-center">
          <svg viewBox="170 10 590 900" className="w-full max-w-sm h-[420px]">
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
                  opacity={isHovered ? 1 : 0.55 + r.intensity * 0.3}
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

            {Object.entries(cities).map(([code, c]) => (
              <g key={code}>
                <circle cx={c.x} cy={c.y} r={7} fill="#0b1020" stroke="#e5e7eb" strokeWidth="2" />
                <text
                  x={c.x}
                  y={c.y - 12}
                  textAnchor="middle"
                  fontSize="16"
                  fill="rgba(255,255,255,0.8)"
                  fontWeight="600"
                >
                  {code}
                </text>
              </g>
            ))}
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
