# Feature-engineering experiment

Run date: 2026-08-26  
Evaluation protocol: 2024 train / 2025 validation / 2026 held-out test. The test prediction table contains 135,488 grid-week rows (2026-01-05 through 2026-07-20).

## Added feature families

- local 7/14/30-day history, density, and FRP memory;
- eight-nearest-grid, district, and province lagged hotspot activity;
- causal grid-specific seasonality;
- 7/14/30/60-day antecedent rainfall, temperature, soil-moisture, dry-day, rainfall-anomaly, and peat-dryness features;
- static MapBiomas 2022 land-cover class.

Every fire and weather feature is shifted before the target-week start. MapBiomas is static 2022 context, not a claim about land cover in 2025-2026.

## Classification outcome

| Metric | Previous baseline | Enhanced run | Change |
|---|---:|---:|---:|
| ROC-AUC | 0.8309 | 0.8617 | +0.0308 |
| PR-AUC | 0.2922 | 0.3259 | +0.0337 |
| Precision | 0.3332 | 0.2969 | -0.0363 |
| Recall | 0.3257 | 0.4366 | +0.1109 |
| F1 | 0.3294 | 0.3535 | +0.0240 |

The selected occurrence model is histogram gradient boosting, chosen by 2025 validation PR-AUC. Its operational threshold is 0.11; the recall gain therefore comes with lower precision, which is appropriate for a monitoring/watchlist workflow but should be adjusted if false-positive workload is costly.

## Count-model outcome

The selected count model is histogram gradient boosting. Test MAE is 0.1713 (previous 0.1667), RMSE is 1.2486 (previous 1.2423), while Poisson deviance improves to 0.5064 (previous 0.5257). The occurrence model materially improves; the count model has a modest MAE trade-off and should be monitored separately rather than inferred from classification performance.

## Quality gates

- 86,013 Kalimantan FIRMS detections after polygon filtering;
- 4,672 clipped grid cells and 626,048 unique grid-week facts;
- no duplicate grid-week keys, negative count, invalid peat percentage, or FIRMS record outside the requested bounding box;
- 2026 prediction output: unique keys, probabilities in [0, 1], non-negative counts, and complete categories.

See classification_validation_metrics.csv, count_validation_metrics.csv, classification_feature_importance.csv, and model_evaluation.json for machine-readable results.
