"""Build direct-training Feature-2 datasets for the two MVP outputs.

Personnel is the only calibrated response target.  Fire-engine demand is a
transparent capacity conversion from the planned personnel result, not an
independent synthetic dispatch target.  This keeps the product contract
auditable while still producing model-readable CSV and Parquet matrices.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HYBRID_BASE = ROOT / "data/processed/feature2_hybrid_training"
COHORT_PATH = ROOT / "data/processed/feature2_response_planning/feature2_response_planning_cohort.parquet"
EVIDENCE_PATH = ROOT / "data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence.parquet"
OUTPUT_DIR = ROOT / "data/processed/feature2_model_ready"

# Permendagri 114/2018 is implemented in the cited BPK regulations as one
# fire-engine unit operated by six staff: commander, driver/pump operator,
# and four firefighters.  The ratio is used solely as a planning-capacity
# conversion; it is not an assertion that every Karhutla responder rides an
# engine, nor an automated dispatch instruction.
PERSONNEL_PER_FIRE_ENGINE = 6
ENGINE_POLICY_VERSION = "permendagri-114-2018-crew-capacity-v1"
PROHIBITED = {"is_synthetic", "simulation_only", "data_origin", "source_url", "source_sha256"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_row_hash(frame: pd.DataFrame) -> pd.Series:
    """Stable join key for the provenance sidecar, never a model feature."""
    text = frame.astype("string").fillna("<NA>").agg("|".join, axis=1)
    return text.map(lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())


def write_partition(target: str, real: pd.DataFrame, synthetic: pd.DataFrame) -> dict[str, int]:
    schema = list(real.columns)
    if schema != list(synthetic.columns):
        raise RuntimeError(f"{target}: partition schemas differ")
    if "target_value" not in schema:
        raise RuntimeError(f"{target}: target_value missing")
    if PROHIBITED & set(schema):
        raise RuntimeError(f"{target}: provenance leaked into model matrix")
    if real.empty or synthetic.empty:
        raise RuntimeError(f"{target}: a partition is empty")

    training = pd.concat([real, synthetic], ignore_index=True).sample(
        frac=1.0,
        random_state=20260908 + (1 if target == "personnel" else 2),
    ).reset_index(drop=True)
    for directory, frame in (
        (OUTPUT_DIR / "real", real),
        (OUTPUT_DIR / "synthetic", synthetic),
        (OUTPUT_DIR / "training", training),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(directory / f"{target}.parquet", index=False)
        frame.to_csv(directory / f"{target}.csv", index=False)
    return {
        "real_rows": int(len(real)),
        "synthetic_rows": int(len(synthetic)),
        "training_rows": int(len(training)),
        "schema": schema,
    }


def personnel_partitions() -> tuple[pd.DataFrame, pd.DataFrame]:
    real = pd.read_parquet(HYBRID_BASE / "real/personnel.parquet")
    synthetic = pd.read_parquet(HYBRID_BASE / "synthetic/personnel.parquet")
    return real, synthetic


def paired_real_engine_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return model rows and a provenance-only evaluation sidecar.

    Observed personnel is used here only to calibrate the conversion.  At
    serving time it is replaced by the preceding planned-personnel result.
    """
    cohort = pd.read_parquet(COHORT_PATH)
    evidence = pd.read_parquet(EVIDENCE_PATH)
    sources = [
        (
            cohort,
            "personnel_total",
            "fire_engine_count_explicit_positive",
            "incident_id",
            "source_document_id",
            "source_document_url_retrieved",
        ),
        (
            evidence,
            "personnel_total",
            "fire_engine_count_explicit_positive",
            "evidence_id",
            "source_id",
            "source_url",
        ),
    ]
    model_parts: list[pd.DataFrame] = []
    sidecar_parts: list[pd.DataFrame] = []
    for frame, people_col, engine_col, id_col, document_col, url_col in sources:
        subset = frame.loc[
            frame[people_col].notna() & frame[engine_col].notna(),
            [id_col, document_col, url_col, people_col, engine_col],
        ].copy()
        subset[people_col] = pd.to_numeric(subset[people_col], errors="coerce")
        subset[engine_col] = pd.to_numeric(subset[engine_col], errors="coerce")
        subset = subset.loc[(subset[people_col] > 0) & (subset[engine_col] >= 0)].copy()
        model = pd.DataFrame(
            {
                "planned_personnel": subset[people_col].round().astype(int),
                "target_value": subset[engine_col].round().astype(int),
            }
        )
        model["_row_hash"] = model_row_hash(model)
        sidecar = pd.DataFrame(
            {
                "model_row_hash": model["_row_hash"],
                "incident_or_evidence_id": subset[id_col].astype(str).to_numpy(),
                "source_document_id": subset[document_col].astype(str).to_numpy(),
                "source_url": subset[url_col].astype(str).to_numpy(),
                "observed_personnel_total": model["planned_personnel"].to_numpy(),
                "observed_fire_engine_count": model["target_value"].to_numpy(),
            }
        )
        model_parts.append(model.drop(columns="_row_hash"))
        sidecar_parts.append(sidecar)
    return pd.concat(model_parts, ignore_index=True), pd.concat(sidecar_parts, ignore_index=True)


