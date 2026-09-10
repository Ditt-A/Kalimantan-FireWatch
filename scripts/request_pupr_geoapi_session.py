#!/usr/bin/env python3
"""Submit a user-authorized GeoAPI download-registration form through the UI.

Credentials are intentionally supplied only through process environment
variables and are never written to disk or printed.  CAPTCHA is not bypassed:
if the portal requires an interactive challenge, this script reports that
state and stops.

Required environment variables: PUPR_REG_NAME, PUPR_REG_EMAIL,
PUPR_REG_AGE, PUPR_REG_REASON.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, sync_playwright


GEOAPI_URL = "https://sigi.pu.go.id/geoapi/"
LAYER = "sumur"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "reports" / "pupr_geoapi_registration_attempt.json"
SENSITIVE_QUERY_KEYS = {"token", "access_token", "api_key", "key", "session", "sid"}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value) for key, value in parse_qsl(parts.query)]
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
        raise RuntimeError(f"Portal select {selector} cannot select the requested value")


def main() -> int:
    values = {
        "name": os.environ["PUPR_REG_NAME"],
        "email": os.environ["PUPR_REG_EMAIL"],
        "age": os.environ["PUPR_REG_AGE"],
        "reason": os.environ["PUPR_REG_REASON"],
    }
    sys.stdout.reconfigure(encoding="utf-8")
    api_requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def capture(request) -> None:
            parts = urlsplit(request.url)
            if parts.netloc == "sigi.pu.go.id" and parts.path.startswith("/geoapi/api/"):
                api_requests.append(redact_url(request.url))

        page.on("request", capture)
        page.goto(GEOAPI_URL, wait_until="domcontentloaded", timeout=90_000)
        close_notice = page.locator("button[aria-label='Tutup']")
        if close_notice.is_visible():
            close_notice.click()
        page.wait_for_function(
            "document.querySelector('#sel-layer option[value=\"sumur\"]') !== null"
        )
        set_portal_select(page, "#sel-layer", LAYER)
        page.wait_for_function("!document.querySelector('#sel-jenis').disabled")
        set_portal_select(page, "#sel-jenis", "data")
        page.wait_for_function("!document.querySelector('#sel-format').disabled")
        set_portal_select(page, "#sel-format", "geojson")
        page.wait_for_function("!document.querySelector('#sel-geom').disabled")
        set_portal_select(page, "#sel-geom", "dengan")
        page.get_by_role("button", name="▶ Run Query", exact=True).first.click(force=True)
        page.locator("#gf-r-btn").wait_for(state="visible", timeout=30_000)

        page.locator("#gf-r-nama").fill(values["name"])
        page.locator("#gf-r-email").fill(values["email"])
        page.locator("#gf-r-umur").select_option(values["age"])
        page.locator("#gf-r-alasan").fill(values["reason"])
        page.locator("#gf-r-btn").click()
        page.wait_for_timeout(8_000)
        gate_visible = page.locator("#gf-r-btn").is_visible()
        body_tail = page.locator("body").inner_text()[-2_500:]
        browser.close()

    # No user-provided registration fields are included in this report.
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "portal": GEOAPI_URL,
        "layer": LAYER,
        "registration_form_submitted": True,
        "registration_gate_still_visible": gate_visible,
        "captured_geoapi_requests": list(dict.fromkeys(api_requests)),
        "body_tail": body_tail,
        "next_action": (
            "stop_for_human_captcha_or_portal_processing"
            if gate_visible
            else "session_unlocked_check_generated_raw_endpoint"
        ),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
