# Feature 2 modelling report

Generated: 2026-09-09T04:11:10.178196+00:00

## Personnel — benchmark vs boosting

Evaluation uses a fixed 80:20 random split from the combined matrix:
706 train rows and 177 test rows. Both
partitions remain mixed real/synthetic; provenance is neither an input nor a
split key.

| Model | MAE | RMSE | Median AE | R² |
| --- | ---: | ---: | ---: | ---: |
| Area-only Huber benchmark | 11.409 | 24.708 | 5.266 | 0.507 |
| Hybrid HistGradientBoosting | 1.429 | 3.625 | 0.69 | 0.989 |

The boosting artefact is a pilot planning model only. Mixed-holdout metrics
measure fit to the configured hybrid scenario distribution, not field accuracy.

## Fire-engine planning capacity — benchmark vs boosting

Evaluation uses a fixed 80:20 random split from the combined engine matrix:
679 train rows and 170 test rows. The
only input is `planned_personnel`.

| Model | MAE | RMSE | Median AE | R² |
| --- | ---: | ---: | ---: | ---: |
| Capacity benchmark `ceil(planned_personnel / 6)` | 0.059 | 0.42 | 0.0 | 0.995 |
| Personnel-only HistGradientBoosting | 0.124 | 0.479 | 0.0 | 0.994 |

The boosting engine result is **experimental only**. The serving contract stays
with the transparent six-person capacity policy; the mixed engine test is
dominated by deterministic synthetic policy rows and cannot validate an
incident-level deployment claim.

## Guardrails

- The train matrices have no source/origin/synthetic feature.
- Source provenance remains in evaluation sidecars and is not a model feature.
- Train and test are the same deliberately mixed real/synthetic distribution.
- The engine conversion is planning capacity under a municipal staffing
  standard, not a claim that all Karhutla responders ride on engines.
