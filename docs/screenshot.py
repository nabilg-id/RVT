"""Take README screenshots of the two GUI pages.

Not a test: a one-off capture tool, run by hand after the UI changes. It starts
the real app on a real port and drives a real browser, so the pictures in the
README are pictures of the thing that actually runs rather than a mock - which
is how the Clipper page was caught still calling itself 'YouTube Viral
Clipper' while the Downloader page had been rebranded.

    py -3 docs/screenshot.py

Requires playwright and an Edge/Chrome install. Edge's path is hardcoded
because it is machine-specific; adjust EDGE if the capture fails to launch.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "images"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
EDGE = r"C:\Program Files (x86)\Microsoft\EdgeCore\154.0.4258.53\msedge.exe"


def main() -> int:
    from clipper.app import create_server

    OUT.mkdir(parents=True, exist_ok=True)
    server = create_server("127.0.0.1", PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.5)

    # A sample the user can recognise as YouTube, so the screenshot shows the
    # form filled in rather than empty.
    SAMPLE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=EDGE)
            page = browser.new_page(viewport={"width": 1280, "height": 900},
                                    device_scale_factor=2)

            # --- Clipper (/) ---
            page.goto(f"{BASE}/", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "gui-clipper.png"), full_page=False)

            # --- Downloader (/download) ---
            page.goto(f"{BASE}/download", wait_until="networkidle")
            page.wait_for_timeout(1200)
            # Fill the url input so the page is not photographed blank.
            filled = page.evaluate(
                """(sample) => {
                    const el = document.querySelector('input[type=text], input#url, input[name=url]');
                    if (!el) return false;
                    el.value = sample;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }""",
                SAMPLE,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / "gui-downloader.png"), full_page=False)
            print(f"download form filled: {filled}")

            browser.close()
    finally:
        server.shutdown()

    for name in ("gui-clipper.png", "gui-downloader.png"):
        path = OUT / name
        print(f"{path}: {path.stat().st_size} bytes" if path.exists()
              else f"{path}: MISSING")
    return 0


if __name__ == "__main__":
    sys.exit(main())