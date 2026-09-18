import { useState } from "react";
import TopRoutesCard from "../components/TopRoutesCard";
import ApixPanel from "../components/ApixPanel";
import TrendChart from "../components/TrendChart";
import RouteHeatmap from "../components/RouteHeatmap";
import AssistantPanel from "../components/AssistantPanel";

export default function Dashboard() {
  const [selectedRoute, setSelectedRoute] = useState(null);

  return (
    <div className="flex-1 p-4 md:p-8 space-y-6">
      <header className="mb-2">
        <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">Dashboard</h1>
        <p className="text-white/50 text-sm mt-1">
          Live overview of the Airfare Price Index (APIX) across top domestic routes.
        </p>
      </header>

      <TopRoutesCard selectedRoute={selectedRoute} onSelectRoute={setSelectedRoute} />

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        <div className="xl:col-span-3">
          <TrendChart selectedRoute={selectedRoute} onSelectRoute={setSelectedRoute} />
        </div>
        <div className="xl:col-span-2">
          <ApixPanel />
        </div>
      </div>

      <AssistantPanel selectedRoute={selectedRoute} />

      <RouteHeatmap />
    </div>
  );
}
