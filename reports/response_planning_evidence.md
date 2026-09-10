# Evidentiary rulebook — MVP Response Planning & Resource Calculator

## Decision boundary

This MVP is a **planning reference**, not a dispatch system. It gives an
operator a transparent U.S. empirical reference range and identifies conditions
that require a more conservative plan in Kalimantan. It never assigns people,
vehicles, aircraft, or a water point. Those require local availability, road
condition, water-volume, permission, and incident-command confirmation data.

The implementation is `firewatch.response_planning.initial_response_plan` and
is verified by `scripts/validate_response_planning.py`.

## Rules and evidence

| Rule | What the MVP does | Evidence / why this is not an invented multiplier | Guardrail |
| --- | --- | --- | --- |
| U.S. baseline | For an operator-selected fire-area scenario, report the observed median and P90 `WF_PEAK_PERSONNEL` from the ICS-209-PLUS wildfire EDA. | The project EDA separates peak personnel from sums across sitreps. It is an empirical benchmark, not a causal staffing equation. | `FINAL_ACRES` is final outcome data. The app must never infer that area solely from FIRMS hotspot count at first detection. |
| High fire weather | If official BMKG FWI is **high** (7–13) or **very high** (>13), choose P90, rather than median, as the planning reference. | BMKG publishes these categories and says FWI represents potential fire intensity; BMKG SPARTAN uses a locally calibrated FDRS based on wind, humidity, rain and temperature. Selecting an empirical upper quantile does **not** multiply personnel by an arbitrary constant. | Re-evaluate whenever forecast FWI changes. This is a conservative planning reference, not an automatic escalation order. |
| Peat context | If the verified incident location overlaps peat, choose P90 and add sustained suppression/cool-down and re-ignition monitoring requirements. | Indonesian peat experiments show subsurface smouldering can persist after short water application; water injection is more efficient than surface spray in the reported experiments. This justifies different **capabilities/tasks**, but not a universal litres/personnel conversion. | Do not calculate water volume from laboratory values; field peat, fire perimeter and pumps differ. |
| Water cover/proximity | Calculate distance to JRC pixels identified as permanent (`seasonality=12`) or any seasonal/permanent surface water. Surface-water evidence produces *candidates*, never a confirmed source. | JRC provides 30m occurrence/seasonality layers for 1984–2024. Wildfire logistics research shows water-supply point distance affects vehicle travel; Central Kalimantan practice identifies canal, pond and confined groundwater as possible sources. | Water index cannot reduce resource need. Verify depth/volume, pumpability, permission and vehicle access in the field. |
| Relative low water proximity | If a grid is in the bottom 25% of the Kalimantan water-proximity distribution, add an alternate-source/mobile-water verification task. | The threshold is explicitly a **relative spatial triage bucket**, calculated from the project’s own grid, not a claimed universal safe distance. | It changes logistics flags only; it does not create a numeric personnel multiplier. |
| Combined modifiers | Apply at most the P90 reference in the same area band; do not multiply peat × weather × water factors. | Prevents compound, unvalidated transfer assumptions. | Local response logs are required before estimating Indonesian resource multipliers. |
| Access/routing | Do not apply an access-time multiplier yet. OSM PBF is local and ready, but no road-condition or routable verified water points are available. | Initial-attack research models standard response as resources arriving inside a time limit and explicitly includes station capacity. | Add OSRM travel-time and field-confirmed water points only when those inputs exist. |

## Primary sources

1. BMKG, [Fire Weather Index — Peringatan Karhutla](https://www.bmkg.go.id/cuaca/karhutla/fwi): category bands and H+7 forecast.
2. BMKG, [SPARTAN/FDRS explanation](https://www.bmkg.go.id/berita/utama/bmkg-spartan-dukung-pemantauan-dan-pencegahan-potensi-karhutla): Indonesian FDRS uses locally calibrated weather parameters.
3. JRC, [Global Surface Water Data Access](https://global-surface-water.appspot.com/download), and Pekel et al., [Nature (2016)](https://doi.org/10.1038/nature20584): occurrence and seasonality products; map attribution is `Source: EC JRC/Google`.
4. Lee et al., [Deploying initial attack resources for wildfire suppression](https://research.fs.usda.gov/treesearch/42818): response resources are constrained by required arrival time and capacity.
5. Irawan et al., [Experimental study on peat fire suppression through water injection](https://doi.org/10.1016/j.aej.2022.06.036): Indonesian peat smouldering and direct water injection evidence.
6. Santoso et al., [Water spray and smouldering in Indonesian peat fires](https://doi.org/10.1016/j.firesaf.2017.04.012): short surface-water suppression can fail and re-ignition can occur.
7. Hanan et al., [water-supply points and forest-fire logistics](https://www.publish.csiro.au/wf/fulltext/WF20103): water-supply distance affects firefighting vehicle travel.
8. Takahashi, Jaya & Limin, [water resources for peat-firefighting in Central Kalimantan](https://doi.org/10.1007/978-4-431-55681-7_27): canals, fire-prevention ponds and confined groundwater can be possible sources, subject to operational checks.

## Calibration plan (post-MVP)

For each local response, store verified perimeter/area, peat condition, BMKG FWI,
source used and distance, travel time, personnel, equipment, water volume,
containment time, and re-ignition. Only after enough incident records exist may
we fit and validate local resource adjustments. The U.S. reference then becomes
a prior/benchmark rather than the principal estimator.
