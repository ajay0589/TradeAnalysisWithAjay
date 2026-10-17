"""Read-only browser verification; all mutating controls are intercepted."""
from pathlib import Path
import sys
from playwright.sync_api import expect, sync_playwright


def main():
    url=sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:8766"
    errors,commands=[],[]
    out=Path("logs/stock-ui-qa");out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel="chrome",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda e:errors.append(str(e)))
        def route(r):
            if r.request.method=="POST":
                commands.append(r.request.url)
                r.fulfill(json={"phase":"idle","setups":{},"variants":{},"rules":[]})
            else:r.continue_()
        page.route("**/api/**",route)
        page.goto(url,wait_until="domcontentloaded")
        expect(page.locator("#stockSummary")).to_contain_text("Live scanners approved")
        for old in ("tab-purple","tab-krishna","backtestRunBtn"):
            expect(page.locator("#"+old)).to_have_count(0)
        assert not commands
        expect(page.locator("#stockCandidates tr")).to_have_count(10)
        page.locator("#stockCandidatePager [data-step='1']").click()
        expect(page.locator("#stockCandidatePager")).to_contain_text("Page 2")
        page.locator("#stockSymbol").fill("RELIANCE")
        expect(page.locator("#stockCandidates")).to_contain_text("RELIANCE")
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
        page.locator('[data-backtest-target="generic"]').click()
        expect(page.locator("#genericStrategySelect")).to_be_visible()
        expect(page.locator("#genericStrategySelect")).not_to_contain_text("Krishna")
        page.locator('[data-backtest-target="validation"]').click()
        for width,height in ((1440,1000),(390,844)):
            page.set_viewport_size({"width":width,"height":height})
            for name in ("stocks","backtest","scans","pullbacks"):
                page.locator(f'[data-research-target="{name}"]').click()
                page.screenshot(path=str(out/f"{width}-{name}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"),name
        assert not errors,errors
        assert not commands,commands
        browser.close()
    print("Stock workspace: retired panels absent, filters, pagination, CSV and responsive views passed")


if __name__=="__main__":main()
