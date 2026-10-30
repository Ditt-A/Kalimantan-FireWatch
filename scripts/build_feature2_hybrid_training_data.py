"""Build provenance-preserving real, synthetic, and training-ready Feature-2 data.

The combined ``training`` files intentionally contain no origin/synthetic
indicator.  Real and generated records remain in separate sibling directories
and are fully described in manifests, so origin can be audited without being a
model feature that leaks into evaluation.

Synthetic labels are *scenario recommendations*, not reconstructed historical
deployments.  They use the U.S. ICS-209-PLUS area/personnel distribution only
as a prior shape, then calibrate magnitude to Indonesian observations.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor


ROOT = Path(__file__).resolve().parents[1]
COHORT_PATH = ROOT / "data/processed/feature2_response_planning/feature2_response_planning_cohort.parquet"
EVIDENCE_PATH = ROOT / "data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence.parquet"
US_SUMMARY_PATH = ROOT / "reports/us_fire_resource_eda/summary.json"
US_MANIFEST_PATH = ROOT / "data/figshare_all_hazards/figshare_manifest.json"
US_WILDFIRE_ZIP = ROOT / "data/figshare_all_hazards/ics209plus-wildfire.zip"
WATER_PATH = ROOT / "data/processed/water_access_grid.parquet"
OUTPUT_DIR = ROOT / "data/processed/feature2_hybrid_training"
SEED = 20260908
# Equal log-area strata ensure the personnel model sees both local-scale
# initial-attack cases and the less frequent large, complex scenarios.  These
# remain planning scenarios, not invented historical incident records.
SYNTHETIC_ROWS = 840

FEATURE_COLUMNS = [
    "area_ha",
    "log_area_ha",
    "soil_class",
    "fire_type_class",
    "fwi_class",
    "water_access_index_relative_0_100",
    "nearest_permanent_surface_water_km",
    "water_access_class",
    "us_area_band",
    "us_peak_personnel_median",
    "us_peak_personnel_p90",
    "operational_complexity_level",
]
TARGETS = {
    "personnel": "target_personnel",
    "fire_engine": "target_fire_engine",
    "water_tanker": "target_water_tanker",
    "pickup": "target_pickup",
}
AREA_BANDS = ["<100", "100-999", "1k-9.9k", "10k-99.9k", "100k+"]
DOMAIN_RULE_SOURCES = [
    {
        "rule": "incident_complexity_quantile_selection",
        "source": "NWCG Wildland Fire Risk and Complexity Assessment",
        "url": "https://www.nwcg.gov/announcement/publications/updated-nwcg-wildland-fire-risk-and-complexity-assessment-pms-236",
        "use": "Complexity is selected from multiple changing risk indicators; it is not treated as an area-only rule.",
    },
    {
        "rule": "peat_sustained_suppression_escalation",
        "source": "State of Indonesia's Forests 2020, Ministry of Environment and Forestry",
        "url": "https://kemlu.go.id/files-service/storage/repositori/72942/State-Forest-of-Indonesia-2020.pdf",
        "use": "Peat suppression is harder, needs more time/resources, and may require water injection below the surface.",
    },
    {
        "rule": "water_logistics_escalation",
        "source": "Wetlands International, Manual for the Control of Fire in Peatlands and Peatland Forest",
        "url": "https://indonesia.wetlands.org/publication/manual-for-the-control-of-fire-in-peatlands-and-peatland-forest/",
        "use": "Water availability and site access are explicit operational constraints in peat-fire control.",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_soil(value: object) -> str:
    text = str(value or "").strip().lower()
    if "gambut" in text or text == "peat":
        return "peat"
    if "mineral" in text:
        return "mineral"
    return "unknown"


def normalize_fire_type(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "atas" in text and "bawah" in text:
        return "mixed"
    if "tajuk" in text or "atas" in text:
        return "crown"
    if "bawah" in text or "ground" in text:
        return "ground_smouldering"
    if "permukaan" in text or "surface" in text:
        return "surface"
    return "unknown"


def us_area_band(area_ha: pd.Series) -> pd.Series:
    acres = pd.to_numeric(area_ha, errors="coerce") * 2.47105381
    return pd.cut(
        acres,
        bins=[-np.inf, 100, 1_000, 10_000, 100_000, np.inf],
        labels=AREA_BANDS,
        right=False,
    ).astype("string")


def reference_lookup(summary: dict[str, Any]) -> pd.DataFrame:
    table = pd.DataFrame(summary["resource_by_final_acres_band"])
    required = {"final_acres_band", "peak_personnel_median", "peak_personnel_p90"}
    missing = required - set(table.columns)
    if missing:
        raise RuntimeError(f"U.S. resource summary lacks {sorted(missing)}")
    return table.set_index("final_acres_band")


def feature_frame(
    source: pd.DataFrame,
    us_reference: pd.DataFrame,
    *,
    synthetic: bool,
) -> pd.DataFrame:
    frame = source.copy()
    frame["area_ha"] = pd.to_numeric(frame["area_ha"], errors="coerce")
    if frame["area_ha"].isna().any() or (frame["area_ha"] <= 0).any():
        raise RuntimeError("Training rows require positive area_ha")
    frame["log_area_ha"] = np.log1p(frame["area_ha"])
    frame["soil_class"] = frame.get("soil_class", "unknown").map(normalize_soil)
    frame["fire_type_class"] = frame.get("fire_type_class", "unknown").map(normalize_fire_type)
    frame["fwi_class"] = frame.get("fwi_class", "unknown").fillna("unknown").astype(str)
    frame["water_access_index_relative_0_100"] = pd.to_numeric(
        frame.get("water_access_index_relative_0_100", np.nan), errors="coerce"
    )
    frame["nearest_permanent_surface_water_km"] = pd.to_numeric(
        frame.get("nearest_permanent_surface_water_km", np.nan), errors="coerce"
    )
    if synthetic:
        index = frame["water_access_index_relative_0_100"]
        frame["water_access_class"] = np.select(
            [index < 25, index < 75], ["remote", "constrained"], default="accessible"
        )
    else:
        frame["water_access_class"] = "unknown"
    frame["us_area_band"] = us_area_band(frame["area_ha"])
    frame["us_peak_personnel_median"] = frame["us_area_band"].map(
        us_reference["peak_personnel_median"]
    ).astype(float)
    frame["us_peak_personnel_p90"] = frame["us_area_band"].map(
        us_reference["peak_personnel_p90"]
    ).astype(float)
    if synthetic and "operational_complexity_level" in frame:
        # Set later by the generator; kept as a categorical, explainable
        # planning input rather than a hidden model-only feature.
        frame["operational_complexity_level"] = frame["operational_complexity_level"].astype(str)
    else:
        frame["operational_complexity_level"] = "unknown"
    return frame


def real_target_rows(
    cohort: pd.DataFrame,
    evidence: pd.DataFrame,
    us_reference: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    cohort_common = pd.DataFrame({
        "area_ha": cohort["area_ha"],
        "soil_class": cohort["soil_class_source"],
        "fire_type_class": cohort["fire_type"],
        "fwi_class": "unknown",
        "water_access_index_relative_0_100": np.nan,
        "nearest_permanent_surface_water_km": np.nan,
    })
    evidence_common = pd.DataFrame({
        "area_ha": evidence["area_ha"],
        "soil_class": evidence["soil_class_source"],
        "fire_type_class": "unknown",
        "fwi_class": "unknown",
        "water_access_index_relative_0_100": np.nan,
        "nearest_permanent_surface_water_km": np.nan,
    })

    def build(
        cohort_target: str | None,
        evidence_target: str | None,
    ) -> pd.DataFrame:
        pieces: list[pd.DataFrame] = []
        if cohort_target:
            part = cohort_common.copy()
            part["target_value"] = pd.to_numeric(cohort[cohort_target], errors="coerce")
            pieces.append(part)
        if evidence_target:
            part = evidence_common.copy()
            part["target_value"] = pd.to_numeric(evidence[evidence_target], errors="coerce")
            pieces.append(part)
        combined = pd.concat(pieces, ignore_index=True)
        combined = combined.loc[
            combined["area_ha"].notna() & combined["area_ha"].gt(0) & combined["target_value"].notna()
        ].copy()
        combined["target_value"] = combined["target_value"].round().astype(int)
        return feature_frame(combined, us_reference, synthetic=False)[FEATURE_COLUMNS + ["target_value"]]

    return {
        "personnel": build("personnel_total", "personnel_total"),
        "fire_engine": build("fire_engine_count_explicit_positive", "fire_engine_count_explicit_positive"),
        "water_tanker": build("water_tanker_count_explicit_positive", "water_tanker_count_explicit_positive"),
        "pickup": build("pickup_count_explicit_positive", "pickup_count_explicit_positive"),
    }


def synthetic_scenarios(
    real_personnel: pd.DataFrame,
    water: pd.DataFrame,
    us_reference: pd.DataFrame,
    *,
    minimum_real_rows: int = 30,
) -> pd.DataFrame:
    """Generate a balanced scenario lattice with targets from explicit rules.

    Amounts are calibrated to local personnel residual quantiles.  U.S. data
    selects the area/complexity reference shape but is never interpreted as a
    direct Indonesian staffing standard.
    """
    rng = np.random.default_rng(SEED)
    if len(real_personnel) < minimum_real_rows:
        raise RuntimeError(f"At least {minimum_real_rows} training-only real area/personnel rows are required for local calibration")
    x = real_personnel[["log_area_ha"]].to_numpy()
    y = real_personnel["target_value"].to_numpy(dtype=float)
    robust = HuberRegressor().fit(x, y)
    residuals = y - robust.predict(x)
    local_median = float(np.quantile(y, 0.50))
    local_p90 = float(np.quantile(y, 0.90))
    residual_quantiles = {q: float(np.quantile(residuals, q)) for q in (0.35, 0.50, 0.65, 0.80, 0.90)}

    required_water = {
        "water_access_index_relative_0_100",
        "nearest_permanent_surface_water_km",
    }
    if required_water - set(water.columns):
        raise RuntimeError("Water Access Index source is missing required columns")
    water_pool = water.loc[:, sorted(required_water)].dropna().reset_index(drop=True)
    if water_pool.empty:
        raise RuntimeError("Water Access Index has no usable records")

    # The previous 0.05–60 ha lattice did not contain enough high-area cases:
    # a tree model consequently had no evidence with which to distinguish a
    # 100-ha input from a 1,000-ha input.  The upper strata match the US
    # reference's larger final-area bands, while Indonesian magnitude remains
    # anchored to the observed local median/P90 rather than copied from US
    # headcounts.
    ranges = [
        (0.05, 0.5),
        (0.5, 2.0),
        (2.0, 10.0),
        (10.0, 60.0),
        (60.0, 250.0),
        (250.0, 1_000.0),
        (1_000.0, 10_000.0),
    ]
    counts = [SYNTHETIC_ROWS // len(ranges)] * len(ranges)
    counts[-1] += SYNTHETIC_ROWS - sum(counts)
    area = np.concatenate([
        np.exp(rng.uniform(np.log(low), np.log(high), size=count))
        for (low, high), count in zip(ranges, counts, strict=True)
    ])
    frame = pd.DataFrame({"area_ha": area})
    large_area = frame["area_ha"].ge(60.0).to_numpy()
    # Complex peat conditions are intentionally more represented in the large
    # area strata. This produces an explicit high-case planning envelope,
    # instead of making all large cells benign by the small-incident mix.
    peat_probability = np.where(large_area, 0.70, 0.55)
    frame["soil_class"] = np.where(rng.random(len(frame)) < peat_probability, "peat", "mineral")
    peat_mask = frame["soil_class"].eq("peat").to_numpy()
    frame["fire_type_class"] = np.where(
        peat_mask,
        rng.choice(["ground_smouldering", "mixed", "surface"], len(frame), p=[0.55, 0.30, 0.15]),
        rng.choice(["surface", "mixed", "crown"], len(frame), p=[0.60, 0.30, 0.10]),
    )
    frame["fwi_class"] = rng.choice(
        ["low", "moderate", "high", "very_high"], len(frame), p=[0.15, 0.35, 0.35, 0.15]
    )
    if large_area.any():
        frame.loc[large_area, "fwi_class"] = rng.choice(
            ["moderate", "high", "very_high"], int(large_area.sum()), p=[0.10, 0.45, 0.45]
        )
    sampled_water = water_pool.iloc[rng.integers(0, len(water_pool), size=len(frame))].reset_index(drop=True)
    frame["water_access_index_relative_0_100"] = sampled_water["water_access_index_relative_0_100"]
    frame["nearest_permanent_surface_water_km"] = sampled_water["nearest_permanent_surface_water_km"]
    provisional = feature_frame(frame, us_reference, synthetic=True)
    water_remote = provisional["water_access_class"].eq("remote")
    water_constrained = provisional["water_access_class"].eq("constrained")
    fwi_high = provisional["fwi_class"].isin(["high", "very_high"])
    ground_or_mixed = provisional["fire_type_class"].isin(["ground_smouldering", "mixed"])
    score = (
        provisional["soil_class"].eq("peat").astype(int)
        + ground_or_mixed.astype(int)
        + fwi_high.astype(int)
        + water_remote.astype(int)
    )
    # A constrained source contributes to complexity only for a peat fire;
    # otherwise it remains an explicit logistics input for tanker planning.
    score += (water_constrained & provisional["soil_class"].eq("peat")).astype(int)
    score = score.clip(0, 4).astype(int)
    provisional["operational_complexity_level"] = score.map(
        {0: "initial_attack", 1: "elevated", 2: "extended", 3: "high", 4: "very_high"}
    )

    quantile = score.map({0: 0.35, 1: 0.50, 2: 0.65, 3: 0.80, 4: 0.90}).astype(float)
    # First derive a U.S. *relative area shape*.  It is anchored at the
    # <100-acre reference band and is then applied to locally calibrated
    # quantiles. This preserves how the comparative data changes between area
    # bands without treating any U.S. headcount as an Indonesian standard.
    us_span = provisional["us_peak_personnel_p90"] - provisional["us_peak_personnel_median"]
    us_quantile = provisional["us_peak_personnel_median"] + (
        ((quantile - 0.50).clip(lower=0) / 0.40) * us_span
    )
    anchor_median = float(us_reference.loc["<100", "peak_personnel_median"])
    anchor_p90 = float(us_reference.loc["<100", "peak_personnel_p90"])
    anchor_us_quantile = anchor_median + (
        ((quantile - 0.50).clip(lower=0) / 0.40) * (anchor_p90 - anchor_median)
    )
    relative_us_area_shape = us_quantile / anchor_us_quantile.replace(0, np.nan)
    local_quantile = local_median + (
        ((quantile - 0.50).clip(lower=0) / 0.40) * (local_p90 - local_median)
    )
    local_area_shape_prior = local_quantile * relative_us_area_shape
    local_area_estimate = robust.predict(provisional[["log_area_ha"]].to_numpy())
    residual_adjustment = quantile.map(residual_quantiles).to_numpy()
    local_small_area_estimate = local_area_estimate + residual_adjustment
    area_values = provisional["area_ha"].to_numpy()
    area_shape_blend = np.select(
        [area_values >= 1_000.0, area_values >= 250.0, area_values >= 60.0, area_values >= 10.0],
        [0.80, 0.70, 0.50, 0.25],
        default=0.10,
    )
    personnel = np.maximum(
        5,
        np.rint(
            (1.0 - area_shape_blend) * local_small_area_estimate
            + area_shape_blend * local_area_shape_prior
        ),
    ).astype(int)

    engine = (
        1
        + ((score.to_numpy() >= 3)).astype(int)
        + ((score.to_numpy() >= 2) & (area_values >= 1.5)).astype(int)
        + ((score.to_numpy() >= 4) & (area_values >= 10.0)).astype(int)
    ).clip(1, 4)
    tanker = (
        (water_constrained | water_remote | provisional["soil_class"].eq("peat")).astype(int).to_numpy()
        + ((score.to_numpy() >= 3) & (area_values >= 2.0)).astype(int)
        + (water_remote.to_numpy() & (area_values >= 5.0)).astype(int)
        + ((score.to_numpy() >= 4) & (area_values >= 10.0)).astype(int)
    ).clip(0, 4)
    pickup = (
        ((score.to_numpy() > 0) | (area_values >= 0.5)).astype(int)
        + ((score.to_numpy() >= 2) & (area_values >= 2.0)).astype(int)
        + ((score.to_numpy() >= 3) & (area_values >= 10.0)).astype(int)
    ).clip(0, 4)
    provisional["target_personnel"] = personnel
    provisional["target_fire_engine"] = engine.astype(int)
    provisional["target_water_tanker"] = tanker.astype(int)
    provisional["target_pickup"] = pickup.astype(int)
    return provisional[FEATURE_COLUMNS + list(TARGETS.values())]


def write_target_dataset(
    real: pd.DataFrame,
    synthetic: pd.DataFrame,
    target_column: str,
    target_name: str,
) -> dict[str, int]:
    real_dir = OUTPUT_DIR / "real"
    synthetic_dir = OUTPUT_DIR / "synthetic"
    training_dir = OUTPUT_DIR / "training"
    for directory in (real_dir, synthetic_dir, training_dir):
        directory.mkdir(parents=True, exist_ok=True)
    real_out = real[FEATURE_COLUMNS + ["target_value"]].copy()
    synthetic_out = synthetic[FEATURE_COLUMNS + [target_column]].rename(columns={target_column: "target_value"})
    training_out = pd.concat([real_out, synthetic_out], ignore_index=True).sample(
        frac=1.0,
        random_state=SEED + {"personnel": 1, "fire_engine": 2, "water_tanker": 3, "pickup": 4}[target_name],
    ).reset_index(drop=True)
    # There is deliberately no data-origin flag, ID, source URL, or source
    # hash in the model matrices.  Those would be leakage/metadata features.
    prohibited = {"is_synthetic", "simulation_only", "data_origin", "source_url", "source_sha256"}
    if prohibited & set(training_out.columns):
        raise RuntimeError("Training matrix contains prohibited provenance feature")
    for frame in (real_out, synthetic_out, training_out):
        frame["target_value"] = pd.to_numeric(frame["target_value"], errors="raise").round().astype(int)
    for directory, frame in ((real_dir, real_out), (synthetic_dir, synthetic_out), (training_dir, training_out)):
        frame.to_parquet(directory / f"{target_name}.parquet", index=False)
        frame.to_csv(directory / f"{target_name}.csv", index=False)
    return {"real_rows": int(len(real_out)), "synthetic_rows": int(len(synthetic_out)), "training_rows": int(len(training_out))}


def main() -> None:
    cohort = pd.read_parquet(COHORT_PATH)
    evidence = pd.read_parquet(EVIDENCE_PATH)
    summary = json.loads(US_SUMMARY_PATH.read_text(encoding="utf-8"))
    us_manifest = json.loads(US_MANIFEST_PATH.read_text(encoding="utf-8"))
    water = pd.read_parquet(WATER_PATH)
    us_reference = reference_lookup(summary)
    real = real_target_rows(cohort, evidence, us_reference)
    synthetic = synthetic_scenarios(real["personnel"], water, us_reference)
    datasets = {
        target_name: write_target_dataset(real[target_name], synthetic, target_column, target_name)
        for target_name, target_column in TARGETS.items()
    }
    manifest = {
        "status": "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "synthetic_scenario_rows": SYNTHETIC_ROWS,
        "directory_contract": {
            "real": "Observed source-derived labels only.",
            "synthetic": "Domain- and reference-calibrated planning scenarios only.",
            "training": "Deterministically shuffled union of real and synthetic matrices; no origin/simulation column is present.",
        },
        "datasets": datasets,
        "input_provenance": {
            "kalteng_cohort": {"path": str(COHORT_PATH.relative_to(ROOT)), "sha256": sha256(COHORT_PATH)},
            "vehicle_evidence": {"path": str(EVIDENCE_PATH.relative_to(ROOT)), "sha256": sha256(EVIDENCE_PATH)},
            "water_access_index": {"path": str(WATER_PATH.relative_to(ROOT)), "sha256": sha256(WATER_PATH)},
            "us_ics209_plus": {
                "figshare_article": us_manifest["figshare_article"],
                "doi": us_manifest["doi"],
                "wildfire_zip": str(US_WILDFIRE_ZIP.relative_to(ROOT)),
                "wildfire_zip_sha256": sha256(US_WILDFIRE_ZIP),
                "eda_summary": str(US_SUMMARY_PATH.relative_to(ROOT)),
                "eda_summary_sha256": sha256(US_SUMMARY_PATH),
                "reference_definition": "WF_PEAK_PERSONNEL by final-area band; used as a shape/quantile prior, not an Indonesian standard.",
            },
        },
        "local_personnel_calibration": {
            "real_rows": int(len(real["personnel"])),
            "median": float(real["personnel"]["target_value"].median()),
            "p90": float(real["personnel"]["target_value"].quantile(0.90)),
            "method": "Huber regression on log1p(area_ha) plus local residual quantiles for small areas; larger scenario labels blend this local estimate with the relative ICS-209-PLUS area-band shape anchored to the local median/P90.",
        },
        "domain_rule_sources": DOMAIN_RULE_SOURCES,
        "synthetic_label_rules": {
            "personnel": "Local Huber area trend + local residual quantile; complexity selects q=.35/.50/.65/.80/.90. The relative ICS-209-PLUS area-band shape is anchored to local median/P90 and receives a 10%/25%/50%/70%/80% blend by 0–10/10–60/60–250/250–1,000/1,000–10,000 ha, respectively. U.S. headcounts are never copied as Indonesian labels.",
            "fire_engine": "Initial one engine; increments for high complexity and larger scenarios; bounded 1–4 based on observed local explicit range.",
            "water_tanker": "Escalates when peat and/or constrained water logistics are present; bounded 0–4 based on observed local explicit range.",
            "pickup": "Logistics support increases with area and complexity; bounded 0–4 based on observed local explicit range.",
        },
        "not_permitted": [
            "Treat a synthetic planning label as an historical deployment fact.",
            "Treat U.S. personnel values as Indonesian staffing standards.",
            "Use a training-file row order as a substitute for a temporal/geographic validation split.",
            "Expose these model outputs as an automatic dispatch order.",
        ],
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "synthetic_generation_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["datasets"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
