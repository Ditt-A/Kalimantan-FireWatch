"""Download the official Kalimantan concession layers and the Figshare archive.

This is a lightweight replacement for the supplied notebook: it uses public
HTTP/ArcGIS REST endpoints directly, with no Jupyter, Colab, browser, or
Playwright dependency.

Examples
--------
python scripts/acquire_kalimantan_data.py
python scripts/acquire_kalimantan_data.py --datasets wiup,pbph
python scripts/acquire_kalimantan_data.py --datasets figshare
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import requests
from shapely.geometry import MultiPolygon, Polygon, mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OFFICIAL_DIR = DATA_DIR / "official_concessions"
FIGSHARE_DIR = DATA_DIR / "figshare_all_hazards"

KALIMANTAN_BBOX = {
    "xmin": 108.5,
    "ymin": -4.6,
    "xmax": 119.5,
    "ymax": 4.6,
    "spatialReference": {"wkid": 4326},
}

MOMI_CONFIG_URL = "https://geoportal.esdm.go.id/minerba/config.json"
SIGAP_URL = "https://sigap.kehutanan.go.id/"
FIGSHARE_ARTICLE_ID = 19858927
FIGSHARE_API_URL = f"https://api.figshare.com/v2/articles/{FIGSHARE_ARTICLE_ID}"

# Used only if an official app configuration is temporarily unavailable.
WIUP_FALLBACK_URL = (
    "https://geoportal.esdm.go.id/monaresia/sharing/servers/"
    "3b305b4113384b41b7490479e0702093/rest/services/Pusat/WIUP_Publish/MapServer/0"
)
PBPH_FALLBACK_URL = (
    "https://geoportal.planologi.kehutanan.go.id/server/rest/services/"
    "Peta_Interaktif_2026/PBPH_AR_50K/MapServer/0"
)


class AcquisitionError(RuntimeError):
    """The official source could not provide a usable public response."""


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "Kalimantan-FireWatch-data-acquisition/1.0 "
            "(public ArcGIS REST client)",
            "Accept": "application/json, text/plain, */*",
        }
    )
    return client


def get_json(client: requests.Session, url: str, **params: Any) -> dict[str, Any]:
    response = client.get(url, params=params, timeout=120)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise AcquisitionError(f"Expected a JSON object from {response.url}")
    if "error" in payload:
        raise AcquisitionError(f"ArcGIS error from {response.url}: {payload['error']}")
    return payload


def post_json(client: requests.Session, url: str, data: dict[str, Any]) -> dict[str, Any]:
    response = client.post(url, data=data, timeout=240)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise AcquisitionError(f"Expected a JSON object from {response.url}")
    if "error" in payload:
        raise AcquisitionError(f"ArcGIS error from {response.url}: {payload['error']}")
    return payload


def walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def as_layer_url(url: str, preferred_layer: int = 0) -> str:
    return url.rstrip("/") if re.search(r"/(?:MapServer|FeatureServer)/\d+$", url) else f"{url.rstrip('/')}/{preferred_layer}"


def discover_wiup(client: requests.Session) -> str:
    """Read the current public ESDM web-map configuration and select WIUP."""
    try:
        app_config = get_json(client, MOMI_CONFIG_URL)
        portal_url = str(app_config["portalUrl"]).rstrip("/")
        item_id = str(app_config["map"]["itemId"])
        webmap = get_json(
            client,
            f"{portal_url}/sharing/rest/content/items/{item_id}/data",
            f="json",
        )
        candidates = []
        for item in walk_dicts(webmap):
            label = " ".join(str(item.get(key, "")) for key in ("title", "name", "label"))
            url = item.get("url")
            if isinstance(url, str) and "izin usaha pertambangan" in label.lower():
                candidates.append(as_layer_url(url))
        if candidates:
            return candidates[0]
    except (KeyError, TypeError, requests.RequestException, AcquisitionError) as error:
        print(f"WIUP discovery failed; using current official fallback: {error}")
    return WIUP_FALLBACK_URL


def discover_pbph(client: requests.Session) -> str:
    """Read SIGAP's public Experience Builder config and select PBPH."""
    try:
        page = client.get(SIGAP_URL, timeout=120)
        page.raise_for_status()
        match = re.search(r"PETA_INTERAKTIF_URL:\\?\"([^\"]+)", page.text)
        if not match:
            match = re.search(
                r"https://[^\"']+/portal/apps/experiencebuilder/experience/\\?id=[a-f0-9]{32}",
                page.text,
                flags=re.IGNORECASE,
            )
        experience_url = match.group(1) if match and match.lastindex else match.group(0) if match else ""
        query = parse_qs(urlparse(experience_url).query)
        item_id = query["id"][0]
        parsed = urlparse(experience_url)
        portal_url = f"{parsed.scheme}://{parsed.netloc}/portal"
        config = get_json(
            client,
            f"{portal_url}/sharing/rest/content/items/{item_id}/data",
            f="json",
        )
        candidates = []
        for item in walk_dicts(config):
            label = " ".join(
                str(item.get(key, "")) for key in ("sourceLabel", "label", "title", "name")
            )
            url = item.get("url")
            if isinstance(url, str) and "pbph" in label.lower():
                candidates.append(as_layer_url(url))
        if candidates:
            return candidates[0]
    except (KeyError, TypeError, requests.RequestException, AcquisitionError) as error:
        print(f"PBPH discovery failed; using current official fallback: {error}")
    return PBPH_FALLBACK_URL


