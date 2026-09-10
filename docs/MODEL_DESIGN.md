# Model design — Kalimantan FireWatch MVP

## Objective

The dashboard has two separate analytical products. They must not be presented
as one monolithic “AI model”, because they answer different questions and have
very different label availability.

| Product | Decision question | MVP approach |
| --- | --- | --- |
| Incident Prioritization & Localization | Which areas/alerts need monitoring attention? | Validated temporal ML for grid/week risk, plus source-transparent incident context. |
| Response Planning & Resource Calculator | What initial personnel/ground-vehicle planning reference is defensible? | Evidence retrieval + bounded rules + empirical benchmark; no local resource-prediction ML yet. |

## Model A — Grid-week hotspot occurrence

### Prediction target

`hotspot_next_7d > 0` for each 0.1-degree grid in Indonesian Kalimantan. The
target is **FIRMS active-fire detection occurrence**, not confirmed wildfire
occurrence, burned area, loss, or dispatch demand.

### Inputs available before the target window

- historical FIRMS counts, recency, confidence and FRP features;
- ERA5-Land rainfall, temperature, soil-moisture and dry-period features;
- BIG peatland share/context;
- temporal features (seasonality/calendar);
- static MapBiomas land-cover features only with their source-year limitation.

Administrative boundaries are localization/reporting attributes, not causal
predictors. Water Access Index belongs to response logistics; it must not be
quietly treated as evidence of fire occurrence without validation.

### Current baseline

The existing chronological evaluation selected HistGradientBoosting. Its 2026
held-out evaluation reports ROC-AUC 0.869, PR-AUC 0.336, precision 0.371,
recall 0.345, and F1 0.358 at a 0.18 threshold. These are detection-risk
metrics, not incident-confirmation metrics.

The current training split is chronological: 2023–2024 train, 2025 validation,
and 2026 test. This structure must remain; random record splits are forbidden
because they leak seasonal/spatial information.

### Output contract

```text
grid_id
week_start
predicted_fire_probability
risk_category
model_version
feature_snapshot_timestamp
data_coverage_status
```

The UI should display probability and category with the model/snapshot version,
not a claim that a fire will certainly occur.

### Threshold policy

Thresholds are operational triage settings, not model constants. The existing
stability analysis indicates validation thresholds of 0.21 (2024) and 0.18
(2025), with a recommended reference around 0.195. Select the final dashboard
category cutoffs from the validation policy and store them in a versioned
`priority_thresholds.json`, including expected precision/recall trade-off.

No threshold may be tuned on the held-out 2026 test set.

## Model B — Expected hotspot-detection count

### Prediction target

`hotspot_count_next_7d` for each grid/week, using the same pre-window feature
discipline as Model A.

### Current baseline

The existing HistGradientBoosting count model has 2026 test MAE 0.178, RMSE
1.246, and Poisson deviance 0.516. Its output is an expected **detection count**
and must not be called projected burned area or number of incidents.

### Output contract

```text
grid_id
week_start
predicted_hotspot_count_next_7d
model_version
feature_snapshot_timestamp
```

Model B is a supporting signal for prioritization, not an independent response
resource calculator input.

## Incident-localization layer — not a third predictive model

The system should map actual FIRMS detections to province/district/grid and add
BIG peat/water context. A presentation-oriented spatial-temporal grouping may
produce an `alert_cluster_id`, but it does not turn detections into confirmed
fire incidents. Cluster parameters must be versioned and evaluated against
page-verified local reports before being relied on operationally.

The decision UI presents both grid forecast and actual alert evidence rather
than fabricating a composite score with unvalidated weights.

## Response Planner — hybrid personnel estimator and person-derived engine capacity

The page-reviewed Kalteng corpus has 74 incident rows, including 50
HIGH-confidence personnel labels, and the cross-Kalimantan vehicle register
adds explicit fire-engine evidence. This permits a reproducible hybrid
personnel estimator, but it does not establish a national Indonesian dispatch
standard: local historical labels remain limited by geography and time.

The direct-training package is
`data/processed/feature2_model_ready/`; its contract is documented in
`docs/FEATURE2_MODEL_READY_DATA.md`. It has exactly two MVP outputs:

1. **Personnel** is the only estimated response target. Its training data is a
   separate real/synthetic hybrid package with provenance retained outside the
   model matrix.
2. **Fire engine** is not a second independent prediction target. It is a
   deterministic capacity conversion from `planned_personnel` only:
   `ceil(planned_personnel / 6)`. Six personnel per fire-engine unit follows
   the Permendagri 114/2018 staffing implementation cited in the model-ready
   data card. The output is a planning capacity, not automatic deployment.

There are only nine currently paired historical person/engine records. They
are retained for provenance and sanity checks, not used to infer an empirical
causal ratio. An unreported vehicle subtype continues to mean unknown, never
zero.

### MVP response method

