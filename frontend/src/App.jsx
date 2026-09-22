import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Dashboard from "./pages/Dashboard";

// Code-split: only loaded when the user actually navigates to this route.
const PricePrediction = lazy(() => import("./pages/PricePrediction"));

function PageFallback() {
  return (
    <div className="flex-1 p-4 md:p-8">
      <div className="glass h-64 rounded-xl bg-white/5 animate-pulse" />
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col md:flex-row">
        <Sidebar />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route
            path="/priceprediction"
            element={
              <Suspense fallback={<PageFallback />}>
                <PricePrediction />
              </Suspense>
            }
          />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;
