/* Read-only views. Opening these panels never starts a scanner or quote feed. */
const ledgerViews = {};
const ledgerSources = ["NIFTY", "BANKNIFTY", "SENSEX", "MCX", "scalping", "expiry", "pullbacks", "purple", ...Array.from({length: 5}, (_, i) => `Nifty_Setup${i + 1}`)];
const ledgerHeading = '<tr><th>Trade / scanner</th><th>Status / direction</th><th>Result / basis</th><th>Horizon / variant</th><th>Entry (IST)</th><th>Entry price</th><th>Exit (IST)</th><th>Exit price</th><th>Open time</th><th>Stop / target</th><th>India VIX at entry</th><th>India VIX at exit</th><th>Exit reason</th></tr>';

function tradeResultCell(result) {
  if (result?.unit) return paperResult(result.value, result.unit, {status:result.status,performance_eligible:result.performance_eligible}, result.basis);
  if (!result || result.percent == null) return '<span class="cell-note">Quote unavailable</span>';
  const color = result.percent > 0 ? "points-positive" : result.percent < 0 ? "points-negative" : "";
  const sign = result.percent > 0 ? "+" : "";
  return `<span class="${color}">${result.percent > 0 ? "Profit" : result.percent < 0 ? "Loss" : "Flat"} ${sign}${fmt(result.points)} / ${sign}${fmt(result.percent)}%</span><div class="cell-note">${result.basis === "closed" ? "Closed gross underlying; before costs" : "Unrealized gross underlying"}<br>${fmtDateTime(result.time)}</div>`;
}

function mountTradeLedger(key, bodyId, source) {
  const body = bodyId ? $(bodyId) : document.createElement("tbody");
  const table = bodyId ? body.closest("table") : document.createElement("table");
  table.classList.add("readable-table");
  table.dataset.managedPagination = "true";
  if (bodyId) table.querySelector("thead").innerHTML = ledgerHeading;
  else { table.innerHTML = `<thead>${ledgerHeading}</thead>`; table.append(body); $("dashboardLedger").append(table); }
  const outer = bodyId ? table.parentElement : $("dashboardLedger");
  outer.classList.add("ledger-scroll");
  const controls = document.createElement("div");
  controls.className = "ledger-filters";
  controls.innerHTML = `<label>Trade status<select data-filter="status"><option value="all">All trades</option><option value="open" ${key === "dashboard" ? "selected" : ""}>Open</option><option value="closed">Closed</option></select></label>
    ${key === "dashboard" ? `<label>Scanner<select data-filter="source"><option value="all">All scanners</option>${ledgerSources.map(s => `<option value="${s}">${s}</option>`).join("")}</select></label>` : ""}
    <label>Date basis (IST)<select data-filter="date_basis"><option value="entry">Entry date</option><option value="exit">Exit date</option></select></label>
    <label>From<input type="date" data-filter="from_date"></label><label>To<input type="date" data-filter="to_date"></label><button type="button" data-reset>Clear filters</button>`;
  outer.before(controls);
  const pager = document.createElement("div");
  pager.className = "table-pagination compact-pagination";
  pager.innerHTML = '<button type="button" data-prev title="Previous page" aria-label="Previous page">&#8592;</button><span role="status"></span><button type="button" data-next title="Next page" aria-label="Next page">&#8594;</button>';
  outer.after(pager);
  const note = document.createElement("p");
  note.className = "cell-note";
  note.textContent = "Each result shows its units and cost basis. Underlying-price results are not option P&L. Excluded observations are not validated performance. India VIX is NIFTY volatility context; unrecorded values remain blank.";
  pager.after(note);
  const view = ledgerViews[key] = {body, controls, pager, source, page: 1, generation: 0, signature: null, pending: null};
  controls.addEventListener("change", () => { view.page = 1; loadTradeLedger(key); });
  controls.querySelector("[data-reset]").addEventListener("click", () => {
    controls.querySelectorAll("input").forEach(e => e.value = "");
    controls.querySelectorAll("select").forEach(e => e.selectedIndex = 0);
    view.page = 1; loadTradeLedger(key);
  });
  pager.querySelector("[data-prev]").addEventListener("click", () => { view.page = Math.max(1, view.page - 1); loadTradeLedger(key); });
  pager.querySelector("[data-next]").addEventListener("click", () => { view.page++; loadTradeLedger(key); });
}