def layer_metadata(client: requests.Session, layer_url: str) -> dict[str, Any]:
    metadata = get_json(client, layer_url, f="json")
    if metadata.get("geometryType") != "esriGeometryPolygon":
        raise AcquisitionError(f"Not a polygon layer: {layer_url}")
    if "query" not in str(metadata.get("capabilities", "")).lower():
        raise AcquisitionError(f"Layer has no public query capability: {layer_url}")
    return metadata


def polygon_from_arcgis_rings(rings: list[list[list[float]]]) -> dict[str, Any] | None:
    """Convert Esri polygon rings to GeoJSON while retaining holes and islands."""
    polygons = []
    for ring in rings:
        if len(ring) < 4:
            continue
        polygon = Polygon(ring)
        if not polygon.is_empty and polygon.area > 0:
            polygons.append(polygon)
    if not polygons:
        return None

    parents: list[int | None] = []
    for index, polygon in enumerate(polygons):
        containers = [
            other_index
            for other_index, other in enumerate(polygons)
            if other_index != index
            and other.area > polygon.area
            and other.contains(polygon.representative_point())
        ]
        parents.append(min(containers, key=lambda item: polygons[item].area) if containers else None)

    def depth(index: int) -> int:
        current, levels = parents[index], 0
        while current is not None:
            levels += 1
            current = parents[current]
        return levels

    assembled = []
    for index, polygon in enumerate(polygons):
        if depth(index) % 2:
            continue
        holes = [
            list(polygons[child].exterior.coords)
            for child in range(len(polygons))
            if parents[child] == index and depth(child) % 2 == 1
        ]
        assembled.append(Polygon(list(polygon.exterior.coords), holes))
    geometry = assembled[0] if len(assembled) == 1 else MultiPolygon(assembled)
    return mapping(geometry)


def as_geojson_feature(raw: dict[str, Any], geometry_type: str) -> dict[str, Any]:
    """Normalise either GeoJSON or Esri JSON feature records to GeoJSON."""
    properties = raw.get("properties") or raw.get("attributes") or {}
    geometry = raw.get("geometry")
    if geometry is None:
        output_geometry = None
    elif isinstance(geometry, dict) and geometry.get("type"):
        output_geometry = geometry
    elif geometry_type == "esriGeometryPolygon" and isinstance(geometry, dict):
        output_geometry = polygon_from_arcgis_rings(geometry.get("rings") or [])
    else:
        raise AcquisitionError(f"Unsupported ArcGIS geometry response: {geometry_type}")
    return {"type": "Feature", "properties": properties, "geometry": output_geometry}


