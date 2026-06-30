const state = {
  symbols: [],
  lastAnalysis: null,
  bulkJobId: null,
  bulkPollTimer: null,
  krishnaRefreshJobId: null,
  lastKrishnaRows: [],
  lastBacktest: null,
  strategies: [],
  lastGenericBacktest: null,
  optionMonitorJobId: null,
  optionMonitorPollTimer: null,
  nifty: {
    context: null,
    candidates: [],
    payoff: null,
    backtest: null,
  },
};

const $ = (id) => document.getElementById(id);

function activateTab(name) {
  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    const active = button.dataset.tabTarget === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.tabPanel === name);
  });
}

const BULK_TIMEFRAME_LABELS = {
  month: "Monthly",
  week: "Weekly",
  day: "Day",
  "60minute": "1 hour",
  "15minute": "15 min",
};

const BULK_DERIVED_MIN_DAYS = {
  month: 1460,
  week: 730,
};

const OPPORTUNITY_LABELS = {
  bullish_breakout: "Bullish Breakout",
  bullish_pullback: "Bullish Pullback",
  bullish_trend: "Bullish Trend",
  bearish_breakdown: "Bearish Breakdown",
  bearish_pullback: "Bearish Pullback",
  bearish_trend: "Bearish Trend",
  neutral_range: "Neutral Range",
  compression: "Compression Watch",
  compression_watch: "Compression Watch",
  avoid: "Avoid / Choppy",
  avoid_choppy: "Avoid / Choppy",
};

function fmt(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return value.toFixed(2);
  return String(value);
}

function fmtInt(value) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("en-IN");
}

