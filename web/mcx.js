/* A view never starts market data requests or sends notifications. */
const mcx = {data: null, root: "SILVERM", page: 1, loading: false, action: false, generation: 0};
const mcxLabel = v => String(v ?? "-").replaceAll("_", " ");
function mcxDates() { return new URLSearchParams({from_date: $("mcxFrom").value, to_date: $("mcxTo").value}); }

async function loadMcx() {
  if (mcx.loading || mcx.action || state.activeTab !== "mcx") return;
  mcx.loading = true;
  const query = mcxDates().toString(), generation = ++mcx.generation;
  try {
    const data = await api(`/api/mcx/status?${query}`);
    if (generation === mcx.generation && query === mcxDates().toString()) { mcx.data = data; renderMcx(); }
  } catch (error) { $("mcxAction").textContent = error.message; }
  finally { mcx.loading = false; if (query !== mcxDates().toString()) loadMcx(); }
}

async function controlMcx(action) {
  if (mcx.action) return;
  if (action === "stop" && !confirm("Stop MCX data, entry and exit monitoring? Existing paper trades remain open and any interrupted results are excluded from win rate.")) return;
  mcx.action = true;
  ++mcx.generation;
  $("mcxAction").textContent = `${mcxLabel(action)} requested...`;
  for (const id of ["mcxStart", "mcxStop", "mcxDownloadData", "mcxTest"]) $(id).disabled = true;
  try {
    const result = await postApi(`/api/mcx/${action}`, action === "start" ? {telegram_enabled: $("mcxTelegram").checked} : {});
    $("mcxAction").textContent = action === "test-telegram" ? (result.sent ? "Test sent" : result.error || "Delivery failed") : "";
  } catch (error) { $("mcxAction").textContent = error.message; }
  finally { mcx.action = false; await loadMcx(); if (mcx.data) renderMcx(); }
}

