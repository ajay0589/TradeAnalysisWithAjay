"""Read-only Help navigation, downloads, and responsive layout check."""
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright, expect


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    output = Path("logs/help-ui-qa")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors, posts = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        def read_only(route):
            if route.request.method == "POST":
                posts.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        page.route("**/api/**", read_only)
        page.goto(base, wait_until="domcontentloaded")
        page.locator('[data-tab-target="help"]').click()
        expect(page.locator("#helpSections details")).to_have_count(9)
        page.locator('#helpContents a[href="#help-parity"]').click()
        expect(page.locator("#help-parity")).to_have_attribute("open", "")
        expect(page.locator("#help-parity")).to_contain_text("not fully replayed")
        page.locator("#help-options summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#help-options")).to_have_attribute("open", "")
        for button, filename, content in (("helpDownload", "trading-desk-guide.txt", "India VIX"),
                                          ("helpReviewDownload", "trading-session-review.txt", "Trade ID")):
            with page.expect_download() as download:
                page.locator("#" + button).click()
            assert download.value.suggested_filename == filename
            assert content in Path(download.value.path()).read_text(encoding="utf-8")
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            page.locator("#helpVersion").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"help-{width}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.locator("#help-factors summary").click()
            page.locator("#help-factors").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"factors-{width}.png"))
            page.locator("#help-factors summary").click()
        assert not errors, errors
        assert not posts, posts
        browser.close()
    print("Help guide, keyboard navigation, downloads and desktop/mobile layout passed; no POST requests.")


if __name__ == "__main__":
    main()