function fmtPct(value) {
  if (value === null || value === undefined || value === "") return "-";
  return `${Number(value).toFixed(2)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function csvValue(value) {
  const text = Array.isArray(value) ? value.join("; ") : String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadCsv(filename, rows, columns) {
  if (!rows || !rows.length) {
    setNotes("No rows available to download.", true);
    return;
  }
  const header = columns.map((column) => csvValue(column.label)).join(",");
  const lines = rows.map((row) => columns.map((column) => csvValue(column.value(row))).join(","));
  const blob = new Blob([[header, ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function copyText(text, successMessage) {
  if (!text) {
    setNotes("Nothing to copy.", true);
    return;
  }
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
  } else {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  setNotes(successMessage);
}

function chartParams() {
  const params = new URLSearchParams({
    timeframe: $("timeframeSelect").value,
  });
  const days = $("daysBack").value.trim();
  const fromDate = $("fromDate").value.trim();
  const toDate = $("toDate").value.trim();
  if (days) params.set("days", days);
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  return params;
}

function scanParams() {
  const params = new URLSearchParams({
    timeframe: $("scanTimeframeSelect").value,
  });
  const days = $("scanDays").value.trim();
  const limit = $("scanLimit").value.trim();
  if (days) params.set("days", days);
  params.set("limit", limit || "all");
  params.set("option_chain", $("scanOptionChainToggle").checked ? "true" : "false");
  params.set("option_chain_limit", $("scanOptionChainLimit").value.trim() || "5");
  params.set("strikes_around", $("scanStrikesAround").value.trim() || "10");
  if ($("scanExpiry").value) params.set("expiry", $("scanExpiry").value);
  return params;
}

async function api(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(niftyRestartHint(path, payload.error || "Request failed"));
  return payload;
}

async function postApi(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(niftyRestartHint(path, payload.error || "Request failed"));
  return payload;
}

function niftyRestartHint(path, message) {
  if (String(path).includes("/api/nifty") && message === "Not found") {
    return "NIFTY API is not active in the running server. Restart the UI with scripts/start_web_ui.ps1 so web_app.py reloads.";
  }
  return message;
}

async function loadZerodhaLoginUrl() {
  try {
    const data = await api("/api/zerodha/login-url");
    $("zerodhaLoginLink").href = data.login_url;
    $("zerodhaLoginLink").classList.remove("disabled");
  } catch (error) {
    $("zerodhaLoginLink").removeAttribute("href");
    setNotes([error.message], true);
  }
}

async function checkZerodhaStatus() {
  setZerodhaStatus("checking", "checking");
  try {
    const data = await api("/api/zerodha/status");
    renderZerodhaStatus(data);
    return data;
  } catch (error) {
    setZerodhaStatus("failed", "failed");
    setNotes([error.message], true);
    return null;
  }
}

async function updateZerodhaToken() {
  const requestToken = $("zerodhaRedirectUrl").value.trim();
  if (!requestToken) {
    setNotes(["Paste the redirected Zerodha URL or request_token first."], true);
    return;
  }
  setZerodhaStatus("updating", "checking");
  try {
    const data = await postApi("/api/zerodha/access-token", { request_token: requestToken });
    $("zerodhaRedirectUrl").value = "";
    setNotes(data.message);
    await checkZerodhaStatus();
  } catch (error) {
    setZerodhaStatus("failed", "expired");
    setNotes([error.message], true);
  }
}

function renderZerodhaStatus(data) {
  const status = data.token_status || "missing";
  const label = status === "valid" ? "valid" : status === "missing" ? "missing" : "expired";
  setZerodhaStatus(label, label);
  if (data.message) setNotes(data.message, status !== "valid" && status !== "missing");
}

function setZerodhaStatus(text, className) {
  const status = $("zerodhaStatus");
  status.textContent = text;
  status.className = `token-status ${className}`;
}

async function loadSymbols() {
  const data = await api("/api/symbols");
  state.symbols = data.symbols;
  $("availableCount").textContent = `${data.available} ready`;
  $("missingCount").textContent = `${data.missing} missing candles`;
  $("dataStatus").textContent = `${data.total_fno_symbols || data.total} F&O stocks + ${data.total_indexes || 0} indexes tracked`;

  const list = $("symbolList");
  const monitorList = $("optionMonitorSymbolList");
  list.innerHTML = "";
  monitorList.innerHTML = "";
  data.symbols.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.symbol;
    option.label = row.name || row.symbol;
    list.appendChild(option);
    const monitorOption = document.createElement("option");
    monitorOption.value = row.symbol;
    monitorOption.label = row.name || row.symbol;
    monitorList.appendChild(monitorOption);
  });
}

async function loadSectorStatus() {
  try {
    const data = await api("/api/sector-map/status");
    renderSectorStatus(data);
  } catch (error) {
    $("sectorStatus").textContent = error.message;
  }
}

function renderSectorStatus(data) {
  if (!data.exists) {
    $("sectorStatus").textContent = `Missing sector map: ${data.path}`;
    return;
  }
  $("sectorStatus").textContent = `${data.mapped} mapped / ${data.unmapped} unmapped / ${data.sectors} sectors (${data.generated_on || "unknown date"})`;
}

async function uploadSectorCsv() {
  const file = $("sectorCsvFile").files[0];
  if (!file) {
    setNotes(["Choose a sector CSV file first."], true);
    return;
  }
  setNotes("Generating sector map...");
  try {
    const csvText = await file.text();
    const data = await postApi("/api/sector-map/from-csv", { csv_text: csvText });
    renderSectorStatus(data);
    setNotes(`Sector map generated: ${data.mapped} mapped, ${data.unmapped} unmapped.`);
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function loadFiiDii(refresh = false) {
  try {
    const data = refresh ? await postApi("/api/fii-dii/refresh", {}) : await api("/api/fii-dii");
    renderFiiDii(data);
    if (data.error) setNotes([`FII/DII refresh failed: ${data.error}`], true);
  } catch (error) {
    $("fiiDiiStatus").textContent = error.message;
  }
}

function renderFiiDii(data) {
  const rows = data.rows || [];
  $("fiiDiiStatus").textContent = rows.length
    ? `${rows.length} FII/DII row(s) loaded from ${data.path}`
    : `No FII/DII rows available at ${data.path}`;
  if (!rows.length) {
    $("fiiDiiHead").innerHTML = "";
    $("fiiDiiBody").innerHTML = "";
    return;
  }
  const columns = Object.keys(rows[0]).slice(0, 6);
  $("fiiDiiHead").innerHTML = `<tr>${columns.map((key) => `<th>${key}</th>`).join("")}</tr>`;
  $("fiiDiiBody").innerHTML = rows
    .slice(0, 6)
    .map((row) => `<tr>${columns.map((key) => `<td>${row[key] || "-"}</td>`).join("")}</tr>`)
    .join("");
}

async function loadOptionExpiries() {
  const symbol = $("symbolInput").value.trim().toUpperCase();
  const select = $("expirySelect");
  select.innerHTML = `<option value="">Nearest expiry</option>`;
  if (!symbol) return;
  try {
    const data = await api(`/api/option-expiries?symbol=${encodeURIComponent(symbol)}`);
    (data.expiries || []).forEach((expiry) => {
      const option = document.createElement("option");
      option.value = expiry;
      option.textContent = expiry === data.nearest ? `${expiry} (nearest)` : expiry;
      select.appendChild(option);
    });
    await loadOptionSnapshots();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function loadOptionMonitorExpiries() {
  const firstSymbol = firstMonitorSymbol();
  const select = $("optionMonitorExpiry");
  select.innerHTML = `<option value="">Nearest expiry</option>`;
  if (!firstSymbol) {
    $("optionMonitorExpiryStatus").textContent = "Enter a stock/index to load expiries.";
    return;
  }
  $("optionMonitorExpiryStatus").textContent = `Loading expiries for ${firstSymbol.toUpperCase()}...`;
  try {
    const data = await api(`/api/option-expiries?symbol=${encodeURIComponent(firstSymbol)}`);
    if (data.symbol && firstSymbol.toUpperCase() !== data.symbol) {
      replaceFirstMonitorSymbol(data.symbol);
    }
    (data.expiries || []).forEach((expiry) => {
      const option = document.createElement("option");
      option.value = expiry;
      option.textContent = expiry === data.nearest ? `${expiry} (nearest)` : expiry;
      select.appendChild(option);
    });
    $("optionMonitorExpiryStatus").textContent = (data.expiries || []).length
      ? `${data.expiries.length} expiry date(s) loaded for ${data.symbol}.`
      : `No expiries found for ${data.symbol}.`;
  } catch (error) {
    $("optionMonitorExpiryStatus").textContent = error.message;
  }
}

function firstMonitorSymbol() {
  return ($("optionMonitorSymbols").value || "")
    .split(",")
    .map((value) => value.trim())
    .find(Boolean) || "";
}

function replaceFirstMonitorSymbol(symbol) {
  const input = $("optionMonitorSymbols");
  const parts = input.value.split(",").map((value) => value.trim());
  if (!parts.length) {
    input.value = symbol;
    return;
  }
  parts[0] = symbol;
  input.value = parts.filter(Boolean).join(", ");
}

async function loadOptionSnapshots() {
  const symbol = $("symbolInput").value.trim().toUpperCase();
  const select = $("previousSnapshotSelect");
  select.innerHTML = `<option value="">Auto latest saved snapshot</option>`;
  if (!symbol) {
    $("snapshotStatus").textContent = "Enter a symbol to load snapshots.";
    return;
  }
  const params = new URLSearchParams({ symbol });
  if ($("expirySelect").value) params.set("expiry", $("expirySelect").value);
  try {
    const data = await api(`/api/option-snapshots?${params.toString()}`);
    const snapshots = data.snapshots || [];
    snapshots.forEach((snapshot) => {
      const option = document.createElement("option");
      option.value = snapshot.path;
      option.textContent = snapshot.label;
      select.appendChild(option);
    });
    $("snapshotStatus").textContent = `${snapshots.length} saved snapshot(s) for ${data.symbol}${data.expiry ? ` ${data.expiry}` : ""}`;
  } catch (error) {
    $("snapshotStatus").textContent = error.message;
  }
}

function useSelectedSnapshot() {
  $("previousSnapshot").value = $("previousSnapshotSelect").value;
}

async function analyze() {
  const symbol = $("symbolInput").value.trim().toUpperCase();
  if (!symbol) return;

  setNotes("Loading analysis...");
  const params = new URLSearchParams({
    symbol,
    option_chain: $("optionChainToggle").checked ? "true" : "false",
    previous_snapshot: $("previousSnapshot").value.trim(),
    expiry: $("expirySelect").value,
    strikes_around: $("strikesAround").value.trim() || "10",
    all_strikes: $("allStrikesToggle").checked ? "true" : "false",
    refresh: $("refreshToggle").checked ? "true" : "false",
  });
  chartParams().forEach((value, key) => params.set(key, value));
  try {
    const data = await api(`/api/analyze?${params.toString()}`);
    state.lastAnalysis = data;
    renderAnalysis(data);
    $("reportStatus").textContent = "Report ready to save";
    setNotes((data.warnings || []).concat(data.decision.warnings || []));
    if ($("optionChainToggle").checked) await loadOptionSnapshots();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function startBulkDownload() {
  adjustBulkDaysForHigherFrames();
  const timeframes = selectedBulkTimeframes();
  if (!timeframes.length) {
    setNotes(["Select at least one timeframe for bulk download."], true);
    return;
  }
  const payload = {
    timeframes,
    days: $("bulkDays").value ? Number($("bulkDays").value) : 90,
    limit: $("bulkLimit").value ? Number($("bulkLimit").value) : null,
  };
  try {
    const job = await postApi("/api/bulk-candles", payload);
    state.bulkJobId = job.job_id;
    renderBulkJob(job);
    pollBulkJob();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function pollBulkJob() {
  if (!state.bulkJobId) return;
  clearTimeout(state.bulkPollTimer);
  try {
    const job = await api(`/api/job?job_id=${encodeURIComponent(state.bulkJobId)}`);
    renderBulkJob(job);
    if (["queued", "running"].includes(job.status)) {
      state.bulkPollTimer = setTimeout(pollBulkJob, 1500);
    }
  } catch (error) {
    $("bulkStatus").textContent = error.message;
  }
}

function renderBulkJob(job) {
  const total = job.total || 0;
  const completed = job.completed || 0;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  const requested = bulkFrameLabels(job.requested_timeframes || job.timeframes || []);
  const sources = bulkFrameLabels(job.source_timeframes || job.timeframes || []);
  const windowDays = bulkWindowSummary(job) || (job.window && job.window.days ? `${job.window.days} days` : "");
  const requestNote = requested && sources && requested !== sources
    ? ` | requested ${requested}, downloaded ${sources}`
    : requested
      ? ` | ${requested}`
      : "";
  const windowNote = windowDays ? ` | ${windowDays}` : "";
  $("bulkProgressBar").style.width = `${percent}%`;
  $("bulkMeta").textContent = `${job.status} / ${completed}/${total}`;
  $("bulkStatus").textContent = `${job.status}: ${job.current || "idle"} | success ${job.successes} | failures ${job.failures}${requestNote}${windowNote}`;
  $("bulkErrors").innerHTML = (job.errors || [])
    .slice(-6)
    .map((error) => `<div>${error}</div>`)
    .join("");
}

function selectedBulkTimeframes() {
  const timeframes = [];
  if ($("bulkMonth").checked) timeframes.push("month");
  if ($("bulkWeek").checked) timeframes.push("week");
  if ($("bulkDay").checked) timeframes.push("day");
  if ($("bulkHour").checked) timeframes.push("60minute");
  if ($("bulk15").checked) timeframes.push("15minute");
  return timeframes;
}

function adjustBulkDaysForHigherFrames() {
  const minimum = selectedBulkTimeframes().reduce(
    (highest, timeframe) => Math.max(highest, BULK_DERIVED_MIN_DAYS[timeframe] || 0),
    0
  );
  if (!minimum) return;
  const field = $("bulkDays");
  const current = Number(field.value || 0);
  if (!current || current < minimum) field.value = String(minimum);
}

function bulkFrameLabels(values) {
  return (values || []).map((value) => BULK_TIMEFRAME_LABELS[value] || value).join(", ");
}

function bulkWindowSummary(job) {
  const windows = job.timeframe_windows || {};
  const entries = Object.entries(windows);
  if (!entries.length) return "";
  return entries
    .map(([timeframe, window]) => `${BULK_TIMEFRAME_LABELS[timeframe] || timeframe} ${window.days || "-"}d`)
    .join(", ");
}

async function startOptionMonitor() {
  const symbols = $("optionMonitorSymbols").value.trim() || $("symbolInput").value.trim();
  if (!symbols) {
    setNotes(["Enter at least one stock/index for the option-chain monitor."], true);
    return;
  }
  const payload = {
    symbols,
    expiry: $("optionMonitorExpiry").value || null,
    interval_minutes: Number($("optionMonitorInterval").value || 15),
    max_snapshots: Number($("optionMonitorKeep").value || 5),
    strikes_around: Number($("optionMonitorStrikes").value || 10),
    run_once: $("optionMonitorRunOnce").checked,
  };
  try {
    const job = await postApi("/api/option-chain-monitor/start", payload);
    state.optionMonitorJobId = job.job_id;
    renderOptionMonitorJob(job);
    pollOptionMonitorJob();
    setNotes("Option-chain monitor started. Keep this web UI server running for recurring pulls.");
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function stopOptionMonitor() {
  if (!state.optionMonitorJobId) {
    $("optionMonitorStatus").textContent = "No option-chain monitor job is active.";
    return;
  }
  try {
    const job = await postApi("/api/option-chain-monitor/stop", { job_id: state.optionMonitorJobId });
    renderOptionMonitorJob(job);
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function pollOptionMonitorJob() {
  if (!state.optionMonitorJobId) return;
  clearTimeout(state.optionMonitorPollTimer);
  try {
    const job = await api(`/api/job?job_id=${encodeURIComponent(state.optionMonitorJobId)}`);
    renderOptionMonitorJob(job);
    if (["queued", "running", "sleeping", "stopping"].includes(job.status)) {
      state.optionMonitorPollTimer = setTimeout(pollOptionMonitorJob, 2500);
    }
  } catch (error) {
    $("optionMonitorStatus").textContent = error.message;
  }
}

function renderOptionMonitorJob(job) {
  const nextRun = job.next_run_at ? ` | next ${fmtDateTime(job.next_run_at)}` : "";
  $("optionMonitorMeta").textContent = `${job.status} / pulls ${job.completed || 0}`;
  $("optionMonitorStatus").textContent = `${job.status}: ${(job.symbols || []).join(", ")} | success ${job.successes || 0} | failures ${job.failures || 0}${nextRun}`;
  $("optionMonitorResults").innerHTML = (job.results || [])
    .slice(-6)
    .reverse()
    .map((row) => `<div>${row.symbol} ${row.expiry}: ${row.contracts} contracts, PCR ${fmt(row.pcr_oi)}, max pain ${fmt(row.max_pain)}<div class="cell-note">${row.history_snapshot || ""}</div></div>`)
    .join("");
  if ((job.errors || []).length) {
    $("optionMonitorResults").innerHTML += (job.errors || [])
      .slice(-4)
      .map((error) => `<div class="error">${error}</div>`)
      .join("");
  }
}

async function saveReport() {
  if (!state.lastAnalysis) {
    setNotes(["Run Analyze before saving a report."], true);
    return;
  }
  try {
    const data = await postApi("/api/export-report", state.lastAnalysis);
    $("reportStatus").textContent = `Saved: ${data.path}`;
    setNotes(`Report saved: ${data.path}`);
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function scan(type) {
  setNotes(`Loading ${type} scan...`);
  setScanProgress("running", `Preparing ${type} scan...`);
  try {
    if ($("scanRefreshToggle").checked) {
      await refreshCandlesForScan();
    }
    const params = scanParams();
    params.set("type", type);
    const optionNote = $("scanOptionChainToggle").checked
      ? ` Pulling option chain for top ${$("scanOptionChainLimit").value || 5} shown candidate(s).`
      : "";
    setScanProgress("running", `Scanning ${type} candidates from candle data.${optionNote}`);
    const data = await api(`/api/scan?${params.toString()}`);
    if (data.summary && $("scanRefreshToggle").checked) {
      data.summary.latest_candles_pulled = true;
      data.summary.points = [
        "Pulled latest candles with the bulk downloader before running this scan.",
        ...(data.summary.points || []),
      ];
    }
    $("scanTitle").textContent = `${capitalize(type)} candidates`;
    $("scanMeta").textContent = `${data.results.length} shown / ${data.matched_symbols} matched / ${data.available_symbols} ready / ${data.timeframe_label}`;
    renderScanSummary(data.summary);
    renderScan(data.results);
    setScanProgress("completed", `${data.results.length} shown from ${data.matched_symbols} matched ${type} setup(s).`);
    setNotes(`${data.strategy}. Verify option chain, liquidity, event risk, and risk/reward before trade.`);
  } catch (error) {
    setScanProgress("failed", error.message);
    setNotes([error.message], true);
  }
}

async function scanOpportunity(type) {
  const label = OPPORTUNITY_LABELS[type] || type;
  setNotes(`Loading ${label} scan...`);
  setScanProgress("running", `Preparing ${label} scan...`);
  try {
    if ($("scanRefreshToggle").checked) {
      await refreshCandlesForScan();
    }
    const params = scanParams();
    params.set("type", type);
    setScanProgress("running", `Scanning ${label} setups from cached candle data.`);
    const data = await api(`/api/scan-opportunities?${params.toString()}`);
    if (data.summary && $("scanRefreshToggle").checked) {
      data.summary.latest_candles_pulled = true;
      data.summary.points = [
        "Pulled latest candles with the bulk downloader before running this scan.",
        ...(data.summary.points || []),
      ];
    }
    $("scanTitle").textContent = `${label} setups`;
    $("scanMeta").textContent = `${data.results.length} shown / ${data.matched_symbols} matched / ${data.analyzed_symbols} analyzed / ${data.timeframe_label}`;
    renderScanSummary(data.summary);
    renderScan(data.results);
    setScanProgress("completed", `${data.results.length} shown from ${data.matched_symbols} ${label} setup(s).`);
    setNotes("Setup scan is rule-based analysis from cached candles. Treat it as a shortlist, not trade advice.");
  } catch (error) {
    setScanProgress("failed", error.message);
    setNotes([error.message], true);
  }
}

async function refreshCandlesForScan() {
  const timeframe = $("scanTimeframeSelect").value;
  const days = Number($("scanDays").value || 90);
  const payload = {
    timeframes: scanRefreshTimeframes(timeframe),
    days,
    limit: null,
  };
  const job = await postApi("/api/bulk-candles", payload);
  renderScanRefreshJob(job);
  await waitForScanRefreshJob(job.job_id);
}

function scanRefreshTimeframes(timeframe) {
  const frames = new Set([timeframe, "day", "60minute", "15minute"]);
  return Array.from(frames);
}

async function waitForScanRefreshJob(jobId) {
  while (true) {
    const job = await api(`/api/job?job_id=${encodeURIComponent(jobId)}`);
    renderScanRefreshJob(job);
    if (!["queued", "running"].includes(job.status)) {
      if (job.status !== "completed") {
        throw new Error(`Candle refresh ${job.status}. ${job.errors?.[0] || ""}`.trim());
      }
      return job;
    }
    await delay(1500);
  }
}

function renderScanRefreshJob(job) {
  const total = job.total || 0;
  const completed = job.completed || 0;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  $("scanProgressMeta").textContent = `refresh ${job.status} / ${completed}/${total}`;
  $("scanProgressStatus").textContent = `Refreshing candles before scan: ${job.current || "starting"} | success ${job.successes || 0} | failures ${job.failures || 0}`;
  $("scanProgressBar").style.width = `${percent}%`;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function setScanProgress(status, detail) {
  const percent = status === "completed" ? "100%" : status === "failed" ? "100%" : "55%";
  $("scanProgressMeta").textContent = status;
  $("scanProgressStatus").textContent = detail;
  $("scanProgressBar").style.width = percent;
  $("scanProgressBar").classList.toggle("active", status === "running");
}

function renderScanSummary(summary) {
  if (!summary) {
    $("scanSummaryCards").innerHTML = "";
    $("scanSummaryPoints").innerHTML = "";
    return;
  }
  const cards = [
    ["Analyzed", summary.analyzed_symbols],
    ["Matched", summary.matched_symbols],
    ["Shown", summary.shown_symbols],
    ["Errors", summary.error_count],
    ["Latest candles", summary.latest_candles_pulled ? "Pulled" : "No"],
    ["Option chain", summary.option_chain_pulled ? "Pulled" : "No"],
    ["OC attempts", summary.option_chain_attempts || 0],
    ["OC success", summary.option_chain_successes || 0],
  ];
  $("scanSummaryCards").innerHTML = cards
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${fmtMetric(value)}</strong></div>`)
    .join("");
  $("scanSummaryPoints").innerHTML = (summary.points || []).map((point) => `<div>${point}</div>`).join("");
}

