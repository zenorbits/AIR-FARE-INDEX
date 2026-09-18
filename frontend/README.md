# Airfare Index Dashboard — Frontend

React (Vite) + Tailwind dashboard for the Airfare Price Index (APIX).

## Run it

```bash
npm install
npm run dev
```

By default this runs against mock data (`src/data/mockData.js`) so the
dashboard is fully interactive without a backend.

## Connect to the real backend

1. Start the backend (see `../Backend/README.md`) — it listens on
   `http://localhost:8000` by default.
2. In this folder, copy the env template and fill in the API key so it
   matches the backend's `Backend/.env`:
   ```bash
   cp .env.example .env
   ```
3. Restart `npm run dev`. `src/api/client.js` now calls the real endpoints
   (`/index/current`, `/index/history`) instead of the mock data — if the
   backend is unreachable it falls back to mock data automatically and logs
   a warning to the console.

**"Ask about this index" (RAG assistant):** this calls `POST /assistant/ask`
(`Backend/api/assistant.py`), which also needs `ANTHROPIC_API_KEY` set in the
backend's own `.env` (see `Backend/.env.example`) — without it, that one
endpoint returns a 503 and the frontend falls back to a canned (non-AI)
mock answer, same as any other unreachable endpoint.

**Note:** the backend requires an `X-API-Key` header on every data endpoint.
`VITE_API_KEY` ships inside the browser bundle, which is fine for local
development but not for a public deployment — see the warning at the top of
`src/api/client.js` for what to do instead (an authenticating proxy, or a
separate public read-only endpoint).

## Regenerating the India map data

`src/data/indiaGeo.json` (India's coastline, used by the heatmap) is
extracted from `world-atlas`. To regenerate it, see
`scripts/extract-india-geo.js`.
