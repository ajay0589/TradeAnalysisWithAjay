"""Read-only browser checks: fixture trades and intercepted controls, no broker/Telegram."""
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    out = Path("logs/nifty-lab-ui-qa")
    out.mkdir(parents=True, exist_ok=True)
    with urlopen(url + "/api/nifty-lab/status") as response:
        fixture = json.load(response)
    assert fixture["running_count"] == 0, "Browser fixture expects idle lab"
    day = fixture["date"]
    fixture["trades"] = [dict(id=f"NL-UI-{i}", setup="Nifty_Setup1", variant="technical" if i % 2 else "combined",
                             direction="bullish", status="closed", entry_time=day+"T10:00:00+05:30", entry_price=23000,
                             exit_time=day+"T10:20:00+05:30", exit_price=23050, stop_level=22950, target_level=23100,
                             open_seconds=1200, net_r=.816, performance_eligible=True, exit_reason="holding_limit") for i in range(25)]
    for row in fixture["comparisons"]:
        for variant in ("technical", "combined"):
            row["metrics"][variant].update(closed=3, open=1, win_rate=66.67, net_r=1.2, expectancy_r=.4,
                profit_factor=1.5, average_open_minutes=25, max_drawdown_r=.5,
                curve=[{"time":None,"net_r":0}, {"time":day+"T10:00:00+05:30","net_r":1},
                       {"time":day+"T11:00:00+05:30","net_r":.5}, {"time":day+"T12:30:00+05:30","net_r":1.2 if variant == "technical" else .8}])
    errors, commands = [], []
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1100})
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route_api(route):
            path = route.request.url
            if "/api/nifty-lab/status" in path:
                route.fulfill(json=fixture)
            elif "/api/nifty-lab/" in path and route.request.method == "POST":
                commands.append((path.rsplit("/", 1)[-1], route.request.post_data_json))
                payload = route.request.post_data_json
                for key in ([payload["setup"]] if payload.get("setup") else fixture["states"]):
                    fixture["states"][key]["enabled"] = path.endswith("/start")
                fixture["running_count"] = sum(s["enabled"] for s in fixture["states"].values())
                route.fulfill(json=fixture)
            elif route.request.method == "POST":
                if path.endswith("/client-event"): route.continue_()
                else: route.abort()
            else: route.continue_()

        page.route("**/api/**", route_api)
        page.goto(url, wait_until="domcontentloaded")
        page.locator('[data-tab-target="nifty-lab"]').click()
        expect(page.locator("#labStatus")).to_contain_text("0 / 5", timeout=15000)
        assert not commands
        expect(page.locator("#labOverview tr")).to_have_count(5)
        expect(page.locator("#labTrades tr")).to_have_count(20)
        page.locator("#labNext").click()
        expect(page.locator("#labTrades tr")).to_have_count(5)
        page.locator("#labVariant").select_option("combined")
        expect(page.locator("#labTrades tr")).to_have_count(13)
        for i in range(1,6):
            page.locator(f'[data-lab-setup="Nifty_Setup{i}"]').click()
            expect(page.locator("#labSetupTitle")).to_contain_text(f"Nifty_Setup{i}")
        page.locator('[data-lab-setup="Nifty_Setup1"]').click()
        for width, height in ((1440,1100), (390,844), (768,1024)):
            page.set_viewport_size({"width":width,"height":height})
            page.locator("#labSetupTitle").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"{width}-trades.png"))
            dimensions = page.evaluate("({body:document.documentElement.scrollWidth, viewport:innerWidth})")
            assert dimensions["body"] <= dimensions["viewport"] + 1, dimensions
            pixels = page.locator("#labCurve").evaluate("""canvas => {
                const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                let green = 0, orange = 0;
                for(let i=0;i<data.length;i+=4) {
                    if(data[i]<60 && data[i+1]>90 && data[i+1]<170 && data[i+2]<140) green++;
                    if(data[i]>150 && data[i+1]<130 && data[i+2]<80) orange++;
                }
                return {green, orange};
            }""")
            assert pixels["green"] > 10 and pixels["orange"] > 10, pixels
            page.locator("#labStartAll").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"{width}-overview.png"))
        page.locator("#labStartAll").click()
        expect(page.locator("#labStatus")).to_contain_text("5 / 5")
        page.locator('[data-lab-action="stop"][data-setup="Nifty_Setup3"]').click()
        expect(page.locator("#labStatus")).to_contain_text("4 / 5")
        page.locator("#labStopAll").click()
        expect(page.locator("#labStatus")).to_contain_text("0 / 5")
        assert [c[0] for c in commands] == ["start", "stop", "stop"]
        with page.expect_download() as result:
            page.locator("#labCsv").click()
        result.value.save_as(str(out / "fixture-trades.csv"))
        assert "NL-UI-0" in (out / "fixture-trades.csv").read_text()
        with page.expect_download() as result:
            page.locator("#labDownload").click()
        result.value.save_as(str(out / "live-empty-audit.zip"))
        assert not errors, errors
        browser.close()
    print("NIFTY Lab: five subtabs, mocked controls, ledgers, pagination, exports, desktop/mobile and canvas checks passed.")


if __name__ == "__main__":
    main()