function fmtMetric(value) {
  return typeof value === "number" ? fmtInt(value) : String(value ?? "-");
}

async function runKrishnaScan() {
  setNotes("Running Krishna bullish setup scan...");
  setKrishnaProgress("running", "Preparing daily bullish setup filter...");
  try {
    if ($("krishnaRefreshToggle").checked) {
      await refreshDailyForKrishna();
    }
    const params = new URLSearchParams();
    const days = $("krishnaDays").value.trim();
    const limit = $("krishnaLimit").value.trim();
    if (days) params.set("days", days);
    params.set("limit", limit || "50");
    setKrishnaProgress("running", "Filtering cached daily candles for Krishna setup...");
    const data = await api(`/api/krishna-setup-scan?${params.toString()}`);
    if (data.summary && $("krishnaRefreshToggle").checked) {
      data.summary.latest_candles_pulled = true;
      data.summary.points = [
        "Pulled latest daily candles with the bulk downloader before running this setup filter.",
        ...(data.summary.points || []),
      ];
    }
    $("krishnaMeta").textContent = `${data.results.length} shown / ${data.matched_symbols} matched / ${data.analyzed_symbols} analyzed / Daily`;
    $("krishnaResultMeta").textContent = `${data.results.length} shown`;
    renderKrishnaSummary(data.summary);
    renderKrishnaResults(data.results);
    setKrishnaProgress("completed", `${data.results.length} shown from ${data.matched_symbols} matching stock(s).`);
    setNotes("Krishna setup shortlist is ready. Use it for manual chart review; entries are not automated.");
  } catch (error) {
    setKrishnaProgress("failed", error.message);
    setNotes([error.message], true);
  }
}

async function refreshDailyForKrishna() {
  const days = Number($("krishnaDays").value || 365);
  const job = await postApi("/api/bulk-candles", {
    timeframes: ["day"],
    days,
    limit: null,
  });
  state.krishnaRefreshJobId = job.job_id;
  renderKrishnaRefreshJob(job);
  await waitForKrishnaRefreshJob(job.job_id);
}

async function waitForKrishnaRefreshJob(jobId) {
  while (true) {
    const job = await api(`/api/job?job_id=${encodeURIComponent(jobId)}`);
    renderKrishnaRefreshJob(job);
    if (!["queued", "running"].includes(job.status)) {
      if (job.status !== "completed") {
        throw new Error(`Daily candle refresh ${job.status}. ${job.errors?.[0] || ""}`.trim());
      }
      return job;
    }
    await delay(1500);
  }
}

function renderKrishnaRefreshJob(job) {
  const total = job.total || 0;
  const completed = job.completed || 0;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  $("krishnaProgressMeta").textContent = `refresh ${job.status} / ${completed}/${total}`;
  $("krishnaProgressStatus").textContent = `Refreshing daily candles: ${job.current || "starting"} | success ${job.successes || 0} | failures ${job.failures || 0}`;
  $("krishnaProgressBar").style.width = `${percent}%`;
}

function setKrishnaProgress(status, detail) {
  const percent = status === "completed" ? "100%" : status === "failed" ? "100%" : "55%";
  $("krishnaProgressMeta").textContent = status;
  $("krishnaProgressStatus").textContent = detail;
  $("krishnaProgressBar").style.width = percent;
  $("krishnaProgressBar").classList.toggle("active", status === "running");
}

