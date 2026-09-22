const NAV_ITEMS = [{ key: "dashboard", label: "Dashboard", icon: "📊" }];

export default function Sidebar({ active = "dashboard" }) {
  return (
    <aside className="glass w-full md:w-60 md:min-h-screen md:sticky md:top-0 flex md:flex-col shrink-0 p-4 md:p-6 gap-4 md:gap-8 rounded-none md:rounded-r-2xl border-t-0 md:border-t border-l-0">
      <div className="flex items-center gap-2 md:mb-4">
        <div className="h-9 w-9 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center text-lg">
          ✈️
        </div>
        <div>
          <p className="font-semibold text-white leading-tight">Airfare Index</p>
          <p className="text-xs text-white/50 leading-tight">APIX Dashboard</p>
        </div>
      </div>

      <nav className="flex md:flex-col gap-2">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-medium transition-colors w-full text-left ${
              active === item.key
                ? "bg-white/15 text-white shadow-inner border border-white/20"
                : "text-white/60 hover:bg-white/8 hover:text-white/90"
            }`}
          >
            <span aria-hidden="true">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="hidden md:block mt-auto text-xs text-white/40">
        <p>Data updates every 24h.</p>
        <p className="mt-1">v0.1.0</p>
      </div>
    </aside>
  );
}
