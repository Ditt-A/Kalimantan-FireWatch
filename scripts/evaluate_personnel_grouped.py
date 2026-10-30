"""Evaluate real incidents with fold-local augmentation; never replace serving weights."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from train_feature2_models import clean_features, boost_pipeline, fold_synthetic, metrics, DATA_DIR

def evaluate():
    real = pd.read_parquet(DATA_DIR / "real/personnel.parquet")
    provenance = pd.read_csv(DATA_DIR / "evaluation_sidecars/personnel_real_provenance.csv")
    if len(real) != len(provenance) or provenance["source_document_id"].isna().any():
        raise ValueError("Real rows and source-group provenance must align")
    # Same incident can occur in multiple reports: union connected incident and
    # document groups before splitting, instead of assuming document IDs suffice.
    parent = list(range(len(real)))
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
    groups = pd.Series([find(i) for i in range(len(real))])
    if groups.nunique() < 2:
        raise ValueError("Independent document/incident groups are insufficient for evaluation")
    predictions, folds = [], []
    features = real.drop(columns="target_value").columns.tolist()
    for fold, (train, test) in enumerate(GroupKFold(n_splits=min(5, groups.nunique())).split(real, groups=groups), 1):
        train_real, test_real = real.iloc[train].copy(), real.iloc[test].copy()
        baseline = HuberRegressor(epsilon=1.35, alpha=1.0).fit(train_real[["log_area_ha"]], train_real["target_value"])
        # Fold-local generator currently requires >=30 records. Fall back to
        # real-only training on smaller folds; never pull held-out labels in.
        synthetic = fold_synthetic(train_real) if len(train_real) >= 30 else real.iloc[0:0].copy()
        train_frame = pd.concat([train_real, synthetic], ignore_index=True)
        weights = np.r_[np.full(len(train_real), 10.0), np.ones(len(synthetic))]
        booster = boost_pipeline(clean_features(train_frame[features]), min_samples_leaf=10)
        booster.fit(clean_features(train_frame[features]), train_frame["target_value"], regressor__sample_weight=weights)
        actual = test_real["target_value"].to_numpy()
        predictions.append(pd.DataFrame({
            "row_index": test, "fold": fold, "source_document_id": provenance.iloc[test]["source_document_id"].to_numpy(),
            "actual_personnel": actual,
            "baseline_area_huber": np.clip(baseline.predict(test_real[["log_area_ha"]]), 1, None),
            "boosting_hybrid": np.clip(booster.predict(clean_features(test_real[features])), 1, None),
        }))
        folds.append({"fold": fold, "real_train": len(train), "real_test": len(test), "synthetic_train": len(synthetic), "groups_disjoint": not bool(set(groups.iloc[train]) & set(groups.iloc[test]))})
    predicted = pd.concat(predictions).sort_values("row_index")
    if predicted["row_index"].duplicated().any() or len(predicted) != len(real):
        raise ValueError("OOF evaluation must predict every real incident exactly once")
    summary = {"evaluation": "real-only out-of-fold; union(document, incident); fold-local synthesis", "real_rows": len(real), "independent_groups": int(groups.nunique()), "folds": folds, "metrics": {}}
    y = predicted["actual_personnel"].to_numpy()
    for model in ["baseline_area_huber", "boosting_hybrid"]:
        p = predicted[model].to_numpy()
        summary["metrics"][model] = {**metrics(y, p), "underestimate_rate": float(np.mean(p < y)), "mean_shortfall_personnel": float(np.maximum(y-p, 0).mean())}
    summary["limitations"] = ["This evaluates historical deployed personnel, not optimal required staffing.", "Sparse real weather/water labels and limited provinces remain unresolved.", "The existing mixed-holdout serving model is not replaced by this audit."]
    output = ROOT / "reports/feature2_grouped_evaluation"
    output.mkdir(parents=True, exist_ok=True)
    predicted.to_csv(output / "real_oof_predictions.csv", index=False)
    (output / "evaluation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    evaluate()