def personnel_real_provenance(real_personnel: pd.DataFrame) -> pd.DataFrame:
    """Build source-group metadata matching the upstream direct personnel order."""
    cohort = pd.read_parquet(COHORT_PATH)
    evidence = pd.read_parquet(EVIDENCE_PATH)
    source_parts = []
    for frame, id_col, document_col, url_col in (
        (cohort, "incident_id", "source_document_id", "source_document_url_retrieved"),
        (evidence, "evidence_id", "source_id", "source_url"),
    ):
        subset = frame.loc[
            frame["area_ha"].notna()
            & pd.to_numeric(frame["area_ha"], errors="coerce").gt(0)
            & frame["personnel_total"].notna(),
            [id_col, document_col, url_col, "area_ha", "personnel_total"],
        ].copy()
        source_parts.append(
            pd.DataFrame(
                {
                    "incident_or_evidence_id": subset[id_col].astype(str).to_numpy(),
                    "source_document_id": subset[document_col].astype(str).to_numpy(),
                    "source_url": subset[url_col].astype(str).to_numpy(),
                    "area_ha": pd.to_numeric(subset["area_ha"], errors="raise").to_numpy(),
                    "personnel_total": pd.to_numeric(subset["personnel_total"], errors="raise").round().astype(int).to_numpy(),
                }
            )
        )
    source = pd.concat(source_parts, ignore_index=True)
    if len(source) != len(real_personnel):
        raise RuntimeError("Personnel source rows no longer match the direct real partition")
    if not np.allclose(source["area_ha"], real_personnel["area_ha"].astype(float)):
        raise RuntimeError("Personnel source area order no longer matches the direct real partition")
    if not np.array_equal(source["personnel_total"], real_personnel["target_value"].astype(int)):
        raise RuntimeError("Personnel source target order no longer matches the direct real partition")
    sidecar = source.drop(columns=["area_ha", "personnel_total"])
    sidecar.insert(0, "model_row_hash", model_row_hash(real_personnel))
    return sidecar


def synthetic_engine_rows(synthetic_personnel: pd.DataFrame) -> pd.DataFrame:
    planned = pd.to_numeric(synthetic_personnel["target_value"], errors="raise").round().astype(int)
    return pd.DataFrame(
        {
            "planned_personnel": planned,
            "target_value": np.ceil(planned / PERSONNEL_PER_FIRE_ENGINE).astype(int),
        }
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    real_personnel, synthetic_personnel = personnel_partitions()
    personnel_summary = write_partition("personnel", real_personnel, synthetic_personnel)
    personnel_sidecar = personnel_real_provenance(real_personnel)

    real_engine, engine_sidecar = paired_real_engine_rows()
    synthetic_engine = synthetic_engine_rows(synthetic_personnel)
    engine_summary = write_partition("fire_engine", real_engine, synthetic_engine)
    sidecar_dir = OUTPUT_DIR / "evaluation_sidecars"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    personnel_sidecar.to_csv(sidecar_dir / "personnel_real_provenance.csv", index=False)
    engine_sidecar.to_csv(sidecar_dir / "fire_engine_real_provenance.csv", index=False)

    manifest = {
        "status": "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Direct-training MVP datasets for personnel and fire-engine planning capacity.",
        "directory_contract": {
            "real": "Observed labels only; no provenance field is fed to the model.",
            "synthetic": "Deterministic planning scenarios only.",
            "training": "Deterministically shuffled real + synthetic union; no origin/simulation column.",
            "evaluation_sidecars": "Provenance and source-group holdout metadata; never a model input.",
        },
        "datasets": {"personnel": personnel_summary, "fire_engine": engine_summary},
        "fire_engine_contract": {
            "serving_dependency": "planned_personnel only",
            "formula": "ceil(planned_personnel / 6)",
            "personnel_per_fire_engine": PERSONNEL_PER_FIRE_ENGINE,
            "policy_version": ENGINE_POLICY_VERSION,
            "source": {
                "citation": "Permendagri 114/2018 implementation: one fire-engine unit is operated by six fire staff (commander, driver/pump operator, four firefighters).",
                "url": "https://peraturan.bpk.go.id/Download/361863/PERBUP%20NO.%2045%20TAHUN%202020.pdf",
                "scope_guardrail": "Municipal fire-service staffing standard used as a capacity proxy. It is not an Indonesian Karhutla dispatch standard and must remain operator-approved.",
            },
            "observed_pair_count": int(len(real_engine)),
            "observed_pair_limit": "Observed pairs are retained for calibration/sanity checks, not used to claim an empirical incident-level causal ratio.",
        },
        "input_provenance": {
            "personnel_hybrid_real": {
                "path": str((HYBRID_BASE / 'real/personnel.parquet').relative_to(ROOT)),
                "sha256": sha256(HYBRID_BASE / "real/personnel.parquet"),
            },
            "personnel_hybrid_synthetic": {
                "path": str((HYBRID_BASE / 'synthetic/personnel.parquet').relative_to(ROOT)),
                "sha256": sha256(HYBRID_BASE / "synthetic/personnel.parquet"),
            },
            "kalteng_cohort": {"path": str(COHORT_PATH.relative_to(ROOT)), "sha256": sha256(COHORT_PATH)},
            "vehicle_evidence": {"path": str(EVIDENCE_PATH.relative_to(ROOT)), "sha256": sha256(EVIDENCE_PATH)},
        },
        "not_permitted": [
            "Use observed personnel_total as a fire-engine feature at serving time; use the preceding planned_personnel result instead.",
            "Treat the six-person capacity conversion as an automatic dispatch order.",
            "Use source/provenance fields as model features or report synthetic-only validation as real-world accuracy.",
        ],
    }
    (OUTPUT_DIR / "feature2_model_ready_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["datasets"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
