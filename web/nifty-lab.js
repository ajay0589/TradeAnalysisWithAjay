/* Isolated view state: opening a tab never starts a scanner or a download. */
const niftyLab = {data: null, setup: "Nifty_Setup1", page: 1, loading: false, action: false, request: 0};
const labLabel = (value) => String(value ?? "-").replaceAll("_", " ");
const labVariant = (value) => value === "technical" ? "Technical only" : "Technical + options";

function labSessionDate() {
  return new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
}

async function loadNiftyLab() {
  if (niftyLab.loading || niftyLab.action || state.activeTab !== "nifty-lab") return;
  niftyLab.loading = true;
  const request = ++niftyLab.request, day = $("labDate").value;
  try {
    const data = await api(`/api/nifty-lab/status?date=${encodeURIComponent(day)}`);
    if (request === niftyLab.request && day === $("labDate").value) {
      niftyLab.data = data;
      renderNiftyLab();
    }
  } catch (error) { $("labStatus").textContent = `Status unavailable: ${error.message}`; }
  finally { niftyLab.loading = false; if (day !== $("labDate").value) loadNiftyLab(); }
}

async function controlNiftyLab(action, setup = null) {
  if (niftyLab.action) return;
  const open = (niftyLab.data?.trades || []).some(t => t.status === "open" && (!setup || t.setup === setup));
  if (action === "stop" && open && !confirm("Stop entry and exit monitoring? Open paper trades will be marked interrupted and excluded from performance when closed.")) return;
  niftyLab.action = true;
  ++niftyLab.request;
  $("labAction").textContent = `${action === "test-telegram" ? "Test" : labLabel(action)} requested...`;
  document.querySelectorAll("[data-lab-action]").forEach(b => b.disabled = true);
  $("labStartAll").disabled = $("labStopAll").disabled = true;
  try {
    const data = await postApi(`/api/nifty-lab/${action}`, {setup, telegram_enabled: $("labTelegram").checked});
    $("labAction").textContent = action === "test-telegram" ? (data.sent ? "Test sent" : data.error || "Delivery failed") : "";
  } catch (error) { $("labAction").textContent = error.message; }
  finally { niftyLab.action = false; if (niftyLab.data) renderNiftyLab(); await loadNiftyLab(); }
}

function labShortMetric(m) {
  return `${m.closed} / ${fmt(m.win_rate)}% / ${fmt(m.net_r)} R<div class="cell-note">${m.open} open / ${m.reference_exits} excluded</div>`;
}

