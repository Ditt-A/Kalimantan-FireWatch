# Methodology

## Spatial and temporal units

The analytical unit is a **0.1° grid cell × complete Monday-starting week**. Cells are clipped to the union of Central, East, North, South, and West Kalimantan. Area is calculated in an equal-area CRS and density is reported per 1,000 km².

## Source treatment

- FIRMS: final NOAA-20 SP data are used through 31 May 2026; NOAA-20 NRT data continue 1 June–31 July 2026. The outputs are de-duplicated and polygon-filtered to Indonesian Kalimantan.
- ERA5-Land: hourly precipitation, 2 m temperature, and layer-1 soil moisture are sampled at grid representative points. Precipitation is converted from m to mm.
- Peatland: the percentage of each clipped grid intersecting the peatland union is calculated in an equal-area CRS.
- MapBiomas: the 2022 land-cover code is sampled at each grid representative point and used as a static categorical predictor. It is never described as a measured 2025-2026 condition.

## Predictive feature engineering

The model combines the following leakage-safe feature families:

- local fire memory: 7-, 14-, and 30-day hotspot totals, prior FRP intensity, time since the last active week, and area-normalized hotspot density;
- spatial contagion: prior-week and prior-30-day totals in the eight closest grid cells, district, and province;
- seasonality: cyclic month/week encoding and a same-grid/week-of-year baseline constructed from earlier years only;
- antecedent environment: 7-, 14-, 30-, and 60-day rainfall; 7- and 30-day temperature and soil-moisture summaries; 30-/60-day dry-day counts; and a causal rainfall anomaly;
- context and interactions: peat coverage, peat-dryness interaction, rainfall ratio, and static MapBiomas 2022 land-cover class.

Missing environmental values are imputed inside the modeling pipeline using training-data medians. Static land-cover missingness is represented as an explicit unknown category.

## Leakage-safe feature timing

A target row represents hotspot activity during its target week. Environmental rolling windows and hotspot history are shifted one day/week, so they only use observations available before the target week begins.

The target variables are:

- `fire_next_7d`: 1 if one or more hotspot detections occur during the target week;
- `hotspot_next_7d`: count of detections during the target week.

## Validation

Splits are chronological: 2024 training, 2025 validation/model selection, and 2026 final test. Classification is assessed using ROC-AUC, PR-AUC, precision, recall, and F1. Count prediction uses MAE, RMSE, and Poisson deviance. Accuracy is deliberately not used as the primary classification metric.

Priority categories combine independently predicted fire probability and expected count. Their cut-offs are quantiles calculated from validation predictions, rather than an arbitrary weighted index.

## Interpretability

For tree models that do not expose native importances, reports/classification_feature_importance.csv uses permutation importance: one feature is shuffled at a time in a fixed 25,000-row sample from the held-out 2026 test set, and the mean reduction in PR-AUC over three repeats is reported. This is descriptive only and is not used to select or tune models.
