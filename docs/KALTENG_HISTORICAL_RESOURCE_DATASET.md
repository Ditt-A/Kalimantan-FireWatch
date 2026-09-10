# Kalteng Historical Karhutla Resource Deployment Dataset

## Purpose

This is a provenance-first local calibration corpus for the Response Planning &
Resource Calculator. A row records **resources deployed in a documented
operation**, not a national minimum standard or an automatic dispatch order.

The current reviewed expansion has **74 documented Kalteng incidents** from
2018–2019 daily Posko Karhutla reports. **50 HIGH-confidence incidents** have
both an explicit numeric personnel total and an explicit numeric ground-response
vehicle total in that incident's own resource block. It is a defensible floor
for a small, shrinkage/case-based calibration experiment, not for a standalone
predictive dispatch model or an operational standard.

## Files

- `data/raw/kalteng_historical_deployments/kalteng_karhutla_resource_deployment_initial.xlsx` is the untouched submitted extraction workbook.
- `data/raw/kalteng_historical_deployments/documents/` contains the retained official PPID PDF copies.
- `data/raw/kalteng_historical_deployments/source_documents_manifest.json` records retrieval URLs, SHA-256 hashes, byte counts, and page counts for the five source PDFs behind the reviewed table.
- `data/interim/kalteng_historical_deployments/page_verified_citations.csv` adds human-verified page references and only those WGS84 coordinates explicitly present in the reports.
- `data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment.parquet` is the reviewed local table.
- `data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment_enriched.parquet` retains the same observations and adds source-separated context fields.
- `data/raw/kalteng_historical_deployments/acquisition_manifest.json` and `expansion_acquisition_manifest.json` retain the official discovery/download records used by the expansion.
- `data/raw/kalteng_historical_deployments/documents/` and `expansion_documents/` retain the downloaded official PPID PDFs. Their hashes are rechecked before the table is rebuilt.
- `data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment_expanded.parquet` and `.csv` are the 74-row page-reviewed expansion.
- `data/processed/kalteng_historical_deployments/expanded_source_register.csv` maps every retained source document to its official URL, SHA-256, page count, and output incident IDs.
- `data/processed/kalteng_historical_deployments/expanded_quality_report.json` and `expanded_validation_report.json` are the machine-readable build and provenance checks.
- `data/processed/feature2_response_planning/feature2_response_planning_cohort.parquet` is the 50-row Feature-2 cohort. It makes the distinction between an exact personnel label, a documented mixed ground-response-vehicle total, and sparsely observed vehicle subtypes explicit.
- `data/processed/feature2_response_planning/feature2_data_readiness.json` and `feature2_cohort_validation.json` record model-readiness boundaries and prohibit fake zero subtype labels.
- `docs/FEATURE2_DATA_READINESS.md` is the dashboard/backend data contract in plain language.

## Provenance contract

Every processed row contains:

- `source_document_url_original`: original PPID Kalteng URL supplied with the workbook;
- `source_document_url_retrieved`: retrievable official PPID Kemendagri copy;
- `source_document_id` and `source_document_sha256`: stable identity and content verification;
- `source_page`: the manually verified PDF page;
- `source_excerpt`: the workbook's source-faithful extraction note;
- `source_field_provenance_json`: source-document provenance for every populated report field;
- `source_workbook_sha256`: hash of the unchanged submitted workbook.
- `record_sha256`: content identity for the processed output record.

The report's monitoring period is stored as `report_period_start` and
`report_period_end`. `incident_date` remains null unless a report explicitly
states it. A daily report must not be silently converted into a precise ignition
date.

## Confidence and eligibility

`resource_mapping_confidence` means:

- `high`: incident conditions and resource mapping are explicit;
- `medium`: useful incident record but one or more fields are incomplete;
- `low`: a Poslap/shift or multi-location operation may share resources.

`training_eligible_personnel` additionally requires `high`, `incident`, and an
exact numeric personnel total. `training_eligible_vehicle` requires `high`,
`incident`, and an explicit vehicle count. The expanded corpus has 50 rows that
satisfy both conditions. These flags identify calibration candidates only.

The vehicle target is **documented ground-response vehicles**, preserving the
source-specific count of fire engine, tanker, pickup, R4, R2, TOSA, trail, and
other vehicles. It is not a fire-engine-only label: only 3 HIGH full-resource
rows explicitly contain a fire-engine count. Therefore an estimator may
calibrate a ground-response vehicle range, but must not claim to predict a
specific number of fire engines yet.

## Enrichment contract

Report values are never overwritten.

- `firms_*` is NASA FIRMS `VIIRS_SNPP_SP`, limited to detections within 10 km
  during the **report monitoring window**. It is a candidate association, not
  proof of a matching perimeter.
- `big_peat_*` is a BIG peat-location map lookup. It is not an in-situ soil
  measurement; absence from the layer is not proof of mineral soil.
- `era5_*` is ERA5-Land nearest-cell weather over the report monitoring window,
  only when every required month file is present.
- `fwi_value` is null until a source-backed historical FWI calculation is added.
- `mapbiomas_event_landcover` is null because the locally available 2022 layer
  post-dates these 2018–2019 events and would be temporally misleading.

## Rebuild

```bash
./.venv-wsl/bin/python scripts/build_kalteng_historical_resource_dataset.py \
  /mnt/c/Users/USER/Downloads/kalteng_karhutla_resource_deployment_initial.xlsx
./.venv-wsl/bin/python scripts/validate_kalteng_historical_resource_dataset.py
./.venv-wsl/bin/python scripts/enrich_kalteng_historical_resource_dataset.py
./.venv-wsl/bin/python scripts/build_kalteng_expanded_resource_dataset.py
./.venv-wsl/bin/python scripts/validate_kalteng_expanded_resource_dataset.py
./.venv-wsl/bin/python scripts/build_feature2_response_planning_cohort.py
./.venv-wsl/bin/python scripts/validate_feature2_response_planning_cohort.py
```
