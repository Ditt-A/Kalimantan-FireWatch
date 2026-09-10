# Dashboard architecture — Kalimantan FireWatch MVP

## Decision

The product dashboard will use **Next.js**, not Streamlit. The existing Python
data pipeline and analytical modules remain the source of truth. A small
FastAPI service exposes validated, dashboard-ready data and executes spatial
lookups and response-planning logic.

```text
Google Drive (source archive)
  -> rclone sync + validation
  -> Python ETL/model outputs + serving tables
  -> FastAPI
  -> Next.js + MapLibre GL JS + self-hosted OSM tiles
```

The dashboard must not read Google Drive directly in a browser request. Drive
is the versioned source/archive; the deployed application reads local or
mounted validated serving outputs.

## Scope and guardrails

The MVP covers two decision-support workflows:

1. **AI Incident Prioritization & Localization Engine**: a map of forecast
   priority grids and satellite fire alerts, with transparent spatial context.
2. **AI Response Planning & Resource Calculator**: a planning reference for
   personnel and ground-response vehicles, with local historical evidence,
   rules, provenance, and confidence.

It is not a real-time emergency dispatch system. No person, vehicle, station,
water point, route, or resource availability is automatically assigned.

Deferred from MVP: live BMKG ingestion, population/building/critical-facility
layers, slope, staging locations, station availability, routable travel time,
water volume, pump/pressure calculations, and automatic dispatch.

## Technology

| Layer | MVP choice | Reason |
| --- | --- | --- |
| Web application | Next.js App Router + TypeScript | Product-grade routing, deployment, and server integration. |
| UI | Tailwind CSS + shadcn/ui | Fast, consistent operational interface. |
| Interactive map | MapLibre GL JS | Open-source mapping; no Mapbox key required. |
| Basemap | Self-hosted/configured OSM tiles | Does not depend on a commercial map key. |
| API | FastAPI | Keeps Python geospatial and model logic reusable. |
| Serving data | Parquet/GeoParquet, GeoJSON, PMTiles | Efficient, auditable artifacts derived from raw sources. |
| Data science | Existing Python modules under `src/firewatch/` | Avoids translating validated geospatial logic into a second language. |

FastAPI/PostGIS/Redis can be expanded later for multi-user operations and live
data. PostGIS is not required for the initial static/historical MVP.

## Application routes

| Route | Purpose |
| --- | --- |
| `/` | Snapshot overview, data freshness, and top-priority summary. |
| `/prioritization` | Feature 1: risk/alert map, filters, queue, and incident detail drawer. |
| `/response-planner` | Feature 2: scenario input, planning reference, evidence, and limitations. |
| `/evidence` | Data catalogue, quality results, source documents, rulebook, and coverage. |

## User flows

### Feature 1 — Prioritization and localization

```text
Filter place/date -> inspect priority grid or FIRMS alert -> open detail
-> inspect source-backed factors -> open Response Planner with location prefilled
```

The interface must distinguish:

- **Predicted priority area**: a spatial grid/week model output for monitoring.
- **Satellite fire alert**: a NASA FIRMS active-fire detection, not a confirmed
  independent wildfire event.

The prioritization map exposes forecast priority, FIRMS detections, BIG peat
context, Water Access Index, and administrative boundaries. OSM responder
locations are optional reference markers only; they do not establish station
capacity or vehicle availability.

The alert drawer shows the detection time, FIRMS confidence, FRP, coordinates,
province/district, peat lookup, water-access status, model output, data
snapshot time, and source links. Any visual grouping of detections uses a
parameterised spatial-temporal `alert_cluster_id` solely for presentation; all
raw FIRMS detections remain traceable.

### Feature 2 — Response planning

```text
Selected alert or manual coordinate + operator scenario inputs
-> peat/water/context lookup + local-case retrieval + rule evaluation
-> personnel/vehicle planning reference + confidence + evidence
```

Required inputs are coordinate and operator-estimated incident-size scenario.
Optional inputs include field-confirmed peat, operator weather assessment, and
known water-source information. Until BMKG live data exists, weather must be
labelled as operator supplied or cached historical context, never as live BMKG.

Output cards show:

- personnel empirical reference range;
- ground-response vehicle reference only when comparable local evidence exists;
- optional source-recorded breakdown: pickup, water tanker, R4, R2;
- confidence and evidence-match count;
- applied rules, field-verification requirements, document/page links, and
  explicit non-dispatch disclaimer.

The generic label is **ground-response vehicles**, not “fire trucks”. The latter
may be used only when a source identifies a tanker/fire-engine type.

## API contract

Initial FastAPI endpoints:

```text
GET  /api/v1/metadata/status
GET  /api/v1/priority/grid?date=&province=&district=
GET  /api/v1/alerts?date=&province=&district=&confidence=
GET  /api/v1/alerts/{alert_id}
POST /api/v1/response-plans
GET  /api/v1/evidence/cases
GET  /api/v1/evidence/rules
GET  /api/v1/evidence/sources/{source_document_id}
```

`POST /api/v1/response-plans` returns inputs, data/version snapshot, spatial
context, reference range, evidence cases, rule IDs, constraints, and
confidence. It must return an explicit `automatic_dispatch_permitted: false`.

## Serving-data contracts

Build these artifacts from validated project data:

| Artifact | Minimum content |
| --- | --- |
| `priority_grid_week.parquet` | Grid/week prediction, categories, model version, snapshot date. |
| `incident_alert_candidates.parquet` | Raw FIRMS traceability, optional display cluster, spatial/temporal fields, and administrative labels. |
| `incident_spatial_context.parquet` | Peat and water lookup values/statuses associated with alerts. |
| `response_evidence_cases.parquet` | Page-verified Kalteng cases, labels, confidence, and source provenance. |
| `response_rules.parquet` | Rule ID, condition, outcome, source/evidence, version, and guardrail. |
| `data_catalog.parquet` | Dataset source, coverage, snapshot, quality status, and known limitations. |

Use PMTiles or simplified vector tiles for large static polygon layers such as
peatland. Serve filtered alert/grid geometry over the API. Raw PDFs remain
available through controlled source links, not map payloads.

## Python and web structure

```text
web/                         Next.js application
  app/
    prioritization/
    response-planner/
    evidence/
  components/
  lib/api.ts

api/                         FastAPI application
  main.py
  routers/
    priority.py
    incidents.py
    response_planning.py
    evidence.py
    metadata.py

src/firewatch/
  dashboard_data.py
  incident_prioritization.py
  response_planning.py
  evidence.py
  quality.py
```

## Implementation order

1. Create serving-table build step and API data contracts.
2. Scaffold Next.js, MapLibre, self-hosted OSM configuration, and data-status
   header.
3. Implement prioritization map, filters, alert drawer, and detail API.
4. Implement response-planner form and FastAPI calculation endpoint.
5. Implement evidence/source views and downloadable scenario summary.
6. Add API, data, and UI regression tests; deploy only validated artifacts.

## MVP acceptance criteria

- A user can select an alert or priority grid and see its inputs, source, and
  snapshot timestamp.
- The selected alert can prefill the response-planner location.
- A plan is reproducible from versioned inputs/rules and contains provenance.
- No response result claims an automatic dispatch or confirms a water source.
- Coordinates outside Indonesian Kalimantan, empty source tables, and evidence
  records without document provenance are rejected before display.
- The UI never calls a snapshot “live” unless an actual refresh timestamp and
  ingestion status support that claim.
