"""Nested real-group validation for synthetic jitter; candidate weights stay separate."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_absolute_error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from train_feature2_models import boost_pipeline, clean_features, metrics, DATA_DIR
from build_feature2_hybrid_training_data import synthetic_scenarios, reference_lookup, US_SUMMARY_PATH, WATER_PATH, us_area_band
from src.firewatch.releases import atomic_json, sha256

CONFIGS = {
    "reference": {"area_sigma": 0.0, "label_sigma": 0.0, "align_missingness": False},
    "jitter_light": {"area_sigma": 0.08, "label_sigma": 0.10, "align_missingness": True},
    "jitter_medium": {"area_sigma": 0.15, "label_sigma": 0.20, "align_missingness": True},
}

def independent_groups(provenance):
    parent = list(range(len(provenance)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for column in ["source_document_id", "incident_or_evidence_id"]:
        seen = {}
        for i, value in enumerate(provenance[column]):
            if pd.isna(value):
                continue
            if value in seen:
                parent[find(i)] = find(seen[value])
            else:
                seen[value] = i
    return pd.Series([find(i) for i in range(len(provenance))])

def augment(real, config_name, seed):
    config = CONFIGS[config_name]
    rng = np.random.default_rng(seed)
    reference = reference_lookup(json.loads(US_SUMMARY_PATH.read_text()))
    water = pd.read_parquet(WATER_PATH)
    synthetic = synthetic_scenarios(real, water, reference, minimum_real_rows=12)
    synthetic = synthetic.drop(columns=["target_fire_engine", "target_water_tanker", "target_pickup"]).rename(columns={"target_personnel": "target_value"})
    # Area jitter represents uncertain measurements, not an altered historical
    # label. Keep derived area bands and benchmark features internally coherent.
    synthetic["area_ha"] = (synthetic["area_ha"] * rng.lognormal(0, config["area_sigma"], len(synthetic))).clip(0.01, 10_000)
    synthetic["log_area_ha"] = np.log1p(synthetic["area_ha"])
    synthetic["us_area_band"] = us_area_band(synthetic["area_ha"])
    for target, source in [("us_peak_personnel_median", "peak_personnel_median"), ("us_peak_personnel_p90", "peak_personnel_p90")]:
        synthetic[target] = synthetic["us_area_band"].map(reference[source]).astype(float)
    # Mean-one heteroscedastic multiplicative noise softens the deterministic
    # teacher. These are candidate assumptions selected on inner validation.
    sigma = config["label_sigma"] * (0.5 + 0.5 * np.sqrt(synthetic["log_area_ha"] / np.log1p(10_000)))
    synthetic["target_value"] = np.maximum(1, np.rint(synthetic["target_value"] * np.exp(rng.normal(0, 1, len(synthetic)) * sigma - 0.5 * sigma ** 2)))
    if config["align_missingness"]:
        # Match missingness from training rows only. Do not manufacture weather
        # or water observations for real rows, and do not expose origin to ML.
        for column in real.columns.drop("target_value"):
            if column in {"area_ha", "log_area_ha", "us_area_band", "us_peak_personnel_median", "us_peak_personnel_p90"}:
                continue
            if pd.api.types.is_numeric_dtype(real[column]):
                rate = real[column].isna().mean()
                synthetic.loc[rng.random(len(synthetic)) < rate, column] = np.nan
            else:
                rate = real[column].fillna("unknown").eq("unknown").mean()
                synthetic.loc[rng.random(len(synthetic)) < rate, column] = "unknown"
    return synthetic

def fit_candidate(real, config_name, seed):
    synthetic = augment(real, config_name, seed)
    frame = pd.concat([real, synthetic], ignore_index=True)
    x = clean_features(frame.drop(columns="target_value"))
    model = boost_pipeline(x, min_samples_leaf=15)
    model.fit(x, frame["target_value"], regressor__sample_weight=np.r_[np.full(len(real), 10.0), np.ones(len(synthetic))])
    return model, synthetic

def choose_config(real, groups, seed):
    losses = {name: [] for name in CONFIGS}
    for fold, (train, valid) in enumerate(GroupKFold(n_splits=min(3, groups.nunique())).split(real, groups=groups)):
        for name in CONFIGS:
            model, _ = fit_candidate(real.iloc[train].copy(), name, seed + fold)
            predicted = model.predict(clean_features(real.iloc[valid].drop(columns="target_value")))
            losses[name].append((len(valid), mean_absolute_error(real.iloc[valid]["target_value"], predicted)))
    mean = {name: float(sum(n * loss for n, loss in values) / sum(n for n, _ in values)) for name, values in losses.items()}
    return min(mean, key=mean.get), mean

def main():
    real_path = DATA_DIR / "real/personnel.parquet"
    provenance_path = DATA_DIR / "evaluation_sidecars/personnel_real_provenance.csv"
    real = pd.read_parquet(real_path)
    provenance = pd.read_csv(provenance_path)
    if len(real) != len(provenance) or provenance["source_document_id"].isna().any():
        raise ValueError("Real data and provenance must be complete and aligned")
    groups = independent_groups(provenance)
    if groups.nunique() < 5:
        raise ValueError("At least five independent source/incident groups are needed")
    results, fold_reports = [], []
    for fold, (train, test) in enumerate(GroupKFold(n_splits=5).split(real, groups=groups), 1):
        train_real, test_real = real.iloc[train].copy(), real.iloc[test].copy()
        selection, inner = choose_config(train_real.reset_index(drop=True), groups.iloc[train].reset_index(drop=True), 7300 + fold * 100)
        test_x = clean_features(test_real.drop(columns="target_value"))
        selected, _ = fit_candidate(train_real, selection, 9300 + fold)
        reference, _ = fit_candidate(train_real, "reference", 9300 + fold)
        baseline = HuberRegressor(epsilon=1.35, alpha=1.0).fit(train_real[["log_area_ha"]], train_real["target_value"])
        results.append(pd.DataFrame({"row_index": test, "fold": fold, "actual": test_real["target_value"].to_numpy(), "nested_selected": selected.predict(test_x), "reference": reference.predict(test_x), "area_huber": baseline.predict(test_real[["log_area_ha"]])}))
        fold_reports.append({"fold": fold, "selected_by_inner_real_validation": selection, "inner_mae": inner, "outer_real_test_rows": len(test), "group_overlap": bool(set(groups.iloc[train]) & set(groups.iloc[test]))})
        print(f"Completed outer real-data fold {fold}/5", flush=True)
    predictions = pd.concat(results).sort_values("row_index")
    selection, inner = choose_config(real, groups, 13300)
    final_model, synthetic = fit_candidate(real, selection, 14300)
    output = ROOT / "models/candidates/personnel_jitter"
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, output / "personnel.joblib")
    training = pd.concat([real, synthetic], ignore_index=True)
    training.to_parquet(output / "training.parquet", index=False)
    # Origin is kept in a sidecar and never enters the model matrix.
    pd.DataFrame({"row_index": np.arange(len(training)), "origin": ["real"] * len(real) + ["synthetic"] * len(synthetic)}).to_csv(output / "training_origin.csv", index=False)
    predictions.to_csv(output / "real_nested_oof.csv", index=False)
    summary = {
        "status": "candidate_not_promoted", "protocol": "nested GroupKFold; both inner validation and outer test are untouched real cases; fold-local synthetic generation",
        "real_rows": len(real), "groups": int(groups.nunique()), "folds": fold_reports,
        "metrics": {name: metrics(predictions["actual"].to_numpy(), predictions[name].to_numpy()) for name in ["nested_selected", "reference", "area_huber"]},
        "final_configuration": selection, "final_inner_validation_mae": inner, "configurations": CONFIGS,
        "model_sha256": sha256(output / "personnel.joblib"), "real_dataset_sha256": sha256(real_path), "provenance_sha256": sha256(provenance_path),
        "limitations": ["Jitter adds uncertainty, not new independent field evidence.", "Nonlinear response assumptions are inherited from the scenario generator and are not validated causal resource laws.", "Current real FWI/water missingness severely limits validation of those feature effects.", "Retain operator review and prospective shadow evaluation; 43 real cases do not establish operational accuracy."],
    }
    atomic_json(output / "evaluation.json", summary)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
