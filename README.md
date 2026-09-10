# Kalimantan FireWatch

Kalimantan FireWatch is a decision-support dashboard for land and peat-fire monitoring across the five Indonesian Kalimantan provinces. It combines satellite detections, historical fire context, peat and water-access mapping, a fire-weather calculation, local Indonesian response evidence, and trained planning models.

It supports an operator's decision process; it does not automatically dispatch personnel or vehicles.

## What is included

### 1. AI Incident Prioritization & Localization

- NASA FIRMS VIIRS active-fire detections for Kalimantan;
- weekly hotspot-priority grid with predicted occurrence probability and expected hotspot count;
- BIG peat context and SoilGrids mineral-reference context;
- configurable nearby-detection grouping using a minimum sensor-footprint threshold and kilometre merge radius;
- OSM fire-station landmarks and road/helicopter-reference routing.

### 2. AI Response Planning & Resource Calculator

- personnel and fire-engine-capacity planning output;
- local Indonesian incident-resource evidence, supplemented by ICS-209 benchmark context;
- peat, fire-weather FWI, surface-water access, fire-type observation, and spatial grid context;
- traceable source documents and rulebook for planning conditions.

## Repository contents

All dashboard-ready data, raw spatial/source data, trained model weights, and reports are versioned in this repository. Large files use **Git LFS**. Virtual environments, `node_modules`, caches, logs, and credentials are deliberately excluded because they are machine-specific or reproducible from the files below.

```text
api/                    FastAPI service used by the dashboard
web/                    Next.js / React dashboard
src/firewatch/          Data processing, modeling, FWI, soil, water, and planning code
scripts/                Acquisition, preprocessing, training, audit, and promotion scripts
data/raw/               Source spatial, satellite, weather, OSM, and evidence data (Git LFS)
data/interim/           Reusable feature-engineering outputs (Git LFS)
data/processed/         Dashboard-ready tables and spatial layers (Git LFS)
models/                 Serialized Feature 1 and Feature 2 model artifacts (Git LFS)
reports/                Audit, evaluation, and provenance reports
docs/                   PRD, methodology, data dictionary, and evidence documentation
secrets.example.txt     Credential placeholder only; never contains a real key
```

## Quick start

These steps run the current dashboard without downloading data again.

### Prerequisites

- Git 2.40+ and [Git LFS](https://git-lfs.com/)
- Python 3.10 or later
- Node.js 20 or later and npm
- On Windows, WSL2 is recommended for the geospatial Python dependencies.

### Clone and retrieve LFS data

```bash
git clone https://github.com/Ditt-A/DSA_Compe.git
cd DSA_Compe
git lfs install
git lfs pull
```

Do not skip `git lfs pull`; without it, large data/model files are only small pointer files.

### Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Install the web application

```bash
cd web
npm ci
cp .env.example .env.local                # Windows PowerShell: Copy-Item .env.example .env.local
cd ..
```

`web/.env.local` defaults to `http://localhost:8000/api/v1`; no API key is needed for the packaged dashboard snapshot.

### Run the dashboard

Open two terminals from the repository root.

Terminal 1 — API:

```bash
source .venv/bin/activate
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 — Next.js:

```bash
cd web
npm run dev -- --hostname 0.0.0.0 --port 3000
```

Open [http://localhost:3000/prioritization](http://localhost:3000/prioritization). The API health check is [http://localhost:8000/health](http://localhost:8000/health).

For a production-style local run, execute `npm run build` then `npm run start` in `web/`.

## Credentials for optional data refreshes

The included dashboard runs from the versioned snapshot. A key is needed only to acquire new NASA FIRMS or Copernicus ERA5 data.

```bash
cp secrets.example.txt secrets.local.txt
# Edit secrets.local.txt locally with your own values. It is ignored by Git.
```

Use the same local file with the acquisition scripts:

```bash
python scripts/scrape_firms.py --start 2026-09-01 --end 2026-09-03 --secrets-file secrets.local.txt
python scripts/scrape_era5.py --start 2026-09-01 --end 2026-09-03 --secrets-file secrets.local.txt
```

Never commit `secrets.local.txt`, `env.txt`, `.env.local`, `.cdsapirc`, API keys, or access tokens.

## Data and modeling notes

- A FIRMS point is an active-fire satellite detection, not a confirmed incident perimeter.
- The map's grouped area is the accumulated VIIRS sensor-footprint proxy (`scan × track`), used as an initial planner value and requiring field/perimeter verification.
- BIG peat context is retained when a forecast grid overlaps peat by at least 10%, even when its centroid is outside a peat polygon.
- Fire-weather context uses Open-Meteo weather with a local Canadian FWI calculation. It is a planning indicator, not an official warning product.
- Resource outputs cover personnel and fire-engine capacity. Fleet availability, water supply, access, perimeter, and command authority require operational confirmation.

## Validation and reproducibility

```bash
# Rebuild feature/data outputs when intentionally refreshing sources.
python scripts/build_dataset.py --refresh-features --force

# Retrain Feature 1 and Feature 2 artifacts.
python scripts/train_models.py

# Build the web dashboard for type checking and production output.
cd web && npm run build
```

The traceability material in `reports/`, source metadata in the processed tables, and the Evidence page identify the provenance of resource and spatial context used by the dashboard.

## Security

Before every push, audit staged files for credentials. This repository intentionally contains only `secrets.example.txt` and `web/.env.example` as placeholders.