def fetch_features(
    client: requests.Session, layer_url: str, metadata: dict[str, Any]
) -> list[dict[str, Any]]:
    common = {
        "where": "1=1",
        "geometry": json.dumps(KALIMANTAN_BBOX, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
    }
    ids = post_json(client, f"{layer_url}/query", {**common, "returnIdsOnly": "true", "f": "json"})
    object_ids = ids.get("objectIds") or []
    if not isinstance(object_ids, list):
        raise AcquisitionError(f"Unexpected object ID response from {layer_url}")

    print(f"{layer_url}: {len(object_ids):,} Kalimantan features")
    max_record_count = int(metadata.get("maxRecordCount") or 500)
    chunk_size = max(1, min(max_record_count, 500))
    features: list[dict[str, Any]] = []
    for offset in range(0, len(object_ids), chunk_size):
        subset = object_ids[offset : offset + chunk_size]
        page = post_json(
            client,
            f"{layer_url}/query",
            {
                "objectIds": ",".join(map(str, subset)),
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            },
        )
        chunk = page.get("features") or []
        if not isinstance(chunk, list):
            raise AcquisitionError(f"Unexpected feature response from {layer_url}")
        features.extend(
            as_geojson_feature(feature, str(metadata["geometryType"])) for feature in chunk
        )
        print(f"  downloaded {min(offset + len(subset), len(object_ids)):,}/{len(object_ids):,}")
    return features


def save_geojson(
    destination: Path, name: str, features: list[dict[str, Any]], source_url: str, metadata: dict[str, Any]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    feature_collection = {
        "type": "FeatureCollection",
        "name": name,
        "features": features,
    }
    destination.write_text(json.dumps(feature_collection, ensure_ascii=False), encoding="utf-8")
    provenance = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_layer_url": source_url,
        "source_layer_name": metadata.get("name"),
        "feature_count": len(features),
        "bbox_wgs84": KALIMANTAN_BBOX,
    }
    destination.with_suffix(".provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_attributes_csv(destination: Path, features: list[dict[str, Any]]) -> Path:
    rows = [feature["properties"] for feature in features]
    fields = sorted({field for row in rows for field in row})
    csv_path = destination.with_name(destination.stem + "_attributes.csv")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def acquire_layer(client: requests.Session, dataset: str) -> dict[str, Any]:
    if dataset == "wiup":
        source_url = discover_wiup(client)
        output = OFFICIAL_DIR / "wiup_kalimantan_official.geojson"
        name = "WIUP Kalimantan — official ESDM"
    else:
        source_url = discover_pbph(client)
        output = OFFICIAL_DIR / "pbph_kalimantan_official.geojson"
        name = "PBPH Kalimantan — official SIGAP"

    metadata = layer_metadata(client, source_url)
    features = fetch_features(client, source_url, metadata)
    save_geojson(output, name, features, source_url, metadata)
    geometry_count = sum(feature["geometry"] is not None for feature in features)
    result = {
        "output": str(output),
        "source_layer": source_url,
        "features": len(features),
        "features_with_geometry": geometry_count,
    }
    if geometry_count == 0:
        csv_path = save_attributes_csv(output, features)
        result["attributes_csv"] = str(csv_path)
        print(
            "The public server returned PBPH attributes but no feature geometries; "
            f"saved the EDA-ready attribute table to {csv_path}."
        )
    return result


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_figshare(client: requests.Session) -> dict[str, Any]:
    article = get_json(client, FIGSHARE_API_URL)
    FIGSHARE_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for remote in article.get("files", []):
        name = remote["name"]
        expected_md5 = remote["supplied_md5"]
        destination = FIGSHARE_DIR / name
        if destination.is_file() and file_md5(destination) == expected_md5:
            print(f"Figshare already verified: {name}")
            downloaded.append({"name": name, "status": "verified"})
            continue

        temporary = destination.with_suffix(destination.suffix + ".part")
        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with client.get(remote["download_url"], headers=headers, stream=True, timeout=300) as response:
            response.raise_for_status()
            mode = "ab" if offset and response.status_code == 206 else "wb"
            with temporary.open(mode) as stream:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        stream.write(block)
        if file_md5(temporary) != expected_md5:
            raise AcquisitionError(f"Checksum mismatch after downloading {name}")
        temporary.replace(destination)
        print(f"Downloaded and verified: {name}")
        downloaded.append({"name": name, "status": "downloaded"})

    manifest = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "figshare_article": article["figshare_url"],
        "doi": article.get("doi"),
        "citation": article.get("citation"),
        "license": article.get("license"),
        "files": article.get("files"),
    }
    (FIGSHARE_DIR / "figshare_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"output": str(FIGSHARE_DIR), "files": downloaded}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        default="wiup,pbph,figshare",
        help="Comma-separated: wiup, pbph, figshare (default: all)",
    )
    args = parser.parse_args()
    requested = {value.strip().lower() for value in args.datasets.split(",") if value.strip()}
    unknown = requested - {"wiup", "pbph", "figshare"}
    if unknown:
        parser.error(f"Unknown dataset(s): {', '.join(sorted(unknown))}")

    client = session()
    results: dict[str, Any] = {"started_at_utc": datetime.now(timezone.utc).isoformat()}
    for dataset in ("wiup", "pbph"):
        if dataset in requested:
            results[dataset] = acquire_layer(client, dataset)
    if "figshare" in requested:
        results["figshare"] = download_figshare(client)

    results["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "acquisition_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
