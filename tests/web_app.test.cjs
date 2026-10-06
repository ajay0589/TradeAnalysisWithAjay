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
