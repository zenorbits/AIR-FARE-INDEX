import { useEffect, useMemo, useState } from "react";
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
import { getRoutesList, predictPrice, predictPriceCurve } from "../api/client";
import { cityName } from "../data/mockData";

// Mirrors Backend/ml/features.py's AIRLINE_CODE_MAP -- the backend has no
// /airlines endpoint, so the option list is kept in sync here by hand.
const AIRLINES = [
  { code: "6E", name: "IndiGo" },
  { code: "AI", name: "Air India" },
  { code: "IX", name: "Air India Express" },
  { code: "QP", name: "Akasa Air" },
  { code: "9I", name: "Alliance Air" },
  { code: "SG", name: "SpiceJet" },
  { code: "S5", name: "Star Air" },
];

// Mirrors Backend/api/main.py's TRAINED_LEAD_TIMES -- the model has no
// observations beyond this, so predictions are clamped here the same way
// predict_price_curve() clamps them server-side, keeping the point
// prediction and the curve's "book now" point describing the same flight.
const MAX_TRAINED_LEAD_TIME = 45;

function defaultDepartureDate() {
  const d = new Date();
  d.setDate(d.getDate() + 21);
  return d.toISOString().slice(0, 10);
}

// JS getDay() is 0=Sunday..6=Saturday; the backend wants 0=Monday..6=Sunday.
function isoWeekday(date) {
  return (date.getDay() + 6) % 7;
}

