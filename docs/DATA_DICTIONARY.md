# Dashboard Data Dictionary

## `fact_grid_week`

| Field | Meaning |
|---|---|
| `grid_id`, `week_start` | Unique grid-week key and target-week start |
| `hotspot_count` | FIRMS active-fire detections in the target week |
| `hotspot_density_per_1000km2` | Detections normalized by clipped grid area |
| `high_confidence_count`, `high_confidence_pct` | FIRMS high-confidence detection summaries |
| `frp_sum`, `avg_frp` | Fire Radiative Power summaries in MW |
| `rainfall_7d/14d/30d` | Prior rolling precipitation totals in mm |
| `temperature_mean_7d`, `temperature_max_7d` | Prior temperature features in °C |
| `soil_moisture_7d` | Prior mean layer-1 volumetric soil moisture |
| `dry_days_30d` | Prior 30-day days with rainfall below 1 mm |
| `peat_pct` | Grid share covered by peatland |
| `hotspot_prev_7d/14d/30d` | Prior detection counts |
| `avg_frp_prev_30d` | Prior 30-day FRP average |
| `days_since_last_hotspot` | Days since the prior active week |
| `fire_next_7d` | Occurrence target for the target week |
| `hotspot_next_7d` | Count target for the target week |

## `prediction_grid_week`

| Field | Meaning |
|---|---|
| `predicted_fire_probability` | Out-of-sample probability of one or more target-week detections |
| `predicted_hotspot_count_next_7d` | Out-of-sample expected target-week detection count |
| `risk_category` | Low / Moderate / High / Very High probability category |
| `count_category` | Zero/Low / Medium / High / Very High count category |
| `priority_category` | Low / Watch / High / Critical joint priority category |

## Enhanced predictive features

| Field family | Meaning |
|---|---|
| `neighbour_hotspot_prev_*` | Prior detection totals in the eight closest grids |
| `district/province_hotspot_prev_*` | Prior administrative-area detection totals |
| `seasonal_grid_hotspot_mean_prior` | Same grid/week-of-year mean from preceding years only |
| `rainfall_60d`, `dry_days_60d`, `soil_moisture_30d` | Longer antecedent environmental state |
| `rainfall_30d_anomaly` | Current prior-30-day rainfall minus its causal grid history |
| `rainfall_7_to_30_ratio` | Short-term rainfall relative to antecedent 30-day rainfall |
| `peat_dryness_index` | Interaction of peat share and antecedent soil-moisture dryness |
| `landcover_code_2022` | MapBiomas 2022 code sampled at grid representative point; static categorical feature |