function renderKrishnaSummary(summary) {
  if (!summary) {
    $("krishnaSummaryCards").innerHTML = "";
    $("krishnaSummaryPoints").innerHTML = "";
    return;
  }
  const cards = [
    ["Analyzed", summary.analyzed_symbols],
    ["Matched", summary.matched_symbols],
    ["Shown", summary.shown_symbols],
    ["Errors", summary.error_count],
    ["Latest candles", summary.latest_candles_pulled ? "Pulled" : "No"],
  ];
  $("krishnaSummaryCards").innerHTML = cards
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${fmtMetric(value)}</strong></div>`)
    .join("");
  $("krishnaSummaryPoints").innerHTML = (summary.points || []).map((point) => `<div>${point}</div>`).join("");
}

function renderKrishnaResults(rows) {
  state.lastKrishnaRows = rows || [];
  $("krishnaBody").innerHTML = state.lastKrishnaRows
    .map((row) => {
      const reasons = row.reasons || (row.reasons_text ? row.reasons_text.split(";") : []);
      const reasonList = reasons.length
        ? `<ul class="reason-list">${reasons.slice(0, 4).map((reason) => `<li>${escapeHtml(reason.trim())}</li>`).join("")}</ul>`
        : "-";
      const warnings = (row.warnings || []).length
        ? `<div class="cell-note">${escapeHtml(row.warnings.join(" | "))}</div>`
        : "";
      const trigger = row.entry_trigger || {};
      const triggerStatus = row.entry_trigger_status || trigger.status || "missing";
      const triggerClass = triggerStatus === "entry_allowed" ? "points-positive" : triggerStatus === "wait" ? "points-warn" : "";
      return `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${fmtInt(row.score)}</td>
          <td>${escapeHtml(row.confidence || "-")}</td>
          <td>${fmt(row.close)}</td>
          <td>${fmt(row.yellow_line)}</td>
          <td>${fmt(row.yellow_gap_percent)}%<div class="cell-note">${row.yellow_gap_atr === null || row.yellow_gap_atr === undefined ? "-" : `${fmt(row.yellow_gap_atr)} ATR`}</div></td>
          <td>${fmt(row.ema9)} / ${fmt(row.ema26)}</td>
          <td>${fmt(row.vwma20)} / ${fmt(row.vwap)}</td>
          <td>${fmt(row.donchian_upper20)} / ${fmt(row.donchian_mid20)} / ${fmt(row.donchian_lower20)}</td>
          <td>${fmt(row.volume_ratio20)}</td>
          <td>${escapeHtml(row.structure_trend || "-")}</td>
          <td class="${triggerClass}">
            ${escapeHtml(triggerStatus)}
            <div class="cell-note">C ${fmt(row.entry_trigger_close || trigger.close)} / Y ${fmt(row.entry_trigger_yellow_line || trigger.yellow_line)} / VWMA ${fmt(row.entry_trigger_vwma20 || trigger.vwma20)}</div>
          </td>
          <td>${reasonList}${warnings}</td>
        </tr>
      `;
    })
    .join("");

  document.querySelectorAll("#krishnaBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function copyKrishnaSymbols() {
  const symbols = state.lastKrishnaRows.map((row) => row.symbol).filter(Boolean);
  copyText(symbols.join(", "), `Copied ${symbols.length} Krishna setup symbol(s).`);
}

function downloadKrishnaCsv() {
  downloadCsv("krishna_setup_filtered_stocks.csv", state.lastKrishnaRows, [
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Score", value: (row) => row.score },
    { label: "Confidence", value: (row) => row.confidence },
    { label: "Close", value: (row) => row.close },
    { label: "Yellow CK", value: (row) => row.yellow_line },
    { label: "Gap %", value: (row) => row.yellow_gap_percent },
    { label: "Gap ATR", value: (row) => row.yellow_gap_atr },
    { label: "EMA9", value: (row) => row.ema9 },
    { label: "EMA26", value: (row) => row.ema26 },
    { label: "VWMA20", value: (row) => row.vwma20 },
    { label: "VWAP", value: (row) => row.vwap },
    { label: "Donchian Upper20", value: (row) => row.donchian_upper20 },
    { label: "Donchian Mid20", value: (row) => row.donchian_mid20 },
    { label: "Donchian Lower20", value: (row) => row.donchian_lower20 },
    { label: "Vol x20", value: (row) => row.volume_ratio20 },
    { label: "Structure", value: (row) => row.structure_trend },
    { label: "2H Entry Status", value: (row) => row.entry_trigger_status },
    { label: "2H Close", value: (row) => row.entry_trigger_close },
    { label: "2H Yellow CK", value: (row) => row.entry_trigger_yellow_line },
    { label: "2H VWMA20", value: (row) => row.entry_trigger_vwma20 },
    { label: "Reasons", value: (row) => row.reasons || row.reasons_text },
    { label: "Warnings", value: (row) => row.warnings },
  ]);
}

async function loadStrategies() {
  const data = await api("/api/strategies");
  state.strategies = data.strategies || [];
  const select = $("genericStrategySelect");
  select.innerHTML = state.strategies
    .map((strategy) => `<option value="${escapeHtml(strategy.strategy_id)}">${escapeHtml(strategy.label)}</option>`)
    .join("");
  if (state.strategies.length) {
    select.value = state.strategies[0].strategy_id;
    populateStrategyParams();
  }
}

function populateStrategyParams() {
  const selected = state.strategies.find((strategy) => strategy.strategy_id === $("genericStrategySelect").value);
  if (!selected) return;
  $("genericStrategyParams").value = JSON.stringify(selected.default_params || {}, null, 2);
  $("genericBacktestTimeframe").value = selected.default_timeframe || "day";
}

function parseJsonTextarea(id, label) {
  const text = $(id).value.trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(`${label} must be a JSON object.`);
    }
    return parsed;
  } catch (error) {
    throw new Error(`${label}: ${error.message}`);
  }
}

async function runGenericBacktest() {
  $("genericBacktestStatus").textContent = "Running";
  setNotes("Running generic strategy backtest on cached candles...");
  try {
    const symbolsText = $("genericBacktestSymbol").value.trim();
    const payload = {
      strategy_id: $("genericStrategySelect").value,
      symbols: symbolsText ? symbolsText.split(",").map((item) => item.trim()).filter(Boolean) : null,
      timeframe: $("genericBacktestTimeframe").value,
      days: $("genericBacktestDays").value ? Number($("genericBacktestDays").value) : null,
      from_date: $("genericBacktestFromDate").value || null,
      to_date: $("genericBacktestToDate").value || null,
      strategy_params: parseJsonTextarea("genericStrategyParams", "Strategy params"),
      backtest_params: parseJsonTextarea("genericBacktestParams", "Backtest params"),
      limit_symbols: $("genericLimitSymbols").value.trim() || "50",
    };
    const data = await postApi("/api/backtest-strategy", payload);
    state.lastGenericBacktest = data;
    renderGenericBacktest(data);
    setNotes("Generic backtest complete. Review score buckets, symbol performance, and trade log before trusting any setup.");
  } catch (error) {
    $("genericBacktestStatus").textContent = "Failed";
    setNotes([error.message], true);
  }
}

function renderGenericBacktest(data) {
  $("genericBacktestStatus").textContent = "Completed";
  const strategy = data.strategy || {};
  $("genericBacktestMeta").textContent = `${strategy.label || data.strategy_id || "Strategy"} / ${data.analyzed_symbols || 0} analyzed / ${data.signal_count || 0} signals / ${data.trade_count || 0} trades`;
  const metrics = data.metrics || {};
  const cards = [
    ["Trades", metrics.trades],
    ["Win rate", fmtPct(metrics.win_rate)],
    ["Avg return", fmtPct(metrics.avg_return)],
    ["Expectancy", fmtPct(metrics.expectancy)],
    ["Profit factor", fmt(metrics.profit_factor)],
    ["Max DD", fmtPct(metrics.max_drawdown)],
    ["Ending return", fmtPct(metrics.ending_return)],
    ["Avg R", fmt(metrics.avg_r_multiple)],
  ];
  $("genericBacktestSummaryCards").innerHTML = cards
    .map(([label, value]) => `<div class="compact-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
  $("genericBacktestSummaryPoints").innerHTML = ((data.summary && data.summary.points) || [])
    .map((point) => `<div>${escapeHtml(point)}</div>`)
    .join("");
  renderGenericBacktestBuckets(data.score_buckets || []);
  renderGenericBacktestSymbols(data.symbol_performance || []);
  renderGenericBacktestMonthly(data.monthly_performance || []);
  renderGenericBacktestTrades(data.trades || []);
}