function renderNiftyLab() {
  const d = niftyLab.data;
  if (!d) return;
  $("labStatus").textContent = `${d.running_count} / 5 running | 10 paper ledgers | ${d.version}`;
  $("labStartAll").disabled = niftyLab.action || d.running_count === 5;
  $("labStopAll").disabled = niftyLab.action || d.running_count === 0;
  $("labTelegramStatus").textContent = d.telegram_configured ? "Lab chat configured" : "Lab chat not configured";
  $("labTestTelegram").disabled = !d.telegram_configured || niftyLab.action;
  $("labTelegram").disabled = d.running_count > 0;
  $("labOverview").innerHTML = d.comparisons.map(row => {
    const s = d.states[row.setup], definition = d.setups[row.setup];
    return `<tr><td><strong>${escapeHtml(row.setup)}</strong><div class="cell-note">${escapeHtml(definition.name)} / ${escapeHtml(definition.frame)}</div></td>
      <td>${s.enabled ? "Running" : "Stopped"}<div class="cell-note">${escapeHtml(labLabel(s.phase))}<br>${escapeHtml(labLabel(s.error || s.reason))}</div></td>
      <td>${fmtDateTime(s.started_at)}<br>${fmtDateTime(s.stopped_at)}</td><td>${fmtDateTime(s.last_checked)}<br>${fmtDateTime(s.last_bar)}</td>
      <td>${labShortMetric(row.metrics.technical)}</td><td>${labShortMetric(row.metrics.combined)}</td>
      <td><button type="button" data-lab-action="start" data-setup="${row.setup}" ${s.enabled ? "disabled" : ""}>Start</button><button type="button" data-lab-action="stop" data-setup="${row.setup}" ${s.enabled ? "" : "disabled"}>Stop</button></td></tr>`;
  }).join("");
  const evidence = d.option_evidence || {};
  $("labDataSummary").textContent = `Options: ${labLabel(evidence.state || "unavailable")} / ${labLabel(evidence.bias)}`;
  const feeds = d.data_service || {};
  $("labFeeds").textContent = `Candles: ${labLabel(feeds.candles?.status || "idle")} ${feeds.candles?.error || ""} | Last refresh ${fmtDateTime(feeds.candles?.last_success)} | Options: ${labLabel(feeds.options?.status)} ${feeds.options?.error || ""} | Last refresh ${fmtDateTime(feeds.options?.last_success)} | Quotes: ${d.quote_feed?.mode || "stopped"} ${d.quote_feed?.error || ""}`;
  $("labFreshness").innerHTML = (d.freshness || []).map(r => `<tr><td>${escapeHtml(r.frame)}</td><td>${escapeHtml(r.state)}</td><td>${fmtDateTime(r.latest)}</td><td>${fmtDateTime(r.expected)}</td></tr>`).join("") +
    `<tr><td>Live spot: ${fmt(d.spot?.price)}</td><td>${d.spot_fresh ? "fresh" : d.spot?.price ? "stale" : "missing"} / ${escapeHtml(d.spot?.source || "unavailable")}</td><td>${fmtDateTime(d.spot?.quote_time)}</td><td>-</td></tr>`;
  $("labOptions").innerHTML = [3, 6, 15].map(m => {
    const w = evidence.windows?.[String(m)] || {};
    return `<tr><td>${m} minutes</td><td>${escapeHtml(labLabel(w.bias || "warming_up"))}</td><td>${w.matched ?? "-"} / ${fmt(w.coverage == null ? null : w.coverage * 100)}%</td><td>${fmtDateTime(w.from_received_at)}<br>${fmtDateTime(w.to_received_at)}</td><td>${escapeHtml(labLabel(w.reason || evidence.reason))}<div class="cell-note">${escapeHtml(Object.entries(w.classifications || {}).map(([k,v]) => `${labLabel(k)}: ${v}`).join("; "))}</div></td></tr>`;
  }).join("");
  document.querySelectorAll("[data-lab-setup]").forEach(b => {
    const selected = b.dataset.labSetup === niftyLab.setup;
    b.classList.toggle("active", selected);
    b.setAttribute("aria-selected", String(selected));
  });
  const definition = d.setups[niftyLab.setup], row = d.comparisons.find(r => r.setup === niftyLab.setup);
  $("labSetupTitle").textContent = `${niftyLab.setup}: ${definition.name}`;
  $("labRules").textContent = definition.rules;
  $("labMetrics").innerHTML = `<table class="readable-table"><thead><tr><th>Variant</th><th>Open</th><th>Closed</th><th>Win %</th><th>Net R</th><th>Expectancy R</th><th>Profit factor</th><th>Max drawdown R</th><th>Avg duration</th><th>Excluded</th></tr></thead><tbody>${["technical", "combined"].map(v => {
    const m = row.metrics[v];
    return `<tr><td>${labVariant(v)}</td><td>${m.open}</td><td>${m.closed}</td><td>${fmt(m.win_rate)}%</td><td>${fmt(m.net_r)}</td><td>${fmt(m.expectancy_r)}</td><td>${m.no_losses ? "No losses" : fmt(m.profit_factor)}</td><td>${fmt(m.max_drawdown_r)}</td><td>${fmt(m.average_open_minutes)} min</td><td>${m.reference_exits}</td></tr>`;
  }).join("")}</tbody></table>`;
  drawLabCurve(row.metrics);
  $("labSample").textContent = `${row.checked_candles} candles checked / ${row.signals} technical signals / ${row.paired_signals} paired entries. Session metrics use closed, uninterrupted observations after assumed costs. Spot results are not option P&L; small samples are inconclusive.`;
  renderLabTrades();
  const decisions = (d.decisions || []).filter(r => r.setup === niftyLab.setup).slice(0, 50);
  $("labDecisionCount").textContent = `${decisions.length} shown / ${row.checked_candles} total`;
  $("labDecisions").innerHTML = decisions.map(r => `<tr><td>${fmtDateTime(r.checked_at)}</td><td>${fmtDateTime(r.closed_at)}</td><td>${escapeHtml(labLabel(r.direction || r.reason))}</td><td>${escapeHtml(labLabel(r.variants?.technical))}</td><td>${escapeHtml(labLabel(r.variants?.combined))}</td><td><details><summary>Values</summary><pre>${escapeHtml(JSON.stringify({indicators:r.indicators,entry:r.entry_checks,options:r.option_evidence}, null, 2))}</pre></details></td></tr>`).join("") || '<tr><td colspan="6">No decisions for this session.</td></tr>';
  $("labEvents").innerHTML = (d.events || []).filter(e => !e.setup || e.setup === niftyLab.setup).slice().reverse().map(e => `<p>${fmtDateTime(e.time)} | ${escapeHtml(labLabel(e.event))} | ${escapeHtml(labLabel(e.reason || e.error || e.phase || e.worker || "-"))}</p>`).join("") || "No monitor events.";
  $("labDownload").href = `/api/nifty-lab/export?date=${encodeURIComponent($("labDate").value)}&format=zip`;
}

