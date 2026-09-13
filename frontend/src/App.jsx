import Sidebar from "./components/Sidebar";
import Dashboard from "./pages/Dashboard";

function App() {
  return (
    <div className="min-h-screen flex flex-col md:flex-row">
      <Sidebar active="dashboard" />
      <Dashboard />
    </div>
  );
}

export default App;
