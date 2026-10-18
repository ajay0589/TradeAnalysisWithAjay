"""Real GET contracts, mocked paper controls, desktop/mobile views; no live jobs."""
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from playwright.sync_api import expect,sync_playwright


def main():
    url=sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:8767"
    with urlopen(url+"/api/expiry/status") as r:fixture=json.load(r)
    errors=[];commands=[];out=Path("logs/expiry-ui-qa");out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel="chrome",headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda e:errors.append(str(e)))
        def readonly(route):
            if route.request.method=="POST":route.abort()
            else:route.continue_()
        page.route("**/api/**",readonly)
        page.goto(url,wait_until="domcontentloaded")
        page.locator('[data-tab-target="expiry"]').click()
        expect(page.locator("#expiryStates tr")).to_have_count(8)
        for view in ("NIFTY","SENSEX","comparison","logs","overview"):
            page.locator(f'[data-expiry-view="{view}"]').click()
            expect(page.locator(f'[data-expiry-view="{view}"]')).to_have_attribute("aria-selected","true")
        for link in ("expiryCsv","expiryZip"):
            response=page.request.get(url+page.locator("#"+link).get_attribute("href"))
            assert response.ok,response.text()
        page.unroute("**/api/**")
        now=fixture["now"]
        fixture["trades"]=[dict(id=f"EX-UI-{i}",setup="NIFTY:Expiry_1",symbol="NIFTY",direction="bullish",status="closed",
            long={"tradingsymbol":"NIFTY_TEST_CE","expiry":now[:10]},short={"tradingsymbol":"NIFTY_TEST_SHORT_CE"},quantity=50,
            entry_time=now,exit_time=now,debit=42,exit_fill={"value":45},open_seconds=300,net_pnl=100,net_r=.04,
            stop_level=23000,portfolio_reason="accepted",portfolio_accepted=True,performance_eligible=True) for i in range(25)]
        fixture["portfolio_trades"]=[{**fixture["trades"][0],"id":"EX-UI-0-P","variant":"portfolio"}]
        def mocked(route):
            if "/api/expiry/status" in route.request.url:route.fulfill(json=fixture)
            elif route.request.method=="POST":
                commands.append((route.request.url,route.request.post_data_json))
                route.fulfill(json={"ok":True})
            else:route.continue_()
        page.route("**/api/**",mocked)
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-tab-target="expiry"]').click()
        expect(page.locator("#expiryStates tr")).to_have_count(8)
        assert not commands
        page.locator('[data-expiry-view="NIFTY"]').click()
        expect(page.locator("#expiryTrades tr")).to_have_count(10)
        page.locator('#expiryTradePager [data-step="1"]').click()
        expect(page.locator("#expiryTradePager")).to_contain_text("Page 2 of 3")
        page.locator("#expiryTradeStatus").select_option("open")
        expect(page.locator("#expiryTrades")).to_contain_text("No trades")
        page.locator("#expiryTradeStatus").select_option("all")
        page.locator("#expiryLedger").select_option("portfolio")
        expect(page.locator("#expiryTrades tr")).to_have_count(1)
        expect(page.locator("#expiryTrades")).to_contain_text("EX-UI-0-P")
        page.locator("#expiryLedger").select_option("all")
        page.locator('[data-expiry-view="SENSEX"]').click()
        expect(page.locator("#expiryTrades")).to_contain_text("No trades")
        page.locator("#expiryAck").check();page.locator("#expiryStart").click()
        expect(page.locator("#expiryAction")).to_contain_text("enabled")
        assert commands[-1][1]["acknowledge"] is True
        assert commands[-1][1]["telegram_enabled"] is False
        for width,height in ((1440,1000),(390,844)):
            page.set_viewport_size({"width":width,"height":height})
            for view in ("overview","NIFTY","SENSEX","comparison","logs"):
                page.locator(f'[data-expiry-view="{view}"]').click()
                page.locator("#expiryStatus").scroll_into_view_if_needed()
                page.screenshot(path=str(out/f"{width}-{view}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"),view
        assert not errors,errors
        browser.close()
    print("Expiry UI: real API dates/exports, tabs, filters, ten-row pagination, explicit paper start, desktop/mobile passed")


if __name__=="__main__":main()
