"""Optional local-server UI check: python tests/browser_index_smoke.py [base URL]."""
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright, expect


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    errors = []
    output = Path("logs/index-ui-qa")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        def read_only(route):
            path = route.request.url.split("?", 1)[0]
            if route.request.method == "POST" and not path.endswith(("/backtest", "/client-event", "/strategy-suggestions")):
                route.abort()
            else:
                route.continue_()
        page.route("**/api/**", read_only)
        response = page.goto(url, wait_until="domcontentloaded")
        print({"url": page.url, "status": response.status if response else None, "title": page.title()})
        expect(page.locator("#availableCount")).not_to_have_text("-", timeout=20000)
        page.screenshot(path=str(output / "initial.png"))
        page.locator('[data-tab-target="indices"]').click()
        expect(page.locator("#allIndexStatusBody tr")).to_have_count(3)
        expect(page.locator("#niftyLiveView")).to_be_visible()
        expect(page.locator("#niftyMode")).to_be_hidden()
        expect(page.locator("#niftyBacktestMethod")).to_be_hidden()
        assert page.locator("#niftyLiveView #niftyAutoStartBtn").count() == 1
        assert page.locator("#niftyLiveView #niftyAlertsBody").count() == 1
        assert page.locator("#niftyLiveView #niftyTradesBody").count() == 1
        assert page.locator("#niftyLiveView #niftyAlertContext").count() == 1
        assert page.locator("#niftyResearchView #niftyMode").count() == 1
        assert page.locator("#niftyBacktestsView #niftyBacktestMethod").count() == 1
        page.locator(".nifty-view-tabs").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "desktop-live.png"))
        page.locator("#niftyResearchTab").click()
        expect(page.locator("#niftyLiveView")).to_be_hidden()
        page.locator("#niftyMode").select_option("swing")
        page.locator("#niftyRefresh").uncheck()
        page.locator("#niftyRunContextBtn").click()
        expect(page.locator("#niftyMeta")).to_contain_text("swing /", timeout=30000)
        expect(page.locator("#niftyContextCards")).to_contain_text("Research Candle Price")
        page.locator(".nifty-view-tabs").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "desktop-research.png"))
        page.locator("#niftyResearchTab").focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator("#niftyBacktestsTab")).to_be_focused()
        expect(page.locator("#niftyBacktestsView")).to_be_visible()
        page.keyboard.press("Home")
        expect(page.locator("#niftyLiveTab")).to_be_focused()
        expect(page.locator("#niftyLiveView")).to_be_visible()
        page.locator(".index-monitor-band .market-evidence summary").click()
        expect(page.locator("#indexMarketEvidence tr")).to_have_count(3)
        page.screenshot(path=str(output / "desktop-quotes.png"))
        page.locator("#niftyBacktestsTab").click()
        page.locator("#niftyBacktestMethod").select_option("comparison")
        page.locator("#niftyBacktestHorizon").select_option("intraday")
        page.locator("#niftyBacktestFrom").fill("2026-09-01")
        page.locator("#niftyBacktestTo").fill("2026-10-07")
        page.locator("#niftyScannerBacktestBtn").click()
        expect(page.locator("#comparisonResults tbody tr")).to_have_count(4, timeout=30000)
        expect(page.locator("#comparisonExport")).to_be_enabled()
        page.locator("#comparisonResults").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "desktop-comparison.png"))
        page.locator("#comparisonVariant").select_option("oi")
        expect(page.locator("#niftyScannerBacktestMeta")).to_contain_text("rolling OI")
        with page.expect_download() as info:
            page.locator("#comparisonExport").click()
        info.value.save_as(str(output / "comparison.json"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#niftyBacktestMethod").scroll_into_view_if_needed()
        page.screenshot(path=str(output / "mobile-comparison.png"))
        width = page.evaluate("({body: document.documentElement.scrollWidth, viewport: innerWidth})")
        assert width["body"] <= width["viewport"] + 1, width
        page.locator("#comparisonTrigger").select_option("15m_5m")
        page.locator("#niftyBacktestHorizon").select_option("swing")
        expect(page.locator("#comparisonTrigger")).to_have_value("standard")
        for name in ("live", "research", "backtests"):
            page.locator(f'[data-nifty-view-target="{name}"]').click()
            page.locator(".nifty-view-tabs").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"mobile-{name}.png"))
            width = page.evaluate("({body: document.documentElement.scrollWidth, viewport: innerWidth})")
            assert width["body"] <= width["viewport"] + 1, (name, width)
        expect(page.locator("#niftyMode")).to_have_value("swing")
        page.locator('[data-index-target="banknifty"]').click()
        expect(page.locator("#indexScannerMeta")).to_contain_text("Bank")
        page.locator('[data-index-target="sensex"]').click()
        expect(page.locator("#indexScannerMeta")).to_contain_text("Sensex")
        page.locator('[data-index-target="nifty"]').click()
        expect(page.locator("#niftyBacktestsView")).to_be_visible()
        expect(page.locator("#niftyScannerBacktestMeta")).to_contain_text("rolling OI")
        page.locator("#indexDiagnosticsDate").fill("2026-10-07")
        with page.expect_download(timeout=30000) as info:
            page.locator("#indexDiagnosticsDownload").click()
        info.value.save_as(str(output / "scan-audit.zip"))
        assert not errors, errors
        browser.close()
    print("Desktop/mobile UI, comparison variants/export, index navigation and ZIP download passed.")


if __name__ == "__main__":
    main()
