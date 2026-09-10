#!/usr/bin/env python3
"""Inspect the public PUPR GeoAPI browser UI without submitting any forms.

Run this with a Python environment that has Playwright and Chromium.  It only
loads the public GeoAPI page and prints the interactive controls so a caller
can select a dataset through the UI, then capture its generated API endpoint.
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


GEOAPI_URL = "https://sigi.pu.go.id/geoapi/"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # The portal keeps background connections open, so `networkidle` can
        # wait forever even though the query form is already usable.
        page.goto(GEOAPI_URL, wait_until="domcontentloaded", timeout=90_000)
        controls = page.locator("select, input, button").evaluate_all(
            """elements => elements.map(element => ({
                tag: element.tagName,
                id: element.id,
                name: element.name,
                type: element.type || null,
                text: (element.innerText || element.value || '').trim(),
                ariaLabel: element.getAttribute('aria-label'),
                title: element.getAttribute('title'),
                disabled: Boolean(element.disabled),
                options: element.tagName === 'SELECT'
                    ? [...element.options].map(option => ({text: option.text, value: option.value}))
                    : null
            }))"""
        )
        print(json.dumps({"title": page.title(), "url": page.url, "controls": controls}, ensure_ascii=False, indent=2))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