function renderMcx() {
  const d = mcx.data;
  if (!d) return;
  $("mcxStatus").textContent = `${d.running ? "Running" : "Stopped"} / ${mcxLabel(d.phase)} / ${d.version}`;
  $("mcxStart").disabled = $("mcxDownloadData").disabled = d.busy || mcx.action;
  $("mcxStop").disabled = !d.busy || mcx.action;
  $("mcxTelegram").disabled = d.busy;
  if (d.running) $("mcxTelegram").checked = d.settings.telegram_enabled;
  $("mcxTest").disabled = !d.telegram_configured || mcx.action;
  $("mcxTelegramStatus").textContent = d.telegram_configured ? "MCX chat configured" : "MCX chat not configured";
  $("mcxError").textContent = [d.error, !d.calendar_verified ? "Calendar year not verified; new entries blocked." : ""].filter(Boolean).join(" | ");
  $("mcxTiming").textContent = `Started ${fmtDateTime(d.started_at)} | Stopped ${fmtDateTime(d.stopped_at)} | Last check ${fmtDateTime(d.last_completed)} | Next ${fmtDateTime(d.next_check)} | ${d.duration_seconds ?? "-"} sec`;
  $("mcxDataProgress").textContent = `${d.completed} / ${d.total} | ${d.current || "idle"}`;
  $("mcxOverview").innerHTML = d.roots.map(root => {
    const c = d.contracts[root] || {}, q = d.quotes[c.symbol] || {}, decision = d.decisions.find(r => r.root === root);
    const feeds = d.freshness.filter(r => r.symbol === c.symbol);
    return `<tr><td><strong>${escapeHtml(root)}</strong></td><td>${escapeHtml(c.symbol || "-")}</td><td>${escapeHtml(c.expiry || "-")}</td><td>${fmt(q.bid)} / ${fmt(q.ask)}</td><td>${fmtDateTime(q.quote_time)}<div class="cell-note">${q.fresh ? "Fresh" : "Stale / missing"}</div></td><td>${feeds.map(r => `${r.frame === "day" ? "Daily" : "1h"}: ${escapeHtml(r.state)}`).join("<br>") || "Missing"}</td><td>${escapeHtml(mcxLabel(decision?.reason))}<div class="cell-note">${fmtDateTime(decision?.checked_at)}</div></td></tr>`;
  }).join("");
  const contract = d.contracts[mcx.root] || {}, decision = d.decisions.find(r => r.root === mcx.root), i = decision?.indicators || {};
  $("mcxContractTitle").textContent = `${mcx.root} / ${contract.symbol || "No contract selected"}`;
  $("mcxTechnicals").innerHTML = `<tr><td>${fmt(i.daily_close)}</td><td>${fmt(i.daily_ema20)} / ${fmt(i.daily_ema50)}</td><td>${fmt(i.daily_atr14)} / ${fmt(i.atr_percent)}%</td><td>${fmt(i.hour_close)}</td><td>${fmt(i.hour_ema20)} / ${fmt(i.hour_ema50)}</td><td>${fmt(i.hour_rsi14)}</td><td>${fmt(i.breakout_high)} / ${fmt(i.breakout_low)}</td><td>${fmt(i.daily_oi)} / ${fmt(i.daily_oi_change)}</td></tr>`;
  const m = d.metrics_by_root[mcx.root];
  $("mcxMetrics").innerHTML = [["Open", m.open], ["Closed eligible", m.closed], ["Win rate", `${fmt(m.win_rate)}%`], ["Net model R", fmt(m.net_r)], ["Expectancy R", fmt(m.expectancy_r)], ["Profit factor", fmt(m.profit_factor)], ["Interrupted / excluded", m.excluded]].map(([name, value]) => `<div class="metric"><span>${name}</span><strong>${value}</strong></div>`).join("");
  $("mcxFreshness").innerHTML = d.freshness.map(r => `<tr><td>${escapeHtml(r.symbol)} / ${r.frame === "day" ? "Daily" : "1h"}</td><td>${escapeHtml(r.state)}</td><td>${fmtDateTime(r.latest_closed)}</td><td>${fmtDateTime(r.expected)}</td><td>${fmtDateTime(r.last_success)}</td><td>${r.bars ?? "-"}</td><td>${escapeHtml(r.error || "-")}</td></tr>`).join("");
  $("mcxEvents").innerHTML = d.events.map(r => `<tr><td>${fmtDateTime(r.time)}</td><td>${escapeHtml(mcxLabel(r.event))}</td><td><details><summary>${escapeHtml(r.error || r.symbol || r.scope || r.reason || "Details")}</summary><pre>${escapeHtml(JSON.stringify(r, null, 2))}</pre></details></td></tr>`).join("") || '<tr><td colspan="3">No events in this date range.</td></tr>';
  $("mcxDelivery").innerHTML = d.delivery.map(r => `<tr><td>${escapeHtml(r.trade_id)}</td><td>${escapeHtml(r.event_kind)}</td><td>${fmtDateTime(r.created_at)}</td><td>${escapeHtml(r.status)}</td><td>${escapeHtml(r.error || "-")}</td></tr>`).join("") || '<tr><td colspan="5">No alert deliveries in this date range.</td></tr>';
  $("mcxExport").href = `/api/mcx/export?${mcxDates()}&format=zip`;
  renderMcxTrades();
  drawMcxChart();
}

function renderMcxTrades() {
  const rows = (mcx.data?.trade_history || mcx.data?.trades || []).filter(t => tradeHistoryMatches(t, "mcx") && t.root === mcx.root && ($("mcxTradeState").value === "all" || t.status === $("mcxTradeState").value));
  const pages = Math.max(1, Math.ceil(rows.length / 10));
  mcx.page = Math.min(mcx.page, pages);
  $("mcxTrades").innerHTML = rows.slice((mcx.page-1)*10, mcx.page*10).map(t => `<tr><td>${escapeHtml(t.id)}<div class="cell-note">${escapeHtml(t.symbol)} / ${escapeHtml(t.expiry)}</div></td><td>${escapeHtml(t.status)} / ${escapeHtml(t.direction)}</td><td>${tradeResultCell(t.result)}</td><td>${fmtDateTime(t.entry_time)}<br>${fmt(t.entry_price)}</td><td>${fmtDateTime(t.signal_time)}<br>${fmt(t.reference_price)}</td><td>${fmtDateTime(t.exit_time)}<br>${fmt(t.exit_price)}</td><td>${formatSeconds(t.open_seconds)}</td><td>${fmt(t.stop_level)} / ${fmt(t.target_level)}</td><td>${paperResult(t.net_r,"R",t,"After assumed costs")}</td><td>${fmt(t.indicators?.atr_percent)}%</td><td>${escapeHtml(mcxLabel(t.exit_reason || "-"))}<div class="cell-note">${escapeHtml(mcxLabel(t.monitoring_interrupted || ""))}</div></td></tr>`).join("") || '<tr><td colspan="11">No trades matching these filters.</td></tr>';
  $("mcxPage").textContent = `Page ${mcx.page} of ${pages} / ${rows.length} trades`;
  $("mcxPrev").disabled = mcx.page <= 1;
  $("mcxNext").disabled = mcx.page >= pages;
}

