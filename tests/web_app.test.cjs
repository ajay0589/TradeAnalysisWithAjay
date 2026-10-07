const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "web/app.js"), "utf8");

function element(attributes = {}) {
  const classes = new Set((attributes.class || "").split(" "));
  const handlers = {};
  return {
    attributes, handlers, value: attributes.value || "", checked: false, disabled: false,
    hidden: false, textContent: "", innerHTML: "", style: {}, children: [],
    dataset: Object.fromEntries(Object.entries(attributes).filter(([k]) => k.startsWith("data-")).map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v])),
    classList: { contains: (key) => classes.has(key), add: (key) => classes.add(key), remove: (key) => classes.delete(key),
      toggle: (key, on) => { on ??= !classes.has(key); if (on) classes.add(key); else classes.delete(key); return on; } },
    setAttribute: (key, value) => { attributes[key] = value; },
    getAttribute: (key) => attributes[key],
    addEventListener: (key, fn) => { handlers[key] = fn; },
    appendChild(child) { this.children.push(child); },
    querySelector: () => element(), querySelectorAll: () => [],
    parentElement: { hidden: false }, click() { this.handlers.click?.(); },
    focus() { this.focused = true; },
  };
}

function app() {
  const nodes = [...html.matchAll(/<[a-z][^>]*>/gi)].map(([tag]) => {
    const attrs = Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map((m) => [m[1], m[2]]));
    return element(attrs);
  });
  const ids = new Map(nodes.filter((node) => node.attributes.id).map((node) => [node.attributes.id, node]));
  const requests = [];
  const events = {};
  const document = {
    getElementById(id) { assert.ok(ids.has(id), `Missing HTML element ${id}`); return ids.get(id); },
    querySelectorAll(selector) {
      const match = selector.match(/^\[([\w-]+)\]$/);
      return match ? nodes.filter((node) => match[1] in node.attributes) : [];
    },
    createElement: () => element(),
  };
  const context = vm.createContext({
    document, console, URL, URLSearchParams, Blob, Intl, Date,
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    window: { setInterval: () => 1, clearInterval() {}, addEventListener: (name, fn) => { events[name] = fn; } },
    localStorage: { getItem: () => null, setItem() {} },
    fetch(url, options) { return new Promise((resolve, reject) => requests.push({url, options, reject,
      resolve: (body, ok = true) => resolve({ ok, json: async () => body })})); },
  });
  vm.runInContext(source, context, { filename: "web/app.js" });
  return {context, requests, ids, events, run: (code) => vm.runInContext(code, context)};
}

test("all initial UI controls are wired to existing elements", () => {
  const ui = app();
  assert.equal(typeof ui.ids.get("analyzeBtn").handlers.click, "function");
  assert.equal(typeof ui.ids.get("appDiagnosticsDownload").handlers.click, "function");
});

test("the latest analysis wins when an earlier request finishes last", async () => {
  const ui = app();
  const rendered = [];
  ui.context.renderAnalysis = (data) => rendered.push(data.symbol);
  ui.ids.get("symbolInput").value = "NIFTY";
  const first = ui.context.analyze();
  ui.ids.get("symbolInput").value = "SBIN";
  const second = ui.context.analyze();
  const [oldRequest, newRequest] = ui.requests.filter((r) => r.url.startsWith("/api/analyze?"));
  newRequest.resolve({ symbol: "SBIN", decision: {} });
  await second;
  oldRequest.resolve({ symbol: "NIFTY", decision: {} });
  await first;
  assert.deepEqual(rendered, ["SBIN"]);
  assert.equal(ui.run("state.lastAnalysis.symbol"), "SBIN");
});

test("changing the symbol invalidates pending analysis and clears old option inputs", async () => {
  const ui = app();
  ui.context.renderAnalysis = () => assert.fail("Old instrument must not render");
  ui.ids.get("symbolInput").value = "NIFTY";
  const pending = ui.context.analyze();
  ui.ids.get("previousSnapshot").value = "old.csv";
  ui.ids.get("symbolInput").value = "SENSEX";
  ui.ids.get("symbolInput").handlers.input();
  ui.requests.find((r) => r.url.startsWith("/api/analyze?")).resolve({ symbol: "NIFTY", decision: {} });
  await pending;
  assert.equal(ui.ids.get("previousSnapshot").value, "");
  assert.equal(ui.ids.get("analysisResults").hidden, true);
  assert.equal(ui.run("state.lastAnalysis"), null);
});