function labFilteredTrades() {
  return (niftyLab.data?.trades || []).filter(t => t.setup === niftyLab.setup && ($("labVariant").value === "all" || t.variant === $("labVariant").value) && ($("labTradeState").value === "all" || t.status === $("labTradeState").value));
}

function renderLabTrades() {
  const rows = labFilteredTrades(), pages = Math.max(1, Math.ceil(rows.length / 20));
  niftyLab.page = Math.min(niftyLab.page, pages);
  $("labTrades").innerHTML = rows.slice((niftyLab.page - 1) * 20, niftyLab.page * 20).map(t => {
    const delivery = (niftyLab.data.delivery || []).filter(e => e.trade_id === t.id).map(e => `${e.event_kind}: ${e.status}${e.error ? ` (${e.error})` : ""}`).join("; ");
    const seconds = t.open_seconds ?? Math.max(0, (parseDateTime(niftyLab.data.now) - parseDateTime(t.entry_time)) / 1000);
    return `<tr><td>${escapeHtml(t.id)}<div class="cell-note">${labVariant(t.variant)}</div></td><td>${escapeHtml(t.direction)} / ${escapeHtml(t.status)}</td><td>${fmtDateTime(t.entry_time)}</td><td>${fmt(t.entry_price)}</td><td>${fmtDateTime(t.exit_time)}</td><td>${fmt(t.exit_price)}</td><td>${formatSeconds(seconds)}</td><td>${fmt(t.stop_level)} / ${fmt(t.target_level)}</td><td>${fmt(t.net_r)}</td><td>${escapeHtml(labLabel(t.monitoring_interrupted || t.exit_reason || t.price_basis))}${t.status === "closed" && !t.performance_eligible ? '<div class="cell-note">Excluded from metrics</div>' : ""}</td><td>${escapeHtml(delivery || (t.telegram_enabled ? "No delivery record for this session" : "Off"))}</td></tr>`;
  }).join("") || '<tr><td colspan="11">No trades matching these filters.</td></tr>';
  $("labPage").textContent = `Page ${niftyLab.page} of ${pages} / ${rows.length} trades`;
  $("labPrev").disabled = niftyLab.page === 1;
  $("labNext").disabled = niftyLab.page === pages;
}

