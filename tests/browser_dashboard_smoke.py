"""Read-only dashboard, ledger and application-wide pagination browser regression."""
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    out = Path("logs/dashboard-ui-qa")
    out.mkdir(parents=True, exist_ok=True)
    errors, commands = [], []
    vix = dict(value=16.25, quote_time="2026-10-13T10:00:00+05:30", captured_at="2026-10-13T10:00:00+05:30", status="recorded")
    rows = [dict(id=f"UI-{symbol}-{i:02}", scanner=symbol, symbol=symbol, status="closed" if i % 2 else "open", direction="bearish",
                 horizon="intraday", variant="fixture", entry_time="2026-10-13T10:00:00+05:30", entry_price=50000,
                 exit_time="2026-10-14T10:00:00+05:30" if i % 2 else None, exit_price=49900 if i % 2 else None,
                 open_seconds=100, stop=50100, target=49800, india_vix_entry=vix, india_vix_exit=vix if i % 2 else None,
                 result=dict(points=100, percent=.2, basis="closed" if i % 2 else "live", time="2026-10-13T11:00:00+05:30"))
            for symbol in ("NIFTY", "BANKNIFTY", "SENSEX") for i in range(25)]

    def response(query):
        source = query.get("source", ["all"])[0]
        status = query.get("status", ["all"])[0]
        filtered = [r for r in rows if (source == "all" or source == r["scanner"]) and (status == "all" or status == r["status"])]
        for kind in ("from_date", "to_date"):
            if query.get(kind):
                basis = query.get("date_basis", ["entry"])[0]+"_time"
                day = query[kind][0]
                filtered = [r for r in filtered if r[basis] and (r[basis][:10] >= day if kind == "from_date" else r[basis][:10] <= day)]
        total = len(filtered)
        pages = max(1, (total+9)//10)
        page = min(int(query.get("page", [1])[0]), pages)
        return dict(trades=filtered[(page-1)*10:page*10], page=page, pages=pages, total=total,
                    counts={s: sum(r["status"] == s for r in filtered) for s in ("open", "closed")})

    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width":1440, "height":1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        pending = []
        delay_bank = [False]

        def route_api(route):
            request = route.request
            path = urlparse(request.url)
            if request.method == "POST":
                if not path.path.endswith("/client-event"): commands.append(path.path)
                route.fulfill(json={})
            elif path.path == "/api/trade-ledger":
                q = parse_qs(path.query)
                if delay_bank[0] and q.get("source") == ["BANKNIFTY"]:
                    pending.append((route, q))
                else: route.fulfill(json=response(q))
            else: route.continue_()

        page.route("**/api/**", route_api)
        page.goto(url, wait_until="domcontentloaded")
        page.locator('[data-tab-target="dashboard"]').click()
        expect(page.locator("#dashboardStatus")).to_contain_text("running", timeout=15000)
        expect(page.locator("#dashboardScanners tr:visible")).to_have_count(10)
        inventory_pager = page.locator("#tab-dashboard .auto-pagination").first
        inventory_pager.locator("button").last.click()
        expect(page.locator("#dashboardScanners tr:visible")).to_have_count(6)
        inventory_pager.locator("button").first.click()
        expect(page.locator("#dashboardLedger tbody tr")).to_have_count(10)
        filters = page.locator("#tab-dashboard .ledger-filters")
        pager = page.locator("#dashboardLedger + .table-pagination")
        expect(filters.locator('[data-filter="status"]')).to_have_value("open")
        filters.locator('[data-filter="source"]').select_option("BANKNIFTY")
        expect(pager).to_contain_text("13 trades")
        pager.locator("[data-next]").click()
        expect(page.locator("#dashboardLedger tbody tr")).to_have_count(3)
        filters.locator('[data-filter="status"]').select_option("closed")
        expect(pager).to_contain_text("Page 1 of 2 / 12 trades")
        filters.locator('[data-filter="from_date"]').fill("2026-10-14")
        expect(pager).to_contain_text("0 trades")
        filters.locator('[data-filter="date_basis"]').select_option("exit")
        expect(pager).to_contain_text("12 trades")
        filters.locator('[data-filter="to_date"]').fill("2026-10-14")
        expect(pager).to_contain_text("12 trades")
        expect(page.locator("#dashboardLedger tbody")).to_contain_text("16.25")
        expect(page.locator("#dashboardLedger tbody")).to_contain_text("+100.00 / +0.20%")
        for width, height in ((1440,1000), (390,844), (768,1024)):
            page.set_viewport_size({"width":width, "height":height})
            page.locator("#dashboardStatus").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"{width}-dashboard.png"))
            page.locator("#dashboardLedger").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"{width}-trades.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")

        page.set_viewport_size({"width":1440,"height":1000})
        page.locator('[data-tab-target="indices"]').click()
        page.locator('[data-index-target="banknifty"]').click()
        expect(page.locator("#indexScannerTrades")).to_contain_text("UI-BANKNIFTY", timeout=15000)
        expect(page.locator("#indexScannerTrades tr")).to_have_count(10)
        index_filters = page.locator("#indexScannerTrades").locator("xpath=ancestor::table/parent::*/preceding-sibling::*[contains(@class,'ledger-filters')]")
        delay_bank[0] = True
        index_filters.locator('[data-filter="status"]').select_option("open")
        page.wait_for_timeout(150)
        page.locator('[data-index-target="sensex"]').click()
        expect(page.locator("#indexScannerTrades")).to_contain_text("UI-SENSEX", timeout=15000)
        for route, q in pending: route.fulfill(json=response(q))
        delay_bank[0] = False
        page.wait_for_timeout(150)
        expect(page.locator("#indexScannerTrades")).not_to_contain_text("UI-BANKNIFTY")
        expect(page.locator("#indexScannerTrades")).to_contain_text("16.25")

        # Any un-managed result table receives a pager; replacements do not leak pagers.
        page.evaluate("""() => {
          const table = document.createElement('table'); table.id='qaTable';
          table.innerHTML='<tbody>'+Array.from({length:23},(_,i)=>`<tr><td>row ${i}</td></tr>`).join('')+'</tbody>';
          document.querySelector('#tab-indices').append(table);
        }""")
        expect(page.locator("#qaTable tr:visible")).to_have_count(10)
        page.locator("#qaTable + .auto-pagination button").last.click()
        expect(page.locator("#qaTable tr:visible")).to_have_count(10)
        page.locator("#qaTable + .auto-pagination button").last.click()
        expect(page.locator("#qaTable tr:visible")).to_have_count(3)
        page.evaluate("document.querySelector('#qaTable tbody').innerHTML='<tr><td>new result</td></tr>'")
        expect(page.locator("#qaTable + .auto-pagination")).to_be_hidden()
        assert not commands, commands
        assert not errors, errors
        browser.close()
    print("Dashboard, all index ledgers, VIX/result cells, IST filters, race protection, 10-row pagination and responsive layouts passed.")


if __name__ == "__main__":
    main()