function renderGenericBacktestBuckets(rows) {
  $("genericBacktestBucketBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.score_bucket)}</td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.avg_return)}</td>
          <td>${fmtPct(row.expectancy)}</td>
        </tr>
      `
    )
    .join("");
}

function renderGenericBacktestSymbols(rows) {
  $("genericBacktestSymbolMeta").textContent = `${rows.length} symbol row(s)`;
  $("genericBacktestSymbolBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.avg_return)}</td>
          <td>${fmt(row.profit_factor)}</td>
          <td>${fmtPct(row.ending_return)}</td>
        </tr>
      `
    )
    .join("");
  document.querySelectorAll("#genericBacktestSymbolBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function renderGenericBacktestMonthly(rows) {
  $("genericBacktestMonthlyBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.month)}</td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.return_sum)}</td>
          <td>${fmtPct(row.avg_return)}</td>
        </tr>
      `
    )
    .join("");
}

function renderGenericBacktestTrades(rows) {
  const shown = rows.slice(0, 200);
  $("genericBacktestTradeMeta").textContent = `${shown.length} shown / ${rows.length} trade(s)`;
  $("genericBacktestTradeBody").innerHTML = shown
    .map((row) => {
      const reasons = (row.reasons || []).slice(0, 2);
      const reasonList = reasons.length
        ? `<ul class="reason-list">${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
        : "-";
      return `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${escapeHtml(row.side || "-")}</td>
          <td>${escapeHtml(row.signal_date || "-")}</td>
          <td>${escapeHtml(row.entry_date || "-")}</td>
          <td>${escapeHtml(row.exit_date || "-")}</td>
          <td>${fmtInt(row.score)}</td>
          <td>${fmt(row.entry_price)}</td>
          <td>${fmt(row.exit_price)}</td>
          <td class="${Number(row.return_percent || 0) >= 0 ? "points-positive" : "points-negative"}">${fmtPct(row.return_percent)}</td>
          <td>${reasonList}</td>
        </tr>
      `;
    })
    .join("");
  document.querySelectorAll("#genericBacktestTradeBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

async function runKrishnaBacktest() {
  setNotes("Running Krishna setup backtest on cached daily candles...");
  $("backtestStatus").textContent = "Running";
  try {
    const params = new URLSearchParams();
    const symbol = $("backtestSymbol").value.trim();
    const days = $("backtestDays").value.trim();
    const holdingDays = $("backtestHoldingDays").value.trim();
    const entryTrigger = $("backtestEntryTrigger").checked;
    const triggerHoldingBars = $("backtestTriggerHoldingBars").value.trim();
    const targetR = $("backtestTargetR").value.trim();
    const fromDate = $("backtestFromDate").value.trim();
    const toDate = $("backtestToDate").value.trim();
    const limitSymbols = $("backtestLimitSymbols").value.trim();
    if (symbol) params.set("symbol", symbol);
    if (days) params.set("days", days);
    if (holdingDays) params.set("holding_days", holdingDays);
    params.set("entry_trigger", entryTrigger ? "true" : "false");
    if (triggerHoldingBars) params.set("trigger_holding_bars", triggerHoldingBars);
    if (targetR) params.set("target_r_multiple", targetR);
    if (fromDate) params.set("from_date", fromDate);
    if (toDate) params.set("to_date", toDate);
    params.set("limit_symbols", limitSymbols || "50");

    const data = await api(`/api/krishna-setup-backtest?${params.toString()}`);
    state.lastBacktest = data;
    renderBacktest(data);
    setNotes("Backtest complete. Use the score buckets and forward accuracy to judge whether the setup has useful directional edge.");
  } catch (error) {
    $("backtestStatus").textContent = "Failed";
    setNotes([error.message], true);
  }
}

function renderBacktest(data) {
  $("backtestStatus").textContent = "Completed";
  const entryMode = data.use_entry_trigger ? "Daily + 2H trigger" : "Daily";
  $("backtestMeta").textContent = `${data.analyzed_symbols} analyzed / ${data.signal_count} signals / ${data.trade_count} trades / ${entryMode}`;
  const metrics = data.metrics || {};
  const baseline = data.baselines || {};
  const buyHold = baseline.buy_and_hold || {};
  const emaBaseline = baseline.ema20_gt_ema50 || {};
  const cards = [
    ["Trades", metrics.trades],
    ["Win rate", fmtPct(metrics.win_rate)],
    ["Avg return", fmtPct(metrics.avg_return)],
    ["Expectancy", fmtPct(metrics.expectancy)],
    ["Profit factor", fmt(metrics.profit_factor)],
    ["Max DD", fmtPct(metrics.max_drawdown)],
    ["Ending return", fmtPct(metrics.ending_return)],
    ["Buy/Hold avg", fmtPct(buyHold.avg_return)],
    ["EMA20>50 trades", emaBaseline.trades],
    ["EMA20>50 win", fmtPct(emaBaseline.win_rate)],
  ];
  $("backtestSummaryCards").innerHTML = cards
    .map(([label, value]) => `<div class="compact-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
  $("backtestSummaryPoints").innerHTML = ((data.summary && data.summary.points) || [])
    .map((point) => `<div>${escapeHtml(point)}</div>`)
    .join("");
  renderBacktestForward(data.forward_accuracy || []);
  renderBacktestBuckets(data.confidence_buckets || []);
  renderBacktestSymbols(data.symbol_results || []);
  renderBacktestMonthly(data.monthly_performance || []);
  renderBacktestTrades(data.trades || []);
}

function renderBacktestForward(rows) {
  $("backtestForwardBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${fmtInt(row.horizon_days)} days</td>
          <td>${fmtInt(row.signals)}</td>
          <td>${fmtInt(row.successes)}</td>
          <td>${fmtPct(row.accuracy)}</td>
          <td>${fmtPct(row.avg_forward_return)}</td>
        </tr>
      `
    )
    .join("");
}

function renderBacktestBuckets(rows) {
  $("backtestBucketBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.score_bucket)}</td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.avg_return)}</td>
          <td>${fmtPct(row.expectancy)}</td>
        </tr>
      `
    )
    .join("");
}

function renderBacktestSymbols(rows) {
  $("backtestSymbolMeta").textContent = `${rows.length} symbol row(s)`;
  $("backtestSymbolBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${escapeHtml(row.status)}</td>
          <td>${fmtInt(row.signals)}</td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.avg_return)}</td>
          <td>${fmt(row.profit_factor)}</td>
          <td>${fmtPct(row.max_drawdown)}</td>
          <td>${fmtPct(row.buy_hold_return)}</td>
        </tr>
      `
    )
    .join("");
  document.querySelectorAll("#backtestSymbolBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function renderBacktestMonthly(rows) {
  $("backtestMonthlyBody").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.month)}</td>
          <td>${fmtInt(row.trades)}</td>
          <td>${fmtPct(row.win_rate)}</td>
          <td>${fmtPct(row.return_sum)}</td>
          <td>${fmtPct(row.avg_return)}</td>
        </tr>
      `
    )
    .join("");
}

function renderBacktestTrades(rows) {
  const shown = rows.slice(0, 200);
  $("backtestTradeMeta").textContent = `${shown.length} shown / ${rows.length} trade(s)`;
  $("backtestTradeBody").innerHTML = shown
    .map((row) => {
      const reasons = (row.reasons || []).slice(0, 3);
      const reasonList = reasons.length
        ? `<ul class="reason-list">${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
        : escapeHtml(row.reason_text || "-");
      return `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${escapeHtml(row.signal_date)}</td>
          <td>${escapeHtml(row.entry_date)}</td>
          <td>${escapeHtml(row.exit_date)}</td>
          <td>${fmtInt(row.score)}</td>
          <td>${escapeHtml(row.confidence || "-")}</td>
          <td>${fmt(row.entry_price)}</td>
          <td>${fmt(row.exit_price)}</td>
          <td class="${Number(row.return_percent || 0) >= 0 ? "points-positive" : "points-negative"}">${fmtPct(row.return_percent)}</td>
          <td>${reasonList}</td>
        </tr>
      `;
    })
    .join("");
  document.querySelectorAll("#backtestTradeBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function downloadBacktestTrades() {
  const rows = (state.lastBacktest && state.lastBacktest.trades) || [];
  downloadCsv("krishna_backtest_trades.csv", rows, [
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Signal Date", value: (row) => row.signal_date },
    { label: "Entry Date", value: (row) => row.entry_date },
    { label: "Exit Date", value: (row) => row.exit_date },
    { label: "Score", value: (row) => row.score },
    { label: "Confidence", value: (row) => row.confidence },
    { label: "Entry Price", value: (row) => row.entry_price },
    { label: "Exit Price", value: (row) => row.exit_price },
    { label: "Return %", value: (row) => row.return_percent },
    { label: "Win", value: (row) => row.win },
    { label: "Structure", value: (row) => row.structure_trend },
    { label: "Support", value: (row) => row.support },
    { label: "Resistance", value: (row) => row.resistance },
    { label: "Invalidation", value: (row) => row.invalidation },
    { label: "Reasons", value: (row) => row.reasons },
  ]);
}

function downloadBacktestSignals() {
  const rows = (state.lastBacktest && state.lastBacktest.signals) || [];
  downloadCsv("krishna_backtest_signal_features.csv", rows, [
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Signal Date", value: (row) => row.signal_date },
    { label: "Signal Close", value: (row) => row.signal_close },
    { label: "Score", value: (row) => row.score },
    { label: "Confidence", value: (row) => row.confidence },
    { label: "Trade Status", value: (row) => row.trade_status },
    { label: "Structure", value: (row) => row.structure_trend },
    { label: "Support", value: (row) => row.support },
    { label: "Resistance", value: (row) => row.resistance },
    { label: "Invalidation", value: (row) => row.invalidation },
    { label: "Forward 5 %", value: (row) => row.forward_returns && row.forward_returns["5"] && row.forward_returns["5"].return_percent },
    { label: "Forward 5 Success", value: (row) => row.forward_success && row.forward_success["5"] },
    { label: "Forward 10 %", value: (row) => row.forward_returns && row.forward_returns["10"] && row.forward_returns["10"].return_percent },
    { label: "Forward 10 Success", value: (row) => row.forward_success && row.forward_success["10"] },
    { label: "Forward 15 %", value: (row) => row.forward_returns && row.forward_returns["15"] && row.forward_returns["15"].return_percent },
    { label: "Forward 15 Success", value: (row) => row.forward_success && row.forward_success["15"] },
    { label: "Yellow CK", value: (row) => row.features && row.features.yellow_line },
    { label: "Gap %", value: (row) => row.features && row.features.yellow_gap_percent },
    { label: "Gap ATR", value: (row) => row.features && row.features.yellow_gap_atr },
    { label: "EMA9", value: (row) => row.features && row.features.ema9 },
    { label: "EMA26", value: (row) => row.features && row.features.ema26 },
    { label: "VWMA20", value: (row) => row.features && row.features.vwma20 },
    { label: "VWAP", value: (row) => row.features && row.features.vwap },
    { label: "Vol x20", value: (row) => row.features && row.features.volume_ratio20 },
    { label: "Reasons", value: (row) => row.reasons },
  ]);
}

function renderAnalysis(data) {
  renderAnalysisHeader(data.analysis_header, data);
  $("biasValue").textContent = data.decision.bias;
  $("biasValue").className = data.decision.bias;
  $("scoreValue").textContent = data.decision.score;
  $("strategyValue").textContent = data.setup.strategy;
  $("decisionValue").textContent = data.decision.decision;
  $("structureMeta").textContent = `${(data.structure_timeframes || []).length} timeframe(s)`;

  renderScoreBreakdown(data.decision.score_breakdown);
  renderIndicatorSuite(data.indicator_suite);
  renderMultiTimeframe(data.multi_timeframe);
  renderEntryTrigger(data.entry_trigger);
  renderEntryContext(data.entry_context);
  renderOptionGuide(data.option_trade_guide);
  renderCoverage(data.analysis_summary);
  renderStructureTimeframes(data.structure_timeframes);

  renderRelativeStrength(data.relative_strength);
  renderOptionChain(data.option_chain);
  renderSnapshotStatus(data.option_snapshot);
}

function renderAnalysisHeader(header, data) {
  const source = header || {};
  const symbol = source.symbol || data.symbol || "-";
  const type = source.instrument_type ? ` (${source.instrument_type})` : "";
  const timeframe = source.timeframe_label ? ` / ${source.timeframe_label}` : "";
  $("analysisInstrument").textContent = `${symbol}${type}${timeframe}`;
  $("analysisPrice").textContent = fmt(source.latest_price ?? data.chart?.technical?.close);
  $("analysisPriceTime").textContent = fmtDateTime(source.latest_price_time || data.chart?.to);
  $("analysisRunTime").textContent = fmtDateTime(source.analyzed_at);
  $("analysisPriceSource").textContent = source.latest_price_source || "latest analyzed candle close";
}

function renderScoreBreakdown(breakdown) {
  if (!breakdown) {
    $("scoreBreakdownMeta").textContent = "-";
    $("scoreBase").textContent = "-";
    $("scoreComponentTotal").textContent = "-";
    $("scoreRaw").textContent = "-";
    $("scoreFinal").textContent = "-";
    $("scoreBreakdownBody").innerHTML = "";
    return;
  }
  const components = breakdown.components || [];
  const componentTotal = components.reduce((total, component) => total + Number(component.points || 0), 0);
  $("scoreBreakdownMeta").textContent = `Base ${breakdown.base_score} + components ${fmtSigned(componentTotal)} = ${breakdown.raw_score}, capped to ${breakdown.final_score}`;
  $("scoreBase").textContent = fmtInt(breakdown.base_score);
  $("scoreComponentTotal").textContent = fmtSigned(componentTotal);
  $("scoreComponentTotal").className = pointsClass(componentTotal);
  $("scoreRaw").textContent = fmtInt(breakdown.raw_score);
  $("scoreFinal").textContent = fmtInt(breakdown.final_score);
  $("scoreBreakdownBody").innerHTML = components
    .map(
      (component) => `
        <tr>
          <td>${component.name}</td>
          <td class="${pointsClass(component.points)}">${fmtSigned(component.points)}</td>
          <td>${component.detail || "-"}</td>
        </tr>
      `
    )
    .join("");
}

function renderIndicatorSuite(suite) {
  if (!suite) {
    $("indicatorMeta").textContent = "-";
    $("indicatorBody").innerHTML = "";
    return;
  }
  $("indicatorMeta").textContent = `${suite.bias || "-"} / score ${fmtInt(suite.score)} / ${suite.summary || ""}`;
  $("indicatorBody").innerHTML = (suite.rows || [])
    .map(
      (row) => `
        <tr>
          <td>${row.name}</td>
          <td><span class="status-badge status-${statusKey(row.signal)}">${statusLabel(row.signal || "-")}</span></td>
          <td>${row.value || "-"}</td>
          <td>${row.reference || "-"}</td>
          <td>${row.detail || "-"}</td>
        </tr>
      `
    )
    .join("");
}

function renderEntryTrigger(trigger) {
  if (!trigger) {
    $("entryTriggerMeta").textContent = "-";
    $("entryTriggerStatus").textContent = "Wait";
    $("entryTriggerStatus").className = "status-badge status-wait";
    $("entryTriggerSummary").textContent = "Run analysis to load entry triggers.";
    $("entryTriggerBody").innerHTML = "";
    $("entryCandidateBody").innerHTML = "";
    return;
  }
  $("entryTriggerMeta").textContent = `${trigger.candidates.length} candidate(s)`;
  $("entryTriggerStatus").textContent = trigger.status;
  $("entryTriggerStatus").className = `status-badge status-${trigger.status_key || statusKey(trigger.status)}`;
  $("entryTriggerSummary").textContent = trigger.summary || "-";
  $("entryTriggerBody").innerHTML = (trigger.rows || [])
    .map(
      (row) => `
        <tr>
          <td>${row.factor}</td>
          <td><span class="status-badge status-${row.status_key || statusKey(row.status)}">${row.status}</span></td>
          <td>${row.detail || "-"}</td>
        </tr>
      `
    )
    .join("");
  $("entryCandidateBody").innerHTML = (trigger.candidates || [])
    .map((candidate) => {
      const strike = candidate.strike === null || candidate.strike === undefined
        ? "-"
        : `${fmt(candidate.strike)} ${candidate.option_type}`;
      const blockers = (candidate.blockers || []).length
        ? `<div class="cell-note">${candidate.blockers.join(" | ")}</div>`
        : "";
      return `
        <tr>
          <td>${candidate.action}</td>
          <td>${strike}</td>
          <td><span class="status-badge status-${candidate.status_key || statusKey(candidate.status)}">${candidate.status}</span></td>
          <td>${candidate.entry_trigger || "-"}${blockers}</td>
          <td>${candidate.risk_trigger || "-"}</td>
          <td>${fmtInt(candidate.score)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderEntryContext(context) {
  if (!context || !context.rows) {
    $("entryContextMeta").textContent = "-";
    $("entryContextBody").innerHTML = "";
    return;
  }
  $("entryContextMeta").textContent = context.summary || "-";
  $("entryContextBody").innerHTML = context.rows
    .map(
      (row) => `
        <tr>
          <td>${row.zone}</td>
          <td><span class="status-badge status-${row.status}">${statusLabel(row.status)}</span></td>
          <td>${row.signal}</td>
          <td>${row.level}</td>
          <td>${row.detail}</td>
        </tr>
      `
    )
    .join("");
}

function renderMultiTimeframe(mtf) {
  if (!mtf || !mtf.rows) {
    $("mtfMeta").textContent = "-";
    $("mtfBody").innerHTML = "";
    return;
  }
  $("mtfMeta").textContent = mtf.summary;
  $("mtfBody").innerHTML = mtf.rows
    .map((row) => {
      if (row.status !== "analyzed") {
        return `
          <tr>
            <td>${row.label}</td>
            <td><span class="status-badge status-${row.status}">${statusLabel(row.status)}</span></td>
            <td>${fmtInt(row.candle_count)}</td>
            <td colspan="10">${row.message || "Not available"} ${row.path || ""} ${mtfWindowLabel(row)}</td>
          </tr>
        `;
      }
      return `
        <tr>
          <td>${row.label}</td>
          <td><span class="status-badge status-analyzed">${row.volume_signal}</span></td>
          <td>${fmtInt(row.candle_count)}<div class="cell-note">${mtfWindowLabel(row)}</div></td>
          <td>${fmt(row.close)}</td>
          <td class="${row.technical_trend}">${row.technical_trend}</td>
          <td>${row.structure_trend}</td>
          <td>${row.score}</td>
          <td>${fmt(row.rsi14)}</td>
          <td>${fmtInt(row.volume)}</td>
          <td>${fmt(row.volume_ratio20)}</td>
          <td>${fmt(row.support)}</td>
          <td>${fmt(row.resistance)}</td>
          <td>${fmt(row.invalidation)}</td>
        </tr>
      `;
    })
    .join("");
}

function mtfWindowLabel(row) {
  const days = row.lookback_days ? `${row.lookback_days}d` : "";
  const range = row.from && row.to ? `${String(row.from).slice(0, 10)} to ${String(row.to).slice(0, 10)}` : "";
  if (days && range) return `${days} / ${range}`;
  return days || range || "";
}

function renderOptionGuide(guide) {
  if (!guide || !guide.rows) {
    $("optionGuideMeta").textContent = "-";
    $("optionGuideBody").innerHTML = "";
    return;
  }
  $("optionGuideMeta").textContent = guide.summary || "-";
  $("optionGuideBody").innerHTML = guide.rows
    .map(
      (row) => `
        <tr>
          <td>${row.action}</td>
          <td>${row.strike_zone}</td>
          <td>${row.why}</td>
          <td>${row.risk_check}</td>
        </tr>
      `
    )
    .join("");
}

function renderCoverage(summary) {
  if (!summary || !summary.rows) {
    $("coverageMeta").textContent = "-";
    $("coverageBody").innerHTML = "";
    return;
  }
  const analyzed = summary.rows.filter((row) => row.status === "analyzed").length;
  const pulled = summary.rows.filter((row) => row.status === "pulled").length;
  const missing = summary.rows.filter((row) => ["missing", "failed", "not_analyzed", "not_requested", "not_applicable"].includes(row.status)).length;
  const instrument = summary.instrument
    ? `${summary.instrument.symbol} ${summary.instrument.type || ""}`.trim()
    : summary.symbol || "-";
  $("coverageMeta").textContent = `${instrument} / ${summary.timeframe_label} / ${analyzed} analyzed / ${pulled} pulled / ${missing} skipped, NA, or missing`;
  $("coverageBody").innerHTML = summary.rows
    .map(
      (row) => `
        <tr>
          <td>${row.name}</td>
          <td><span class="status-badge status-${row.status}">${statusLabel(row.status)}</span></td>
          <td>${row.detail || "-"}</td>
          <td>${row.source || "-"}</td>
        </tr>
      `
    )
    .join("");
}

function renderStructureTimeframes(rows) {
  $("structureBody").innerHTML = (rows || [])
    .map((row) => {
      if (row.status !== "analyzed") {
        return `
          <tr>
            <td>${row.label || row.timeframe}</td>
            <td colspan="6">${row.message || "Not available"}<div class="cell-note">${row.path || ""}</div></td>
            <td><span class="status-badge status-${row.status || "missing"}">${statusLabel(row.status || "missing")}</span></td>
          </tr>
        `;
      }
      return `
        <tr>
          <td>${row.label}</td>
          <td>${fmt(row.close)}</td>
          <td>${row.technical_trend}</td>
          <td>${row.structure_trend}</td>
          <td>${fmt(row.support)}</td>
          <td>${fmt(row.resistance)}</td>
          <td>${fmt(row.invalidation)}</td>
          <td><span class="status-badge status-analyzed">${fmtInt(row.candle_count)} candles</span></td>
        </tr>
      `;
    })
    .join("");
}

function renderRelativeStrength(rs) {
  const rows = [
    ["Stock vs Nifty", rs.stock_vs_nifty],
    ["Stock vs Sector", rs.stock_vs_sector],
    ["Sector vs Nifty", rs.sector_vs_nifty],
  ];
  $("rsBody").innerHTML = rows
    .map(([label, signal]) => {
      if (!signal) return `<tr><td>${label}</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>`;
      return `
        <tr>
          <td>${label}</td>
          <td>${fmt(signal.subject_return_percent)}%</td>
          <td>${fmt(signal.benchmark_return_percent)}%</td>
          <td>${fmt(signal.relative_return_percent)}%</td>
          <td>${signal.label}</td>
        </tr>
      `;
    })
    .join("");
}

function renderOptionChain(chain) {
  if (!chain) {
    $("optionMeta").textContent = "not loaded";
    $("optionBody").innerHTML = "";
    return;
  }
  $("optionMeta").textContent = `${chain.expiry} PCR ${fmt(chain.pcr_oi)} Max pain ${fmt(chain.max_pain)} ATM IV ${fmt(chain.atm_iv)} Vol ${chain.total_volume}`;
  $("optionBody").innerHTML = chain.rows
    .map(
      (row) => `
        <tr>
          <td>${fmt(row.strike)}</td>
          <td>${row.option_type}</td>
          <td>${fmt(row.last_price)}</td>
          <td>${fmt(row.implied_volatility)}</td>
          <td>${fmt(row.iv_change)}</td>
          <td>${row.oi}</td>
          <td>${fmt(row.oi_change)}</td>
          <td>${fmt(row.oi_change_percent)}</td>
          <td>${row.buildup}</td>
        </tr>
      `
    )
    .join("");
}

function renderSnapshotStatus(snapshot) {
  if (!snapshot) return;
  const comparison = snapshot.previous_snapshot_found
    ? `Compared with ${snapshot.previous_snapshot}`
    : `No previous snapshot found at ${snapshot.previous_snapshot}`;
  $("snapshotStatus").textContent = `${comparison}. Saved history: ${snapshot.history_snapshot}`;
}

function renderScan(rows) {
  $("scanBody").innerHTML = rows
    .map(
      (row) => {
        const setup = row.setup || row.strategy || row.stance || row.setup_type || "-";
        const direction = row.direction || row.bias || "-";
        const zone = row.trigger_zone || row.target_zone || row.range_zone || row.option_zone || "-";
        const reasons = row.reasons_text || (row.reasons || []).join("; ") || row.reason || row.stock_vs_nifty || "-";
        const warnings = (row.warnings || []).join("; ") || "-";
        const optionNote = row.option_chain_context ? `<div class="cell-note">${scanOptionChainCell(row.option_chain_context)}</div>` : "";
        return `
          <tr>
            <td><button class="linkBtn" data-symbol="${row.symbol}">${row.symbol}</button></td>
            <td>${setup}</td>
            <td class="${direction}">${direction}</td>
            <td>${row.score}</td>
            <td>${row.confidence || "-"}</td>
            <td>${fmt(row.close)}</td>
            <td>${fmt(row.support)}</td>
            <td>${fmt(row.resistance)}</td>
            <td>${fmt(row.invalidation)}</td>
            <td>${zone}${optionNote}</td>
            <td>${reasons}</td>
            <td>${warnings}</td>
          </tr>
        `;
      }
    )
    .join("");

  document.querySelectorAll(".linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function scanOptionChainCell(context) {
  if (!context) return "-";
  if (context.status === "failed") return `<span class="error">${context.summary || "failed"}</span>`;
  return `
    <div>${context.expiry || "-"} PCR ${fmt(context.pcr_oi)} / MP ${fmt(context.max_pain)}</div>
    <div class="cell-note">ATM IV ${fmt(context.atm_iv)} | OI% ${fmt(context.total_oi_change_percent)} | ${context.previous_snapshot_found ? "compared" : "new snapshot"}</div>
  `;
}

async function loadNiftyExpiries() {
  const weekly = $("niftyWeeklyExpiry");
  const monthly = $("niftyMonthlyExpiry");
  if (!weekly || !monthly) return;
  weekly.innerHTML = `<option value="">Auto weekly</option>`;
  monthly.innerHTML = `<option value="">Auto monthly</option>`;
  try {
    const data = await api("/api/option-expiries?symbol=NIFTY");
    (data.expiries || []).forEach((expiry, index) => {
      const weeklyOption = document.createElement("option");
      weeklyOption.value = expiry;
      weeklyOption.textContent = expiry === data.nearest ? `${expiry} (nearest)` : expiry;
      weekly.appendChild(weeklyOption);

      const monthlyOption = document.createElement("option");
      monthlyOption.value = expiry;
      monthlyOption.textContent = index === (data.expiries || []).length - 1 ? `${expiry} (furthest loaded)` : expiry;
      monthly.appendChild(monthlyOption);
    });
  } catch (error) {
    $("niftyMeta").textContent = `Expiry load failed: ${error.message}`;
  }
}

function niftyContextParams() {
  const params = new URLSearchParams({
    mode: $("niftyMode").value,
    weekly_expiry: $("niftyWeeklyExpiry").value,
    monthly_expiry: $("niftyMonthlyExpiry").value,
    include_option_chain: $("niftyIncludeOptionChain").checked ? "true" : "false",
    include_iv: $("niftyIncludeIv").checked ? "true" : "false",
    refresh: $("niftyRefresh").checked ? "true" : "false",
    timeframe: $("niftyTimeframe").value,
    days: $("niftyDays").value || "45",
  });
  if ($("niftyToDate").value) params.set("to_date", $("niftyToDate").value);
  return params;
}

async function runNiftyContext() {
  setNotes("Loading NIFTY desk context...");
  $("niftyMeta").textContent = "Running";
  try {
    const data = await api(`/api/nifty/context?${niftyContextParams().toString()}`);
    state.nifty.context = data;
    renderNiftyContext(data);
    setNotes((data.summary && data.summary.points) || data.warnings || []);
  } catch (error) {
    $("niftyMeta").textContent = "Failed";
    setNotes([error.message], true);
  }
}

async function runNiftySuggestions() {
  setNotes("Building NIFTY strategy suitability candidates...");
  $("niftyStrategyMeta").textContent = "Running";
  try {
    const data = await postApi("/api/nifty/strategy-suggestions", {
      mode: $("niftyMode").value,
      weekly_expiry: $("niftyWeeklyExpiry").value || null,
      monthly_expiry: $("niftyMonthlyExpiry").value || null,
      risk_profile: $("niftyRiskProfile").value,
      refresh: $("niftyRefresh").checked,
      to_date: $("niftyToDate").value || null,
    });
    state.nifty.context = data;
    state.nifty.candidates = data.candidates || [];
    renderNiftyContext(data);
    renderNiftyStrategies(data.candidates || []);
    setNotes(`${(data.candidates || []).length} NIFTY strategy candidate(s) loaded.`);
  } catch (error) {
    $("niftyStrategyMeta").textContent = "Failed";
    setNotes([error.message], true);
  }
}

function renderNiftyContext(data) {
  const technical = data.technical || {};
  const options = data.options || {};
  const iv = data.iv || {};
  const source = (data.summary && data.summary.candle_sources && data.summary.candle_sources.daily) || {};
  $("niftyMeta").textContent = `${data.mode || "auto"} / latest daily ${fmtDateTime(source.to || data.as_of)}`;
  $("niftyContextCards").innerHTML = [
    ["Spot", fmt(technical.spot)],
    ["Intraday Bias", technical.bias_intraday || "-"],
    ["Swing Bias", technical.bias_swing || "-"],
    ["Positional Bias", technical.bias_positional || "-"],
    ["ATR", fmt(technical.atr14)],
    ["RSI", fmt(technical.rsi14)],
    ["VWAP", fmt(technical.vwap)],
    ["ATM IV", fmt(iv.atm_iv)],
    ["Latest Candle", fmtDateTime(source.to)],
  ]
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("niftyContextMeta").textContent = `${technical.timeframe || "-"} / structure ${technical.market_structure || "-"}`;
  $("niftyContextBody").innerHTML = [
    ["Support", (technical.support_levels || []).join(", ") || "-", "Nearby levels where buyers may defend."],
    ["Resistance", (technical.resistance_levels || []).join(", ") || "-", "Nearby levels where sellers may defend."],
    ["Previous Day", `${fmt(technical.previous_day_high)} / ${fmt(technical.previous_day_low)} / ${fmt(technical.previous_day_close)}`, "High / low / close reference for intraday range."],
    ["Day Open", fmt(technical.day_open), "Used for gap and opening context."],
    ["Candle Signal", technical.candle_signal || "-", "Latest candle location and momentum context."],
    ["Data Used", candleSourceText(data.summary && data.summary.candle_sources), "Shows candle count and latest timestamp actually analyzed."],
    ["Refresh", refreshResultText(data.summary && data.summary.refresh_results), "Shows whether latest candles were pulled in this run."],
    ["Notes", (technical.notes || []).join(" | ") || "-", "Derived from cached candle data."],
  ]
    .map(([area, value, read]) => `<tr><td>${area}</td><td>${value}</td><td>${read}</td></tr>`)
    .join("");
  $("niftyFactorMeta").textContent = `${(technical.factors || []).length} factor(s) used for bias`;
  $("niftyFactorBody").innerHTML = (technical.factors || [])
    .map((factor) => `
      <tr>
        <td>${factor.factor}</td>
        <td><span class="status-badge status-${statusKey(factor.signal)}">${statusLabel(factor.signal)}</span></td>
        <td>${factor.value || "-"}</td>
        <td>${factor.purpose || "-"}</td>
      </tr>
    `)
    .join("");
  $("niftyOptionMeta").textContent = `${options.option_bias || "-"} / IV ${iv.iv_regime || "-"}`;
  $("niftyOptionBody").innerHTML = [
    ["Weekly PCR", fmt(options.pcr_oi), "Put/call OI ratio from selected cached snapshot."],
    ["PCR Volume", fmt(options.pcr_volume), "Put/call volume ratio from selected cached snapshot."],
    ["Max Pain", fmt(options.max_pain), "Strike with lowest aggregate option pain from current OI."],
    ["ATM Strike", fmt(options.atm_strike), "Nearest available strike to inferred spot."],
    ["ATM IV", fmt(options.atm_iv), "Approximate ATM implied volatility from cached chain."],
    ["IV Rank", fmt(iv.iv_rank), "Current IV location versus saved IV history."],
    ["IV Percentile", fmt(iv.iv_percentile), "Percent of saved IV observations below current IV."],
    ["OI Support", fmt(options.support_by_oi), "Highest PE OI at/below spot."],
    ["OI Resistance", fmt(options.resistance_by_oi), "Highest CE OI at/above spot."],
    ["CE Writing", (options.ce_writing_strikes || []).join(", ") || "-", "Call strikes with positive OI change."],
    ["PE Writing", (options.pe_writing_strikes || []).join(", ") || "-", "Put strikes with positive OI change."],
  ]
    .map(([metric, value, read]) => `<tr><td>${metric}</td><td>${value}</td><td>${read}</td></tr>`)
    .join("");
  renderNiftyStrategies(data.candidates || state.nifty.candidates || []);
}

function candleSourceText(sources) {
  if (!sources) return "-";
  return Object.entries(sources)
    .map(([frame, row]) => `${frame}: ${fmtInt(row.count)} candles, latest ${fmtDateTime(row.to)}`)
    .join(" | ");
}

function refreshResultText(rows) {
  if (!rows || !rows.length) return $("niftyRefresh").checked ? "Refresh requested but no candle pull completed." : "Refresh not requested.";
  return rows.map((row) => `${row.timeframe}: ${row.candles} candles -> ${row.output}`).join(" | ");
}

function renderNiftyStrategies(candidates) {
  $("niftyStrategyMeta").textContent = `${candidates.length} candidate(s)`;
  $("niftyStrategyBody").innerHTML = candidates
    .map((candidate, index) => `
      <tr>
        <td>${candidate.label}</td>
        <td>${candidate.horizon}</td>
        <td>${candidate.structure}</td>
        <td>${candidate.suitability_score}</td>
        <td>${candidate.confidence}</td>
        <td>${candidate.expiry_plan}</td>
        <td>${candidate.best_when}</td>
        <td>${candidate.avoid_when}</td>
        <td>${(candidate.reasons || []).join("; ")}</td>
        <td>${(candidate.risks || []).join("; ") || "-"}</td>
        <td>${(candidate.required_confirmations || []).join("; ") || "-"}</td>
        <td>
          <button class="linkBtn nifty-payoff-btn" data-index="${index}">Payoff</button>
          <button class="linkBtn nifty-backtest-btn" data-index="${index}">Backtest</button>
        </td>
      </tr>
    `)
    .join("");
  document.querySelectorAll(".nifty-payoff-btn").forEach((button) => {
    button.addEventListener("click", () => runNiftyPayoff(candidates[Number(button.dataset.index)]));
  });
  document.querySelectorAll(".nifty-backtest-btn").forEach((button) => {
    button.addEventListener("click", () => runNiftyBacktest(candidates[Number(button.dataset.index)]));
  });
}

async function runNiftyPayoff(candidate) {
  const spot = Number((state.nifty.context && state.nifty.context.technical && state.nifty.context.technical.spot) || 24500);
  const legs = defaultNiftyPayoffLegs(candidate, spot);
  try {
    const data = await postApi("/api/nifty/payoff", { spot, lot_size: 75, legs });
    state.nifty.payoff = data;
    renderNiftyPayoff(data);
  } catch (error) {
    setNotes([error.message], true);
  }
}

function defaultNiftyPayoffLegs(candidate, spot) {
  const base = Math.round(spot / 50) * 50;
  if ((candidate.strategy_id || "").includes("bull_call")) {
    return [
      { side: "buy", option_type: "CE", strike: base, premium: 120 },
      { side: "sell", option_type: "CE", strike: base + 200, premium: 50 },
    ];
  }
  if ((candidate.strategy_id || "").includes("bear_put")) {
    return [
      { side: "buy", option_type: "PE", strike: base, premium: 120 },
      { side: "sell", option_type: "PE", strike: base - 200, premium: 50 },
    ];
  }
  if ((candidate.strategy_id || "").includes("strangle")) {
    return [
      { side: "sell", option_type: "PE", strike: base - 300, premium: 80 },
      { side: "sell", option_type: "CE", strike: base + 300, premium: 80 },
    ];
  }
  return [
    { side: "sell", option_type: "PE", strike: base - 200, premium: 70 },
    { side: "buy", option_type: "PE", strike: base - 400, premium: 30 },
    { side: "sell", option_type: "CE", strike: base + 200, premium: 70 },
    { side: "buy", option_type: "CE", strike: base + 400, premium: 30 },
  ];
}

function renderNiftyPayoff(data) {
  $("niftyPayoffMeta").textContent = `Net premium ${fmt(data.net_premium)} / lot ${data.lot_size}`;
  $("niftyPayoffNotes").innerHTML = [data.max_profit_note, data.max_loss_note, data.breakeven_note]
    .map((item) => `<div>${item}</div>`)
    .join("");
  $("niftyPayoffBody").innerHTML = (data.payoff_table || [])
    .map((row) => `<tr><td>${fmt(row.spot)}</td><td>${fmt(row.payoff)}</td></tr>`)
    .join("");
}

async function runNiftyBacktest(candidate) {
  $("niftyBacktestMeta").textContent = "Running";
  try {
    const data = await postApi("/api/nifty/backtest", {
      strategy_id: candidate.strategy_id,
      mode: candidate.horizon || $("niftyMode").value,
      days: 365,
      exit_rules: { holding_bars: 5 },
    });
    state.nifty.backtest = data;
    renderNiftyBacktest(data);
  } catch (error) {
    $("niftyBacktestMeta").textContent = "Failed";
    setNotes([error.message], true);
  }
}

function renderNiftyBacktest(data) {
  const metrics = data.metrics || {};
  $("niftyBacktestMeta").textContent = `${metrics.signals || 0} signal(s) / context-only`;
  $("niftyBacktestCards").innerHTML = [
    ["Signals", fmtInt(metrics.signals)],
    ["Forward Accuracy", fmtPct(metrics.accuracy)],
    ["Avg Forward Move", fmtPct(metrics.avg_forward_return)],
    ["Trades", fmtInt(metrics.trade_count)],
  ]
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("niftyBacktestBody").innerHTML = (data.context_forward_returns || [])
    .slice(-25)
    .map((row) => `
      <tr>
        <td>${fmtDateTime(row.signal_date)}</td>
        <td>${row.side}</td>
        <td>${fmt(row.entry_reference)}</td>
        <td>${fmt(row.exit_reference)}</td>
        <td>${fmtPct(row.forward_return)}</td>
        <td>${row.success ? "favorable" : "unfavorable"}</td>
      </tr>
    `)
    .join("");
  if ((data.warnings || []).length) setNotes(data.warnings);
}

function setNotes(value, isError = false) {
  const notes = $("notes");
  notes.className = isError ? "notes error" : "notes";
  if (Array.isArray(value)) {
    notes.innerHTML = value.length ? value.map((item) => `<div>${item}</div>`).join("") : "";
  } else {
    notes.textContent = value || "";
  }
}

function capitalize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function statusLabel(value) {
  return value.replaceAll("_", " ");
}

function statusKey(value) {
  return String(value || "wait").toLowerCase().replaceAll("/", "_").replaceAll(" ", "_");
}

function fmtSigned(value) {
  const number = Number(value || 0);
  if (number > 0) return `+${number}`;
  return String(number);
}

function pointsClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "points-positive";
  if (number < 0) return "points-negative";
  return "points-zero";
}

function fmtDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("en-IN", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function enhanceCollapsibleSections() {
  document.querySelectorAll(".table-panel > .panel-head").forEach((head, index) => {
    if (head.querySelector(".collapse-btn")) return;
    const title = head.querySelector("h2");
    if (title && !title.parentElement.classList.contains("panel-head-title")) {
      const wrapper = document.createElement("div");
      wrapper.className = "panel-head-title";
      title.replaceWith(wrapper);
      wrapper.appendChild(title);
    }
    const button = document.createElement("button");
    button.className = "collapse-btn";
    button.type = "button";
    button.textContent = "-";
    button.title = "Collapse section";
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", `panel-body-${index}`);
    const wrapper = head.querySelector(".panel-head-title") || head;
    wrapper.insertBefore(button, wrapper.firstChild);
    button.addEventListener("click", () => {
      const panel = head.closest(".table-panel");
      const collapsed = !panel.classList.contains("collapsed");
      panel.classList.toggle("collapsed", collapsed);
      button.textContent = collapsed ? "+" : "-";
      button.title = collapsed ? "Expand section" : "Collapse section";
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
    });
  });
}

$("analyzeBtn").addEventListener("click", analyze);
$("checkZerodhaBtn").addEventListener("click", checkZerodhaStatus);
$("updateZerodhaTokenBtn").addEventListener("click", updateZerodhaToken);
$("bulkDownloadBtn").addEventListener("click", startBulkDownload);
$("bulkMonth").addEventListener("change", adjustBulkDaysForHigherFrames);
$("bulkWeek").addEventListener("change", adjustBulkDaysForHigherFrames);
$("sectorUploadBtn").addEventListener("click", uploadSectorCsv);
$("refreshFiiDiiBtn").addEventListener("click", () => loadFiiDii(true));
$("saveReportBtn").addEventListener("click", saveReport);
$("krishnaScanBtn").addEventListener("click", runKrishnaScan);
$("krishnaCopyBtn").addEventListener("click", copyKrishnaSymbols);
$("krishnaDownloadBtn").addEventListener("click", downloadKrishnaCsv);
$("genericStrategySelect").addEventListener("change", populateStrategyParams);
$("genericBacktestRunBtn").addEventListener("click", runGenericBacktest);
$("backtestRunBtn").addEventListener("click", runKrishnaBacktest);
$("backtestDownloadTradesBtn").addEventListener("click", downloadBacktestTrades);
$("backtestDownloadSignalsBtn").addEventListener("click", downloadBacktestSignals);
$("niftyRunContextBtn").addEventListener("click", runNiftyContext);
$("niftySuggestBtn").addEventListener("click", runNiftySuggestions);
$("startOptionMonitorBtn").addEventListener("click", startOptionMonitor);
$("stopOptionMonitorBtn").addEventListener("click", stopOptionMonitor);
$("optionMonitorSymbols").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadOptionMonitorExpiries();
});
$("optionMonitorSymbols").addEventListener("blur", loadOptionMonitorExpiries);
$("optionMonitorSymbols").addEventListener("change", loadOptionMonitorExpiries);
$("symbolInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") analyze();
});
$("symbolInput").addEventListener("blur", loadOptionExpiries);
$("expirySelect").addEventListener("change", loadOptionSnapshots);
$("previousSnapshotSelect").addEventListener("change", useSelectedSnapshot);
$("refreshSnapshotsBtn").addEventListener("click", loadOptionSnapshots);
$("previousSnapshot").addEventListener("input", () => {
  $("previousSnapshotSelect").value = "";
});
document.querySelectorAll("[data-scan]").forEach((button) => {
  button.addEventListener("click", () => scan(button.dataset.scan));
});
document.querySelectorAll("[data-opportunity]").forEach((button) => {
  button.addEventListener("click", () => scanOpportunity(button.dataset.opportunity));
});
document.querySelectorAll("[data-tab-target]").forEach((button) => {
  button.setAttribute("role", "tab");
  button.setAttribute("aria-selected", button.classList.contains("active") ? "true" : "false");
  button.addEventListener("click", () => activateTab(button.dataset.tabTarget));
});
enhanceCollapsibleSections();

Promise.all([loadZerodhaLoginUrl(), checkZerodhaStatus(), loadSymbols(), loadStrategies(), loadNiftyExpiries(), loadSectorStatus(), loadFiiDii(false)])
  .then(() => {
    const first = state.symbols.find((row) => row.has_daily);
    if (first) {
      $("symbolInput").value = first.symbol;
      loadOptionExpiries();
      analyze();
    }
    return scan("neutral");
  })
  .catch((error) => setNotes([error.message], true));
