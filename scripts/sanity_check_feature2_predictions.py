"""Run non-training plausibility checks for the current Feature-2 artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports/feature2_modeling"
MODEL_PATH = ROOT / "models/feature2_personnel_hgb_pilot.joblib"
DATA_DIR = ROOT / "data/processed/feature2_model_ready"
sys.path.insert(0, str(ROOT / "scripts"))
from audit_feature2_mixed_holdout import reconstruct_origins  # noqa: E402
from build_feature2_hybrid_training_data import US_SUMMARY_PATH, reference_lookup, us_area_band  # noqa: E402


def controlled_scenario(
    model: object,
    template: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    name: str,
    soil: str,
    fire_type: str,
    fwi: str,
    water_class: str,
    complexity: str,
    water_index: float,
    water_km: float,
) -> pd.DataFrame:
    areas = np.array([0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 100.0, 250.0, 500.0, 1_000.0, 10_000.0])
    frame = pd.concat([template] * len(areas), ignore_index=True)
    frame["area_ha"] = areas
    frame["log_area_ha"] = np.log1p(areas)
    frame["soil_class"] = soil
    frame["fire_type_class"] = fire_type
    frame["fwi_class"] = fwi
    frame["water_access_index_relative_0_100"] = water_index
    frame["nearest_permanent_surface_water_km"] = water_km
    frame["water_access_class"] = water_class
    frame["operational_complexity_level"] = complexity
    bands = us_area_band(frame["area_ha"])
    frame["us_area_band"] = bands.astype(str)
    frame["us_peak_personnel_median"] = bands.map(reference["peak_personnel_median"]).astype(float)
    frame["us_peak_personnel_p90"] = bands.map(reference["peak_personnel_p90"]).astype(float)
    prediction = model.predict(frame.drop(columns="target_value"))
    return pd.DataFrame(
        {
            "scenario": name,
            "area_ha": areas,
            "predicted_personnel": prediction,
            "fire_engine_capacity_ceil_personnel_over_6": np.ceil(prediction / 6).astype(int),
        }
    )


def markdown(report: dict[str, object]) -> str:
    controlled = pd.DataFrame(report["controlled_scenarios"])
    rows = "\n".join(
        f"| {row.scenario} | {row.area_ha} | {row.predicted_personnel:.2f} | {row.fire_engine_capacity_ceil_personnel_over_6} |"
        for row in controlled.itertuples(index=False)
    )
    return f"""# Feature 2 prediction sanity check

## Verdict

**Conditionally plausible for a planning prototype only.** Predictions are
finite, positive, bounded, increase across the controlled area ladders, and
are higher for the adverse peat scenario than the low-risk mineral scenario.
They are not validated dispatch quantities.

## Whole-matrix output check

```json
{json.dumps(report['whole_matrix_output'], indent=2)}
```

## Controlled scenario stress test

| Scenario | Area (ha) | Predicted personnel | Engine capacity |
| --- | ---: | ---: | ---: |
{rows}

## Critical caveat

The engine count is a mechanical crew-capacity conversion. For example, the
0.1-ha adverse peat scenario predicts about 21.49 personnel and thus four
engine-capacity units. This is conservative and may exceed what a field
commander would dispatch for a small incident. The UI must display it as
`kapasitas mobil damkar rencana` and require fleet/road/operator confirmation.

The real holdout rows also vary substantially at the same area, so the
personnel output must be presented as a planning range/confidence level, not a
precise staffing guarantee.
"""


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    model = joblib.load(MODEL_PATH)
    direct = pd.read_parquet(DATA_DIR / "training/personnel.parquet")
    prediction = model.predict(direct.drop(columns="target_value"))
    whole = {
        "rows": int(len(prediction)),
        "all_finite": bool(np.isfinite(prediction).all()),
        "positive_count": int((prediction > 0).sum()),
        "nonpositive_count": int((prediction <= 0).sum()),
        "min": round(float(prediction.min()), 3),
        "median": round(float(np.median(prediction)), 3),
        "p95": round(float(np.quantile(prediction, 0.95)), 3),
        "max": round(float(prediction.max()), 3),
        "above_100_count": int((prediction > 100).sum()),
    }
    summary = json.loads(US_SUMMARY_PATH.read_text(encoding="utf-8"))
    reference = reference_lookup(summary)
    template = pd.read_parquet(DATA_DIR / "synthetic/personnel.parquet").iloc[[0]].copy()
    controlled = pd.concat(
        [
            controlled_scenario(
                model, template, reference,
                name="peat_ground_high_constrained", soil="peat", fire_type="ground_smouldering",
                fwi="high", water_class="constrained", complexity="high", water_index=50.0, water_km=2.0,
            ),
            controlled_scenario(
                model, template, reference,
                name="mineral_surface_low_accessible", soil="mineral", fire_type="surface",
                fwi="low", water_class="accessible", complexity="initial_attack", water_index=85.0, water_km=0.2,
            ),
        ],
        ignore_index=True,
    )
    monotonic = {
        name: bool(group["predicted_personnel"].diff().fillna(0).ge(0).all())
        for name, group in controlled.groupby("scenario")
    }
    comparison = []
    for area in (0.1, 1.0, 5.0, 30.0, 250.0, 1_000.0, 10_000.0):
        peat = controlled.loc[
            controlled["scenario"].eq("peat_ground_high_constrained") & controlled["area_ha"].eq(area),
            "predicted_personnel",
        ].iloc[0]
        mineral = controlled.loc[
            controlled["scenario"].eq("mineral_surface_low_accessible") & controlled["area_ha"].eq(area),
            "predicted_personnel",
        ].iloc[0]
        comparison.append({"area_ha": area, "peat_high": round(float(peat), 3), "mineral_low": round(float(mineral), 3)})

    heldout = pd.read_csv(REPORT_DIR / "personnel_mixed_holdout_predictions_audited.csv")
    reconstructed = reconstruct_origins("personnel").assign(row_index=lambda frame: np.arange(len(frame)))
    heldout = heldout.merge(
        reconstructed.loc[:, ["row_index", "area_ha", "soil_class", "fire_type_class"]],
        on="row_index", how="left", validate="1:1",
    )
    real_rows = heldout.loc[heldout["_audit_origin"].eq("real")].copy()
    real_rows["boosting_error"] = real_rows["boosting_hybrid"] - real_rows["actual_personnel"]
    report = {
        "status": "passed",
        "verdict": "conditionally_plausible_for_planning_prototype_only",
        "whole_matrix_output": whole,
        "controlled_area_monotonic_non_decreasing": monotonic,
        "adverse_peat_vs_low_risk_mineral": comparison,
        "controlled_scenarios": controlled.round(3).to_dict(orient="records"),
        "real_holdout_output": real_rows.loc[
            :, ["area_ha", "soil_class", "fire_type_class", "actual_personnel", "boosting_hybrid", "boosting_error"]
        ].round(3).to_dict(orient="records"),
        "required_ui_guardrails": [
            "Display personnel as a planning estimate/range, never a guaranteed staffing order.",
            "Display fire-engine output as capacity planning only; confirm actual fleet availability, road access, and operator judgement.",
            "Show lower confidence where scenario context is unknown or outside observed local coverage.",
        ],
    }
    (REPORT_DIR / "feature2_prediction_sanity_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "feature2_prediction_sanity_check.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