function drawMcxChart() {
  const canvas = $("mcxChart"), parent = canvas.parentElement;
  const w = Math.max(260, parent.clientWidth), h = 180, ratio = window.devicePixelRatio || 1;
  canvas.width = w*ratio; canvas.height = h*ratio;
  const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
  ctx.fillStyle = "#f8fafc"; ctx.fillRect(0, 0, w, h);
  const rows = mcx.data?.charts[mcx.root] || [];
  $("mcxChartNote").textContent = rows.length ? `Closed daily futures / ${rows.length} candles / ${fmtDateTime(rows[0].time)} to ${fmtDateTime(rows.at(-1).time)}` : "No downloaded daily candles.";
  if (rows.length < 2) { ctx.fillStyle = "#526274"; ctx.font = "13px sans-serif"; ctx.fillText("Daily futures history unavailable", 16, 88); return; }
  const values = rows.map(r => r.close), lo = Math.min(...values), hi = Math.max(...values), span = Math.max(hi-lo, 1);
  ctx.strokeStyle = "#dae2e8"; ctx.lineWidth = 1;
  for (let n = 0; n < 3; n++) { const y = 20+n*65; ctx.beginPath(); ctx.moveTo(12, y); ctx.lineTo(w-82, y); ctx.stroke(); ctx.fillStyle = "#526274"; ctx.font = "11px sans-serif"; ctx.fillText(fmt(hi-span*n/2), w-76, y+4); }
  ctx.beginPath(); ctx.strokeStyle = values.at(-1) >= values[0] ? "#147d64" : "#c34141"; ctx.lineWidth = 2;
  rows.forEach((r, n) => { const x = 12+n*(w-100)/(rows.length-1), y = 150-(r.close-lo)/span*130; if (!n) ctx.moveTo(x,y); else ctx.lineTo(x,y); }); ctx.stroke();
}

$("mcxStart").addEventListener("click", () => controlMcx("start"));
$("mcxStop").addEventListener("click", () => controlMcx("stop"));
$("mcxDownloadData").addEventListener("click", () => controlMcx("download"));
$("mcxTest").addEventListener("click", () => controlMcx("test-telegram"));
$("mcxPrev").addEventListener("click", () => { mcx.page--; renderMcxTrades(); });
$("mcxNext").addEventListener("click", () => { mcx.page++; renderMcxTrades(); });
$("mcxTradeState").addEventListener("change", () => { mcx.page = 1; renderMcxTrades(); });
document.querySelectorAll("[data-mcx-root]").forEach(b => b.addEventListener("click", () => {
  mcx.root = b.dataset.mcxRoot; mcx.page = 1;
  document.querySelectorAll("[data-mcx-root]").forEach(el => { const selected = el === b; el.classList.toggle("active", selected); el.setAttribute("aria-selected", String(selected)); });
  renderMcx();
}));
for (const id of ["mcxFrom", "mcxTo"]) $(id).addEventListener("change", () => { mcx.page = 1; ++mcx.generation; loadMcx(); });
const mcxToday = new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
tradeFilterEvents("mcx", () => { mcx.page = 1; renderMcxTrades(); });
$("mcxFrom").value = $("mcxTo").value = mcxToday;
setInterval(loadMcx, 5000);
window.addEventListener("resize", () => { if (state.activeTab === "mcx") drawMcxChart(); });
