#!/usr/bin/env python3
"""Run PUPR GeoAPI queries through its public UI and inspect generated requests.

This script deliberately never fills or submits either the session/profile form
or the permanent-endpoint request form.  If the public UI requires identity
details before a query, it reports that gate and exits without data access.

Run with a Windows Python environment that has Playwright and Chromium:
``py -3 scripts\\probe_pupr_geoapi_layers.py``.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, sync_playwright


GEOAPI_URL = "https://sigi.pu.go.id/geoapi/"
LAYERS = ("jalan_perbatasan_kalimantan", "sumur")
SENSITIVE_QUERY_KEYS = {"token", "access_token", "api_key", "key", "session", "sid"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "reports" / "pupr_geoapi_probe.json"


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value) for key, value in parse_qsl(parts.query)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def required_gate_is_visible(page: Page) -> bool:
    return page.locator("#gf-r-btn").is_visible() and page.locator("#gf-r-nama").is_visible()


def set_portal_select(page: Page, selector: str, value: str) -> None:
    """Set a hidden native select and fire the portal's native change event."""
    result = page.locator(selector).evaluate(
        """(element, value) => {
            const option = [...element.options].find(item => item.value === value);
            if (!option) return {found: false, values: [...element.options].map(item => item.value)};
            element.value = value;
            element.dispatchEvent(new Event('input', {bubbles: true}));
            element.dispatchEvent(new Event('change', {bubbles: true}));
            return {found: true, current: element.value};
        }""",
        value,
    )
    if not result["found"] or result.get("current") != value:
        raise RuntimeError(f"Portal select {selector} does not contain {value!r}: {result['values']}")


def run_layer(page: Page, layer: str) -> dict[str, object]:
    requests: list[str] = []

    def capture(request) -> None:
        parts = urlsplit(request.url)
        if (
            request.resource_type in {"fetch", "xhr", "document"}
            and parts.netloc == "sigi.pu.go.id"
            and parts.path.startswith("/geoapi/api/")
        ):
            requests.append(redact_url(request.url))

    page.on("request", capture)
    # The portal renders a custom dropdown and hides its backing <select>.
    # Dispatch the native `change` event that its UI itself registers.
    set_portal_select(page, "#sel-layer", layer)
    page.wait_for_function("!document.querySelector('#sel-jenis').disabled")
    set_portal_select(page, "#sel-jenis", "data")
    page.wait_for_function("!document.querySelector('#sel-format').disabled")
    set_portal_select(page, "#sel-format", "geojson")
    page.wait_for_function("!document.querySelector('#sel-geom').disabled")
    set_portal_select(page, "#sel-geom", "dengan")
    page.get_by_role("button", name="▶ Run Query", exact=True).first.click(force=True)
    page.wait_for_timeout(4_000)
    body = page.locator("body").inner_text()[-4_000:]
    result = {
        "layer": layer,
        "identity_gate_visible": required_gate_is_visible(page),
        "captured_requests": list(dict.fromkeys(requests)),
        "tail_text": body,
        "raw_button_visible": page.locator("#chip-raw").is_visible(),
        "api_button_visible": page.locator("#chip-api").is_visible(),
    }
    page.remove_listener("request", capture)
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(GEOAPI_URL, wait_until="domcontentloaded", timeout=90_000)
        # The public page opens an informational modal about a retired legacy
        # endpoint. Dismiss that notice only; do not interact with any profile
        # or permanent-endpoint request form.
        legacy_notice_close = page.locator("button[aria-label='Tutup']")
        if legacy_notice_close.is_visible():
            legacy_notice_close.click()
        page.wait_for_function(
            "document.querySelector('#sel-layer option[value=\"jalan_perbatasan_kalimantan\"]') !== null"
        )
        results = []
        for layer in LAYERS:
            results.append(run_layer(page, layer))
            page.reload(wait_until="domcontentloaded")
        browser.close()
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "portal": GEOAPI_URL,
        "purpose": "UI-generated endpoint probe; no identity form submitted",
        "results": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(
                {
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "portal": GEOAPI_URL,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
