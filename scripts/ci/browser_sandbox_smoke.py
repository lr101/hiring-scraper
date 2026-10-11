"""Verify the production Chromium build runs with its sandbox at the app UID."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


if os.geteuid() != 10001:
    raise RuntimeError(f"Expected the application runtime UID 10001, got {os.geteuid()}")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                                          args=["--disable-dev-shm-usage"])
    try:
        page = browser.new_page()
        page.set_content("<main id='sandbox-check'>sandboxed</main>")
        if page.locator("#sandbox-check").inner_text() != "sandboxed":
            raise RuntimeError("Chromium rendered an unexpected sandbox smoke page")
        print(f"Sandboxed Chromium launched as UID {os.geteuid()} ({browser.version})")
    finally:
        browser.close()
