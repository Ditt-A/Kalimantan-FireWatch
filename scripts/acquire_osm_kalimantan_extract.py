"""Download and verify a local Geofabrik OSM extract for self-hosted use.

The resulting PBF is a source snapshot for a future local PostGIS/OSRM/PMTiles
stack. It is not an online routing service by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://download.geofabrik.de/asia/indonesia/kalimantan-latest.osm.pbf"
ATTRIBUTION = "© OpenStreetMap contributors"


def md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - required to verify the publisher's MD5 file
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def get_with_retry(
    url: str, *, stream: bool = False, extra_headers: dict[str, str] | None = None
) -> requests.Response:
    headers = {"User-Agent": "FIRELINE/1.0 (self-hosted OSM extract bootstrap)"}
    headers.update(extra_headers or {})
    last_error: requests.RequestException | None = None
    for attempt in range(4):
        try:
            response = requests.get(url, headers=headers, stream=stream, timeout=180)
            if response.status_code in {429, 500, 502, 503, 504}:
                response.close()
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"Unable to download {url} after retries") from last_error


def download_by_ranges(url: str, target: Path) -> int:
    """Download with HTTP Range requests when a mirror rejects a full GET."""
    with get_with_retry(url, extra_headers={"Range": "bytes=0-0"}) as response:
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes 0-0/(\d+)", content_range)
        if response.status_code != 206 or not match:
            raise RuntimeError(f"Range support unavailable for {url}: {content_range!r}")
        total_bytes = int(match.group(1))
    chunk_size = 16 * 1024 * 1024
    with target.open("wb") as stream:
        for start in range(0, total_bytes, chunk_size):
            end = min(total_bytes - 1, start + chunk_size - 1)
            with get_with_retry(
                url, stream=True, extra_headers={"Range": f"bytes={start}-{end}"}
            ) as response:
                expected = end - start + 1
                payload = response.content
                if response.status_code != 206 or len(payload) != expected:
                    raise RuntimeError(
                        f"Invalid byte range {start}-{end}: HTTP {response.status_code}, "
                        f"received {len(payload)} bytes, expected {expected}"
                    )
                stream.write(payload)
    return total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data" / "raw" / "osm"
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(args.url).name
    output = output_dir / filename
    checksum_url = f"{args.url}.md5"
    try:
        with get_with_retry(checksum_url) as expected_response:
            expected_md5 = expected_response.text.strip().split()[0].lower()
    except RuntimeError:
        expected_md5 = None

    if (
        expected_md5
        and output.is_file()
        and output.stat().st_size > 0
        and md5(output) == expected_md5
    ):
        status = "already_verified"
    else:
        partial = output.with_suffix(output.suffix + ".partial")
        try:
            download_by_ranges(args.url, partial)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        actual_md5 = md5(partial)
        if expected_md5 and actual_md5 != expected_md5:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"Checksum mismatch for {filename}")
        partial.replace(output)
        status = "downloaded_verified" if expected_md5 else "downloaded_range_verified"

    actual_md5 = md5(output)
    provenance = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Geofabrik OpenStreetMap extract",
        "download_url": args.url,
        "license": "Open Database License (ODbL) v1.0",
        "attribution": ATTRIBUTION,
        "file": output.name,
        "bytes": output.stat().st_size,
        "publisher_md5": expected_md5,
        "computed_md5": actual_md5,
        "verification": status,
        "intended_use": "Local PostGIS/OSRM/PMTiles input; refresh before production deployment.",
    }
    (output_dir / "kalimantan_osm_extract.provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
