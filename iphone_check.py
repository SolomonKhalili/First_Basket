"""
See the site the way an iPhone does: Safari's engine (WebKit) pretending to be an iPhone 15 — same screen size,
pixel density, touch and user agent. Takes full-page screenshots of the Today and Yesterday views.

    python3 iphone_check.py                       # the local app (http://127.0.0.1:8765)
    python3 iphone_check.py --device "iPhone SE"  # a smaller phone
    python3 iphone_check.py --dark / --light      # colour scheme (default dark)

Screenshots go to iphone_shots/. Needs: pip install playwright && python3 -m playwright install webkit
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "iphone_shots"


def load_all(page):
    """Scroll to the bottom and back so lazy-loaded logos and photos appear in the full-page shot."""
    page.evaluate("""async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) {
        window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); }""")
    page.wait_for_timeout(1500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--device", default="iPhone 15")
    ap.add_argument("--light", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    tag = a.device.replace(" ", "_")
    with sync_playwright() as p:
        browser = p.webkit.launch()
        ctx = browser.new_context(**p.devices[a.device], color_scheme="light" if a.light else "dark")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url, wait_until="networkidle")
        page.wait_for_timeout(2500)
        load_all(page)
        shots = [OUT / f"{tag}_today.png"]
        page.screenshot(path=shots[0], full_page=True)
        if page.locator("#dayYest").count():
            page.tap("#dayYest")
            page.wait_for_timeout(3000)
            load_all(page)
            shots.append(OUT / f"{tag}_yesterday.png")
            page.screenshot(path=shots[1], full_page=True)
        # anything wider than the screen = sideways scrolling on the phone
        wide = page.evaluate("""() => { const w = document.documentElement.clientWidth;
            return [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > w + 1
              && !e.closest('.tscroll')).slice(0, 8).map(e => e.tagName.toLowerCase() + '.' + e.className); }""")
        browser.close()
    for s in shots:
        print("saved", s)
    print("too wide:", wide or "nothing")
    print("page errors:", errors or "none")


if __name__ == "__main__":
    main()
