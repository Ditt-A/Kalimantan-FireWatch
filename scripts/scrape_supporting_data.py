#!/usr/bin/env python3
"""Download mandatory supporting geography for Kalimantan FireWatch."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import geopandas as gpd

ROOT = Path('data/raw')
BBOX = (108.0, -5.0, 119.5, 8.0)
GEOB_URL = 'https://www.geoboundaries.org/api/current/gbOpen/IDN/{level}/'
PEAT_QUERY = 'https://gfw-staging.wri.org/arcgis/rest/services/country_data/asia/MapServer/10/query'


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    failure = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=120, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            failure = exc
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f'GET failed: {url}') from failure


def write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.partial')
    partial.write_bytes(content)
    if not partial.exists() or partial.stat().st_size == 0:
        raise RuntimeError(f'Empty download for {path.name}')
    partial.replace(path)


def download_boundary(session: requests.Session, level: str) -> dict[str, object]:
    metadata = get(session, GEOB_URL.format(level=level)).json()
    source_url = metadata.get('gjDownloadURL')
    if not source_url:
        raise RuntimeError(f'geoBoundaries {level} did not return gjDownloadURL')
    response = get(session, source_url)
    payload = response.json()
    features = payload.get('features', [])
    if payload.get('type') != 'FeatureCollection' or not features:
        raise RuntimeError(f'Invalid/empty geoBoundaries {level} GeoJSON')
    target = ROOT / 'boundaries' / f'idn_{level.lower()}.geojson'
    write_atomic(target, response.content)
    return {'dataset': f'geoBoundaries_{level}', 'source_url': source_url, 'output': str(target), 'features': len(features), 'sha256': hashlib.sha256(response.content).hexdigest()}


def two_dimensional_coordinates(value: object) -> object:
    """Drop optional ArcGIS Z/M ordinates so the response is valid GeoJSON."""
    if not isinstance(value, list):
        return value
    if value and isinstance(value[0], (int, float)):
        return value[:2]
    return [two_dimensional_coordinates(item) for item in value]


def clip_peatland_to_kalimantan(features: list[dict[str, object]]) -> tuple[dict[str, object], int]:
    """Clip the broad WRI/GFW query to the five Kalimantan ADM1 polygons.

    The ArcGIS endpoint only filters by its envelope. An envelope is wider than
    the island, so retaining it as-is can leave geometries outside Kalimantan.
    The published project file must be an exact spatial subset, not merely a
    bounding-box query result.
    """
    cleaned_features = []
    for feature in features:
        geometry = dict(feature.get("geometry") or {})
        geometry["coordinates"] = two_dimensional_coordinates(geometry.get("coordinates"))
        cleaned_features.append({**feature, "geometry": geometry})
    peat = gpd.GeoDataFrame.from_features(cleaned_features, crs="EPSG:4326")
    adm1_path = ROOT / "boundaries" / "idn_adm1.geojson"
    if not adm1_path.exists():
        raise FileNotFoundError(f"ADM1 boundary required before peatland clipping: {adm1_path}")
    adm1 = gpd.read_file(adm1_path)
    name_column = next((column for column in ("shapeName", "NAME_1", "name") if column in adm1.columns), None)
    if name_column is None:
        raise RuntimeError("Could not identify the province-name column in ADM1 boundaries")
    kalimantan = adm1[adm1[name_column].astype(str).str.contains("Kalimantan", case=False, na=False)]
    if len(kalimantan) != 5:
        raise RuntimeError(f"Expected five Kalimantan provinces, found {len(kalimantan)}")
    island_geometry = kalimantan.to_crs(peat.crs).geometry.union_all()
    clipped = peat.loc[peat.geometry.intersects(island_geometry)].copy()
    clipped["geometry"] = clipped.geometry.intersection(island_geometry)
    clipped = clipped.loc[clipped.geometry.notna() & ~clipped.geometry.is_empty].copy()
    if clipped.empty:
        raise RuntimeError("Exact Kalimantan clipping removed every peatland feature")
    return json.loads(clipped.to_json()), len(clipped)


def download_peatland(session: requests.Session) -> dict[str, object]:
    params = {
        'where': '1=1', 'outFields': '*', 'returnGeometry': 'true', 'f': 'geojson',
        'geometry': ','.join(str(value) for value in BBOX), 'geometryType': 'esriGeometryEnvelope',
        'inSR': '4326', 'spatialRel': 'esriSpatialRelIntersects', 'outSR': '4326',
        'resultRecordCount': 1000, 'resultOffset': 0,
    }
    features: list[dict[str, object]] = []
    while True:
        response = get(session, PEAT_QUERY, params=params)
        payload = response.json()
        batch = payload.get('features')
        if not isinstance(batch, list):
            raise RuntimeError(f'Peatland endpoint returned no feature list: {payload}')
        features.extend(batch)
        print(f'peatland offset={params["resultOffset"]} rows={len(batch)}', flush=True)
        if len(batch) < params['resultRecordCount']:
            break
        params['resultOffset'] += len(batch)
    if not features:
        raise RuntimeError('Peatland query returned zero Kalimantan-intersecting features')
    geojson, clipped_count = clip_peatland_to_kalimantan(features)
    content = json.dumps(geojson, separators=(',', ':')).encode('utf-8')
    target = ROOT / 'peatland' / 'kalimantan_peatlands.geojson'
    write_atomic(target, content)
    return {
        'dataset': 'WRI_GFW_peatland', 'source_url': PEAT_QUERY, 'output': str(target),
        'queried_features_bbox': len(features), 'features': clipped_count,
        'spatial_filter': 'exact intersection with five Kalimantan ADM1 geometries',
        'temporal_coverage': 'static spatial layer; source has no date/year attributes',
        'sha256': hashlib.sha256(content).hexdigest(),
    }


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    manifest: list[dict[str, object]] = []
    for level in ('ADM1', 'ADM2'):
        result = download_boundary(session, level)
        manifest.append(result)
        print(f'{result["dataset"]}: {result["features"]} features', flush=True)
    peat = download_peatland(session)
    manifest.append(peat)
    print(f'{peat["dataset"]}: {peat["features"]} features', flush=True)
    record = {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'bbox_wgs84': BBOX, 'datasets': manifest}
    output = ROOT / 'supporting_data_manifest.json'
    output.write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(f'completed manifest={output}', flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
