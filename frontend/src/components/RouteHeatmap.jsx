import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, Polyline, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { getHeatmapRoutes } from "../api/client";

// Real basemap tiles instead of a hand-fitted GeoJSON silhouette + custom
// projection — this renders India's actual coastline/borders correctly at
// any zoom/pan, with no projection math to get wrong.
//
// CARTO's basemap tiles (previously used here) now require a free account
// + API key -- their old anonymous basemaps.cartocdn.com URLs return an
// "API KEY REQUIRED" watermark as of their newer pricing/access policy.
// Esri's World Dark Gray Base is used instead: no signup or key required.
// Note the {z}/{y}/{x} order -- Esri's ArcGIS REST tile services use that
// order, not the {z}/{x}/{y} most other providers (including CARTO) use.
const TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const TILE_ATTRIBUTION =
  "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, © OpenStreetMap contributors, and the GIS user community";

// Tight to India's own extent (not the wider South/Southeast Asia region) so
// the initial view and pan/zoom limits stay focused on India.
const INDIA_BOUNDS = [
  [6, 68],
  [36, 98],
];

// Sequential single-hue (blue) ramp — magnitude reads as one hue from
// dim to bright, never a rainbow. Low intensity recedes into the dark
// basemap; high intensity pops bright blue.
const SEQUENTIAL_BLUE = [
  "#0d366b", // recedes into the dark basemap (low demand)
  "#184f95",
  "#1c5cab",
  "#256abf",
  "#2a78d6",
  "#3987e5",
  "#5598e7",
  "#6da7ec",
  "#86b6ef", // pops against the dark basemap (high demand)
];

function lerpColor(a, b, t) {
  const ah = parseInt(a.slice(1), 16);
  const bh = parseInt(b.slice(1), 16);
  const ar = (ah >> 16) & 0xff,
    ag = (ah >> 8) & 0xff,
    ab = ah & 0xff;
  const br = (bh >> 16) & 0xff,
    bg = (bh >> 8) & 0xff,
    bb = bh & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

function intensityColor(intensity) {
  const clamped = Math.min(1, Math.max(0, intensity));
  const steps = SEQUENTIAL_BLUE.length - 1;
  const pos = clamped * steps;
  const i = Math.min(steps - 1, Math.floor(pos));
  return lerpColor(SEQUENTIAL_BLUE[i], SEQUENTIAL_BLUE[i + 1], pos - i);
}

// A gentle quadratic-bezier arc between two [lat, lng] points, bulging
// north, sampled into a polyline (Leaflet draws straight segments between
// positions, so a real curve has to be pre-sampled like this).
function arcPoints([lat1, lng1], [lat2, lng2], segments = 24) {
  const distance = Math.hypot(lat2 - lat1, lng2 - lng1);
  const bulge = Math.max(0.6, distance * 0.22);
  const controlLat = (lat1 + lat2) / 2 + bulge;
  const controlLng = (lng1 + lng2) / 2;

  const points = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const inv = 1 - t;
    const lat = inv * inv * lat1 + 2 * inv * t * controlLat + t * t * lat2;
    const lng = inv * inv * lng1 + 2 * inv * t * controlLng + t * t * lng2;
    points.push([lat, lng]);
  }
  return points;
}

export default function RouteHeatmap({ leadTimeDays = 30 }) {
  const [routes, setRoutes] = useState([]);
  const [cities, setCities] = useState({});
  const [hovered, setHovered] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getHeatmapRoutes(leadTimeDays).then(({ routes, cities }) => {
      setRoutes(routes);
      setCities(cities);
      setLoading(false);
    });
  }, [leadTimeDays]);

  // Leaflet wants [lat, lng]; CITY_COORDS stores [lng, lat] (GeoJSON order).
  const latLngByCode = useMemo(() => {
    const map = {};
    Object.entries(cities).forEach(([code, c]) => {
      map[code] = [c.coordinates[1], c.coordinates[0]];
    });
    return map;
  }, [cities]);

  return (
    <section className="glass p-5 md:p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-semibold text-lg">India Route Heatmap</h2>
        <div className="flex items-center gap-2 text-xs text-white/45">
          <span
            className="inline-block h-2 w-16 rounded-full"
            style={{ background: `linear-gradient(90deg, ${SEQUENTIAL_BLUE[0]}, ${SEQUENTIAL_BLUE[SEQUENTIAL_BLUE.length - 1]})` }}
          />
          low → high demand
        </div>
      </div>

      {loading ? (
        <div className="h-[420px] rounded-xl bg-white/5 animate-pulse" />
      ) : (
        <div className="relative">
          <MapContainer
            bounds={INDIA_BOUNDS}
            boundsOptions={{ padding: [24, 24] }}
            minZoom={4}
            maxZoom={8}
            maxBounds={INDIA_BOUNDS}
            maxBoundsViscosity={1.0}
            scrollWheelZoom={false}
            className="h-[420px] w-full rounded-xl border border-white/10"
            style={{ background: "#060810" }}
          >
            <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />

            {routes.map((r) => {
              const from = latLngByCode[r.from];
              const to = latLngByCode[r.to];
              if (!from || !to) return null;

              const key = `${r.from}-${r.to}`;
              const isHovered = hovered === key;

              return (
                <Polyline
                  key={key}
                  positions={arcPoints(from, to)}
                  pathOptions={{
                    color: intensityColor(r.intensity),
                    weight: 1.5 + r.intensity * 6,
                    opacity: isHovered ? 1 : 0.7 + r.intensity * 0.25,
                    lineCap: "round",
                  }}
                  eventHandlers={{
                    mouseover: () => setHovered(key),
                    mouseout: () => setHovered(null),
                  }}
                />
              );
            })}

            {Object.entries(cities).map(([code, c]) => {
              const pos = latLngByCode[code];
              if (!pos) return null;

              return (
                <CircleMarker
                  key={code}
                  center={pos}
                  radius={5}
                  pathOptions={{ color: "#93c5fd", weight: 1.5, fillColor: "#060810", fillOpacity: 1 }}
                >
                  <Tooltip direction="top" offset={[0, -6]} opacity={1}>
                    {c.name} ({code})
                  </Tooltip>
                </CircleMarker>
              );
            })}
          </MapContainer>

          {hovered && (
            <div className="absolute top-2 right-2 glass px-3 py-2 rounded-lg text-xs text-white/85 border border-blue-400/30 pointer-events-none z-[1000]">
              {(() => {
                const r = routes.find((r) => `${r.from}-${r.to}` === hovered);
                if (!r) return null;
                return (
                  <>
                    <p className="font-semibold">
                      {cities[r.from]?.name ?? r.from} → {cities[r.to]?.name ?? r.to}
                    </p>
                    <p className="text-white/50">Index: {r.index.toFixed(1)}</p>
                  </>
                );
              })()}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
