# Feature 2 model-ready data

`data/processed/feature2_model_ready/` is the only direct-training package for
the two MVP outputs:

1. `personnel` — an evidence-calibrated hybrid estimate of response personnel;
2. `fire_engine` — a transparent planning-capacity conversion from the first
   output, not a second independent incident-prediction model.

## Direct matrices

| Target | Feature columns in `training/` | Target | Serving contract |
| --- | --- | --- | --- |
| `personnel` | Incident scenario inputs: area, peat/fire context, FWI/water context where available, and documented U.S. reference features. | `target_value` = personnel | Hybrid personnel estimator. |
| `fire_engine` | `planned_personnel` only | `target_value` = units | `ceil(planned_personnel / 6)` planning-capacity conversion. |

Each target has CSV and Parquet files under `real/`, `synthetic/`, and
`training/`. `training/` is the deterministic shuffled union and intentionally
does **not** contain an origin, synthetic, URL, source, or hash column. It is
safe to load directly in a modelling notebook.

Source grouping is deliberately kept in
`evaluation_sidecars/fire_engine_real_provenance.csv`; it is for source-group
holdouts/audit only and must never be joined into model inputs.

## Why fire engines derive from personnel

The implementation converts the planned personnel output to engine capacity
using six personnel per unit. The six-person composition (one commander, one
driver/pump operator, four firefighters) is the implementation of the
Permendagri 114/2018 staffing standard stated in this
[BPK regulation](https://peraturan.bpk.go.id/Download/361863/PERBUP%20NO.%2045%20TAHUN%202020.pdf).

This is a municipal-fire-service **capacity proxy**, not a claim that all
Karhutla responders ride on an engine or a national Karhutla dispatch rule.
The dashboard must call the result `mobil damkar kebutuhan rencana` and retain
operator approval. It must not call it an automatic dispatch order.

## Modelling controls

- The response engine must first generate `planned_personnel`; it must never
  pass an observed `personnel_total` from a historical label at serving time.
- The engine formula is intentionally not trained from the nine currently
  paired real personnel/engine reports; nine cases are preserved for sanity
  checking only and are insufficient to establish a causal empirical ratio.
- Synthetic data permits scenario coverage and model development. The current
  benchmark/boosting report uses the requested mixed holdout; it must be
  described as hybrid-distribution fit, not field-deployment accuracy.
- `scripts/validate_feature2_model_ready_data.py` checks data separation,
  target rules, provenance sidecars, and performs an in-memory sklearn
  fit/predict smoke test for both direct matrices.

## Benchmark and boosting evaluation

`scripts/train_feature2_models.py` produces a reproducible comparison in
`reports/feature2_modeling/`:

- Personnel benchmark: Indonesian real-data, log-area-only Huber regression.
- Personnel booster: regularised `HistGradientBoostingRegressor` trained on the
  direct mixed matrix without an origin/provenance input.
- Fire-engine benchmark: `ceil(out_of_fold_planned_personnel / 6)`.
- Fire-engine booster: personnel-only `HistGradientBoostingRegressor`, retained
  only as an experiment.

The primary test protocol is a fixed random 80:20 split over each direct mixed
matrix, so both train and test contain the configured real/synthetic mix.
Provenance sidecars are retained for audit but are neither a model input nor a
split key. These metrics measure fit to the hybrid scenario distribution—not
field deployment accuracy. Predictions, JSON/Markdown report, and serialized
pilot artefacts are written separately from the direct training matrices.

## Rebuild

```bash
./.venv-wsl/bin/python scripts/build_feature2_hybrid_training_data.py
./.venv-wsl/bin/python scripts/build_feature2_model_ready_data.py
./.venv-wsl/bin/python scripts/validate_feature2_model_ready_data.py
./.venv-wsl/bin/python scripts/train_feature2_models.py
```
