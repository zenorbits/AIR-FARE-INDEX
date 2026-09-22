import { useEffect, useState } from "react";
import { getApixAll, getTopRoutes } from "../api/client";

export default function ApixPanel({ leadTimeDays = 30 }) {
  const [routes, setRoutes] = useState([]);
  const [apix, setApix] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getTopRoutes(leadTimeDays), getApixAll(leadTimeDays)]).then(([topRoutes, apixData]) => {
      setRoutes(topRoutes);
      setApix(apixData);
      setLoading(false);
    });
  }, [leadTimeDays]);

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">Airfare Price Index (APIX)</h2>
        <span className="text-xs text-white/45">baseline = 100</span>
      </div>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-16 rounded-xl bg-white/5 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {routes.map((r) => {
            const detail = apix[r.route];
            const overBaseline = detail ? detail.current - detail.baseline : 0;
            return (
              <div
                key={r.route}
                className="glass p-4 rounded-xl border border-white/10 flex items-center justify-between gap-4"
              >
                <div>
                  <p className="text-sm font-medium text-white/90" title={`${r.origin} → ${r.destination}`}>
                    {r.route}
                  </p>
                  <p className="text-xs text-white/40">
                    weekly avg {detail?.weeklyAvg.toFixed(1)} · monthly avg {detail?.monthlyAvg.toFixed(1)}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xl font-bold text-white">{detail?.current.toFixed(1)}</p>
                  <p className={`text-xs font-medium ${overBaseline >= 0 ? "text-blue-300" : "text-white/50"}`}>
                    {overBaseline >= 0 ? "+" : ""}
                    {overBaseline.toFixed(1)} vs baseline
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