export default function PricePrediction() {
  const [routes, setRoutes] = useState([]);
  const [routesError, setRoutesError] = useState(null);
  const [form, setForm] = useState({
    origin: "",
    destination: "",
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
        const [firstOrigin, firstDestination] = (r[0] ?? "").split("-");
        setForm((f) => ({
          ...f,
          origin: f.origin || firstOrigin || "",
          destination: f.destination || firstDestination || "",
        }));
      })
      .catch((err) => {
        console.warn("[PricePrediction] could not load routes", err);
        setRoutesError("Could not load routes from the backend.");
      });
  }, []);

  const origins = useMemo(
    () => [...new Set(routes.map((r) => r.split("-")[0]))].sort(),
    [routes],
  );

  const destinationsForOrigin = useMemo(
    () =>
      routes
        .filter((r) => r.startsWith(`${form.origin}-`))
        .map((r) => r.split("-")[1])
        .sort(),
    [routes, form.origin],
  );

  function handleOriginChange(origin) {
    setForm((f) => {
      const validDestinations = routes
        .filter((r) => r.startsWith(`${origin}-`))
        .map((r) => r.split("-")[1]);
      const destination = validDestinations.includes(f.destination)
        ? f.destination
        : validDestinations[0] ?? "";
      return { ...f, origin, destination };
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.origin || !form.destination) return;

    setLoading(true);
    setError(null);
    try {
      const route = `${form.origin}-${form.destination}`;
      const departureDate = new Date(form.departureDate);
      const daysUntilDeparture = Math.round((departureDate - new Date().setHours(0, 0, 0, 0)) / 86_400_000);
      const leadTimeDays = Math.max(0, Math.min(daysUntilDeparture, MAX_TRAINED_LEAD_TIME));

      const [point, curve] = await Promise.all([
        predictPrice({
          route,
          airline: form.airline,
          cabin_class: form.cabinClass,
          lead_time_days: leadTimeDays,
          stops: form.stops,
          departure_hour: form.departureHour,
          departure_day_of_week: isoWeekday(departureDate),
          departure_month: departureDate.getMonth() + 1,
        }),
        predictPriceCurve({
          route,
          departureDate: form.departureDate,
          departureHour: form.departureHour,
          airline: form.airline,
          cabinClass: form.cabinClass,
          stops: form.stops,
        }),
      ]);
      setResult({ point, curve, leadTimeDays });
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const chartData = result?.curve.curve.map((p) => ({
    lead_time_days: p.lead_time_days,
    book_by_date: p.book_by_date,
    predicted_fare: Math.round(p.predicted_fare),
  }));

  return (
    <div className="flex-1 p-4 md:p-8 space-y-6">
      <header className="mb-2">
        <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">Price Prediction</h1>
        <p className="text-white/50 text-sm mt-1">
          Predict a flight's fare and see how it's expected to move as departure approaches.
        </p>
      </header>

      <section className="glass p-5 md:p-6">
        {routesError && <p className="text-blue-300 text-sm mb-3">{routesError}</p>}

        <form onSubmit={handleSubmit} className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
          <label className="flex flex-col gap-1 text-xs text-white/50">
            Origin
            <select
              value={form.origin}
              onChange={(e) => handleOriginChange(e.target.value)}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            >
              {origins.map((code) => (
                <option key={code} value={code} className="bg-neutral-900">
                  {cityName(code)} ({code})
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Destination
            <select
              value={form.destination}
              onChange={(e) => setForm({ ...form, destination: e.target.value })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            >
              {destinationsForOrigin.map((code) => (
                <option key={code} value={code} className="bg-neutral-900">
                  {cityName(code)} ({code})
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Airline
            <select
              value={form.airline}
              onChange={(e) => setForm({ ...form, airline: e.target.value })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            >
              {AIRLINES.map((a) => (
                <option key={a.code} value={a.code} className="bg-neutral-900">
                  {a.name} ({a.code})
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Cabin class
            <select
              value={form.cabinClass}
              onChange={(e) => setForm({ ...form, cabinClass: e.target.value })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            >
              <option value="ECONOMY" className="bg-neutral-900">Economy</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Stops
            <input
              type="number"
              min={0}
              max={5}
              value={form.stops}
              onChange={(e) => setForm({ ...form, stops: Number(e.target.value) })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Departure date
            <input
              type="date"
              value={form.departureDate}
              onChange={(e) => setForm({ ...form, departureDate: e.target.value })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-white/50">
            Departure hour
            <input
              type="number"
              min={0}
              max={23}
              value={form.departureHour}
              onChange={(e) => setForm({ ...form, departureHour: Number(e.target.value) })}
              className="bg-white/10 border border-white/15 text-white text-sm rounded-lg px-2 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400/60"
            />
          </label>

          <button
            type="submit"
            disabled={loading || !form.origin || !form.destination}
            className="col-span-2 md:col-span-4 xl:col-span-7 bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2.5 transition-colors"
          >
            {loading ? "Predicting…" : "Predict fare"}
          </button>
        </form>
      </section>

      {loading && (
        <section className="glass p-5 md:p-6 space-y-4">
          <div className="h-16 rounded-xl bg-white/5 animate-pulse" />
          <div className="h-64 rounded-xl bg-white/5 animate-pulse" />
        </section>
      )}

      {error && !loading && (
        <section className="glass p-5 md:p-6">
          <p className="text-blue-300 text-sm">{error}</p>
        </section>
      )}

      {result && !loading && !error && (
        <section className="glass p-5 md:p-6">
          <div className="text-center py-4 border-b border-white/10 mb-5">
            <p className="text-sm text-white/50">
              Predicted fare — {cityName(form.origin)} → {cityName(form.destination)}
            </p>
            <p className="text-5xl font-bold text-blue-300 mt-2 tracking-tight">
              ₹{Math.round(result.point.predicted_fare).toLocaleString("en-IN")}
            </p>
            <p className="text-xs text-white/40 mt-2">
              {AIRLINES.find((a) => a.code === form.airline)?.name ?? form.airline} · Economy ·{" "}
              {form.stops === 0 ? "Nonstop" : `${form.stops} stop(s)`} · booking {result.leadTimeDays} days before
              departure
            </p>
          </div>

          <h3 className="text-white/80 text-sm font-medium mb-2">Fare vs. booking lead time</h3>
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
                    background: "rgba(6, 8, 16, 0.95)",
                    border: "1px solid rgba(59, 130, 246, 0.3)",
                    borderRadius: "0.75rem",
                    color: "#fff",
                  }}
                  labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                  formatter={(value) => [`₹${value}`, "Predicted fare"]}
                  labelFormatter={(v) => `${v} days before departure`}
                />
                <Line type="monotone" dataKey="predicted_fare" stroke="#3b82f6" strokeWidth={2.5} dot={{ r: 3 }} />
                {/* Your predicted flight, highlighted on the curve */}
                <ReferenceDot
                  x={result.leadTimeDays}
                  y={Math.round(result.point.predicted_fare)}
                  r={7}
                  fill="#3b82f6"
                  stroke="#fff"
                  strokeWidth={2}
                />
                {/* Cheapest point on the curve, if different from your prediction */}
                {result.curve.cheapest_lead_time_days !== result.leadTimeDays && (
                  <ReferenceDot
                    x={result.curve.cheapest_lead_time_days}
                    y={Math.round(result.curve.cheapest_fare)}
                    r={6}
                    fill="#060810"
                    stroke="#60a5fa"
                    strokeWidth={2}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="rounded-xl bg-white/5 border border-white/10 px-4 py-3 text-sm text-white/80">
            <p className="font-medium text-white">{result.curve.recommendation}</p>
            {result.curve.extrapolation_note && (
              <p className="text-white/60 text-xs mt-1.5 italic">{result.curve.extrapolation_note}</p>
            )}
            {result.curve.confidence_note && (
              <p className="text-white/40 text-xs mt-1.5">{result.curve.confidence_note}</p>
            )}
            {result.point.model_trained_at && (
              <p className="text-white/30 text-[11px] mt-2">
                Model trained: {new Date(result.point.model_trained_at).toLocaleString()}
              </p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
