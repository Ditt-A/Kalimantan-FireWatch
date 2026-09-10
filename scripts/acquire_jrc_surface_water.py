#!/usr/bin/env python3
"""Acquire the official JRC Global Surface Water tiles covering Kalimantan.

The two downloaded map products are intentionally kept separate:

* occurrence: percentage of valid Landsat observations classified as water;
* seasonality: number of months (1--12) water was present.

They describe long-run surface-water evidence, not whether a fire appliance can
draw water at this moment.  The latter needs field verification and, eventually,
network routing plus locally maintained water-point records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "raw" / "jrc_surface_water"
BASE_URL = (
    "https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/"
    "download2024/Aggregated/VER1-5"
)
PRODUCTS = ("occurrence", "seasonality")
# Kalimantan spans the 100--110E and 110--120E tiles, immediately south and
# north of the equator.  In JRC naming, ``0N`` has bounds -10..0 and ``10N``
# has bounds 0..10.
TILES = ("100E_0N", "110E_0N", "100E_10N", "110E_10N")
SOURCE_PAGE = "https://global-surface-water.appspot.com/download"
ATTRIBUTION = "Source: EC JRC/Google"


def source_url(product: str, tile: str) -> str:
    return f"{BASE_URL}/{product}/{product}_{tile}_v1_5_2024.tif"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def raster_quality(path: Path, product: str) -> dict[str, object]:
    """Perform a bounded read so corrupt/empty GeoTIFFs cannot pass silently."""
    with rasterio.open(path) as source:
        sample = source.read(
            1,
            out_shape=(1, min(512, source.height), min(512, source.width)),
            masked=True,
        )
        raw = sample.compressed()
        # The producer's product metadata uses 255 for no data.  The GeoTIFF
        # itself does not consistently advertise it as a GDAL nodata value.
        valid = raw[raw != 255]
        if valid.size == 0:
            raise RuntimeError(f"{path.name}: no valid pixels in representative sample")
        valid_min = int(valid.min())
        valid_max = int(valid.max())
        allowed_max = 100 if product == "occurrence" else 12
        if valid_min < 0 or valid_max > allowed_max:
            raise RuntimeError(
                f"{path.name}: values [{valid_min}, {valid_max}] outside expected 0..{allowed_max}"
            )
        return {
            "crs": str(source.crs),
            "bounds": [round(value, 6) for value in source.bounds],
            "width": source.width,
            "height": source.height,
            "nodata": source.nodata,
            "sample_valid_pixels": int(valid.size),
            "sample_min": valid_min,
            "sample_max": valid_max,
        }


def download(url: str, destination: Path, product: str) -> dict[str, object]:
    if destination.is_file() and destination.stat().st_size > 1024:
        return {
            "status": "cached",
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
            "raster": raster_quality(destination, product),
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        expected = int(response.headers.get("content-length", 0))
        received = 0
        with partial.open("wb") as handle:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if not block:
                    continue
                handle.write(block)
                digest.update(block)
                received += len(block)
    if received < 1024 or (expected and received != expected):
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"{destination.name}: received {received} of expected {expected} bytes")
    partial.replace(destination)
    return {
        "status": "downloaded",
        "bytes": received,
        "sha256": digest.hexdigest(),
        "raster": raster_quality(destination, product),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    manifest: dict[str, object] = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "JRC Global Surface Water v1.5, aggregated products (1984-2024)",
        "landing_page": SOURCE_PAGE,
        "citation": (
            "Pekel, J.-F., Cottam, A., Gorelick, N. & Belward, A. S. (2016). "
            "High-resolution mapping of global surface water and its long-term changes. "
            "Nature, 540, 418-422. https://doi.org/10.1038/nature20584"
        ),
        "license": "Copernicus Programme: free of charge without restriction of use",
        "required_attribution": ATTRIBUTION,
        "scope_limitations": [
            "Long-run satellite-derived surface-water evidence, not live water depth, quality, "
            "pumpability, legal access, or appliance access.",
            "Occurrence and seasonality are not a substitute for groundwater-table measurements "
            "in peatland.",
        ],
        "files": [],
    }
    for product in PRODUCTS:
        for tile in TILES:
            url = source_url(product, tile)
            destination = args.root / f"{product}_{tile}_v1_5_2024.tif"
            result = download(url, destination, product)
            entry = {
                "product": product,
                "tile": tile,
                "url": url,
                "file": destination.name,
                **result,
            }
            manifest["files"].append(entry)
            print(json.dumps(entry), flush=True)
    (args.root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
