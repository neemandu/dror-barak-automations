"""HTML → PDF via headless Chromium (Playwright).

The campaign report is design-heavy — full-bleed gradient banner, zero page
margins, crisp charts — and Google Drive's HTML→Docs conversion (:mod:`src.lib.pdf`)
cannot do any of that: it applies fixed page margins and resamples images. A real
browser renders the CSS exactly, so the report uses this path instead.

Chromium is bundled by Playwright (no system libraries), so this renders on any
platform locally. **On Lambda it needs a Chromium layer** — Playwright's own build
is ~150MB; a stripped Lambda build (e.g. sparticuz/chromium) fits a layer. Point
``PLAYWRIGHT_CHROMIUM_PATH`` at that binary in the deploy. Until the layer is in
place the monthly job cannot render, which is why the report is not yet scheduled
live (see TASKS.md).

The contract stays on the Drive path: it is mostly text, needs no browser, and
keeping it there avoids making the signing flow depend on Chromium.
"""

from __future__ import annotations

import os
from typing import Any


class ChromiumError(RuntimeError):
    """Raised when the browser could not render the document."""


def render(html: str, *, page_format: str = "A4") -> bytes:
    """Return PDF bytes for ``html``, full-bleed (zero margins, backgrounds on)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - a clear message beats an ImportError
        raise ChromiumError(
            "Playwright is not installed. `pip install playwright` and "
            "`playwright install chromium`."
        ) from exc

    launch: dict[str, Any] = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    # On Lambda the browser is a layer binary, not Playwright's download.
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if exe:
        launch["executable_path"] = exe

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                return page.pdf(
                    format=page_format,
                    print_background=True,               # render the gradient/colours
                    prefer_css_page_size=True,           # honour @page size/margin
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
    except ChromiumError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChromiumError(f"Chromium could not render the PDF: {exc}") from exc
