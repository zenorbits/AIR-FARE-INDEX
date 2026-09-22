import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { getBacktestComparison } from "../api/client";

// APIx validated against the official CPI Airfare index. Route-level DGCA
// average-fare data isn't publicly available, so CPI is the only benchmark
// (see Backend/backtest/validate.py) -- not a gap, a documented design choice.
export default function BacktestPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getBacktestComparison()
      .then(setData)
      .catch((err) => {
        console.warn("[BacktestPanel] backtest endpoint failed", err);
        setData(null);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <section className="glass p-5 md:p-6">
      <h2 className="text-white font-semibold text-lg mb-1">Backtest: APIx vs CPI</h2>
      <p className="text-white/50 text-xs mb-4">
        {data?.benchmark ?? "MoSPI CPI Airfare item index (COICOP 07.3.3.1.2.01, base year 2024)"}
      </p>

      {loading ? (
        <div className="h-56 rounded-xl bg-white/5 animate-pulse" />
      ) : !data || data.points.length === 0 ? (
        <p className="text-white/50 text-sm py-8 text-center">
          {data?.note ?? "Not enough overlapping monthly periods yet — check back after a few more months of data."}
        </p>
      ) : (
        <>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.points} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                <XAxis dataKey="period_start" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
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
                <Line type="monotone" dataKey="cpi_index" name="CPI" stroke="#a3a3a3" strokeDasharray="6 4" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="apix_index" name="APIx" stroke="#ffffff" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {data.correlation != null && (
            <div className="flex gap-6 mt-3 text-xs text-white/60">
              <span>Correlation: <strong className="text-white">{data.correlation.toFixed(3)}</strong></span>
              <span>Mean abs. deviation: <strong className="text-white">{data.mean_absolute_deviation.toFixed(2)}</strong></span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