test("outdated expiry responses cannot populate another instrument", async () => {
  const ui = app();
  ui.ids.get("symbolInput").value = "NIFTY";
  const pending = ui.context.loadOptionExpiries();
  ui.ids.get("symbolInput").value = "SENSEX";
  ui.ids.get("symbolInput").handlers.input();
  ui.requests.filter((r) => r.url.startsWith("/api/option-expiries?")).at(-1).resolve({ expiries: ["2026-10-20"] });
  await pending;
  assert.equal(ui.ids.get("expirySelect").children.length, 0);
});

test("late startup defaults preserve a symbol entered by the user", async () => {
  const ui = app();
  ui.ids.get("symbolInput").value = "SENSEX";
  ui.ids.get("symbolInput").handlers.input();
  for (const request of [...ui.requests]) {
    if (request.url === "/api/symbols") request.resolve({ symbols: [{ symbol: "SBIN", has_daily: true }], available: 1 });
    else if (request.url === "/api/strategies") request.resolve({ strategies: [] });
    else request.resolve({});
  }
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(ui.ids.get("symbolInput").value, "SENSEX");
  assert.equal(ui.requests.filter((r) => r.url.startsWith("/api/analyze?")).length, 0);
});

test("grouped navigation selects all index and stock research views", () => {
  const ui = app();
  for (const name of ["nifty", "banknifty", "sensex"]) {
    ui.context.activateIndexSubtab(name);
    ui.context.activateTab("indices");
    assert.equal(ui.ids.get("tab-indices").classList.contains("active"), true);
    assert.equal(ui.ids.get(name === "nifty" ? "tab-nifty" : "tab-index").classList.contains("active"), true);
    if (name !== "nifty") assert.equal(ui.ids.get("indexScannerSymbol").value, name.toUpperCase());
  }
  for (const name of ["analyze", "scans", "krishna", "backtest", "purple", "data", "reports"]) {
    ui.context.activateTab(name);
    assert.equal(ui.ids.get(`tab-${name}`).classList.contains("active"), true);
  }
});

test("browser errors are recorded without exporting the page or input forms", () => {
  const ui = app();
  ui.events.error({ message: "test error" });
  const request = ui.requests.find((r) => r.url === "/api/diagnostics/client-event");
  assert.deepEqual(Object.keys(JSON.parse(request.options.body)).sort(), ["message", "section", "symbol"]);
});

test("NIFTY opens on live scanning with separate research and backtest views", () => {
  const ui = app();
  assert.equal(ui.ids.get("niftyLiveView").hidden, false);
  assert.equal(ui.ids.get("niftyResearchView").hidden, true);
  assert.equal(ui.ids.get("niftyBacktestsView").hidden, true);
  const requestCount = ui.requests.length;
  for (const [name, tab, panel] of [
    ["research", "niftyResearchTab", "niftyResearchView"],
    ["backtests", "niftyBacktestsTab", "niftyBacktestsView"],
    ["live", "niftyLiveTab", "niftyLiveView"],
  ]) {
    ui.ids.get(tab).click();
    assert.equal(ui.run("state.nifty.view"), name);
    assert.equal(ui.ids.get(panel).hidden, false);
    assert.equal(ui.ids.get(tab).getAttribute("aria-selected"), "true");
    for (const other of ["niftyLiveView", "niftyResearchView", "niftyBacktestsView"].filter((id) => id !== panel)) {
      assert.equal(ui.ids.get(other).hidden, true);
    }
  }
  assert.equal(ui.requests.length, requestCount, "View switches must not start, stop or run anything");
});

