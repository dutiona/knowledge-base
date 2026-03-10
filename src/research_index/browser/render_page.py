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
import sys
from pathlib import Path
from urllib.parse import urlparse


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
        if args.cdp:
            browser = p.chromium.connect_over_cdp(args.cdp)
        else:
            browser = p.chromium.launch(headless=True)

        try:
            # Ephemeral context: no shared cookies/cache/service-workers
            context = browser.new_context()
            try:
                page = context.new_page()
                page.goto(args.url, wait_until="load", timeout=30_000)
                # Brief settle wait for JS to finish rendering after load event
                page.wait_for_timeout(2_000)

                # 1. Rendered HTML
                (out / "page.html").write_text(page.content(), encoding="utf-8")

                # 2. Screenshot (clip caps height to bound memory on long pages)
                page.screenshot(
                    path=str(out / "screenshot.png"),
                    clip={"x": 0, "y": 0, "width": 1280, "height": 8000},
                )
            finally:
                context.close()
        finally:
            if not args.cdp:
                browser.close()


if __name__ == "__main__":
    main()
