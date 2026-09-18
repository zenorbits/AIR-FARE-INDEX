import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceDot,
} from "recharts";
import { getRoutesList, predictPriceCurve } from "../api/client";

function defaultDepartureDate() {
  const d = new Date();
  d.setDate(d.getDate() + 21);
  return d.toISOString().slice(0, 10);
}

// Price prediction UI + lead-time elasticity curve in one panel: the backend's
// /predict-price/curve endpoint already returns "predicted fare at every
// trained lead time for this flight", which IS the elasticity curve -- one
// form, one request, both features.
export default function PricePrediction() {
  const [routes, setRoutes] = useState([]);
  const [form, setForm] = useState({
    route: "",
    airline: "6E",
    cabinClass: "ECONOMY",
    stops: 0,
    departureDate: defaultDepartureDate(),
    departureHour: 9,
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getRoutesList()
      .then((r) => {
        setRoutes(r);
        setForm((f) => ({ ...f, route: f.route || r[0] || "" }));
      })
      .catch((err) => console.warn("[PricePrediction] could not load routes", err));
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const curve = await predictPriceCurve(form);
      setResult(curve);
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const chartData = result?.curve.map((p) => ({
    lead_time_days: p.lead_time_days,
    book_by_date: p.book_by_date,
    predicted_fare: Math.round(p.predicted_fare),
  }));

  return (
    <section className="glass p-5 md:p-6">
      <h2 className="text-white font-semibold text-lg mb-1">Price Prediction &amp; Booking Timing</h2>
      <p className="text-white/50 text-xs mb-4">
        Predicted fare at each trained lead time for one flight — the elasticity curve shows whether
        booking now or waiting is predicted to be cheaper.
      </p>

      <form onSubmit={handleSubmit} className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4">
        <select
          value={form.route}
          onChange={(e) => setForm({ ...form, route: e.target.value })}
          className="col-span-2 bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5"
        >
          {routes.map((r) => (
            <option key={r} value={r} className="bg-slate-800">
              {r}
            </option>
          ))}
        </select>
        <input
          value={form.airline}
          onChange={(e) => setForm({ ...form, airline: e.target.value })}
          placeholder="Airline (e.g. 6E)"
          className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5 placeholder:text-white/30"
        />
        <input
          type="date"
          value={form.departureDate}
          onChange={(e) => setForm({ ...form, departureDate: e.target.value })}
          className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5"
        />
        <input
          type="number"
          min={0}
          max={23}
          value={form.departureHour}
          onChange={(e) => setForm({ ...form, departureHour: Number(e.target.value) })}
          placeholder="Hour"
          className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5"
        />
        <button
          type="submit"
          disabled={loading || !form.route}
          className="bg-indigo-500/80 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-medium rounded-lg px-3 py-1.5 transition-colors"
        >
          {loading ? "Predicting…" : "Predict"}
        </button>
      </form>

      {error && (
        <p className="text-rose-300 text-xs mb-3 bg-rose-500/10 border border-rose-400/30 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {result && (
        <>
          <div className="h-56 mb-3">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                <XAxis
                  dataKey="lead_time_days"
                  tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  label={{ value: "Days before departure", position: "insideBottom", offset: -2, fill: "rgba(255,255,255,0.4)", fontSize: 11 }}
                />
                <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{
                    background: "rgba(17, 24, 39, 0.9)",
                    border: "1px solid rgba(255,255,255,0.15)",
                    borderRadius: "0.75rem",
                    color: "#fff",
                  }}
                  labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                  formatter={(value) => [`₹${value}`, "Predicted fare"]}
                  labelFormatter={(v) => `${v} days before departure`}
                />
                <Line type="monotone" dataKey="predicted_fare" stroke="#4ade80" strokeWidth={2.5} dot={{ r: 3 }} />
                <ReferenceDot
                  x={result.cheapest_lead_time_days}
                  y={Math.round(result.cheapest_fare)}
                  r={6}
                  fill="#fbbf24"
                  stroke="none"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="rounded-xl bg-white/5 border border-white/10 px-4 py-3 text-sm text-white/80">
            <p className="font-medium text-white">{result.recommendation}</p>
            {result.extrapolation_note && <p className="text-amber-300/80 text-xs mt-1.5">{result.extrapolation_note}</p>}
            {result.confidence_note && <p className="text-white/40 text-xs mt-1.5">{result.confidence_note}</p>}
            {result.model_trained_at && (
              <p className="text-white/30 text-[11px] mt-2">Model trained: {new Date(result.model_trained_at).toLocaleString()}</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
