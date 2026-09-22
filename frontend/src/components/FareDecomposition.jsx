import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { getFareBreakdown, getRoutesList, getSources } from "../api/client";
import { routeLabel } from "../data/mockData";

// Fare decomposition: base fare vs taxes/fees, averaged per (route, source)
// from individual /fares/raw rows -- the backend has no aggregate endpoint
// for this, so the averaging happens here.
export default function FareDecomposition() {
  const [routes, setRoutes] = useState([]);
  const [sources, setSources] = useState([]);
  const [route, setRoute] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getRoutesList(), getSources()])
      .then(([r, s]) => {
        setRoutes(r);
        setSources(s);
        setRoute(r[0] ?? "");
      })
      .catch((err) => console.warn("[FareDecomposition] could not load routes/sources", err));
  }, []);

  useEffect(() => {
    if (!route) return;
    getFareBreakdown({ route, limit: 2000 })
      .then(setRows)
      .catch((err) => {
        console.warn("[FareDecomposition] fares/raw failed", err);
        setRows([]);
      })
      .finally(() => setLoading(false));
  }, [route]);

  const bySource = useMemo(() => {
    const groups = {};
    rows.forEach((r) => {
      if (r.is_outlier) return;
      const key = r.source ?? "unknown";
      groups[key] ??= { source: key, baseSum: 0, taxSum: 0, n: 0 };
      groups[key].baseSum += r.base_fare ?? 0;
      groups[key].taxSum += r.taxes_fees ?? 0;
      groups[key].n += 1;
    });
    return Object.values(groups).map((g) => ({
      source: g.source,
      "Base fare": g.n ? Math.round(g.baseSum / g.n) : 0,
      "Taxes & fees": g.n ? Math.round(g.taxSum / g.n) : 0,
      n: g.n,
    }));
  }, [rows]);

  const { origin, destination } = route ? routeLabel(route) : { origin: "", destination: "" };

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div>
          <h2 className="text-white font-semibold text-lg">Fare Decomposition</h2>
          <p className="text-white/50 text-xs mt-0.5">
            Average base fare vs taxes &amp; fees, per source{route && ` — ${origin} → ${destination}`}
          </p>
        </div>
        <select
          value={route}
          onChange={(e) => setRoute(e.target.value)}
          className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-white/40"
        >
          {routes.map((r) => (
            <option key={r} value={r} className="bg-neutral-900">
              {r}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="h-64 rounded-xl bg-white/5 animate-pulse" />
      ) : bySource.length === 0 ? (
        <p className="text-white/50 text-sm py-8 text-center">No fare data for this route yet.</p>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bySource} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey="source" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{
                  background: "rgba(0, 0, 0, 0.9)",
                  border: "1px solid rgba(255,255,255,0.15)",
                  borderRadius: "0.75rem",
                  color: "#fff",
                }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
              />
              <Legend wrapperStyle={{ fontSize: 12, color: "rgba(255,255,255,0.6)" }} />
              <Bar dataKey="Base fare" stackId="fare" fill="#e5e5e5" radius={[0, 0, 4, 4]} />
              <Bar dataKey="Taxes & fees" stackId="fare" fill="#737373" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {sources.length > 0 && !sources.includes("akasa") && (
        <p className="text-white/30 text-[11px] mt-3">
          Akasa Air is scraped directly but excluded from this view and the index — its feed is stored
          separately for fare decomposition/ML, not blended with OTA data.
        </p>
      )}
    </section>
  );
}