```text
operator scenario + coordinate
  -> map context (BIG peat, JRC water; conditions requiring verification)
  -> retrieve comparable page-verified Kalteng cases
  -> U.S. ICS-209-PLUS personnel distribution as the prior benchmark
  -> local personnel calibration/range when comparable HIGH cases exist
  -> planned personnel (P50/P90)
  -> ceil(planned personnel P90 / 6) fire-engine planning capacity
  -> planning range + confidence + source provenance
```

The current U.S. benchmark is peak reported personnel by final-area band. It
must never be treated as an Indonesian standard or inferred solely from FIRMS
point count. Its role is a prior/reference distribution; the Kalteng cases
must be visible alongside any calibrated result.

The UI deliberately limits MVP vehicle output to `fire_engine_planning_count`.
It is computed solely from planned personnel using the six-person capacity
policy and returns its policy version/source. Tanker, pickup, R4, R2, TOSA,
trail, and other vehicle evidence remain preserved as historical context, but
are not MVP calculator outputs.

### Pilot calibration contract

The first runnable calibration should be intentionally simple and auditable:

1. Operator supplies a verified/estimated area scenario; it is never inferred
   from raw FIRMS point count.
2. Retrieve the closest Kalteng HIGH cases by log-area and peat/mineral match;
   do not silently impute unknown soil as mineral.
3. Use their median and interquartile range as the local evidence range. For
   personnel, blend this range conservatively with the U.S. area-band
   benchmark; the blend weight and comparable-case count are returned.
4. Use peat, official/cached FWI and Water Access Index only to choose a
   conservative quantile and add operational requirements. They may not apply
   arbitrary multiplicative resource coefficients.
5. If fewer than three comparable local cases are available, return the U.S.
   reference plus a local-evidence-insufficient flag—not a fabricated
   calibrated number.
6. Convert the conservative planned-personnel value to fire-engine planning
   capacity through `ceil(planned_personnel / 6)`; do not train a separate
   engine-count model from sparse paired labels.

This is a small-data calibration/rules model, not a black-box recommender. The
current benchmark/boosting comparison uses the requested fixed mixed 80:20
holdout and is shown only as hybrid-distribution fit. A future operational
validation should add source/time holdouts before any stronger field-accuracy
claim; the dashboard remains a planning reference requiring operator approval.

### Rule engine

Rules produce planning flags or choose an upper empirical reference within the
same benchmark band. They never multiply personnel counts by arbitrary peat,
weather, or water coefficients.

| Context | Permitted MVP effect |
| --- | --- |
| BIG mapped peat / peat-depth condition | Add sustained suppression, cool-down and re-ignition verification requirements; use conservative reference selection. |
| Official BMKG high/very-high FWI | Use conservative reference selection only while official/cached FWI status is available. |
| Relative low water proximity | Add alternate source/mobile-water verification; never reduce resources. |
| Missing/ambiguous spatial context | Lower confidence and require field confirmation; never silently assume safe conditions. |

Every rule needs a `rule_id`, source, rationale, scope, version, and guardrail.
The source-backed rulebook is `reports/response_planning_evidence.md`.

### Confidence policy

Confidence is a data-quality statement, not a probability that the deployment
will succeed. It is derived from:

- whether the coordinate and area scenario are present;
- peat/water lookup status;
- number and confidence of comparable Kalteng cases;
- field-level resource mapping confidence;
- whether weather data is official/cached, operator supplied, or unavailable.

## Future model: local resource calibration

The 50-case cohort permits the pilot calibration above. A larger corpus is
still needed before fitting a flexible local resource model. Each new record
needs an incident-level, not Poslap/day aggregate, mapping for:

```text
incident id, date/time, coordinate, verified area/perimeter,
peat/mineral condition, fire type, vegetation, water source/access,
weather/FWI, suppression duration,
personnel, tanker/pickup/R4/R2, and source document/page.
```

Minimum readiness is not just row count: it requires sufficient coverage across
peat/mineral contexts, size bands, provinces, seasons, and documented resource
outcomes. Evaluate temporal and geographic holdouts, compare against simple
case-based/rule baselines, and publish calibration/coverage uncertainty before
using an ML estimator. Do not transfer U.S. vehicle labels to Kalimantan.

## Validation and monitoring

| Component | Required test |
| --- | --- |
| Occurrence model | Chronological holdout, PR-AUC, recall, precision, calibration, province-level error review. |
| Count model | Chronological holdout, MAE, RMSE, Poisson deviance, residuals by province/season. |
| Spatial context | Coordinate validity, Kalimantan boundary check, non-empty authoritative layer, source lookup status. |
| Response planner | Reproducible rule/policy version, no automatic dispatch, evidence/document/page present, personnel-first engine capacity conversion, and no provenance field as a model input. |
| Production refresh | Source freshness, schema checks, null-rate checks, out-of-range values, drift against training distributions. |

## Model registry fields

Every served prediction or planning result should include:

```text
model_or_rule_version
training_or_evidence_dataset_version
feature_snapshot_timestamp
source_catalog_version
validation_report_reference
created_at
```

This makes a screenshot, exported scenario, or dashboard decision traceable to
the exact model, data snapshot, and rules that generated it.
