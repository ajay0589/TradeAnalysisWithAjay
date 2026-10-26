"""Stock workspace browser verification with intercepted paper controls and fixtures."""
from pathlib import Path
import sys
from playwright.sync_api import expect, sync_playwright


def main():
    url=sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:8767"
    errors,commands=[],[]
    out=Path("logs/stock-ui-qa");out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel="chrome",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda e:errors.append(str(e)))
        swing={"running":False,"phase":"stopped","settings":{},"readiness":{"ready":True,"blockers":[],"telegram_configured":True},
               "setups":{"trend_pullback":"Trend Pullback","compression_breakout":"Compression Breakout"},"total":23,"completed":23,
               "universe_count":23,"open_count":1,"refresh":{},"errors":[],"delivery":[],
               "decisions":[{"symbol":f"TEST{i:02}","frame":"60minute","family":"trend_pullback","reason":"market_sector_not_aligned",
                             "direction":"long","context":{"market":"long","sector":"neutral"},"indicators":{},"checked_at":"2026-10-26T10:15:03+05:30"} for i in range(23)],
               "trades":[{"id":f"SW-{i:02}","symbol":f"TEST{i:02}","family":"trend_pullback","timeframe":"day","direction":"bullish",
                          "status":"closed","entry_price":100,"exit_price":110 if i%2 else 90,"net_r":1 if i%2 else -1,"performance_eligible":True,
                          "entry_time":"2026-10-19T09:15:03+05:30","exit_time":"2026-10-21T10:00:00+05:30","open_seconds":175500,
                          "stop_level":90,"target_level":120,"exit_reason":"target_reached" if i%2 else "stop_loss"} for i in range(13)]}
        def route(r):
            if r.request.method=="POST":
                commands.append(r.request.url)
                r.fulfill(json={**swing,"running":r.request.url.endswith("/start"),"phase":"waiting_market"})
            elif "/api/stocks/scanner/status" in r.request.url:r.fulfill(json=swing)
            else:r.continue_()
        page.route("**/api/**",route)
        page.goto(url,wait_until="domcontentloaded")
        expect(page.locator("#stockSwingStatus")).to_contain_text("Unvalidated paper")
        expect(page.locator("[data-research-target]")).to_have_count(4)
        for old in ("tab-purple","tab-krishna","backtestRunBtn"):
            expect(page.locator("#"+old)).to_have_count(0)
        assert not commands
        expect(page.locator("#swingStart")).to_be_disabled()
        expect(page.locator("#stockDecisions tr")).to_have_count(10)
        page.locator("#stockDecisionPager [data-step='1']").click()
        expect(page.locator("#stockDecisionPager")).to_contain_text("Page 2")
        page.locator("#swingSymbol").fill("TEST22")
        expect(page.locator("#stockDecisions")).to_contain_text("TEST22")
        page.locator("#swingSymbol").fill("")
        page.locator('[data-research-target="pullbacks"]').click()
        expect(page.locator("#stockTrades tr")).to_have_count(10)
        expect(page.locator("#stockTrades")).to_contain_text("Loss")
        expect(page.locator("#stockTrades")).to_contain_text("Profit")
        page.locator("#swingExportFrom").fill("2026-10-26")
        expect(page.locator("#stockTrades")).to_contain_text("SW-00")
        page.locator("#swingTradeFrom").fill("2026-10-26")
        expect(page.locator("#stockTrades")).to_contain_text("No matching")
        page.locator("#swingTradeClear").click()
        expect(page.locator("#stockTrades tr")).to_have_count(10)
        page.locator('[data-research-target="stocks"]').click()
        page.locator("#stockOpenValidation").click()
        expect(page.locator("#stockResults tr")).to_have_count(10)
        expect(page.locator("#genericStrategySelect")).to_be_hidden()
        page.locator("#stockSetup").select_option("compression_breakout")
        expect(page.locator("#stockResults")).not_to_contain_text("Trend Pullback")
        page.locator("#stockResultPager [data-step='1']").click()
        expect(page.locator("#stockResultPager")).to_contain_text("Page 2")
        with page.expect_download() as d:page.locator("#stockCsv").click()
        d.value.save_as(str(out/"trades.csv"))
        assert "target_r" in (out/"trades.csv").read_text(encoding="utf-8-sig").splitlines()[0]
        page.locator('[data-research-target="analyze"]').click()
        page.locator("#stockAdvancedResearch summary").click()
        expect(page.locator("#genericStrategySelect")).to_be_visible()
        expect(page.locator("#genericStrategySelect")).not_to_contain_text("Krishna")
        for width,height in ((1440,1000),(390,844)):
            page.set_viewport_size({"width":width,"height":height})
            for name in ("stocks","backtest","analyze","pullbacks"):
                page.locator(f'[data-research-target="{name}"]').click()
                page.screenshot(path=str(out/f"{width}-{name}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"),name
        assert not errors,errors
        assert not commands,commands
        page.set_viewport_size({"width":1440,"height":1000})
        page.locator('[data-research-target="stocks"]').click()
        page.locator("#swingAcknowledge").check()
        expect(page.locator("#swingStart")).to_be_enabled()
        page.locator("#swingStart").click()
        expect(page.locator("#swingAction")).to_contain_text("Paper scanner started")
        assert len(commands)==1 and commands[0].endswith("/api/stocks/scanner/start"),commands
        browser.close()
    print("Swing workspace: four views, explicit paper start, trade filters, pagination, CSV and responsive layouts passed")


if __name__=="__main__":main()
