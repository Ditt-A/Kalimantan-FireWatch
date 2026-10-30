"""Versioned runtime snapshots, request pinning, and atomic publication."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache, wraps
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = (
    "data/processed/prediction_grid_week.parquet",
    "data/processed/firms_kalimantan_clean.parquet",
    "data/processed/dim_grid.parquet",
    "data/processed/water_access_grid.parquet",
    "data/processed/soil/soilgrids_histosols_context_grid.parquet",
    "data/processed/peatland/big_peat_extent_kalimantan.parquet",
    "data/processed/peatland/big_peat_function_kalimantan.parquet",
    "data/processed/feature2_response_planning/feature2_response_planning_cohort.parquet",
    "data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence.parquet",
    "data/responders/osm_fire_stations_kalimantan.csv",
    "data/responders/osm_air_response_locations_kalimantan.csv",
    "models/dashboard_model_registry.json",
    "models/fire_occurrence_classifier.joblib",
    "models/hotspot_count_model.joblib",
    "models/feature2_personnel_hgb_pilot.joblib",
    "models/feature2_fire_engine_policy.json",
    "reports/us_fire_resource_eda/resource_by_final_acres_band.csv",
)

@dataclass(frozen=True)
class Snapshot:
    token: str
    root: Path
    metadata: dict[str, Any]

_bound: ContextVar[Snapshot | None] = ContextVar("firewatch_release", default=None)

def release_store() -> Path:
    return Path(os.environ.get("FIREWATCH_RELEASE_ROOT", PROJECT_ROOT / "data/operational/releases")).resolve()

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Invalid release identifier")
    return value

@lru_cache(maxsize=8)
def _published_snapshot(store: str, release_id: str) -> Snapshot:
    root = Path(store) / _safe_id(release_id)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("release_id") != release_id or manifest.get("quality_status") != "passed":
        raise ValueError("Release manifest is not approved")
    for relative, spec in manifest["files"].items():
        path = root / relative
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != spec["bytes"]:
            raise ValueError(f"Release artifact is missing or incomplete: {relative}")
    return Snapshot(release_id, root, manifest)

def current_snapshot() -> Snapshot:
    bound = _bound.get()
    if bound is not None:
        return bound
    store = release_store()
    pointer = store / "current.json"
    if pointer.exists():
        return _published_snapshot(str(store), json.loads(pointer.read_text())["release_id"])
    # Local legacy operation remains usable; cache keys follow actual file updates.
    signature = []
    for relative in RUNTIME_FILES:
        path = PROJECT_ROOT / relative
        stat = path.stat() if path.exists() else None
        signature.append((relative, stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0))
    fingerprint = hashlib.sha256(repr(signature).encode()).hexdigest()[:16]
    return Snapshot("local-" + fingerprint, PROJECT_ROOT, {"mode": "legacy_snapshot"})

@contextmanager
def bind_snapshot(snapshot: Snapshot):
    token = _bound.set(snapshot)
    try:
        yield snapshot
    finally:
        _bound.reset(token)

def release_path(relative: str) -> Path:
    root = current_snapshot().root
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Runtime artifact path escapes release")
    return path

class ReleaseDirectory:
    """Resolve at call time, after middleware pins the request to a release."""
    def __init__(self, relative: str):
        self.relative = relative
    def __truediv__(self, child: str) -> Path:
        return release_path(str(Path(self.relative) / child))

def release_cached(function):
    @lru_cache(maxsize=4)
    def cached(token: str, root: str, args: tuple, kwargs: tuple):
        with bind_snapshot(Snapshot(token, Path(root), {})):
            return function(*args, **dict(kwargs))
    @wraps(function)
    def wrapper(*args, **kwargs):
        snapshot = current_snapshot()
        return cached(snapshot.token, str(snapshot.root), args, tuple(sorted(kwargs.items())))
    wrapper.cache_clear = cached.cache_clear
    return wrapper

def activate_release(release_id: str, *, expected_release_id: str | None = None) -> None:
    store = release_store()
    snapshot = _published_snapshot(str(store), _safe_id(release_id))
    for relative, spec in snapshot.metadata["files"].items():
        if sha256(snapshot.root / relative) != spec["sha256"]:
            raise ValueError(f"Release checksum mismatch: {relative}")
    store.mkdir(parents=True, exist_ok=True)
    with (store / "publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if expected_release_id is not None and current_snapshot().token != expected_release_id:
            raise ValueError("Release changed during processing; rebuild against the current release")
        atomic_json(store / "current.json", {"release_id": release_id, "activated_at_utc": datetime.now(timezone.utc).isoformat()})

def publish_release(source: Path, release_id: str, metadata: dict[str, Any], overrides: dict[str, Path] | None = None, *, activate: bool = True, expected_release_id: str | None = None) -> Path:
    """Copy a complete bundle, checksum it, and atomically replace the pointer."""
    source = source.resolve()
    overrides = overrides or {}
    store = release_store()
    store.mkdir(parents=True, exist_ok=True)
    release_id = _safe_id(release_id)
    destination = store / release_id
    if destination.exists():
        raise ValueError("Release already exists; immutable releases cannot be overwritten")
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=store))
    try:
        files = {}
        for relative in RUNTIME_FILES:
            origin = overrides.get(relative, source / relative)
            if not origin.is_file():
                raise ValueError(f"Required runtime artifact missing: {relative}")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
            files[relative] = {"sha256": sha256(target), "bytes": target.stat().st_size}
        manifest = {**metadata, "release_id": release_id, "quality_status": "passed", "created_at_utc": datetime.now(timezone.utc).isoformat(), "files": files}
        atomic_json(staging / "manifest.json", manifest)
        staging.rename(destination)
        if activate:
            activate_release(release_id, expected_release_id=expected_release_id)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)
