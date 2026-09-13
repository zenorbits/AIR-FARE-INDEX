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

const LINE_COLORS = ["#818cf8", "#38bdf8", "#f472b6", "#4ade80", "#fbbf24", "#fb7185"];

export default function TrendChart({ selectedRoute, onSelectRoute }) {
  const [trend, setTrend] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [mode, setMode] = useState("all"); // "all" | "single"
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Swap for: fetch(`${API_BASE_URL}/api/trends`)
    Promise.all([getTrends(), getTopRoutes()]).then(([trendData, topRoutes]) => {
      setTrend(trendData);
      setRoutes(topRoutes);
      setLoading(false);
    });
  }, []);

  const activeRoute = selectedRoute ?? routes[0]?.route;

  const routeColor = useMemo(() => {
    const map = {};
    routes.forEach((r, i) => {
      map[r.route] = LINE_COLORS[i % LINE_COLORS.length];
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
                mode === "all" ? "bg-white/20 text-white" : "text-white/50 hover:bg-white/8"
              }`}
            >
              All routes
            </button>
            <button
              type="button"
              onClick={() => setMode("single")}
              className={`px-3 py-1.5 font-medium transition-colors ${
                mode === "single" ? "bg-white/20 text-white" : "text-white/50 hover:bg-white/8"
              }`}
            >
              Single route
            </button>
          </div>

          {mode === "single" && (
            <select
              value={activeRoute ?? ""}
              onChange={(e) => onSelectRoute?.(e.target.value)}
              className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            >
              {routes.map((r) => (
                <option key={r.route} value={r.route} className="bg-slate-800">
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
                  <stop offset="0%" stopColor="#818cf8" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#818cf8" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{
                  background: "rgba(17, 24, 39, 0.9)",
                  border: "1px solid rgba(255,255,255,0.15)",
                  borderRadius: "0.75rem",
                  color: "#fff",
                }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
              />
              {mode === "all" ? (
                <>
                  <Legend wrapperStyle={{ fontSize: 12, color: "rgba(255,255,255,0.6)" }} />
                  {routes.map((r) => (
                    <Line
                      key={r.route}
                      type="monotone"
                      dataKey={r.route}
                      stroke={routeColor[r.route]}
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
                  stroke="#818cf8"
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
