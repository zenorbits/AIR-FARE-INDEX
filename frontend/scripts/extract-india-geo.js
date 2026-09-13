// Regenerates src/data/indiaGeo.json — India's real coastline as GeoJSON,
// extracted from Natural Earth data via the `world-atlas` package.
//
// `world-atlas` isn't a runtime dependency (the extracted GeoJSON is
// committed directly), so run this with world-atlas installed temporarily:
//   npm install --no-save world-atlas topojson-client
//   node scripts/extract-india-geo.js
const fs = require("fs");
const path = require("path");
const topojson = require("topojson-client");
const world = require("world-atlas/countries-50m.json");

const geo = topojson.feature(world, world.objects.countries);
const india = geo.features.find((f) => f.id === "356"); // ISO 3166-1 numeric: India

if (!india) {
  throw new Error("India feature not found in world-atlas topology");
}

const out = { type: "FeatureCollection", features: [india] };
const outPath = path.join(__dirname, "..", "src", "data", "indiaGeo.json");
fs.writeFileSync(outPath, JSON.stringify(out));
console.log(`Wrote ${outPath} (${fs.statSync(outPath).size} bytes)`);
