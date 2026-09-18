import { useEffect, useState } from "react";
import { isUsingMockData, subscribeBackendStatus } from "../api/client";

// Every api/client.js function silently fell back to mockData.js on any
// fetch failure (missing/wrong API key, backend down, network error) with
// only a console.warn -- a dead backend looked exactly like live data. This
// banner subscribes to that same fallback signal and surfaces it visibly.
export default function BackendStatusBanner() {
  const [showing, setShowing] = useState(isUsingMockData());

  useEffect(() => subscribeBackendStatus(setShowing), []);

  if (!showing) return null;

  return (
    <div className="rounded-xl border border-amber-400/40 bg-amber-400/10 px-4 py-2.5 text-sm text-amber-200 flex items-center gap-2">
      <span aria-hidden="true">⚠️</span>
      <span>
        Showing sample data — the backend is unreachable (check it's running, and that{" "}
        <code className="text-amber-100">VITE_API_KEY</code> matches the backend's{" "}
        <code className="text-amber-100">API_KEY</code>).
      </span>
    </div>
  );
}
