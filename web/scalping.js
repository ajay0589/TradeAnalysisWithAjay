/* Paper scalping has no implicit start/download on navigation. */
const scalp = {data: null, symbol: "NIFTY", busy: false, loading: false, request: 0, pages: {Trade:1, Decision:1, Research:1, Event:1}};
const scalpText = v => escapeHtml(String(v ?? "-").replaceAll("_", " "));
function scalpRange() { return auditRange("scalpFrom", "scalpTo").query; }
function scalpKey() { return `${scalp.symbol}:${$("scalpSetup").value}`; }
function scalpPage(name, rows, render) {
  const pages = Math.max(1, Math.ceil(rows.length / 10));
  scalp.pages[name] = Math.min(scalp.pages[name], pages);
  $("scalp" + name + "Pager").innerHTML = `<button type="button" data-scalp-page="${name}" data-step="-1" title="Previous page" aria-label="Previous page" ${scalp.pages[name] === 1 ? "disabled" : ""}>&larr;</button><span>Page ${scalp.pages[name]} of ${pages} / ${rows.length} rows</span><button type="button" data-scalp-page="${name}" data-step="1" title="Next page" aria-label="Next page" ${scalp.pages[name] === pages ? "disabled" : ""}>&rarr;</button>`;
  return rows.slice((scalp.pages[name]-1)*10, scalp.pages[name]*10).map(render).join("");
}
async function loadScalping() {
  if (state.activeTab !== "scalping" || scalp.loading || scalp.busy) return;
  scalp.loading = true;
  const request = ++scalp.request;
  try {
    const query = scalpRange(), data = await api(`/api/scalping/status?${query}`);
    if (request === scalp.request && query === scalpRange()) { scalp.data = data; renderScalping(); }
  } catch(error) { $("scalpAction").textContent = error.message; }
  finally { scalp.loading = false; }
}
async function scalpControl(action, setup=null, download=false) {
  if (scalp.busy) return;
  scalp.busy = true; ++scalp.request;
  $("scalpAction").textContent = "Request in progress...";
  try {
    const r = await postApi(`/api/scalping/${action}`, {setup, download, telegram_enabled: $("scalpTelegram").checked});
    $("scalpAction").textContent = action === "test-telegram" ? (r.sent ? "Test sent" : r.error || "Test failed") : action === "stop" ? "New entries stopped; open trades remain monitored." : "Request accepted.";
  } catch(error) { $("scalpAction").textContent = error.message; }
  finally { scalp.busy = false; await loadScalping(); }
}
function renderScalping() {
  const d = scalp.data; if (!d) return;
  const e = scalpText, key = scalpKey();
  $("scalpStatus").textContent = `${d.running_count}/9 running | 27 paper ledgers | Stream ${d.stream_connected ? "connected" : "disconnected"}`;
  $("scalpStart").disabled = d.running_count === 9 || d.research?.phase === "running";
  $("scalpStop").disabled = d.running_count === 0;
  $("scalpTelegram").disabled = d.running_count > 0;
  $("scalpTest").disabled = !d.telegram_configured;
  $("scalpZip").href = `/api/scalping/export?${scalpRange()}&format=zip`;
  $("scalpCsv").href = `/api/scalping/export?${scalpRange()}&format=csv`;
  $("scalpOverview").innerHTML = Object.entries(d.states).map(([k,s]) => `<tr><td>${e(k)}<div class="cell-note">${e(d.setups[k.split(":")[1]])}</div></td><td>${e(s.phase)}<div class="cell-note">${e(s.reason)}</div></td><td>${fmtDateTime(s.started_at)}<br>${fmtDateTime(s.stopped_at)}</td><td>${fmtDateTime(s.last_checked)}<br>${fmtDateTime(s.last_bar)}</td><td><button type="button" data-scalp-action="${s.enabled ? "stop" : "start"}" data-key="${e(k)}">${s.enabled ? "Stop entries" : "Start"}</button></td></tr>`).join("");
  $("scalpFeeds").innerHTML = Object.entries(d.feeds).map(([s,f]) => `<tr><td>${e(s)}</td><td>${e(f.phase)}<br>${fmtDateTime(f.latest_candle)}</td><td>${fmtDateTime(f.last_refresh)}</td><td>${fmt(f.spot?.price)}<br>${fmtDateTime(f.spot?.quote_time)}</td><td>${fmt(f.pair?.strike)} / ${e(f.pair?.expiry)}</td><td>${f.option_samples}<div class="cell-note">${e(f.error || f.stream_error)}</div></td></tr>`).join("");
  document.querySelectorAll("[data-scalp-index]").forEach(b => { b.classList.toggle("active", b.dataset.scalpIndex === scalp.symbol); b.setAttribute("aria-selected", String(b.dataset.scalpIndex === scalp.symbol)); });
  $("scalpMetrics").innerHTML = d.metrics.filter(r => r.setup === key).map(r => `<tr><td>${r.variant}: ${e(d.variants[r.variant])}</td><td>${r.open}</td><td>${r.closed}</td><td>${fmt(r.win_rate)}</td><td>${fmt(r.net_r)}</td><td>${fmt(r.expectancy_r)}</td><td>${fmt(r.profit_factor)}</td><td>${fmt(r.max_drawdown_r)}</td><td>${r.excluded}</td></tr>`).join("");
  const trades = d.trades.filter(t => t.setup === key && ($("scalpVariant").value === "all" || t.variant === $("scalpVariant").value) && ($("scalpTradeStatus").value === "all" || t.status === $("scalpTradeStatus").value));
  $("scalpTrades").innerHTML = scalpPage("Trade", trades, t => {
    const q = d.feeds[t.symbol]?.spot, age = q ? (Date.parse(d.now)-Date.parse(q.quote_time))/1000 : Infinity;
    const mark = t.status === "closed" ? t.net_r : age >= 0 && age <= 3 ? ((q.price-t.entry_price)*(t.direction === "bullish" ? 1 : -1))/t.risk_points : null;
    const duration = t.open_seconds ?? Math.max(0,(Date.parse(d.now)-Date.parse(t.entry_time))/1000);
    const delivery = d.delivery.filter(r => r.trade_id === t.id).map(r => `${r.event_kind}: ${r.status}`).join("; ");
    return `<tr><td>${e(t.id)} / ${e(t.variant)}</td><td>${e(t.direction)} / ${e(t.status)}</td><td>${fmtDateTime(t.entry_time)}<br>${fmt(t.entry_price)}</td><td>${fmtDateTime(t.exit_time)}<br>${fmt(t.exit_price)}</td><td>${formatSeconds(duration)}</td><td>${fmt(t.stop_level)} / ${fmt(t.target_level)}</td><td>${t.status === "closed" && !t.performance_eligible ? "Excluded" : `${fmt(mark)} R`}<div class="cell-note">${t.status === "open" ? "Gross mark" : "Assumed costs"}</div></td><td>${e(t.option_entry?.tradingsymbol)}<br>${e(t.option_entry?.expiry)} / ${fmt(t.option_entry?.strike)}<br>${fmt(t.option_entry?.ask)}</td><td>${fmt(t.option_exit?.bid)} / ${fmt(t.option_gross_points)}</td><td>${vixCell(t.india_vix_entry)}</td><td>${e(t.monitoring_interrupted || t.exit_reason)}<div class="cell-note">${e(delivery || "Telegram off")}</div></td></tr>`;
  }) || '<tr><td colspan="11">No trades for these filters.</td></tr>';
  const decisions=d.decisions.filter(r=>r.setup===key);
  $("scalpDecisionCount").textContent=`${decisions.length} recent / ${d.decision_counts?.[key] ?? 0} total`;
  $("scalpDecisions").innerHTML = scalpPage("Decision", decisions, r=>`<tr><td>${fmtDateTime(r.checked_at)}</td><td>${e(r.direction || r.reason)} / ${fmt(r.signal_delay_seconds)}s</td><td>${e(r.variants.A)}</td><td>${e(r.variants.B)}</td><td>${e(r.variants.C)}</td><td><details><summary>Values</summary><pre>${escapeHtml(JSON.stringify({indicators:r.indicators, spot:r.spot, options:r.option_evidence},null,2))}</pre></details></td></tr>`) || '<tr><td colspan="6">No decisions.</td></tr>';
  const research = d.research || {};
  $("scalpResearchStatus").textContent = `${research.phase || "idle"} | ${research.progress || research.error || Object.entries(research.errors || {}).map(([s,e])=>`${s}: ${e}`).join("; ") || "Unvalidated; no automatic promotion"}`;
  $("scalpResearch").disabled = $("scalpDownloadResearch").disabled = research.phase === "running" || d.running_count > 0;
  $("scalpCancelResearch").disabled = research.phase !== "running";
  $("scalpResearchRows").innerHTML = scalpPage("Research", (research.rows||[]).filter(r=>r.symbol===scalp.symbol && r.setup===$("scalpSetup").value), r=>`<tr><td>${e(r.symbol)} / ${e(r.setup)}</td><td>${r.minutes}m / ${r.target_r}R</td><td>${e(r.window)} / ${e(r.coverage)}</td><td>${r.trades}</td><td>${fmt(r.win_rate)}</td><td>${fmt(r.expectancy_r)}</td><td>${fmt(r.profit_factor)}</td><td>${fmt(r.max_drawdown_r)}</td></tr>`) || '<tr><td colspan="8">No research results.</td></tr>';
  $("scalpEvents").innerHTML = scalpPage("Event", d.events.slice().reverse(), r=>`<p>${fmtDateTime(r.time)} | ${e(r.setup || r.worker)} | ${e(r.event)} ${e(r.error || "")}</p>`);
  $("scalpRules").innerHTML = `<p>Paper-only, unvalidated. Observed spot results are not option fills or option net P&amp;L. Indicative option points use entry ask and exit bid, excluding fees and slippage. Stop new entries leaves exits running.</p><p>Scalp1: completed 5m EMA20/50 trend, 1m pullback and reclaim. Scalp2: completed 09:15-09:30 range breakout followed by a retest. Scalp3: failed opening-range break and confirmation in a flat 5m EMA regime.</p><p>A: technical only. B: A plus same-expiry, fixed-contract ATM CE/PE and spot agreement across 1m and 3m. C: B plus at least 0.5% OI change and positive volume increments on both contracts. OI classifications are hypotheses, not identified buyers or sellers. ATM changes reset evidence.</p><p>Live: 1m signals / structural stop / 1.5R target / maximum 20 minutes / entry cutoff 14:45 / exit 15:20 IST. Quotes at most 3 seconds old; maximum 1.5% option spread; signal age at most 10 seconds. Two basis points per side assumed spot costs. Monitoring gaps are excluded from results.</p><p>Research: 1m and 3m, 1R/1.5R/2R, 1/2 weeks and 1/2/3/6 months. Next-minute-open proxy, stop first on ambiguous candles. Development results are not held-out validation. Exact manual latency and option replay require historical quotes. Filters for futures VWAP, constituent breadth and VIX regimes are not enabled in this baseline.</p>`;
}
$("scalpFrom").value = $("scalpTo").value = labSessionDate();
$("scalpStart").addEventListener("click",()=>scalpControl("start"));
$("scalpStop").addEventListener("click",()=>scalpControl("stop"));
$("scalpTest").addEventListener("click",()=>scalpControl("test-telegram"));
$("scalpResearch").addEventListener("click",()=>scalpControl("research"));
$("scalpDownloadResearch").addEventListener("click",()=>scalpControl("research",null,true));
$("scalpCancelResearch").addEventListener("click",()=>scalpControl("cancel-research"));
$("tab-scalping").addEventListener("click",event=>{
  const b=event.target.closest("button"); if(!b || b.disabled) return;
  if(b.dataset.scalpAction) scalpControl(b.dataset.scalpAction,b.dataset.key);
  if(b.dataset.scalpIndex) { scalp.symbol=b.dataset.scalpIndex; Object.keys(scalp.pages).forEach(k=>scalp.pages[k]=1); renderScalping(); }
  if(b.dataset.scalpPage) { scalp.pages[b.dataset.scalpPage]+=Number(b.dataset.step); renderScalping(); }
});
for(const id of ["scalpSetup","scalpVariant","scalpTradeStatus"]) $(id).addEventListener("change",()=>{Object.keys(scalp.pages).forEach(k=>scalp.pages[k]=1);renderScalping();});
for(const id of ["scalpFrom","scalpTo"]) $(id).addEventListener("change",()=>{++scalp.request;Object.keys(scalp.pages).forEach(k=>scalp.pages[k]=1);loadScalping();});
setInterval(loadScalping,3000);