test("NIFTY view tabs support keyboard navigation and preserve research inputs", () => {
  const ui = app();
  ui.ids.get("niftyMode").value = "swing";
  let prevented = false;
  ui.ids.get("niftyLiveTab").handlers.keydown({ key: "ArrowRight", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(ui.ids.get("niftyResearchTab").focused, true);
  assert.equal(ui.ids.get("niftyLiveTab").getAttribute("tabindex"), "-1");
  ui.context.activateIndexSubtab("banknifty");
  ui.context.activateIndexSubtab("nifty");
  assert.equal(ui.run("state.nifty.view"), "research");
  assert.equal(ui.ids.get("niftyMode").value, "swing");
});

test("manual NIFTY research stays local and clears candidates from older research", async () => {
  const ui = app();
  ui.context.setNotes = () => assert.fail("Manual research must not replace the global banner");
  ui.ids.get("niftyMode").value = "swing";
  ui.ids.get("niftyDays").value = "90";
  ui.ids.get("niftyTimeframe").value = "60minute";
  ui.ids.get("niftyAutoMeta").textContent = "Running / Open";
  ui.run('state.nifty.candidates = [{label: "Old suggestion"}]');
  const pending = ui.context.runNiftyContext();
  ui.context.activateNiftyView("live");
  const request = ui.requests.find((r) => r.url.startsWith("/api/nifty/context?"));
  const query = new URL(request.url, "http://localhost").searchParams;
  assert.equal(query.get("mode"), "swing");
  assert.equal(query.get("days"), "90");
  request.resolve({mode: "swing", technical: {spot: 25000}, summary: {points: ["Manual bearish context"]}});
  await pending;
  assert.equal(ui.ids.get("niftyResearchStatus").textContent, "Manual bearish context");
  assert.equal(ui.ids.get("niftyAutoMeta").textContent, "Running / Open");
  assert.equal(ui.ids.get("niftyStrategyBody").innerHTML, "");
  assert.match(ui.ids.get("niftyContextCards").innerHTML, /Research Candle Price/);
  assert.equal(ui.run("state.nifty.view"), "live");
  assert.equal(ui.requests.some((r) => r.url.includes("/auto/start")), false);
});

test("live NIFTY start sends only the live interval, never manual research settings", async () => {
  const ui = app();
  ui.context.renderNiftyAutoStatus = () => {};
  ui.ids.get("niftyScanInterval").value = "180";
  ui.ids.get("niftyMode").value = "positional";
  ui.ids.get("niftyToDate").value = "2026-01-01";
  ui.ids.get("niftyResearchStatus").textContent = "Stored research";
  const pending = ui.context.startNiftyAutoScan();
  const request = ui.requests.find((r) => r.url === "/api/nifty/auto/start");
  assert.deepEqual(JSON.parse(request.options.body), {scan_interval_seconds: 180});
  request.resolve({running: true});
  await pending;
  assert.equal(ui.ids.get("niftyResearchStatus").textContent, "Stored research");
});

test("live status updates do not overwrite manual research or backtest results", () => {
  const ui = app();
  ui.ids.get("niftyMeta").textContent = "Historical research";
  ui.ids.get("niftyScannerBacktestMeta").textContent = "30 historical trades";
  ui.context.activateNiftyView("research");
  ui.context.renderNiftyAutoStatus({running: true, market_hours: true});
  assert.equal(ui.ids.get("niftyAutoMeta").textContent, "Running / Open");
  assert.equal(ui.ids.get("niftyMeta").textContent, "Historical research");
  assert.equal(ui.ids.get("niftyScannerBacktestMeta").textContent, "30 historical trades");
  assert.equal(ui.run("state.nifty.view"), "research");
});

test("saved alert context remains with live alerts, not manual research", async () => {
  const ui = app();
  ui.context.setNotes = () => assert.fail("Alert context must remain inside live alerts");
  ui.ids.get("niftyMeta").textContent = "My manual analysis";
  const pending = ui.context.viewNiftyContextSnapshot(7);
  ui.requests.find((r) => r.url === "/api/nifty/context-snapshots/7").resolve({snapshot: {spot: 24500}});
  await pending;
  assert.equal(ui.ids.get("niftyAlertContext").hidden, false);
  assert.equal(ui.ids.get("niftyAlertContext").open, true);
  assert.match(ui.ids.get("niftyAlertContextBody").textContent, /Saved context #7/);
  assert.equal(ui.ids.get("niftyMeta").textContent, "My manual analysis");
});

test("NIFTY action failures stay within their own view", async () => {
  const ui = app();
  ui.context.setNotes = () => assert.fail("NIFTY errors must be view-local");
  const research = ui.context.runNiftyContext();
  ui.requests.find((r) => r.url.startsWith("/api/nifty/context?")).reject(new Error("Research failed"));
  await research;
  const live = ui.context.startNiftyAutoScan();
  ui.requests.find((r) => r.url === "/api/nifty/auto/start").reject(new Error("Scanner failed"));
  await live;
  const backtest = ui.context.runNiftyScannerBacktest();
  ui.requests.find((r) => r.url === "/api/nifty/scanner-backtest").reject(new Error("Backtest failed"));
  await backtest;
  assert.equal(ui.ids.get("niftyResearchStatus").textContent, "Research failed");
  assert.equal(ui.ids.get("niftyLiveIssue").textContent, "Scanner failed");
  assert.equal(ui.ids.get("niftyScannerBacktestMeta").textContent, "Failed: Backtest failed");
});