function drawLabCurve(metrics) {
  const canvas = $("labCurve"), ctx = canvas.getContext?.("2d");
  if (!ctx) return;
  canvas.width = Math.max(240, canvas.clientWidth || 1100);
  const width = canvas.width, height = canvas.height, left = 50, right = width - 16, top = 16, bottom = height - 26;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, width, height);
  const all = Object.values(metrics).flatMap(m => m.curve || []);
  const low = Math.min(-1, ...all.map(p => p.net_r)), high = Math.max(1, ...all.map(p => p.net_r));
  const y = v => bottom - (v - low) / (high - low) * (bottom - top);
  const day = $("labDate").value, start = Date.parse(`${day}T09:15:00+05:30`), end = Date.parse(`${day}T15:30:00+05:30`);
  const x = t => left + (t ? Math.max(0, Math.min(1, (Date.parse(t) - start) / (end - start))) : 0) * (right - left);
  ctx.font = "12px sans-serif";
  for (const value of [low, 0, high]) {
    ctx.strokeStyle = "#dde3e9"; ctx.beginPath(); ctx.moveTo(left, y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
    ctx.fillStyle = "#526174"; ctx.fillText(`${value.toFixed(1)} R`, 4, y(value) + 4);
  }
  ctx.fillText("09:15 IST", left, height - 6); ctx.fillText("15:30 IST", right - 68, height - 6);
  for (const [variant, color] of [["technical", "#137967"], ["combined", "#bd541c"]]) {
    const points = metrics[variant].curve || [];
    if (points.length < 2) continue;
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    points.forEach((p, i) => { if (!i) ctx.moveTo(x(p.time), y(p.net_r)); else { ctx.lineTo(x(p.time), y(points[i - 1].net_r)); ctx.lineTo(x(p.time), y(p.net_r)); } });
    ctx.stroke();
  }
  if (!all.some(p => p.time)) { ctx.fillStyle = "#526174"; ctx.fillText("No eligible closed trades", left + 20, top + 20); }
}

function exportLabCsv() {
  if (niftyLab.data?.date !== $("labDate").value) { $("labAction").textContent = "Selected session is still loading."; return; }
  const columns = ["id", "setup", "variant", "direction", "status", "entry_time", "entry_price", "exit_time", "exit_price", "open_seconds", "stop_level", "target_level", "net_r", "cost_points", "performance_eligible", "exit_reason", "monitoring_interrupted"];
  const cell = v => `"${String(v ?? "").replace(/^[=+@]/, "'$&").replaceAll('"', '""')}"`;
  const rows = niftyLab.data?.trades || [];
  downloadBlob(new Blob([columns.join(",") + "\r\n" + rows.map(t => columns.map(k => cell(t[k])).join(",")).join("\r\n")], {type: "text/csv;charset=utf-8"}), `nifty-setup-lab-trades-${$("labDate").value}.csv`);
}

$("labDate").value = labSessionDate();
$("labStartAll").addEventListener("click", () => controlNiftyLab("start"));
$("labStopAll").addEventListener("click", () => controlNiftyLab("stop"));
$("labTestTelegram").addEventListener("click", () => controlNiftyLab("test-telegram"));
$("labOverview").addEventListener("click", e => { const b = e.target.closest("[data-lab-action]"); if (b && !b.disabled) controlNiftyLab(b.dataset.labAction, b.dataset.setup); });
document.querySelectorAll("[data-lab-setup]").forEach(b => b.addEventListener("click", () => { niftyLab.setup = b.dataset.labSetup; niftyLab.page = 1; renderNiftyLab(); }));
document.querySelectorAll("[data-tab-target]").forEach(b => b.addEventListener("click", () => { if (b.dataset.tabTarget === "nifty-lab") loadNiftyLab(); }));
$("labDate").addEventListener("change", () => {
  if (!$("labDate").value) $("labDate").value = labSessionDate();
  $("labDownload").href = `/api/nifty-lab/export?date=${encodeURIComponent($("labDate").value)}&format=zip`;
  niftyLab.page = 1; ++niftyLab.request; loadNiftyLab();
});
for (const id of ["labVariant", "labTradeState"]) $(id).addEventListener("change", () => { niftyLab.page = 1; renderLabTrades(); });
$("labPrev").addEventListener("click", () => { niftyLab.page = Math.max(1, niftyLab.page - 1); renderLabTrades(); });
$("labNext").addEventListener("click", () => { niftyLab.page += 1; renderLabTrades(); });
$("labCsv").addEventListener("click", exportLabCsv);
setInterval(loadNiftyLab, 5000);
window.addEventListener("resize", () => {
  if (state.activeTab === "nifty-lab" && niftyLab.data) drawLabCurve(niftyLab.data.comparisons.find(r => r.setup === niftyLab.setup).metrics);
});
