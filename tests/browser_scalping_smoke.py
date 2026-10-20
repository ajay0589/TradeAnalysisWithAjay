"""UI-only fixtures. All control requests intercepted; no broker or Telegram calls."""
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    with urlopen(url + "/api/scalping/status") as response:
        fixture = json.load(response)
    day = fixture["from_date"]
    fixture["running_count"] = 0
    for state in fixture["states"].values():
        state["enabled"] = False
        state["phase"] = "stopped"
    fixture["research"] = {"phase":"idle", "rows":[]}
    fixture["trades"] = [{"id": f"SC-UI-{i}", "symbol":"NIFTY", "setup":"NIFTY:Scalp1", "variant":"A",
        "direction":"bullish", "status":"closed", "entry_time":day+"T10:00:01+05:30", "entry_price":23000,
        "exit_time":day+"T10:10:00+05:30", "exit_price":23030, "open_seconds":599,
        "stop_level":22980,"target_level":23030,"risk_points":20,"net_r":1,"performance_eligible":True,
        "exit_reason":"spot_target","telegram_enabled":False} for i in range(25)]
    fixture["trade_history"] = fixture["trades"]
    fixture["trade_history"][0].update(entry_time="2026-10-12T10:00:01+05:30", net_r=-1, performance_eligible=False)
    fixture["futures"] = {"NIFTY": dict(state="ready", bias="bullish", reason="multi_window_confirmation",
        latest=dict(contract=dict(tradingsymbol="NIFTY-UI-FUT", expiry="2026-10-27"),
                    price=23100, vwap=23000, basis=50, quote_time=day+"T10:00:00+05:30"),
        windows={str(m): dict(state="ready",price_change_pct=.1,spot_change_pct=.08,oi_change_pct=1,
                             volume_delta=500,interpretation="long_buildup") for m in (3,6,15)})}
    fixture["trade_history"][1]["futures_evidence"] = {**fixture["futures"]["NIFTY"], "alignment":"aligned"}
    errors, commands = [], []
    out = Path("logs/scalping-ui-qa")
    out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome",headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Exercise the real UI/API date contract before replacing responses with fixtures.
        page.route("**/api/**", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
        page.goto(url,wait_until="domcontentloaded")
        page.locator('[data-tab-target="scalping"]').click()
        expect(page.locator("#scalpOverview tr")).to_have_count(9)
        expect(page.locator("#scalpAction")).not_to_contain_text("Unknown scalping filter")
        for symbol in ("BANKNIFTY", "SENSEX", "NIFTY"):
            tab = page.locator(f'[data-scalp-index="{symbol}"]')
            tab.click()
            expect(tab).to_have_attribute("aria-selected", "true")
            expect(page.locator("#scalpMetrics tr")).to_have_count(3)
        for link in ("#scalpCsv", "#scalpZip"):
            response = page.request.get(url + page.locator(link).get_attribute("href"))
            assert response.ok, response.text()
        page.unroute("**/api/**")
        def route(route):
            if "/api/scalping/status" in route.request.url:
                route.fulfill(json=fixture)
            elif route.request.method == "POST":
                if "/api/scalping/" in route.request.url:
                    commands.append(route.request.url)
                    enabled = route.request.url.endswith("/start")
                    for s in fixture["states"].values(): s["enabled"] = enabled
                    fixture["running_count"] = 9 if enabled else 0
                    route.fulfill(json={"ok":True})
                elif route.request.url.endswith("/client-event"): route.continue_()
                else: route.abort()
            else: route.continue_()
        page.route("**/api/**",route)
        page.goto(url,wait_until="domcontentloaded")
        page.locator('[data-tab-target="scalping"]').click()
        expect(page.locator("#scalpStatus")).to_contain_text("0/9")
        expect(page.locator("#scalpOverview tr")).to_have_count(9)
        expect(page.locator("#scalpTrades tr")).to_have_count(10)
        expect(page.locator("#scalpTrades")).to_contain_text("Loss")
        expect(page.locator("#scalpTrades")).to_contain_text("excluded from validated metrics")
        page.locator("#scalpFrom").fill("2026-10-19")
        page.locator("#scalpFrom").dispatch_event("change")
        expect(page.locator("#scalpTrades")).to_contain_text("SC-UI-0")
        page.locator("#scalpTradeFrom").fill(day)
        page.locator("#scalpTradeFrom").dispatch_event("change")
        expect(page.locator("#scalpTradePager")).to_contain_text("24 rows")
        page.locator("#scalpTradeClear").click()
        expect(page.locator("#scalpTradePager")).to_contain_text("25 rows")
        assert not commands
        page.locator('#scalpTradePager [data-step="1"]').click()
        expect(page.locator("#scalpTradePager")).to_contain_text("Page 2 of 3")
        page.locator('#scalpTradePager [data-step="1"]').click()
        expect(page.locator("#scalpTrades tr")).to_have_count(5)
        page.locator("#scalpTradeStatus").select_option("open")
        expect(page.locator("#scalpTrades")).to_contain_text("No trades")
        page.locator("#scalpTradeStatus").select_option("all")
        for symbol in ("BANKNIFTY","SENSEX","NIFTY"):
            page.locator(f'[data-scalp-index="{symbol}"]').click()
            expect(page.locator("#scalpMetrics tr")).to_have_count(3)
        page.locator("#scalpStart").click()
        expect(page.locator("#scalpStatus")).to_contain_text("9/9")
        page.locator("#scalpStop").click()
        expect(page.locator("#scalpStatus")).to_contain_text("0/9")
        for width,height in ((1440,1000),(390,844)):
            page.set_viewport_size({"width":width,"height":height})
            future_panel = page.locator("#scalpFutures").locator("..")
            future_panel.locator(":scope > summary").click()
            expect(page.locator("#scalpFutures")).to_contain_text("NIFTY-UI-FUT")
            expect(page.locator("#scalpFutures")).to_contain_text("aligned")
            future_panel.scroll_into_view_if_needed()
            page.screenshot(path=str(out/f"{width}-futures.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            future_panel.locator(":scope > summary").click()
            page.locator("#scalpStatus").scroll_into_view_if_needed()
            page.screenshot(path=str(out/f"{width}-overview.png"))
            page.locator("#scalpMetrics").scroll_into_view_if_needed()
            page.screenshot(path=str(out/f"{width}-trades.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert len(commands)==2
        assert not errors, errors
        browser.close()
    print("Scalping UI: controls, index tabs, filters, ten-row pagination and responsive layouts passed")


if __name__ == "__main__": main()
