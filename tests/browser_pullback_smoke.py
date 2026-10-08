"""Local UI regression; no scanner starts, Telegram calls or broker downloads."""
from pathlib import Path
import sys

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    output = Path("logs/pullback-ui-qa")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def read_only(route):
            if route.request.method == "POST" and not route.request.url.endswith(("/backtest-strategy", "/client-event")):
                route.abort()
            else:
                route.continue_()

        page.route("**/api/**", read_only)
        page.goto(url, wait_until="domcontentloaded")
        expect(page.locator("#availableCount")).not_to_have_text("-", timeout=20000)
        page.locator('[data-research-target="backtest"]').click()
        expect(page.locator("#genericStrategySelect")).to_be_visible()
        expect(page.locator('[data-backtest-view="krishna"]').first).to_be_hidden()
        page.locator("#genericStrategySelect").select_option("bullish_pullback")
        expect(page.locator("#genericMarketGate")).to_be_checked()
        expect(page.locator("#genericSectorGate")).to_be_checked()
        page.locator("#genericLimitSymbols").fill("2")
        page.locator("#genericBacktestRunBtn").click()
        expect(page.locator("#genericBacktestStatus")).to_have_text("Completed", timeout=60000)
        expect(page.locator("#genericContextSummary")).to_contain_text("NIFTY 50")
        page.locator('[data-backtest-target="generic"]').scroll_into_view_if_needed()
        page.screenshot(path=str(output / "desktop-generic.png"))
        with page.expect_download() as result:
            page.locator("#genericBacktestExport").click()
        result.value.save_as(str(output / "generic-result.json"))
        page.locator('[data-backtest-target="krishna"]').click()
        expect(page.locator("#genericStrategySelect")).to_be_hidden()
        for panel in page.locator('[data-backtest-view="krishna"]').all():
            expect(panel).to_be_visible()
        page.screenshot(path=str(output / "desktop-krishna.png"))
        page.locator('[data-research-target="pullbacks"]').click()
        expect(page.locator("#pullbackStatus")).to_contain_text("Stopped", timeout=15000)
        expect(page.locator("#pullbackStop")).to_be_disabled()
        page.screenshot(path=str(output / "desktop-pullbacks.png"))
        # Render populated audit records without persisting test trades or sending alerts.
        page.evaluate("""() => renderPullbacks({running:false, phase:'stopped', completed:2, total:211,
          settings:{timeframe:'day', benchmark:'NIFTY 50', structure:true}, setup_total:1,
          decisions:[{symbol:'TEST STOCK',reason:'bullish_pullback: sector_neutral | bearish_pullback: market_bullish',checked_at:'2026-10-08T11:00:00+05:30'}],
          setups:[{id:'PB-UI-FIXTURE',symbol:'TEST STOCK',side:'long',timeframe:'day',status:'open',trigger:100,stop:98,target:104,
            entry_price:100.1,entry_time:'2026-10-08T11:00:00+05:30',signal_time:'2026-10-07T00:00:00+05:30',
            context:{evidence:{market:{index:'NIFTY 50',direction:'bullish'},sector:{index:'NIFTY BANK',direction:'bullish'}}}}],
          delivery:[{symbol:'TEST STOCK',trade_id:'PB-UI-FIXTURE',event_kind:'entry',status:'not_configured',error:'Separate channel not configured'}]})""")
        page.screenshot(path=str(output / "desktop-pullbacks-populated.png"))
        for width, height in ((390, 844), (768, 1024)):
            page.set_viewport_size({"width": width, "height": height})
            for tab in ("pullbacks", "backtest"):
                page.locator(f'[data-research-target="{tab}"]').click()
                if tab == "backtest":
                    page.locator('[data-backtest-target="generic"]').click()
                page.screenshot(path=str(output / f"{width}-{tab}.png"))
                dimensions = page.evaluate("({body:document.documentElement.scrollWidth, viewport:innerWidth})")
                assert dimensions["body"] <= dimensions["viewport"] + 1, (tab, dimensions)
        assert not errors, errors
        browser.close()
    print("Backtest segregation, filtered API, exports, pullback audit and responsive views passed.")


if __name__ == "__main__":
    main()
