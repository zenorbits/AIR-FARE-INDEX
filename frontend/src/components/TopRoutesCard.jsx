import { useEffect, useState } from "react";
import { getTopRoutes } from "../api/client";

function TrendBadge({ change }) {
  const isUp = change >= 0;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
        isUp
          ? "bg-emerald-400/15 text-emerald-300 border border-emerald-400/30"
          : "bg-rose-400/15 text-rose-300 border border-rose-400/30"
      }`}
    >
      {isUp ? "▲" : "▼"} {Math.abs(change).toFixed(1)}%
    </span>
  );
}

export default function TopRoutesCard({ selectedRoute, onSelectRoute }) {
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Swap for: fetch(`${API_BASE_URL}/api/routes/top`) via api/client.js
    getTopRoutes().then((data) => {
      setRoutes(data);
      setLoading(false);
    });
  }, []);

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">Top 6 Routes</h2>
        <span className="text-xs text-white/45">by Airfare Index</span>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-24 rounded-xl bg-white/5 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {routes.map((r) => {
            const isSelected = selectedRoute === r.route;
            return (
              <button
                key={r.route}
                type="button"
                onClick={() => onSelectRoute?.(r.route)}
                className={`glass glass-hover text-left p-4 rounded-xl border transition-colors ${
                  isSelected
                    ? "border-indigo-400/60 bg-indigo-400/10"
                    : "border-white/10"
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <p className="text-sm font-medium text-white/90">
                    {r.origin} <span className="text-white/40">→</span> {r.destination}
                  </p>
                  <TrendBadge change={r.change} />
                </div>
                <p className="text-2xl font-bold text-white tracking-tight">{r.index.toFixed(1)}</p>
                <p className="text-xs text-white/40 mt-0.5" title={`${r.origin} → ${r.destination}`}>
                  {r.route}
                </p>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
