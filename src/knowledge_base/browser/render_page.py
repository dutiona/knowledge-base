"""Render a JS-heavy page via Playwright. Outputs HTML + screenshot.

Security notes:
- Only http/https URLs are accepted (validated by caller + defense-in-depth here).
- CDP mode uses ephemeral browser context (no shared state).
- SSRF: the browser executes page JS which can issue secondary requests to
  internal networks. This is an accepted trade-off for an optional, user-configured
  feature. Network-level controls (Docker network isolation, firewall rules) are
  the recommended mitigation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.sync_api import Page

_MIN_ELEMENT_DIMENSION = 80  # px – skip tiny icons/decorations
_MAX_ELEMENT_CAPTURES = 10
_PAGE_LOAD_TIMEOUT_MS = 30_000  # max wait for the load event
_RENDER_SETTLE_MS = 2_000  # brief settle wait for JS after load event
_SCREENSHOT_CLIP_WIDTH = 1280  # px
_SCREENSHOT_CLIP_HEIGHT = 8000  # px – caps height to bound memory on long pages


def _capture_elements(page: Page, out_dir: Path) -> None:
    """Screenshot visible <canvas> and <svg> elements, write manifest."""
    elements = page.query_selector_all("canvas, svg")
    captures: list[dict[str, object]] = []

    for el in elements:
        if len(captures) >= _MAX_ELEMENT_CAPTURES:
            break
        try:
            box = el.bounding_box()
        except Exception as exc:
            print(f"bounding_box failed: {exc}", file=sys.stderr)
            continue
        if not box:
            continue
        w, h = box["width"], box["height"]
        if w < _MIN_ELEMENT_DIMENSION or h < _MIN_ELEMENT_DIMENSION:
            continue
        # Skip elements outside the viewport (negative coords or zero-area)
        if box["x"] + w <= 0 or box["y"] + h <= 0:
            continue

        tag = el.evaluate("el => el.tagName.toLowerCase()")
        filename = f"element_{len(captures)}.png"
        try:
            el.screenshot(path=str(out_dir / filename))
        except Exception as exc:
            print(f"element screenshot failed: {exc}", file=sys.stderr)
            continue

        captures.append(
            {
                "file": filename,
                "tag": tag,
                "width": int(w),
                "height": int(h),
            }
        )

    if captures:
        (out_dir / "elements.json").write_text(json.dumps(captures))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a JS-heavy page and capture HTML + screenshot.",
    )
    parser.add_argument("url", help="URL to render")
    parser.add_argument("output_dir", help="Directory for output files")
    parser.add_argument("--cdp", default=None, help="CDP endpoint (ws://host:port)")
    args = parser.parse_args()

    # Defense-in-depth: reject non-http(s) even though caller validates
    scheme = urlparse(args.url).scheme
    if scheme not in ("http", "https"):
        print(f"Rejected URL scheme: {scheme}", file=sys.stderr)
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    out = Path(args.output_dir)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp) if args.cdp else p.chromium.launch(headless=True)

        try:
            # Ephemeral context: no shared cookies/cache/service-workers
            context = browser.new_context()
            try:
                page = context.new_page()
                page.goto(args.url, wait_until="load", timeout=_PAGE_LOAD_TIMEOUT_MS)
                # Brief settle wait for JS to finish rendering after load event
                page.wait_for_timeout(_RENDER_SETTLE_MS)

                # 1. Rendered HTML
                (out / "page.html").write_text(page.content(), encoding="utf-8")

                # 1b. Final URL (may differ from input after redirects/client navigation)
                (out / "final_url.txt").write_text(page.url, encoding="utf-8")

                # 2. Screenshot (clip caps height to bound memory on long pages)
                page.screenshot(
                    path=str(out / "screenshot.png"),
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": _SCREENSHOT_CLIP_WIDTH,
                        "height": _SCREENSHOT_CLIP_HEIGHT,
                    },
                )

                # 3. Per-element captures for <canvas> and complex <svg> (#132)
                _capture_elements(page, out)
            finally:
                context.close()
        finally:
            if not args.cdp:
                browser.close()


if __name__ == "__main__":
    main()
