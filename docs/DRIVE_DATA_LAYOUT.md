# Kalimantan FireWatch — Drive data layout

This document describes the Google Drive layout only. Local project paths are
unchanged.

## Core source domains

- `nasa_firms/` — FIRMS fire detections and acquisition chunks.
- `mapbiomas/` — MapBiomas source rasters and manifests.
  - `burned_footprints/` — derived monthly Kalimantan burned-footprint layers.
- `era5_land/` — ERA5-Land weather inputs.
- `boundaries/` — ADM1/ADM2 and Kelurahan/Desa boundary layers.

## Environmental and spatial context

- `peatland/`
  - `wri/` — WRI peatland source layer.
  - `big_official/` — BIG official peat location/function data, split into
    `raw/`, `processed/`, and `reports/`.
- `water/`
  - `jrc_global_surface_water/` — JRC source rasters and manifest.
  - `access_index/` — derived Water Access Index and its quality report.
- `osm/`
  - `source/` — OSM PBF extract and provenance.
  - `operational/` — derived roads, waterways, waterbodies, and water points.
- `infrastructure/pupr_geoapi_kalimantan/` — PUPR GeoAPI data, split into
  `raw/`, `processed/`, and `reports/`.
- `concessions/`
  - `official/` — official PBPH/WIUP discoveries and spatial outputs.
  - `legacy_unverified/` — older mining layer retained separately from the
    authoritative collection.

## Operational and benchmark evidence

- `responders/` — fire-station, Manggala Agni, and baseline response data.
- `response_planning/` — implementation evidence and US resource benchmark.
  - `kalteng_historical_deployments/` — provenance-first local historical
    deployment corpus, split into `raw/`, `interim/`, `processed/`, and
    `documentation/`. Only page-reviewed, incident-specific HIGH mappings may
    become calibration candidates; the wider discovered-report corpus remains
    separate until reviewed.
- `figshare_all_hazards/` — original US all-hazards Figshare extracts.
- `metadata/` — EDA outputs and cross-source acquisition validation.
- `sentiment/` — social/sentiment input.
- `models/` — dashboard-backend artifacts only.
  - `feature1/` — active occurrence-classifier and hotspot-count `joblib` weights.
  - `feature2/` — personnel-estimator `joblib` weight and fire-engine capacity policy.
  - `dashboard_model_registry.json` — hashes, input schemas, runtime versions,
    output contracts, and serving status.

All source files remain distinct from processed outputs. Candidate water,
road, responder, and resource records require operational verification before
dispatch use.
