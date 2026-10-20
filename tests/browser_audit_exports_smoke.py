"""Read-only audit download smoke test; no scanner or Telegram actions."""
import io
import json
from pathlib import Path
import sys
import zipfile
from playwright.sync_api import sync_playwright, expect


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    output = Path("logs/audit-export-ui-qa")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", lambda r: r.abort() if r.request.method == "POST" else r.continue_())
        page.goto(base, wait_until="domcontentloaded")
        page.locator('[data-tab-target="nifty-lab"]').click()
        expect(page.locator("#labStatus")).to_contain_text("/ 5", timeout=20000)
        page.locator("#labDate").fill("2026-10-14")
        page.locator("#labExportTo").fill("2026-10-15")
        expect(page.locator("#labDownload")).to_have_attribute("href", "/api/nifty-lab/export?from_date=2026-10-14&to_date=2026-10-15&format=zip")
        with page.expect_download() as downloaded:
            page.locator("#labDownload").click()
        path = downloaded.value.path()
        with zipfile.ZipFile(path) as z:
            data = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".json"))))
            assert data["from_date"] == "2026-10-14" and data["to_date"] == "2026-10-15"
            assert "trades.csv" in z.namelist()
        for endpoint in ("index-scanners/diagnostics", "diagnostics/export", "pullbacks/export"):
            response = page.request.get(base + f"/api/{endpoint}?from_date=2026-10-14&to_date=2026-10-15&format=zip", timeout=60000)
            assert response.status == 200, (endpoint, response.status)
            with zipfile.ZipFile(io.BytesIO(response.body())) as z:
                data = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".json"))))
                assert data["to_date"] == "2026-10-15"
                if endpoint.startswith("index"):
                    assert {"nifty-trades.csv", "banknifty-trades.csv", "sensex-trades.csv"} <= set(z.namelist())
        response = page.request.get(base + "/api/index-scanners/diagnostics?from_date=2026-10-15&to_date=2026-10-14")
        assert response.status == 400
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            page.locator("#labDate").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"range-{width}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
        browser.close()
    print("Range downloads, trade CSVs, validation and desktop/mobile layout passed.")


if __name__ == "__main__":
    main()
