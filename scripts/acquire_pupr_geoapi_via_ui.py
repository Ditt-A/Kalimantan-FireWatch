#!/usr/bin/env python3
"""Acquire selected PUPR GeoAPI layers only through the portal UI.

The GeoAPI portal currently places download queries behind its own registration
dialog.  This script uses a user-authorized, ordinary registration and then
clicks the portal's generated ``Open Raw`` control.  It does not construct or
guess an API data URL, and it does not solve or bypass CAPTCHA.

The registration values are read only from environment variables, never logged
or persisted.  Run from a Windows Python environment with Playwright and a
browser installed.  The raw response and a redacted provenance report are
written to the project after the portal returns data.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Page, Response, sync_playwright


GEOAPI_URL = "https://sigi.pu.go.id/geoapi/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "pupr_geoapi"
REPORT_PATH = PROJECT_ROOT / "reports" / "pupr_geoapi_ui_acquisition.json"
SENSITIVE_QUERY_KEYS = {"token", "access_token", "api_key", "key", "session", "sid", "email"}
LAYERS = (
    ("sumur", "pupr_sumur_bor_nasional.geojson"),
    ("jalan_perbatasan_kalimantan", "pupr_jalan_perbatasan_kalimantan.geojson"),
)
CURRENT_STAGE = "initialising"


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def set_portal_select(page: Page, selector: str, value: str) -> None:
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
        raise RuntimeError(f"Portal select {selector} cannot select requested value")


def choose_query(page: Page, layer: str) -> None:
    global CURRENT_STAGE
    CURRENT_STAGE = f"selecting_layer:{layer}"
    set_portal_select(page, "#sel-layer", layer)
    CURRENT_STAGE = f"waiting_for_data_type:{layer}"
    page.wait_for_function("!document.querySelector('#sel-jenis').disabled")
    CURRENT_STAGE = f"selecting_data_type:{layer}"
    set_portal_select(page, "#sel-jenis", "data")
    CURRENT_STAGE = f"waiting_for_format:{layer}"
    page.wait_for_function("!document.querySelector('#sel-format').disabled")
    CURRENT_STAGE = f"selecting_geojson_format:{layer}"
    set_portal_select(page, "#sel-format", "geojson")
    CURRENT_STAGE = f"waiting_for_geometry:{layer}"
    page.wait_for_function("!document.querySelector('#sel-geom').disabled")
    CURRENT_STAGE = f"selecting_geometry:{layer}"
    set_portal_select(page, "#sel-geom", "dengan")
    CURRENT_STAGE = f"clicking_run_query:{layer}"
    # Match the stable text rather than its decorative play glyph, whose
    # representation differs across browser/terminal encodings.
    page.get_by_role("button", name="Run Query", exact=False).first.click(force=True)


def register_authorized_user(page: Page, values: dict[str, str]) -> None:
    page.locator("#gf-r-btn").wait_for(state="visible", timeout=30_000)
    page.locator("#gf-r-nama").fill(values["name"])
    page.locator("#gf-r-email").fill(values["email"])
    page.locator("#gf-r-umur").select_option(values["age"])
    page.locator("#gf-r-alasan").fill(values["reason"])
    page.locator("#gf-r-btn").click()
    page.wait_for_timeout(3_000)
    if page.locator("#gf-r-btn").is_visible():
        raise RuntimeError("Registration gate remains visible; human CAPTCHA or portal action is required")
    continue_button = page.get_by_text("Lanjutkan Query", exact=False)
    if continue_button.count():
        continue_button.first.click(force=True)
    page.wait_for_timeout(1_000)


def is_data_response(response: Response) -> bool:
    parts = urlsplit(response.url)
    if parts.netloc != "sigi.pu.go.id" or not parts.path.startswith("/geoapi/api/"):
        return False
    return not parts.path.endswith("/columns") and not parts.path.endswith("/registrasi")


def response_as_geojson(response: Response) -> bytes | None:
    """Return a genuine GeoJSON body, never an HTML error page."""
    if not is_data_response(response):
        return None
    try:
        body = response.body()
        parsed = json.loads(body)
    except Exception:
        return None
    if not isinstance(parsed, dict) or parsed.get("type") != "FeatureCollection":
        return None
    features = parsed.get("features")
    if not isinstance(features, list):
        return None
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def page_all_features(
    context: BrowserContext,
    first_url: str,
    first_body: bytes,
) -> tuple[bytes, int]:
    """Follow the portal's own offset/limit contract until its final page.

    The first URL is generated by the ``Open Raw`` UI action in the same
    authenticated browser context.  Only ``offset`` changes; the registered
    email/token remain in memory and are neither printed nor written to disk.
    """
    first = json.loads(first_body)
    all_features = list(first["features"])
    parts = urlsplit(first_url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    try:
        limit = int(params.get("limit", "100"))
        offset = int(params.get("offset", "0"))
    except ValueError as error:
        raise RuntimeError("Portal-generated endpoint has invalid pagination values") from error
    if limit < 1:
        raise RuntimeError("Portal-generated endpoint has non-positive page limit")
    pages = 1
    while len(first["features"]) == limit:
        pages += 1
        if pages > 500:
            raise RuntimeError("Pagination safety limit reached before final page")
        offset += limit
        next_params = {**params, "offset": str(offset)}
        next_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(next_params), parts.fragment)
        )
        response = context.request.get(next_url, timeout=120_000)
        if not response.ok:
            raise RuntimeError(f"Portal pagination request failed with HTTP {response.status}")
        try:
            page = response.json()
        except Exception as error:
            raise RuntimeError("Portal pagination response is not JSON") from error
        if not isinstance(page, dict) or page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise RuntimeError("Portal pagination response is not a GeoJSON FeatureCollection")
        first = page
        all_features.extend(page["features"])
    complete = {"type": "FeatureCollection", "features": all_features}
    return json.dumps(complete, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), pages


def download_layer(context: BrowserContext, page: Page, layer: str, output_name: str) -> dict[str, object]:
    global CURRENT_STAGE
    responses: list[Response] = []

    def capture(response: Response) -> None:
        if is_data_response(response):
            responses.append(response)

    page.on("response", capture)
    CURRENT_STAGE = f"running_ui_query:{layer}"
    choose_query(page, layer)
    CURRENT_STAGE = f"waiting_for_generated_raw_control:{layer}"
    page.locator("#chip-raw").wait_for(state="visible", timeout=30_000)
    control = page.locator("#chip-raw")
    control_details = control.evaluate(
        "element => ({tag: element.tagName, href: element.href || null, target: element.target || null})"
    )
    known_pages = set(context.pages)
    CURRENT_STAGE = f"opening_generated_raw:{layer}"
    control.click(force=True)
    page.wait_for_timeout(5_000)

    raw_bytes: bytes | None = None
    data_response_url: str | None = None
    source_url: str | None = None
    for response in reversed(responses):
        raw_bytes = response_as_geojson(response)
        if raw_bytes is not None:
            data_response_url = response.url
            source_url = redact_url(response.url)
            break

    # Some browsers display GeoJSON in a new page rather than emitting a fetch
    # response on the query page.  Inspect only pages opened by the UI action.
    if raw_bytes is None:
        for candidate in context.pages:
            if candidate in known_pages:
                continue
            candidate.wait_for_timeout(1_000)
            try:
                text = candidate.locator("body").inner_text(timeout=10_000)
                parsed = json.loads(text)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "FeatureCollection" and isinstance(parsed.get("features"), list):
                raw_bytes = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                data_response_url = candidate.url
                source_url = redact_url(candidate.url)
                break
    page.remove_listener("response", capture)
    if raw_bytes is None or data_response_url is None:
        raise RuntimeError(f"Portal did not return a GeoJSON FeatureCollection for {layer}")
    CURRENT_STAGE = f"paginating_portal_data:{layer}"
    raw_bytes, page_count = page_all_features(context, data_response_url, raw_bytes)
    parsed = json.loads(raw_bytes)
    if not parsed["features"]:
        raise RuntimeError(f"Portal returned an empty GeoJSON FeatureCollection for {layer}")
    output_path = RAW_DIR / output_name
    output_path.write_bytes(raw_bytes)
    return {
        "layer": layer,
        "raw_file": str(output_path.relative_to(PROJECT_ROOT)),
        "feature_count": len(parsed["features"]),
        "source_url": source_url,
        "raw_control": control_details,
        "pagination_pages": page_count,
        "bytes": len(raw_bytes),
    }


def main() -> int:
    global CURRENT_STAGE
    values = {
        "name": os.environ["PUPR_REG_NAME"],
        "email": os.environ["PUPR_REG_EMAIL"],
        "age": os.environ["PUPR_REG_AGE"],
        "reason": os.environ["PUPR_REG_REASON"],
    }
    sys.stdout.reconfigure(encoding="utf-8")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        CURRENT_STAGE = "loading_portal"
        page.goto(GEOAPI_URL, wait_until="domcontentloaded", timeout=90_000)
        close_notice = page.locator("button[aria-label='Tutup']")
        if close_notice.is_visible():
            close_notice.click()
        page.wait_for_function("document.querySelector('#sel-layer option[value=\"sumur\"]') !== null")
        CURRENT_STAGE = "opening_initial_sumur_query"
        choose_query(page, "sumur")
        CURRENT_STAGE = "submitting_authorized_registration"
        register_authorized_user(page, values)
        # The initial query remains selected after the dialog closes.
        CURRENT_STAGE = "checking_unlocked_session"
        page.locator("#chip-raw").wait_for(state="visible", timeout=30_000)
        # Re-run through the same UI so response capture is scoped to the data action.
        for layer, output_name in LAYERS:
            results.append(download_layer(context, page, layer, output_name))
        browser.close()

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "portal": GEOAPI_URL,
        "acquisition_method": "user-authorized registration plus UI-generated Open Raw action",
        "captcha_bypassed": False,
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
        # Keep failure reporting operationally useful without retaining any
        # registration-field values that Playwright might include in an error.
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(
                {
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "portal": GEOAPI_URL,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "stage": CURRENT_STAGE,
                    "captcha_bypassed": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
