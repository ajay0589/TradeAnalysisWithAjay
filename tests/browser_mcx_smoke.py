"""MCX UI regression with isolated commands and synthetic ledgers, never live alerts."""
import json
import re
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    with urlopen(url+"/api/mcx/status") as response:
        fixture = json.load(response)
    fixture.update(running=False, busy=False, phase="stopped", telegram_configured=False, error=None)
    fixture["trades"] = []
    day = fixture["as_of"][:10]
    for root in fixture["roots"]:
        symbol = root+"26NOVFUT"
        fixture["contracts"][root] = dict(symbol=symbol,expiry="2026-11-30")
        fixture["charts"][root] = [{"time":f"2026-09-{1+i:02}T00:00:00+05:30","close":100+i+(i%3)} for i in range(25)]
        for i in range(23):
            fixture["trades"].append(dict(id=f"UI-{root}-{i}",root=root,symbol=symbol,expiry="2026-11-30",
                status="closed" if i%2 else "open",direction="bullish",entry_time=day+"T10:00:00+05:30",entry_price=100,
                signal_time=day+"T10:00:00+05:30",reference_price=99,exit_time=day+"T12:00:00+05:30" if i%2 else None,
                exit_price=102 if i%2 else None,open_seconds=7200,stop_level=96,target_level=108,net_r=.5 if i%2 else None,
                indicators={"atr_percent":4},result={"points":2,"percent":2,"basis":"closed" if i%2 else "live","time":day+"T12:00:00+05:30"}))
    fixture["trade_history"] = fixture["trades"]
    commands, errors = [], []
    out = Path("logs/mcx-ui-qa"); out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome",headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.on("dialog",lambda dialog:dialog.accept())
        def route_api(route):
            req = route.request; path = urlparse(req.url).path
            if req.method == "POST":
                if path.startswith("/api/mcx/"):
                    commands.append((path,req.post_data_json))
                    active = path.endswith("/start")
                    fixture.update(running=active,busy=active,phase="idle" if active else "stopped")
                    route.fulfill(json=fixture)
                else:route.fulfill(json={})
            elif path == "/api/mcx/status":route.fulfill(json=fixture)
            else:route.continue_()
        page.route("**/api/**",route_api)
        page.goto(url,wait_until="domcontentloaded")
        page.locator('[data-tab-target="mcx"]').click()
        expect(page.locator("#mcxStatus")).to_contain_text("Stopped")
        expect(page.locator("#mcxOverview tr")).to_have_count(4)
        expect(page.locator("#mcxTrades tr")).to_have_count(10)
        assert not commands
        page.locator("#mcxNext").click(); page.locator("#mcxNext").click()
        expect(page.locator("#mcxTrades tr")).to_have_count(3)
        page.locator("#mcxTradeState").select_option("open")
        expect(page.locator("#mcxPage")).to_contain_text("Page 1 of 2 / 12 trades")
        page.locator('[data-mcx-root="NATURALGAS"]').click()
        expect(page.locator("#mcxTrades")).to_contain_text("UI-NATURALGAS")
        expect(page.locator("#mcxTrades")).not_to_contain_text("UI-SILVERM")
        page.locator("#mcxFrom").fill("2026-10-01")
        expect(page.locator("#mcxExport")).to_have_attribute("href",re.compile("from_date=2026-10-01"))
        expect(page.locator("#mcxPage")).to_contain_text("12 trades")
        page.locator("#mcxTradeBasis").select_option("exit")
        page.locator("#mcxTradeFrom").fill(day)
        expect(page.locator("#mcxTrades")).to_contain_text("No trades")
        page.locator("#mcxTradeClear").click()
        expect(page.locator("#mcxPage")).to_contain_text("12 trades")
        for width in (1440,768,390):
            page.set_viewport_size({"width":width,"height":1000})
            page.locator("#mcxStart").scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"), width
            pixels=page.locator("#mcxChart").evaluate("""c => {
                const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                let n=0; for(let i=0;i<a.length;i+=4) if(a[i]<40&&a[i+1]>90&&a[i+1]<150&&a[i+2]<130)n++; return n;
            }""")
            assert pixels>20, pixels
            page.screenshot(path=str(out/f"{width}-mcx.png"))
        page.locator("#mcxStart").click()
        expect(page.locator("#mcxStatus")).to_contain_text("Running")
        page.locator("#mcxStop").click()
        expect(page.locator("#mcxStatus")).to_contain_text("Stopped")
        assert [c[0] for c in commands] == ["/api/mcx/start","/api/mcx/stop"]
        assert commands[0][1] == {"telegram_enabled":False}
        with page.expect_download() as download:page.locator("#mcxExport").click()
        dest=out/"actual-candle-audit.zip"; download.value.save_as(str(dest))
        with zipfile.ZipFile(dest) as archive:assert "trades.csv" in archive.namelist()
        assert not errors,errors
        browser.close()
    print("MCX: four subtabs, safe controls, filters, 10-row pagination, ZIP export, responsive UI and nonblank chart passed.")


if __name__ == "__main__":main()
