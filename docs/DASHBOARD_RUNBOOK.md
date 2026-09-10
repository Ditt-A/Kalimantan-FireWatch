# Dashboard runbook

The dashboard is intentionally split into a Python API and a Next.js web app.
The browser never reads model weights or Google Drive directly.

## Local run

From the project root, install the API dependencies into the project virtual
environment, then run the serving API:

```bash
./.venv-wsl/bin/pip install -r api/requirements.txt
./.venv-wsl/bin/python -m uvicorn api.main:app --reload --port 8000
```

In another terminal:

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

If WSL does not yet resolve `node`, use the Linux Node runtime installed for
this workspace before the web commands:

```bash
export PATH=/home/adit/.local/node-v22.17.0-linux-x64/bin:/usr/local/bin:/usr/bin:/bin
```

Open `http://localhost:3000`. The FastAPI interactive contract is available at
`http://localhost:8000/docs`.

## Serving contract

- Feature 1 map data comes from validated `prediction_grid_week.parquet` and
  `firms_kalimantan_clean.parquet` snapshots.
- Feature 2 loads only `models/feature2_personnel_hgb_pilot.joblib` and the
  serving `feature2_fire_engine_policy.json`.
- The non-serving experimental vehicle model is deliberately never imported.
- Set `NEXT_PUBLIC_OSM_TILE_URL` to the deployment's self-hosted OSM raster
  endpoint. A public OpenStreetMap raster fallback makes local development
  immediately visible, but production traffic must use the self-hosted tile
  service.

## Non-dispatch guardrail

Every response endpoint returns `automatic_dispatch_permitted: false`. The UI
must retain the field-verification and availability checks before any operator
uses the result operationally.
