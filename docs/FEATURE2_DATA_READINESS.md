# Feature 2 — Data Readiness

## Scope

`feature2_response_planning_cohort.parquet` is the local Indonesian evidence
set for the Response Planning & Resource Calculator. It is derived only from
the **HIGH-confidence**, page-verified Kalteng historical incident records.
It does not contain fire-station inventory, patrol-only activity, or a
Poslap-wide daily resource total assigned to an individual incident.

## Current evidence

| Item | Value | Meaning |
| --- | ---: | --- |
| Local source corpus | 74 incidents | Kalteng, 2018–2019; all retain official PPID source URL, PDF SHA-256, page, and provenance JSON. |
| Feature-2 cohort | 50 incidents | Personnel and at least one documented ground-response vehicle are both explicit in the same incident resource block. |
| Area reported by source | 36 / 50 | Eligible for area-conditioned case retrieval/calibration. |
| Area and soil reported by source | 16 / 50 | Supports peat/mineral-aware retrieval, but coverage is still sparse. |
| Coordinates reported by source | 6 / 50 | Do not geocode the rest and present them as source coordinates. |
| Explicit fire-engine positive counts | 3 / 50 | Insufficient for a fire-engine-count model. |
| Explicit tanker positive counts | 6 / 50 | Insufficient for a tanker-count model. |
| Explicit pickup positive counts | 12 / 50 | Contextual/case evidence only. |

## Correct target semantics

- `personnel_total` is an exact incident-level count, only in the 50-row
  cohort.
- `documented_ground_response_vehicle_total` is the sum of vehicles listed in
  the source incident block. It can include pickup, fire engine, tanker, R4,
  R2, TOSA, trail, boat, or another named vehicle. It is **not** synonymous
  with `fire_engine_count`.
- `*_explicit_positive` is populated only where that subtype was named with a
  positive number in the source. A null does **not** mean zero units; it means
  the report did not explicitly provide that subtype count.
- `documented_dedicated_suppression_vehicle_lower_bound` is a source-derived
  lower bound from named fire engines, tankers, and pickups. It is not a full
  fleet count and must not be used as an exact fire-engine label.

## What Feature 2 may do now

1. Return an auditable personnel planning range from comparable Kalteng cases,
   using an operator-provided area and peat/mineral context where available.
2. Use ICS-209-PLUS U.S. `WF_PEAK_PERSONNEL` only as a benchmark/prior and
   display the Kalteng cases that support any local adjustment.
3. Return a **documented ground-response-vehicle range** with the source
   breakdown and an explicit `planning_reference_only` status.
4. Apply BIG peat and Water Access Index as conservative planning flags and
   verification requirements, not unvalidated numeric multipliers.

## What Feature 2 must not do yet

- Output a trained or exact number of **mobil damkar** / fire engines.
- Interpret missing subtype labels as zero.
- Convert FIRMS detection count into burned area or resource requirement.
- Dispatch personnel or vehicles automatically.
- Generalize this 2018–2019 Kalteng cohort into a national Indonesia standard.

## Next data improvement, before a flexible model

Prioritize new Indonesian reports that state, within the same incident block:
area or perimeter, coordinates, peat/mineral condition, fire type, water
access, personnel, and separately counted fire engines/tankers/pickups. The
first coverage threshold to fix is the fire-engine target, not more generic
R2/patrol records.
