import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { getTrends, getTopRoutes } from "../api/client";
import { routeLabel } from "../data/mockData";

// Single-hue blue ramp (light -> dark) — each line pairs a shade with a
// distinct dash pattern so up to 6 series stay distinguishable at a glance.
const LINE_STYLES = [
  { stroke: "#93c5fd", dash: undefined },
  { stroke: "#3b82f6", dash: undefined },
  { stroke: "#93c5fd", dash: "6 4" },
  { stroke: "#1d4ed8", dash: undefined },
  { stroke: "#60a5fa", dash: "2 3" },
  { stroke: "#1e40af", dash: "6 3 2 3" },
];

export default function TrendChart({ selectedRoute, onSelectRoute, leadTimeDays = 30 }) {
  const [trend, setTrend] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [mode, setMode] = useState("all"); // "all" | "single"
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getTrends(leadTimeDays), getTopRoutes(leadTimeDays)]).then(([trendData, topRoutes]) => {
      setTrend(trendData);
      setRoutes(topRoutes);
      setLoading(false);
    });
  }, [leadTimeDays]);

  const activeRoute = selectedRoute ?? routes[0]?.route;

  const routeStyle = useMemo(() => {
    const map = {};
    routes.forEach((r, i) => {
      map[r.route] = LINE_STYLES[i % LINE_STYLES.length];
    });
    return map;
  }, [routes]);

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-white font-semibold text-lg">Airfare Index Trend</h2>

        <div className="flex items-center gap-2">
          <div className="flex rounded-lg overflow-hidden border border-white/15 text-xs">
            <button
              type="button"
              onClick={() => setMode("all")}
              className={`px-3 py-1.5 font-medium transition-colors ${
                mode === "all" ? "bg-blue-500/25 text-white" : "text-white/50 hover:bg-white/8"
              }`}
            >
              All routes
            </button>
            <button
              type="button"
              onClick={() => setMode("single")}
              className={`px-3 py-1.5 font-medium transition-colors ${
                mode === "single" ? "bg-blue-500/25 text-white" : "text-white/50 hover:bg-white/8"
              }`}
            >
              Single route
            </button>
          </div>

          {mode === "single" && (
            <select
              value={activeRoute ?? ""}
              onChange={(e) => onSelectRoute?.(e.target.value)}
              className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            >
              {routes.map((r) => (
                <option key={r.route} value={r.route} title={`${r.origin} → ${r.destination}`} className="bg-neutral-900">
                  {r.route}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {loading ? (
        <div className="h-72 rounded-xl bg-white/5 animate-pulse" />
      ) : (
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={trend} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <defs>
                <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{
                  background: "rgba(6, 8, 16, 0.95)",
                  border: "1px solid rgba(59, 130, 246, 0.3)",
                  borderRadius: "0.75rem",
                  color: "#fff",
                }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                formatter={(value, name) => {
                  const { origin, destination } = routeLabel(name);
                  return [value, `${name} (${origin} → ${destination})`];
                }}
              />
              {mode === "all" ? (
                <>
                  <Legend
                    content={({ payload }) => (
                      <ul className="flex flex-wrap gap-x-3 gap-y-1 justify-center mt-3 text-xs">
                        {payload.map((entry) => {
                          const { origin, destination } = routeLabel(entry.value);
                          return (
                            <li
                              key={entry.value}
                              className="flex items-center gap-1.5"
                              title={`${origin} → ${destination}`}
                            >
                              <span
                                className="inline-block h-2 w-2 rounded-full border border-white/40"
                                style={{ background: entry.color }}
                              />
                              <span style={{ color: "rgba(255,255,255,0.6)" }}>{entry.value}</span>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  />
                  {routes.map((r) => (
                    <Line
                      key={r.route}
                      type="monotone"
                      dataKey={r.route}
                      stroke={routeStyle[r.route]?.stroke}
                      strokeDasharray={routeStyle[r.route]?.dash}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  ))}
                </>
              ) : (
                <Area
                  type="monotone"
                  dataKey={activeRoute}
                  stroke="#3b82f6"
                  strokeWidth={2.5}
                  fill="url(#areaFill)"
                  dot={false}
                  activeDot={{ r: 5 }}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