async function loadTradeLedger(key) {
  const v = ledgerViews[key];
  if (!v) return;
  const source = typeof v.source === "function" ? v.source() : v.source;
  const params = new URLSearchParams({source});
  v.controls.querySelectorAll("[data-filter]").forEach(el => { if (el.value) params.set(el.dataset.filter, el.value); });
  if (key === "dashboard") $("dashboardTradeTitle").textContent = params.get("status") === "open" ? "Open Trades" : params.get("status") === "closed" ? "Closed Trades" : "Trades: Open & Closed";
  const signature = params.toString();
  if (v.signature !== signature) { v.page = 1; v.signature = signature; }
  params.set("page", String(v.page));
  const url = `/api/trade-ledger?${params}`;
  if (v.pending === url) return;
  v.pending = url;
  const generation = ++v.generation;
  v.pager.querySelector("span").textContent = "Loading trades...";
  if (v.renderedSignature !== signature) v.body.innerHTML = '<tr><td colspan="13">Loading selected trades...</td></tr>';
  try {
    const data = await api(url);
    if (generation !== v.generation || (typeof v.source === "function" && source !== v.source())) return;
    v.page = data.page;
    v.renderedSignature = signature;
    v.body.innerHTML = data.trades.map(t => `<tr><td>${escapeHtml(t.id)}<div class="cell-note">${escapeHtml(t.scanner)} / ${escapeHtml(t.symbol)}</div></td>
      <td>${escapeHtml(t.status)} / ${escapeHtml(t.direction)}</td><td>${tradeResultCell(t.result)}</td><td>${escapeHtml(t.horizon)}<div class="cell-note">${escapeHtml(t.variant || "-")}</div></td>
      <td>${fmtDateTime(t.entry_time)}</td><td>${signalPriceCell(t.entry_price, t)}</td><td>${fmtDateTime(t.exit_time)}</td><td>${signalPriceCell(t.exit_price, t, "exit")}</td>
      <td>${formatSeconds(t.open_seconds)}</td><td>${fmt(t.stop)} / ${fmt(t.target)}</td>
      <td>${vixCell(t.india_vix_entry)}</td><td>${vixCell(t.india_vix_exit)}</td><td>${escapeHtml(t.exit_reason || "-")}</td></tr>`).join("") || '<tr><td colspan="13">No trades matching these filters.</td></tr>';
    v.pager.querySelector("span").textContent = `Page ${data.page} of ${data.pages} / ${data.total} trades (${data.counts.open} open, ${data.counts.closed} closed)`;
    v.pager.querySelector("[data-prev]").disabled = data.page <= 1;
    v.pager.querySelector("[data-next]").disabled = data.page >= data.pages;
  } catch (e) {
    if (generation === v.generation) { v.pager.querySelector("span").textContent = `Trades unavailable: ${e.message}`; v.body.innerHTML = '<tr><td colspan="13">Unable to load selected trades.</td></tr>'; }
  } finally { if (generation === v.generation) v.pending = null; }
}

let dashboardLoading = false;
async function loadScannerDashboard() {
  if (state.activeTab !== "dashboard" || dashboardLoading) return;
  dashboardLoading = true;
  loadTradeLedger("dashboard");
  try {
    const d = await api("/api/scanner-dashboard");
    $("dashboardStatus").textContent = `${d.running_count} running / ${d.open_trade_count} open trades | ${fmtDateTime(d.as_of)}`;
    $("dashboardIssues").textContent = (d.issues || []).join(" | ");
    $("dashboardVix").innerHTML = `India VIX now: ${vixCell(d.india_vix)}`;
    $("dashboardScanners").innerHTML = d.scanners.map(r => `<tr><td>${escapeHtml(r.name)}</td><td><strong class="${r.running ? "points-positive" : ""}">${r.running ? "Running" : r.phase === "on_demand" ? "On demand" : r.phase === "unavailable" ? "Unavailable" : "Stopped"}</strong><div class="cell-note">${escapeHtml(r.phase)}</div></td>
      <td>${escapeHtml(r.detail)}</td><td>${fmtDateTime(r.started_at)}</td><td>${fmtDateTime(r.last_checked)}</td><td>${fmtDateTime(r.stopped_at)}</td>
      <td><button type="button" data-open-scanner="${escapeHtml(r.id)}">Open</button></td></tr>`).join("");
    $("dashboardScanners").querySelectorAll("[data-open-scanner]").forEach(b => b.addEventListener("click", () => {
      const r = d.scanners.find(s => s.id === b.dataset.openScanner);
      activateTab(r.tab);
      if (r.tab === "indices") activateIndexSubtab(r.subtab);
      if (r.tab === "nifty-lab") { niftyLab.setup = r.subtab; niftyLab.page = 1; loadNiftyLab(); }
    }));
  } catch (e) { $("dashboardStatus").textContent = `Dashboard unavailable: ${e.message}`; }
  finally { dashboardLoading = false; }
}

mountTradeLedger("dashboard", null, "all");
mountTradeLedger("nifty", "niftyTradesBody", "NIFTY");
mountTradeLedger("index", "indexScannerTrades", indexScannerSymbol);
setInterval(loadScannerDashboard, 5000);
