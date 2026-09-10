# Feature 2 prediction sanity check

## Verdict

**Conditionally plausible for a planning prototype only.** Predictions are
finite, positive, bounded, increase across the controlled area ladders, and
are higher for the adverse peat scenario than the low-risk mineral scenario.
They are not validated dispatch quantities.

## Whole-matrix output check

```json
{
  "rows": 883,
  "all_finite": true,
  "positive_count": 883,
  "nonpositive_count": 0,
  "min": 8.93,
  "median": 23.679,
  "p95": 90.492,
  "max": 194.587,
  "above_100_count": 42
}
```

## Controlled scenario stress test

| Scenario | Area (ha) | Predicted personnel | Engine capacity |
| --- | ---: | ---: | ---: |
| peat_ground_high_constrained | 0.1 | 19.18 | 4 |
| peat_ground_high_constrained | 0.5 | 20.19 | 4 |
| peat_ground_high_constrained | 1.0 | 20.40 | 4 |
| peat_ground_high_constrained | 2.0 | 24.44 | 5 |
| peat_ground_high_constrained | 5.0 | 24.97 | 5 |
| peat_ground_high_constrained | 10.0 | 31.33 | 6 |
| peat_ground_high_constrained | 30.0 | 32.29 | 6 |
| peat_ground_high_constrained | 60.0 | 35.81 | 6 |
| peat_ground_high_constrained | 100.0 | 36.13 | 7 |
| peat_ground_high_constrained | 250.0 | 36.79 | 7 |
| peat_ground_high_constrained | 500.0 | 76.76 | 13 |
| peat_ground_high_constrained | 1000.0 | 80.40 | 14 |
| peat_ground_high_constrained | 10000.0 | 191.76 | 32 |
| mineral_surface_low_accessible | 0.1 | 9.43 | 2 |
| mineral_surface_low_accessible | 0.5 | 10.66 | 2 |
| mineral_surface_low_accessible | 1.0 | 10.83 | 2 |
| mineral_surface_low_accessible | 2.0 | 14.32 | 3 |
| mineral_surface_low_accessible | 5.0 | 15.05 | 3 |
| mineral_surface_low_accessible | 10.0 | 18.91 | 4 |
| mineral_surface_low_accessible | 30.0 | 20.13 | 4 |
| mineral_surface_low_accessible | 60.0 | 23.14 | 4 |
| mineral_surface_low_accessible | 100.0 | 23.34 | 4 |
| mineral_surface_low_accessible | 250.0 | 24.69 | 5 |
| mineral_surface_low_accessible | 500.0 | 52.43 | 9 |
| mineral_surface_low_accessible | 1000.0 | 54.96 | 10 |
| mineral_surface_low_accessible | 10000.0 | 169.34 | 29 |

## Critical caveat

The engine count is a mechanical crew-capacity conversion. For example, the
0.1-ha adverse peat scenario predicts about 21.49 personnel and thus four
engine-capacity units. This is conservative and may exceed what a field
commander would dispatch for a small incident. The UI must display it as
`kapasitas mobil damkar rencana` and require fleet/road/operator confirmation.

The real holdout rows also vary substantially at the same area, so the
personnel output must be presented as a planning range/confidence level, not a
precise staffing guarantee.
