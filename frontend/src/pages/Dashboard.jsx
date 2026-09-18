import { lazy, Suspense, useState } from "react";
import TopRoutesCard from "../components/TopRoutesCard";
import ApixPanel from "../components/ApixPanel";
import TrendChart from "../components/TrendChart";
import RouteHeatmap from "../components/RouteHeatmap";
import AssistantPanel from "../components/AssistantPanel";
import BackendStatusBanner from "../components/BackendStatusBanner";
import { AVAILABLE_LEAD_TIMES } from "../api/client";

// Below-the-fold panels: code-split so their (and only their) code loads
// after the above-the-fold dashboard has rendered.
const FareDecomposition = lazy(() => import("../components/FareDecomposition"));
const PricePrediction = lazy(() => import("../components/PricePrediction"));
const BacktestPanel = lazy(() => import("../components/BacktestPanel"));

function PanelFallback() {
  return <div className="glass p-5 md:p-6 h-64 rounded-xl bg-white/5 animate-pulse" />;
}

export default function Dashboard() {
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [leadTimeDays, setLeadTimeDays] = useState(30);

  return (
    <div className="flex-1 p-4 md:p-8 space-y-6">
      <BackendStatusBanner />

      <header className="mb-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-white/50 text-sm mt-1">
            Live overview of the Airfare Price Index (APIX) across top domestic routes.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-white/50">
          Lead time
          <select
            value={leadTimeDays}
            onChange={(e) => setLeadTimeDays(Number(e.target.value))}
            className="bg-white/10 border border-white/15 text-white text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          >
            {AVAILABLE_LEAD_TIMES.map((lt) => (
              <option key={lt} value={lt} className="bg-slate-800">
                T-{lt}d
              </option>
            ))}
          </select>
        </label>
      </header>

      <TopRoutesCard selectedRoute={selectedRoute} onSelectRoute={setSelectedRoute} leadTimeDays={leadTimeDays} />

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        <div className="xl:col-span-3">
          <TrendChart selectedRoute={selectedRoute} onSelectRoute={setSelectedRoute} leadTimeDays={leadTimeDays} />
        </div>
        <div className="xl:col-span-2">
          <ApixPanel leadTimeDays={leadTimeDays} />
        </div>
      </div>

      <AssistantPanel selectedRoute={selectedRoute} />

      <RouteHeatmap leadTimeDays={leadTimeDays} />

      <Suspense fallback={<PanelFallback />}>
        <FareDecomposition />
      </Suspense>

      <Suspense fallback={<PanelFallback />}>
        <PricePrediction />
      </Suspense>

      <Suspense fallback={<PanelFallback />}>
        <BacktestPanel />
      </Suspense>
    </div>
  );
}
