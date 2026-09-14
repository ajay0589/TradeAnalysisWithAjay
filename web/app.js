const state = {
  symbols: [],
  lastAnalysis: null,
  bulkJobId: null,
  bulkPollTimer: null,
  krishnaRefreshJobId: null,
  lastKrishnaRows: [],
  purpleRefreshJobId: null,
  lastPurpleStep1Rows: [],
  lastPurpleRows: [],
  lastPurpleAlerts: [],
  lastPurpleTrades: [],
  lastPurpleAlertResponse: null,
  lastPurpleBacktest: null,
  purpleAutoTimers: {},
  purpleMonitorPollTimer: null,
  purplePendingProfiles: [],
  purpleNextRunAt: {},
  purpleProfileRuns: {
    month: { status: "idle", detail: "Waiting for a scan.", progress: 0, startedAt: null, endedAt: null },
    week: { status: "idle", detail: "Waiting for a scan.", progress: 0, startedAt: null, endedAt: null },
    day: { status: "idle", detail: "Waiting for a scan.", progress: 0, startedAt: null, endedAt: null },
  },
  purpleActiveProfile: null,
  purpleMonitorRunning: false,
  purpleScanInFlight: false,
  purpleCancelRequested: false,
  purplePages: { step1: 1, candidates: 1, setups: 1, exit: 1, trades: 1 },
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
    alertBacktest: null,
    snapshots: [],
    latestData: null,
    autoStatus: null,
    alerts: [],
    autoPollTimer: null,
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
  if (name === "nifty") {
    loadNiftyAutoStatus();
    loadNiftyAlerts();
    loadNiftyContextSnapshots();
    loadNiftyLatestData();
    startNiftyAutoPolling();
  } else {
    stopNiftyAutoPolling();
  }
}

const BULK_TIMEFRAME_LABELS = {
  month: "Monthly",
  week: "Weekly",
  day: "Day",
  "60minute": "1 hour",
  "120minute": "2 hours",
  "30minute": "30 min",
  "15minute": "15 min",
  "10minute": "10 min",
};

const BULK_DERIVED_MIN_DAYS = {
  month: 3000,
  week: 730,
};

const PURPLE_PROFILES = {
  month: { label: "Monthly", confirmation: "5month", early: "120minute", final: "day", exit: "120minute", days: 3000 },
  week: { label: "Weekly", confirmation: "month", early: "30minute", final: "120minute", exit: "30minute", days: 730 },
  day: { label: "Daily", confirmation: "week", early: "10minute", final: "30minute", exit: "10minute", days: 365 },
};

const PURPLE_ACTIVE_PROFILE_KEYS = ["month", "week", "day"];

const PURPLE_PROFILE_ANALYSIS_DETAILS = {
  month: "Analyzing Monthly touch, derived 5-month confirmation, 2-hour early/exit, and Daily final-entry rules.",
  week: "Analyzing Weekly touch, Monthly confirmation, 30-minute early/exit, and 2-hour final-entry rules.",
  day: "Analyzing Daily touch, Weekly confirmation, 10-minute early/exit, and 30-minute final-entry rules.",
};

const PURPLE_PAGE_SIZE = 25;
const PURPLE_PROFILE_ORDER = { month: 0, week: 1, day: 2 };

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

function purpleProfile() {
  return PURPLE_PROFILES[$("purpleTimeframe").value] || PURPLE_PROFILES.week;
}

function updatePurpleDefaults() {
  renderPurpleRules({
    profiles: PURPLE_ACTIVE_PROFILE_KEYS.map((key) => ({
      ...PURPLE_PROFILES[key],
      purple_timeframe: key,
      label: PURPLE_PROFILES[key].label,
      confirmation_timeframe: PURPLE_PROFILES[key].confirmation,
      early_timeframe: PURPLE_PROFILES[key].early,
      final_timeframe: PURPLE_PROFILES[key].final,
      exit_timeframe: PURPLE_PROFILES[key].exit,
    })),
  });
}

async function runPurpleScan() {
  const selected = $("purpleTimeframe").value;
  const profile = purpleProfile();
  setNotes("Running Krishna purple-touch entry scan...");
  setPurpleProgress("running", `Preparing ${profile.label} purple-touch filter...`);
  try {
    if ($("purpleRefreshToggle").checked) {
      await refreshCandlesForPurple();
    }
    const params = new URLSearchParams({ purple_timeframe: selected });
    const days = $("purpleDays").value.trim();
    const limit = $("purpleLimit").value.trim();
    if (days) params.set("days", days);
    params.set("limit", limit || "50");
    setPurpleProgress("running", "Filtering cached candles for purple-touch setup...");
    const data = await api(`/api/krishna-purple-touch-scan?${params.toString()}`);
    if (data.summary && $("purpleRefreshToggle").checked) {
      data.summary.latest_candles_pulled = true;
      data.summary.points = [
        `Pulled required candle sources before running ${profile.label} purple-touch scan.`,
        ...(data.summary.points || []),
      ];
    }
    $("purpleMeta").textContent = `${data.results.length} shown / ${data.matched_symbols} matched / ${data.analyzed_symbols} analyzed / ${data.purple_timeframe_label}`;
    $("purpleResultMeta").textContent = `${data.results.length} shown`;
    renderPurpleRules(data);
    renderPurpleSummary(data.summary);
    renderPurpleResults(data.results);
    setPurpleProgress("completed", `${data.results.length} shown from ${data.matched_symbols} matching stock(s).`);
    setNotes("Purple-touch shortlist is ready for manual chart review with Krishna.");
  } catch (error) {
    setPurpleProgress("failed", error.message);
    setNotes([error.message], true);
  }
}

async function refreshCandlesForPurple() {
  const selected = $("purpleTimeframe").value;
  const profile = purpleProfile();
  const timeframes = Array.from(new Set([selected, profile.confirmation, profile.early, profile.final, profile.exit]));
  await refreshPurpleTimeframes(timeframes, Number($("purpleDays").value || profile.days));
}

async function refreshCandlesForAllPurpleProfiles() {
  const timeframes = Array.from(new Set(PURPLE_ACTIVE_PROFILE_KEYS.flatMap((key) => {
    const profile = PURPLE_PROFILES[key];
    return [key, profile.confirmation, profile.early, profile.final, profile.exit];
  })));
  await refreshPurpleTimeframes(timeframes, Math.max(...PURPLE_ACTIVE_PROFILE_KEYS.map((key) => PURPLE_PROFILES[key].days)));
}

async function refreshCandlesForPurpleProfile(profileKey) {
  const profile = PURPLE_PROFILES[profileKey];
  if (!profile) return refreshCandlesForAllPurpleProfiles();
  const timeframes = Array.from(new Set([profileKey, profile.confirmation, profile.early, profile.final, profile.exit]));
  await refreshPurpleTimeframes(timeframes, profile.days);
}

async function refreshPurpleTimeframes(timeframes, days) {
  const job = await postApi("/api/bulk-candles", {
    timeframes,
    days,
    limit: null,
  });
  state.purpleRefreshJobId = job.job_id;
  state.purpleCancelRequested = false;
  renderPurpleRefreshJob(job, state.purpleActiveProfile || "all");
  await waitForPurpleRefreshJob(job.job_id);
}

async function waitForPurpleRefreshJob(jobId) {
  while (true) {
    const job = await api(`/api/job?job_id=${encodeURIComponent(jobId)}`);
    renderPurpleRefreshJob(job, state.purpleActiveProfile || "all");
    if (!["queued", "running", "stopping"].includes(job.status)) {
      if (job.status === "cancelled") {
        const error = new Error("Candle refresh cancelled. Purple Touch analysis was not started.");
        error.cancelled = true;
        throw error;
      }
      if (job.status !== "completed") {
        throw new Error(`Purple-touch candle refresh ${job.status}. ${job.errors?.[0] || ""}`.trim());
      }
      return job;
    }
    await delay(1500);
  }
}

function renderPurpleRefreshJob(job, purpleTimeframe = "all") {
  const total = job.total || 0;
  const completed = job.completed || 0;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  const refreshStatus = ["queued", "running", "stopping"].includes(job.status)
    ? "refreshing"
    : job.status === "completed"
      ? "refresh complete"
      : job.status;
  const overallProgress = job.status === "completed" ? 75 : Math.round(percent * 0.75);
  $("purpleProgressMeta").textContent = `refresh ${job.status} / ${completed}/${total}`;
  $("purpleProgressStatus").textContent = `Refreshing purple-touch candles: ${job.current || "starting"} | success ${job.successes || 0} | failures ${job.failures || 0}`;
  setPurpleProfileProgress(
    purpleTimeframe,
    refreshStatus,
    `Candles ${completed}/${total}; success ${job.successes || 0}, failures ${job.failures || 0}.`,
    overallProgress,
  );
  const cancellable = ["queued", "running", "stopping"].includes(job.status);
  $("purpleCancelScanBtn").disabled = !cancellable || state.purpleCancelRequested;
}

async function cancelPurpleCurrentScan() {
  if (!state.purpleRefreshJobId || !state.purpleScanInFlight || state.purpleCancelRequested) return;
  state.purpleCancelRequested = true;
  $("purpleCancelScanBtn").disabled = true;
  $("purpleProgressMeta").textContent = "cancelling";
  $("purpleProgressStatus").textContent = "Cancellation requested. Waiting for the current Zerodha request to finish...";
  setPurpleProfileProgress(state.purpleActiveProfile || "all", "cancelling", "Waiting for the current candle request to finish...", 95);
  try {
    const job = await postApi("/api/job/stop", { job_id: state.purpleRefreshJobId });
    renderPurpleRefreshJob(job, state.purpleActiveProfile || "all");
  } catch (error) {
    state.purpleCancelRequested = false;
    $("purpleCancelScanBtn").disabled = false;
    setNotes([error.message], true);
  }
}

function setPurpleProgress(status, detail, purpleTimeframe = "all", progress = null) {
  const percent = progress ?? (status === "completed" || status === "failed" || status === "cancelled" ? 100 : 55);
  $("purpleProgressMeta").textContent = status;
  $("purpleProgressStatus").textContent = detail;
  setPurpleProfileProgress(purpleTimeframe, status, detail, percent);
}

function purpleProfileKeys(purpleTimeframe) {
  return purpleTimeframe === "all"
    ? [...PURPLE_ACTIVE_PROFILE_KEYS]
    : [purpleTimeframe].filter((key) => PURPLE_ACTIVE_PROFILE_KEYS.includes(key));
}

function setPurpleProfileProgress(purpleTimeframe, status, detail, progress) {
  const now = new Date().toISOString();
  purpleProfileKeys(purpleTimeframe).forEach((profileKey) => {
    const run = state.purpleProfileRuns[profileKey];
    if (["running", "refreshing"].includes(status) && !run.startedAt) run.startedAt = now;
    if (["completed", "failed", "cancelled"].includes(status)) run.endedAt = now;
    run.status = status;
    run.detail = detail;
    run.progress = Math.max(0, Math.min(100, Number(progress || 0)));
  });
  renderPurpleProfileProgress();
}

function beginPurpleProfileRun(purpleTimeframe, detail) {
  const now = new Date().toISOString();
  purpleProfileKeys(purpleTimeframe).forEach((profileKey) => {
    state.purpleProfileRuns[profileKey] = {
      status: "running",
      detail,
      progress: 8,
      startedAt: now,
      endedAt: null,
    };
  });
  renderPurpleProfileProgress();
}

function renderPurpleProfileProgress() {
  const prefix = { month: "Month", week: "Week", day: "Day" };
  PURPLE_ACTIVE_PROFILE_KEYS.forEach((profileKey) => {
    const run = state.purpleProfileRuns[profileKey];
    const id = `purple${prefix[profileKey]}`;
    $(`${id}ProgressMeta`).textContent = run.status;
    $(`${id}ProgressStatus`).textContent = run.detail;
    $(`${id}ProgressBar`).style.width = `${run.progress}%`;
    $(`${id}ProgressBar`).classList.toggle("active", ["running", "refreshing", "analysis running", "queued"].includes(run.status));
    $(`${id}LastStarted`).textContent = fmtDateTime(run.startedAt);
    $(`${id}LastEnded`).textContent = fmtDateTime(run.endedAt);
    if ($(`${id}Queued`)) $(`${id}Queued`).textContent = fmtDateTime(run.queuedAt);
    if ($(`${id}Duration`)) $(`${id}Duration`).textContent = run.durationMs == null ? "-" : `${(Number(run.durationMs) / 1000).toFixed(1)} sec`;
    const next = state.purpleNextRunAt[profileKey];
    $(`${id}NextRun`).textContent = next ? fmtDateTime(new Date(next).toISOString()) : "Not scheduled";
  });
}

function renderPurpleRules(data) {
  const profiles = data.profiles || [
    { label: "Weekly", early_timeframe: "30minute", final_timeframe: "120minute", exit_timeframe: "30minute" },
  ];
  $("purpleRuleCards").innerHTML = profiles
    .map((profile) => [
      profile.label || profile.purple_timeframe || "-",
      `Confirm ${profile.confirmation_label || profile.confirmation_timeframe || "-"}; Early ${BULK_TIMEFRAME_LABELS[profile.early_timeframe] || profile.early_timeframe}, Final ${BULK_TIMEFRAME_LABELS[profile.final_timeframe] || profile.final_timeframe}, Exit ${BULK_TIMEFRAME_LABELS[profile.exit_timeframe] || profile.exit_timeframe}`,
    ])
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  const sample = ((data.results || [])[0] || (combinePurpleScanResults(data)[0] || {}));
  const understood = sample.understood_rules || [
    "Weekly: early entry 30m, final entry 2H.",
    "Weekly confirmation: latest Monthly candle open or close must be above its blue Chande Kroll line.",
    "Purple-touch filter requires close above black EMA89.",
    "Blue Chande Kroll must be above purple EMA9 and price must approach purple from blue across the latest 2-3 higher-timeframe candles.",
    "The mapped purple-touch bar is Candle 1. Candle 1 and Candle 2 can each create an entry signal when their entry rules pass.",
    "From Candle 3 onward, the touch remains valid until price breaks the lower of Candle 1 and Candle 2 lows. There is no fixed candle-count expiry.",
    "Entry condition: selected entry timeframe candle closes above yellow Chande Kroll.",
    "Final entry additionally requires yellow Chande Kroll above brown VWMA20.",
    "Exit/review condition: exit timeframe candle closes below yellow Chande Kroll.",
    "Light green is EMA26. Ichimoku, VWAP, and Donchian Channel 20 are ignored for this setup.",
    "RSI bullish divergence is optional context only.",
  ];
  const questions = sample.open_questions || [];
  $("purpleRulePoints").innerHTML = [
    ...understood.map((point) => `<div>${escapeHtml(point)}</div>`),
    ...questions.map((point) => `<div class="cell-note">Open: ${escapeHtml(point)}</div>`),
  ].join("");
}

function renderPurpleSummary(summary) {
  if (!summary) {
    $("purpleSummaryCards").innerHTML = "";
    $("purpleSummaryPoints").innerHTML = "";
    return;
  }
  $("purpleSummaryCards").innerHTML = [
    ["Analyzed", summary.analyzed_symbols],
    ["Step 1 touches", summary.step1_touch_count],
    ["Strict candidates", summary.matched_symbols],
    ["Early ready", summary.early_entry_ready],
    ["Final ready", summary.final_entry_ready],
    ["New entry alerts", summary.new_entry_alerts],
    ["New exit alerts", summary.new_exit_alerts],
    ["Errors", summary.error_count],
    ["Latest candles", summary.latest_candles_pulled ? "Pulled" : "No"],
  ]
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${fmtMetric(value)}</strong></div>`)
    .join("");
  $("purpleSummaryPoints").innerHTML = (summary.points || []).map((point) => `<div>${escapeHtml(point)}</div>`).join("");
}

function renderPurpleResults(rows = null) {
  if (Array.isArray(rows)) state.lastPurpleRows = rows;
  const filteredRows = filterPurpleCandidateRows(
    state.lastPurpleRows,
    "purpleCandidateProfileFilter",
    "purpleCandidateEntryFilter",
    "purpleCandidateDistanceFilter",
    null,
    "purpleCandidateSort",
  );
  const page = paginatePurpleRows(filteredRows, "candidates", "purpleCandidate");
  $("purpleResultMeta").textContent = `${page.rows.length} shown / ${filteredRows.length} filtered / ${state.lastPurpleRows.length} candidate row(s)`;
  $("purpleBody").innerHTML = page.rows.length
    ? page.rows
    .map((row) => {
      const early = row.early_entry || {};
      const final = row.final_entry || {};
      const reasons = row.reasons || [];
      const warnings = row.warnings || [];
      return `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${fmtInt(row.score)}<div class="cell-note">${escapeHtml(row.confidence || "-")}</div></td>
          <td>${escapeHtml(row.purple_timeframe_label || row.purple_timeframe || "-")}</td>
          <td>${fmt(row.close)} / ${fmt(row.purple_ema9)}<div class="cell-note">close above purple ${fmt(row.purple_touch_distance_percent)}%</div></td>
          <td>${purpleDirectionCell(row)}</td>
          <td>${higherConfirmationCell(row.higher_confirmation)}</td>
          <td>${fmt(row.yellow_line)} / ${fmt(row.brown_vwma20)}<div class="cell-note">final needs yellow above brown</div></td>
          <td>${fmt(row.light_green_level)}<div class="cell-note">EMA26; yellow ${row.yellow_below_ema26 === true ? "below" : row.yellow_below_ema26 === false ? "not below" : "unknown"}</div></td>
          <td>
            Black EMA89 ${row.above_black_line === true ? "passed" : row.above_black_line === false ? "failed" : "-"}
            <div class="cell-note">mandatory filter</div>
          </td>
          <td>${entryCell(early)}</td>
          <td>${entryCell(final)}</td>
          <td>${escapeHtml(row.exit_rule || "-")}</td>
          <td>
            <ul class="reason-list">${reasons.slice(0, 4).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
            ${warnings.length ? `<div class="cell-note">${escapeHtml(warnings.slice(0, 3).join(" | "))}</div>` : ""}
          </td>
        </tr>
      `;
    })
    .join("")
    : emptyTableRow(13, "No Weekly setup passes all mandatory filters for this selection.");

  document.querySelectorAll("#purpleBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function renderPurpleStep1Results(rows = null) {
  if (Array.isArray(rows)) state.lastPurpleStep1Rows = rows;
  const meta = $("purpleStep1Meta");
  const body = $("purpleStep1Body");
  if (!body) return;
  const filteredRows = filterPurpleCandidateRows(
    state.lastPurpleStep1Rows,
    "purpleStep1ProfileFilter",
    "purpleStep1EntryFilter",
    "purpleStep1DistanceFilter",
    "purpleStep1StatusFilter",
    "purpleStep1Sort",
  );
  const page = paginatePurpleRows(filteredRows, "step1", "purpleStep1");
  if (meta) {
    const higherTfPassed = filteredRows.filter((row) => row.full_setup_status === "qualified").length;
    meta.textContent = `${page.rows.length} shown / ${filteredRows.length} filtered / ${state.lastPurpleStep1Rows.length} touch row(s), ${higherTfPassed} higher-TF filters passed`;
  }
  body.innerHTML = page.rows.length
    ? page.rows
    .map((row) => {
      const early = row.early_entry || {};
      const final = row.final_entry || {};
      const blockers = row.blockers || [];
      const warnings = row.warnings || [];
      return `
        <tr>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button></td>
          <td>${escapeHtml(row.purple_timeframe_label || row.purple_timeframe || "-")}</td>
          <td>
            ${fmt(row.close)}
            <div class="cell-note">H ${fmt(row.candle_high)} / L ${fmt(row.candle_low)}</div>
          </td>
          <td>
            ${fmt(row.touch_price ?? row.purple_ema9)}
            <div class="cell-note">purple EMA9 touch price</div>
            <div class="cell-note">${fmtDateTime(row.touch_timestamp)}</div>
            <div class="cell-note">close distance ${fmt(row.purple_touch_distance_percent)}%</div>
            <div class="cell-note">physical wick/range touch</div>
          </td>
          <td>
            ${passFailCell(row.above_black_line, "above black EMA89")}
            <div class="cell-note">${fmtInt(row.touch_candle_count || 0)} / 89 ${escapeHtml(purpleProfileLabel(row.purple_timeframe))} candles</div>
            <div class="cell-note">mandatory</div>
          </td>
          <td>
            ${passFailCell(row.above_ema26, "above EMA26")}
            <div class="cell-note">EMA9 ${row.ema9_above_ema26 === true ? "above" : row.ema9_above_ema26 === false ? "not above" : "unknown"} EMA26</div>
          </td>
          <td>${purpleDirectionCell(row)}</td>
          <td>${higherConfirmationCell(row.higher_confirmation)}</td>
          <td>
            Y ${fmt(row.yellow_line)} / B ${fmt(row.brown_vwma20)}
            <div class="cell-note">final needs yellow above brown</div>
          </td>
          <td>${entryCell(early)}</td>
          <td>${entryCell(final)}</td>
          <td>
            <span class="${row.full_setup_status === "qualified" ? "points-positive" : "points-warn"}">${escapeHtml(row.full_setup_status || "-")}</span>
            ${blockers.length ? `<ul class="reason-list">${blockers.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
            ${warnings.length ? `<div class="cell-note">${escapeHtml(warnings.slice(0, 2).join(" | "))}</div>` : ""}
          </td>
        </tr>
      `;
    })
    .join("")
    : emptyTableRow(12, "No Step 1 purple EMA9 touches match these filters.");

  document.querySelectorAll("#purpleStep1Body .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function passFailCell(value, label) {
  if (value === true) return `<span class="points-positive">pass</span><div class="cell-note">${escapeHtml(label)}</div>`;
  if (value === false) return `<span class="points-negative">block</span><div class="cell-note">${escapeHtml(label)}</div>`;
  return `<span class="points-warn">unknown</span><div class="cell-note">${escapeHtml(label)}</div>`;
}

function purpleDirectionCell(row) {
  return `
    ${passFailCell(row.blue_above_purple, "blue above purple")}
    <div class="cell-note">${row.approach_from_blue === true ? "approached blue -> purple" : row.approach_from_blue === false ? "no blue -> purple approach" : "approach unknown"}</div>
  `;
}

function higherConfirmationCell(confirmation = {}) {
  const status = confirmation.status || "missing";
  const cls = status === "pass" ? "points-positive" : status === "block" ? "points-negative" : "points-warn";
  return `
    <span class="${cls}">${escapeHtml(status)}</span>
    <div class="cell-note">${escapeHtml(confirmation.label || confirmation.timeframe || "-")}</div>
    <div class="cell-note">O ${fmt(confirmation.open)} / C ${fmt(confirmation.close)} / Blue ${fmt(confirmation.blue_line)}</div>
  `;
}

function filterPurpleCandidateRows(
  rows,
  profileFilterId,
  entryFilterId,
  distanceFilterId = null,
  setupStatusFilterId = null,
  sortId = null,
) {
  const profile = $(profileFilterId)?.value || "all";
  const entryKind = $(entryFilterId)?.value || "all";
  const setupStatus = setupStatusFilterId ? $(setupStatusFilterId)?.value || "all" : "all";
  const maximumDistance = distanceFilterId ? Number($(distanceFilterId)?.value ?? 1) : null;
  const filtered = (rows || []).filter((row) => {
    if (profile !== "all" && row.purple_timeframe !== profile) return false;
    if (setupStatus !== "all" && row.full_setup_status !== setupStatus) return false;
    if (maximumDistance !== null) {
      const distance = Number(row.purple_touch_distance_percent);
      if (!Number.isFinite(distance) || distance > maximumDistance + 1e-9) return false;
    }
    if (entryKind === "all") return true;
    const earlyStatus = (row.early_entry || {}).status;
    const finalStatus = (row.final_entry || {}).status;
    if (entryKind === "early" || entryKind === "final") {
      return (row[`${entryKind}_entry`] || {}).status === "entry_candidate";
    }
    if (entryKind === "waiting") return earlyStatus === "wait" || finalStatus === "wait";
    if (entryKind === "discarded") return earlyStatus === "discarded" || finalStatus === "discarded";
    return true;
  });
  return sortPurpleRows(filtered, sortId ? $(sortId)?.value : null);
}

function sortPurpleRows(rows, sortValue) {
  const sorted = [...rows];
  const profileOrder = (row) => PURPLE_PROFILE_ORDER[row.purple_timeframe] ?? 99;
  const distance = (row) => {
    const value = Number(row.purple_touch_distance_percent);
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  };
  const comparators = {
    distance_asc: (left, right) => distance(left) - distance(right),
    distance_desc: (left, right) => distance(right) - distance(left),
    score_desc: (left, right) => Number(right.score || 0) - Number(left.score || 0),
    profile_asc: (left, right) => profileOrder(left) - profileOrder(right),
    symbol_asc: (left, right) => String(left.symbol || "").localeCompare(String(right.symbol || "")),
    status_asc: (left, right) => String(left.full_setup_status || "").localeCompare(String(right.full_setup_status || "")),
  };
  const comparator = comparators[sortValue] || comparators.distance_asc;
  return sorted.sort((left, right) => comparator(left, right) || profileOrder(left) - profileOrder(right) || String(left.symbol || "").localeCompare(String(right.symbol || "")));
}

function paginatePurpleRows(rows, pageKey, elementPrefix) {
  const pages = Math.max(1, Math.ceil(rows.length / PURPLE_PAGE_SIZE));
  const current = Math.min(Math.max(1, state.purplePages[pageKey] || 1), pages);
  state.purplePages[pageKey] = current;
  const start = (current - 1) * PURPLE_PAGE_SIZE;
  updatePurplePager(elementPrefix, current, pages, rows.length);
  return { rows: rows.slice(start, start + PURPLE_PAGE_SIZE), page: current, pages, total: rows.length };
}

function updatePurplePager(elementPrefix, page, pages, total) {
  const previous = $(`${elementPrefix}Prev`);
  const next = $(`${elementPrefix}Next`);
  const label = $(`${elementPrefix}PageLabel`);
  if (previous) previous.disabled = page <= 1;
  if (next) next.disabled = page >= pages;
  if (label) label.textContent = `Page ${page} of ${pages} (${total} row${total === 1 ? "" : "s"})`;
}

function emptyTableRow(columns, message) {
  return `<tr class="empty-table-row"><td colspan="${columns}">${escapeHtml(message)}</td></tr>`;
}

function entryCell(entry) {
  const status = entry.status || "missing";
  const cls = status === "entry_candidate" ? "points-positive" : status === "discarded" ? "points-negative" : status === "wait" ? "points-warn" : "";
  return `
    <span class="${cls}">${escapeHtml(status)}</span>
    <div class="cell-note">C ${fmt(entry.close)} / Y ${fmt(entry.yellow_line)}</div>
    <div class="cell-note">Brown ${fmt(entry.brown_vwma20)} / RSI ${fmt(entry.rsi14)}</div>
    <div class="cell-note">Blue/purple ${entry.blue_above_purple === true ? "above" : entry.blue_above_purple === false ? "not above" : "unknown"} (context only)</div>
    <div class="cell-note">${entry.bars_since_touch == null ? "touch not mapped" : `${entry.bars_since_touch} bar(s) after Candle 1`}</div>
    <div class="cell-note">C1 low ${fmt(entry.candle1_low)} / C2 low ${fmt(entry.candle2_low)}</div>
    <div class="cell-note">${entry.setup_discarded ? `touch expired at ${fmt(entry.invalidation_level)}` : entry.reference_lows_ready ? `touch active; break level ${fmt(entry.invalidation_level)}` : "C1 eligible; C2 low pending"}</div>
    <div class="cell-note">${entry.rsi_divergence ? "RSI divergence" : "no RSI divergence"}</div>
  `;
}

function copyPurpleSymbols() {
  const symbols = filterPurpleCandidateRows(state.lastPurpleRows, "purpleCandidateProfileFilter", "purpleCandidateEntryFilter", "purpleCandidateDistanceFilter", null, "purpleCandidateSort")
    .map((row) => row.symbol)
    .filter(Boolean);
  copyText(symbols.join(", "), `Copied ${symbols.length} purple-touch symbol(s).`);
}

function downloadPurpleCsv() {
  const rows = filterPurpleCandidateRows(state.lastPurpleRows, "purpleCandidateProfileFilter", "purpleCandidateEntryFilter", "purpleCandidateDistanceFilter", null, "purpleCandidateSort");
  downloadCsv("krishna_purple_touch_filtered_stocks.csv", rows, [
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Score", value: (row) => row.score },
    { label: "Confidence", value: (row) => row.confidence },
    { label: "Purple Timeframe", value: (row) => row.purple_timeframe_label },
    { label: "Close", value: (row) => row.close },
    { label: "Purple EMA9", value: (row) => row.purple_ema9 },
    { label: "Purple Close Distance %", value: (row) => row.purple_touch_distance_percent },
    { label: "Physical Touch Range Distance %", value: (row) => row.purple_range_distance_percent },
    { label: "Blue CK", value: (row) => row.blue_line },
    { label: "Blue Above Purple", value: (row) => row.blue_above_purple },
    { label: "Approach From Blue", value: (row) => row.approach_from_blue },
    { label: "Higher Confirmation", value: (row) => row.higher_confirmation && row.higher_confirmation.label },
    { label: "Higher Confirmation Status", value: (row) => row.higher_confirmation && row.higher_confirmation.status },
    { label: "Higher Confirmation Blue", value: (row) => row.higher_confirmation && row.higher_confirmation.blue_line },
    { label: "Yellow CK", value: (row) => row.yellow_line },
    { label: "Brown VWMA20", value: (row) => row.brown_vwma20 },
    { label: "EMA26 Light Green", value: (row) => row.light_green_level },
    { label: "Yellow Below EMA26", value: (row) => row.yellow_below_ema26 },
    { label: "Above Black", value: (row) => row.above_black_line },
    { label: "Early Status", value: (row) => row.early_entry && row.early_entry.status },
    { label: "Early Close", value: (row) => row.early_entry && row.early_entry.close },
    { label: "Early Yellow", value: (row) => row.early_entry && row.early_entry.yellow_line },
    { label: "Final Status", value: (row) => row.final_entry && row.final_entry.status },
    { label: "Final Close", value: (row) => row.final_entry && row.final_entry.close },
    { label: "Final Yellow", value: (row) => row.final_entry && row.final_entry.yellow_line },
    { label: "Exit Rule", value: (row) => row.exit_rule },
    { label: "Reasons", value: (row) => row.reasons },
    { label: "Warnings", value: (row) => row.warnings },
  ]);
}

async function runPurpleLiveScan({ quiet = false, purpleTimeframe = "all" } = {}) {
  if (![...PURPLE_ACTIVE_PROFILE_KEYS, "all"].includes(purpleTimeframe)) purpleTimeframe = "all";
  if (state.purpleScanInFlight) {
    queuePurpleProfile(purpleTimeframe);
    return;
  }
  state.purpleScanInFlight = true;
  state.purpleActiveProfile = purpleTimeframe;
  state.purpleRefreshJobId = null;
  state.purpleCancelRequested = false;
  $("purpleLiveScanBtn").disabled = true;
  $("purpleCancelScanBtn").disabled = !$("purpleRefreshToggle").checked;
  const scanLabel = purpleTimeframe === "all" ? "Monthly, Weekly, and Daily" : purpleProfileLabel(purpleTimeframe);
  beginPurpleProfileRun(purpleTimeframe, `Starting ${scanLabel} candle refresh and analysis.`);
  updatePurpleMonitorUi(state.purpleMonitorRunning ? `Scanning ${scanLabel} now...` : `Running ${scanLabel} scan...`);
  if (!quiet) {
    setNotes(`Running Krishna purple-touch live alert scan for ${scanLabel}...`);
    $("purpleAlertMeta").textContent = "running";
  }
  try {
    setPurpleProgress(
      "running",
      `Analyzing ${scanLabel} setup rules from the latest complete candle cache...`,
      purpleTimeframe,
      55,
    );
    purpleProfileKeys(purpleTimeframe).forEach((profileKey) => {
      setPurpleProfileProgress(profileKey, "running", PURPLE_PROFILE_ANALYSIS_DETAILS[profileKey], 85);
    });
    $("purpleProgressMeta").textContent = "analysis running";
    const data = await postApi("/api/krishna-purple-touch-live-scan", {
      purple_timeframe: purpleTimeframe,
      limit: "all",
      force: $("purpleForceToggle").checked,
      send_telegram: $("purpleTelegramToggle").checked,
    });
    const step1Rows = combinePurpleStep1Results(data);
    const rows = combinePurpleScanResults(data);
    state.lastPurpleStep1Rows = mergePurpleProfileRows(state.lastPurpleStep1Rows, step1Rows, purpleTimeframe);
    state.lastPurpleRows = mergePurpleProfileRows(state.lastPurpleRows, rows, purpleTimeframe);
    $("purpleMeta").textContent = `${state.lastPurpleStep1Rows.length} step-1 row(s), ${state.lastPurpleRows.length} entry candidate row(s); latest ${profileList(data)} scan`;
    renderPurpleRules(data);
    renderPurpleSummary(scanSummaryFromLiveScan(data, rows, step1Rows));
    renderPurpleStep1Results();
    renderPurpleResults();
    renderPurpleAlerts(data);
    loadPurpleAlerts();
    loadPurpleSetups();
    loadPurpleMonitorStatus();
    const newAlerts = Number(data.entry_alerts_created || 0) + Number(data.exit_alerts_created || 0);
    setPurpleProgress(
      "completed",
      `${scanLabel} analysis complete: ${step1Rows.length} physical touch row(s), ${rows.length} all-filter setup(s), ${newAlerts} new alert(s).`,
      purpleTimeframe,
      100,
    );
    $("purpleAlertMeta").textContent = data.alert_creation_skipped
      ? "analysis complete / live alerts paused"
      : `${data.entry_alerts_created || 0} entry / ${data.exit_alerts_created || 0} new alert(s)`;
    if (!quiet) {
      setNotes(newAlerts ? `${newAlerts} new Purple Touch alert(s) are shown on the Web UI.` : "Purple Touch scan completed; no new live alerts.");
    }
  } catch (error) {
    $("purpleAlertMeta").textContent = error.cancelled ? "cancelled" : "failed";
    setPurpleProgress(error.cancelled ? "cancelled" : "failed", error.message, purpleTimeframe, 100);
    setNotes([error.message], !error.cancelled);
  } finally {
    state.purpleScanInFlight = false;
    state.purpleActiveProfile = null;
    state.purpleRefreshJobId = null;
    state.purpleCancelRequested = false;
    $("purpleLiveScanBtn").disabled = false;
    $("purpleCancelScanBtn").disabled = true;
    updatePurpleMonitorUi();
    runNextQueuedPurpleProfile();
  }
}

function mergePurpleProfileRows(existing, incoming, purpleTimeframe) {
  if (purpleTimeframe === "all") return incoming;
  return [...(existing || []).filter((row) => row.purple_timeframe !== purpleTimeframe), ...(incoming || [])]
    .sort((left, right) => {
      const profileDifference = (PURPLE_PROFILE_ORDER[left.purple_timeframe] ?? 99) - (PURPLE_PROFILE_ORDER[right.purple_timeframe] ?? 99);
      if (profileDifference) return profileDifference;
      const distanceDifference = Number(left.purple_touch_distance_percent || 0) - Number(right.purple_touch_distance_percent || 0);
      if (distanceDifference) return distanceDifference;
      return String(left.symbol || "").localeCompare(String(right.symbol || ""));
    });
}

function queuePurpleProfile(purpleTimeframe) {
  if (!PURPLE_ACTIVE_PROFILE_KEYS.includes(purpleTimeframe)) return;
  if (!state.purpleMonitorRunning) return;
  if (!state.purplePendingProfiles.includes(purpleTimeframe)) {
    state.purplePendingProfiles.push(purpleTimeframe);
    setPurpleProfileProgress(purpleTimeframe, "queued", `Scheduled ${purpleProfileLabel(purpleTimeframe)} scan is queued behind the active candle refresh.`, 5);
  }
  if (!state.purpleScanInFlight) runNextQueuedPurpleProfile();
}

function runNextQueuedPurpleProfile() {
  if (state.purpleScanInFlight || !state.purplePendingProfiles.length) return;
  const nextProfile = state.purplePendingProfiles.shift();
  window.setTimeout(() => runPurpleLiveScan({ quiet: true, purpleTimeframe: nextProfile }), 0);
}

function combinePurpleScanResults(data) {
  const scans = data.scans || (data.scan ? [data.scan] : []);
  return scans.flatMap((scan) => scan.results || []);
}

function combinePurpleStep1Results(data) {
  if (Array.isArray(data.step1_results)) return data.step1_results;
  const scans = data.step1_scans || (data.step1_scan ? [data.step1_scan] : []);
  return scans.flatMap((scan) => scan.results || []);
}

function scanSummaryFromLiveScan(data, rows, step1Rows = []) {
  const scans = data.scans || (data.scan ? [data.scan] : []);
  const step1Scans = data.step1_scans || (data.step1_scan ? [data.step1_scan] : []);
  const analyzed = scans.reduce((total, scan) => total + Number(scan.analyzed_symbols || 0), 0);
  const matched = scans.reduce((total, scan) => total + Number(scan.matched_symbols || 0), 0);
  const step1Matched = step1Scans.reduce((total, scan) => total + Number(scan.matched_symbols || 0), 0);
  const errors =
    scans.reduce((total, scan) => total + Number((scan.errors || []).length), 0) +
    step1Scans.reduce((total, scan) => total + Number((scan.errors || []).length), 0) +
    Number((data.errors || []).length);
  const earlyReady = rows.filter((row) => (row.early_entry || {}).status === "entry_candidate").length;
  const finalReady = rows.filter((row) => (row.final_entry || {}).status === "entry_candidate").length;
  return {
    analyzed_symbols: analyzed,
    matched_symbols: matched,
    shown_symbols: rows.length,
    step1_touch_count: step1Matched || step1Rows.length,
    early_entry_ready: earlyReady,
    final_entry_ready: finalReady,
    new_entry_alerts: Number(data.entry_alerts_created || 0),
    new_exit_alerts: Number(data.exit_alerts_created || 0),
    error_count: errors,
    latest_candles_pulled: state.purpleMonitorRunning,
    points: [
      `Scanner evaluated ${profileList(data)} purple-touch profile${(data.profiles || []).length === 1 ? "" : "s"}.`,
      `Step 1 found ${step1Matched || step1Rows.length} physical EMA9 touch row(s) across the selected profile(s); rows are ordered by close distance.`,
      `Setups Passing All Filters shows ${rows.length} row(s) after each profile's mandatory EMA89, EMA26, blue/purple direction, 2-3 candle approach, and higher-timeframe confirmation checks.`,
      `${earlyReady} all-filter setup row(s) have Early ready; ${finalReady} have Final ready. Setup rows are not the same as newly created entry signals.`,
      data.market_hours === false && !data.forced
        ? "Market is closed and Force is off: analysis was refreshed, but entry/exit events, trade IDs, and Telegram alerts were intentionally not created."
        : "Market-hours scanner created entry/exit alerts only for fresh matching opportunities.",
      `Telegram: ${telegramText(data.telegram)}.`,
    ],
  };
}

async function loadPurpleAlerts() {
  try {
    const params = new URLSearchParams({
      limit: "50",
      page_size: String(PURPLE_PAGE_SIZE),
      exit_page: String(state.purplePages.exit),
      trade_page: String(state.purplePages.trades),
    });
    addPurpleAlertFilterParams(params, "exit", "purpleExitAlertProfileFilter", "purpleExitAlertKindFilter");
    addPurpleAlertFilterParams(params, "trade", "purpleTradeProfileFilter", "purpleTradeEntryFilter");
    addPurpleHistoryFilterParams(params, "exit", {
      symbolId: "purpleExitAlertSymbolFilter",
      fromId: "purpleExitAlertFromFilter",
      toId: "purpleExitAlertToFilter",
      sortId: "purpleExitAlertSort",
    });
    addPurpleHistoryFilterParams(params, "trade", {
      statusId: "purpleTradeStatusFilter",
      symbolId: "purpleTradeSymbolFilter",
      fromId: "purpleTradeFromFilter",
      toId: "purpleTradeToFilter",
      sortId: "purpleTradeSort",
    });
    renderPurpleAlerts(await api(`/api/krishna-purple-touch-alerts?${params.toString()}`));
  } catch (error) {
    $("purpleAlertMeta").textContent = "alerts unavailable";
  }
}

async function loadPurpleSetups() {
  try {
    const [sort, order] = ($("purpleSetupSort")?.value || "updated_at:desc").split(":");
    const params = new URLSearchParams({
      page: String(state.purplePages.setups),
      page_size: String(PURPLE_PAGE_SIZE),
      sort,
      order: order || "desc",
    });
    const values = {
      profile: $("purpleSetupProfileFilter")?.value,
      status: $("purpleSetupStatusFilter")?.value,
      symbol: $("purpleSetupSymbolFilter")?.value.trim(),
      early_status: $("purpleSetupEarlyFilter")?.value,
      final_status: $("purpleSetupFinalFilter")?.value,
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value && value !== "all") params.set(key, value);
    });
    renderPurpleSetups(await api(`/api/krishna-purple-setups?${params.toString()}`));
  } catch (error) {
    $("purpleSetupMeta").textContent = "Setups unavailable";
  }
}

function renderPurpleSetups(data) {
  const rows = data.setups || [];
  const pagination = data.pagination || { page: 1, pages: 1, total: rows.length };
  state.purplePages.setups = Number(pagination.page || 1);
  $("purpleSetupMeta").textContent = `${fmtInt(pagination.total || 0)} setup(s)`;
  $("purpleSetupBody").innerHTML = rows.map((row) => {
    const lifecycleClass = row.lifecycle_status?.startsWith("discarded") ? "points-negative" : row.lifecycle_status === "final_entry_triggered" ? "points-positive" : "points-warn";
    return `<tr>
      <td>${escapeHtml(row.setup_id)}</td>
      <td><strong>${escapeHtml(row.symbol)}</strong><div class="cell-note">${escapeHtml(purpleProfileLabel(row.purple_timeframe))}</div></td>
      <td>${fmtDateTime(row.touch_candle_timestamp)}<div class="cell-note">Purple ${fmt(row.captured_purple_ema9)}</div></td>
      <td>${fmt(row.current_price)}<div class="cell-note">${row.current_distance_percent == null ? "-" : `${fmt(row.current_distance_percent)}%`}</div></td>
      <td>${fmt(row.upper_discard_level)}<div class="cell-note">fixed at touch</div></td>
      <td>${escapeHtml(row.early_status)}<div class="cell-note">${fmtDateTime(row.early_triggered_at)}</div></td>
      <td>${escapeHtml(row.final_status)}<div class="cell-note">${fmtDateTime(row.final_triggered_at)}</div></td>
      <td>C1 ${fmt(row.candle1_low)}<div class="cell-note">C2 ${fmt(row.candle2_low)}</div></td>
      <td>${fmt(row.invalidation_level)}</td>
      <td>${fmtDateTime(row.last_checked_at)}</td>
      <td><span class="${lifecycleClass}">${escapeHtml(row.lifecycle_status)}</span><div class="cell-note">${escapeHtml(row.discard_reason || "-")}</div></td>
    </tr>`;
  }).join("") || emptyTableRow(11, "No Purple Touch setups match these filters.");
  $("purpleSetupPrev").disabled = !pagination.has_previous;
  $("purpleSetupNext").disabled = !pagination.has_next;
  $("purpleSetupPageLabel").textContent = `Page ${pagination.page || 1} of ${pagination.pages || 1} (${pagination.total || 0} rows)`;
}

function addPurpleAlertFilterParams(params, prefix, profileFilterId, entryFilterId) {
  const profile = $(profileFilterId)?.value || "all";
  const entryKind = $(entryFilterId)?.value || "all";
  if (profile !== "all") params.set(`${prefix}_profile`, profile);
  if (entryKind !== "all") params.set(`${prefix}_kind`, entryKind);
}

function addPurpleHistoryFilterParams(params, prefix, controls) {
  const status = controls.statusId ? $(controls.statusId)?.value || "all" : "all";
  const symbol = controls.symbolId ? $(controls.symbolId)?.value.trim() : "";
  const fromDate = controls.fromId ? $(controls.fromId)?.value : "";
  const toDate = controls.toId ? $(controls.toId)?.value : "";
  const [sortBy, sortOrder] = (controls.sortId ? $(controls.sortId)?.value : "created_at:desc").split(":");
  if (status !== "all" || prefix === "trade") params.set(`${prefix}_status`, status);
  if (symbol) params.set(`${prefix}_symbol`, symbol);
  if (fromDate) params.set(`${prefix}_from`, fromDate);
  if (toDate) params.set(`${prefix}_to`, toDate);
  params.set(`${prefix}_sort`, sortBy);
  params.set(`${prefix}_order`, sortOrder || "desc");
}

async function startPurpleAutoMonitor() {
  try {
    const data = await postApi("/api/krishna-purple-monitor/start", {
      intervals: Object.fromEntries(PURPLE_ACTIVE_PROFILE_KEYS.map((key) => [key, purpleIntervalSeconds(key)])),
      send_telegram: $("purpleTelegramToggle").checked,
      force: $("purpleForceToggle").checked,
    });
    renderPurpleMonitorStatus(data);
    startPurpleMonitorPolling();
    setNotes("Purple Touch candle service and independent scanners are running.");
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function stopPurpleAutoMonitor() {
  try {
    renderPurpleMonitorStatus(await postApi("/api/krishna-purple-monitor/stop", {}));
    setNotes("Purple Touch monitor stopped. Existing setup and trade history was preserved.");
  } catch (error) {
    setNotes([error.message], true);
  }
}

function startPurpleMonitorPolling() {
  if (state.purpleMonitorPollTimer) window.clearInterval(state.purpleMonitorPollTimer);
  state.purpleMonitorPollTimer = window.setInterval(loadPurpleMonitorStatus, 3000);
}

async function loadPurpleMonitorStatus() {
  try {
    const data = await api("/api/krishna-purple-monitor/status");
    renderPurpleMonitorStatus(data);
    loadPurpleSetups();
  } catch (error) {
    $("purpleDataServiceMeta").textContent = "Unavailable";
  }
}

function renderPurpleMonitorStatus(data) {
  state.purpleMonitorRunning = Boolean(data.running);
  state.purpleNextRunAt = Object.fromEntries(
    Object.entries(data.next_profile_runs || {}).map(([key, value]) => [key, new Date(value).getTime()]),
  );
  const current = data.current_refresh;
  $("purpleDataServiceMeta").textContent = data.running ? "Running" : "Stopped";
  $("purpleDataServiceStatus").textContent = current
    ? `Refreshing ${current.symbol} ${current.timeframe}; scanners continue using the previous complete cache.`
    : data.running ? "Refresh worker is ready for the next prioritized source." : "Continuous refresh is stopped.";
  $("purpleDataServiceCards").innerHTML = [
    ["Queue", data.queue_size || 0],
    ["Successful", data.refresh_successes || 0],
    ["Failures", data.refresh_failures || 0],
    ["Last success", fmtDateTime(data.last_refresh_success)],
    ["Latest failure", data.latest_failure || "None"],
    ["Active setups", data.active_setup_count || 0],
  ].map(([label, value]) => `<div class="compact-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  $("purpleFreshnessBody").innerHTML = (data.freshness || []).map((row) => `
    <tr><td>${escapeHtml(row.label)}</td><td class="points-positive">${fmtInt(row.fresh)}</td><td class="points-warn">${fmtInt(row.updating)}</td><td class="points-negative">${fmtInt(row.stale)}</td><td>${fmtInt(row.missing)}</td><td>${fmtDateTime(row.latest_candle_timestamp)}</td><td>${fmtDateTime(row.last_success_at)}</td><td>${fmtDateTime(row.expected_latest_closed_candle)}</td></tr>
  `).join("") || emptyTableRow(8, "No candle sources have been tracked yet.");

  (data.profile_runs || []).forEach((run) => {
    const profile = run.purple_timeframe;
    state.purpleProfileRuns[profile] = {
      status: run.status,
      detail: `${fmtInt(run.symbols_analyzed)} analyzed; ${fmtInt(run.setups_added)} added; ${fmtInt(run.setups_updated)} updated; ${fmtInt(run.error_count)} errors; ${run.duration_ms == null ? "duration pending" : `${(Number(run.duration_ms) / 1000).toFixed(1)} sec`}.`,
      progress: run.status === "running" ? 65 : 100,
      startedAt: run.started_at,
      endedAt: run.completed_at,
      queuedAt: run.queued_at,
      durationMs: run.duration_ms,
    };
  });
  renderPurpleProfileProgress();
  updatePurpleMonitorUi();
}

function updatePurpleMonitorUi(detail) {
  const status = $("purpleMonitorStatus");
  if (!status) return;
  const running = state.purpleMonitorRunning;
  status.classList.toggle("running", running);
  status.classList.toggle("stopped", !running);
  const title = state.purpleScanInFlight ? "Scan running" : running ? "Market monitor running" : "Scanner stopped";
  const text = detail || (running ? purpleScheduleText() : "No recurring scan is running.");
  status.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(text)}</span>`;
  $("purpleAutoStartBtn").disabled = running;
  $("purpleAutoStopBtn").disabled = !running;
  ["purpleMonthIntervalSeconds", "purpleWeekIntervalSeconds", "purpleDayIntervalSeconds"].forEach((id) => {
    $(id).disabled = running;
  });
}

function purpleIntervalSeconds(profileKey) {
  const id = { month: "purpleMonthIntervalSeconds", week: "purpleWeekIntervalSeconds", day: "purpleDayIntervalSeconds" }[profileKey];
  return Number($(id)?.value || { month: 7200, week: 1800, day: 180 }[profileKey]);
}

function purpleScheduleText() {
  const schedules = PURPLE_ACTIVE_PROFILE_KEYS.map((profileKey) => {
    const profile = PURPLE_PROFILES[profileKey];
    const seconds = purpleIntervalSeconds(profileKey);
    const minutes = seconds / 60;
    const interval = minutes >= 60 ? `${minutes / 60}h` : `${minutes}m`;
    const dueAt = state.purpleNextRunAt[profileKey];
    return `${profile.label} ${interval}${dueAt ? ` (next ${fmtDateTime(new Date(dueAt).toISOString())})` : ""}`;
  });
  return `${schedules.join("; ")}.`;
}

function renderPurpleAlerts(data = null) {
  if (data) {
    state.lastPurpleAlertResponse = data;
    state.lastPurpleAlerts = data.recent_alerts || [];
    state.lastPurpleTrades = data.trades || data.open_trades || [];
  }
  data = state.lastPurpleAlertResponse || {};
  const alerts = data.recent_alerts || [];
  const trades = data.trades || data.open_trades || [];
  const counts = data.alert_counts || {};
  const pagination = data.pagination || {};
  const recentEntryCount = alerts.filter((alert) => alert.alert_type === "entry").length;
  const recentExitCount = alerts.filter((alert) => alert.alert_type === "exit").length;
  const totalEntryCount = Number(counts.entry_alerts ?? recentEntryCount);
  const totalExitCount = Number(counts.exit_alerts ?? recentExitCount);
  const totalOpenCount = Number(counts.open_trades ?? trades.length);
  const totalClosedCount = Number(counts.closed_trades ?? totalExitCount);
  const exitAlerts = Array.isArray(data.exit_alerts)
    ? data.exit_alerts
    : filterPurpleAlertRows(alerts.filter((alert) => alert.alert_type === "exit"), "purpleExitAlertProfileFilter", "purpleExitAlertKindFilter");
  const lifecycleTrades = pagination.trades
    ? trades
    : filterPurpleAlertRows(trades, "purpleTradeProfileFilter", "purpleTradeEntryFilter");
  const exitFilteredTotal = Number(pagination.exit?.total ?? totalExitCount);
  const tradeFilteredTotal = Number(pagination.trades?.total ?? totalOpenCount);
  $("purpleExitAlertMeta").textContent = `${exitAlerts.length} shown / ${exitFilteredTotal} filtered / ${totalExitCount} total exit alert(s)`;
  const totalLifecycleCount = totalOpenCount + totalClosedCount;
  $("purpleOpenTradeMeta").textContent = `${lifecycleTrades.length} shown / ${tradeFilteredTotal} filtered / ${totalLifecycleCount} total trade(s)`;
  updatePurpleServerPager("exit", pagination.exit, exitAlerts.length);
  updatePurpleServerPager("trades", pagination.trades, lifecycleTrades.length);
  $("purpleAlertTallyCards").innerHTML = [
    ["Entry signals created", totalEntryCount],
    ["Open entries", totalOpenCount],
    ["Closed entries", totalClosedCount],
    ["Exit signals created", totalExitCount],
  ]
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${fmtInt(value)}</strong></div>`)
    .join("");
  $("purpleAlertSummary").innerHTML = [
    `Profiles scanned: ${profileList(data)}`,
    `Market hours: ${data.market_hours === true ? "Open" : data.market_hours === false ? "Closed" : "-"}`,
    data.alert_creation_skipped && data.next_market_open
      ? `Analysis completed; live entry/exit alert creation resumes at ${fmtDateTime(data.next_market_open)}`
      : "",
    `Entry alerts created this run: ${fmtMetric(data.entry_alerts_created || 0)}`,
    `Exit alerts created this run: ${fmtMetric(data.exit_alerts_created || 0)}`,
    `Entry trade rows loaded on this page: ${fmtMetric(lifecycleTrades.length)} of ${fmtMetric(tradeFilteredTotal)} filtered (${fmtMetric(totalOpenCount)} open, ${fmtMetric(totalClosedCount)} closed).`,
    `Web UI alert history loaded: latest ${alerts.length} event(s) of ${fmtMetric(totalEntryCount + totalExitCount)} total`,
    data.retention?.automatic_purge === false ? "Purple Touch history retention: no automatic purge; use date/status filters to review retained records." : "",
    `Telegram: ${telegramText(data.telegram)}`,
  ]
    .filter(Boolean)
    .map((point) => `<div>${escapeHtml(point)}</div>`)
    .join("");
  const entryTallyMatches = counts.entry_tally_matches ?? totalEntryCount === totalOpenCount + totalClosedCount;
  const exitTallyMatches = counts.exit_tally_matches ?? totalExitCount === totalClosedCount;
  $("purpleAlertTallyPoints").innerHTML = [
    `Entry tally: ${totalEntryCount} total entries = ${totalOpenCount} open + ${totalClosedCount} closed ${entryTallyMatches ? "(matches)" : "(does not match)"}.`,
    `Exit tally: ${totalExitCount} exit alerts = ${totalClosedCount} closed trade IDs ${exitTallyMatches ? "(matches)" : "(does not match)"}.`,
    `The Entry and Exit tables load only the latest ${data.recent_alert_limit || 50} alert events, so their visible rows may be lower than all-time totals.`,
  ]
    .map((point) => `<div>${escapeHtml(point)}</div>`)
    .join("");
  $("purpleExitAlertsBody").innerHTML = renderPurpleAlertTableRows(exitAlerts, "No exit alerts match these filters.", "alert-exit-row");
  $("purpleTradesBody").innerHTML = lifecycleTrades.length
    ? lifecycleTrades
    .map(
      (trade) => `
        <tr>
          <td><code>${escapeHtml(trade.trade_id || "-")}</code></td>
          <td>${escapeHtml(trade.symbol || "-")}</td>
          <td>${escapeHtml(purpleProfileLabel(trade.purple_timeframe))}</td>
          <td>${escapeHtml(trade.entry_kind || "-")}<div class="cell-note">${escapeHtml(trade.entry_timeframe || "-")}</div></td>
          <td><span class="${trade.status === "open" ? "points-warn" : "points-positive"}">${escapeHtml(trade.status || "-")}</span></td>
          <td>${fmtDateTime(trade.opened_at)}</td>
          <td>${fmtDateTime(trade.closed_at)}</td>
          <td>${escapeHtml(durationBetween(trade.opened_at, trade.closed_at))}</td>
          <td>${fmt(trade.entry_price)}</td>
          <td>${fmt(trade.exit_price)}</td>
          <td>${escapeHtml(trade.exit_timeframe || "-")}</td>
        </tr>
      `,
    )
    .join("")
    : emptyTableRow(11, "No open or closed entry trades match these filters.");
  document.querySelectorAll("#purpleExitAlertsBody .linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      activateTab("analyze");
      analyze();
    });
  });
}

function filterPurpleAlertRows(rows, profileFilterId, entryFilterId) {
  const profile = $(profileFilterId)?.value || "all";
  const entryKind = $(entryFilterId)?.value || "all";
  return (rows || []).filter((row) => {
    if (profile !== "all" && row.purple_timeframe !== profile) return false;
    if (entryKind !== "all" && row.entry_kind !== entryKind) return false;
    return true;
  });
}

function updatePurpleServerPager(pageKey, pageInfo, shown) {
  const prefix = { exit: "purpleExit", trades: "purpleTrade" }[pageKey];
  if (!prefix) return;
  const page = Number(pageInfo?.page || state.purplePages[pageKey] || 1);
  const pages = Number(pageInfo?.pages || 1);
  const total = Number(pageInfo?.total ?? shown ?? 0);
  state.purplePages[pageKey] = page;
  updatePurplePager(prefix, page, pages, total);
}

function renderPurpleAlertTableRows(rows, emptyMessage, rowClass) {
  if (!rows.length) return emptyTableRow(8, emptyMessage);
  return rows
    .map(
      (alert) => `
        <tr class="${rowClass}">
          <td><code>${escapeHtml(alert.trade_id || "-")}</code></td>
          <td>${fmtDateTime(alert.created_at)}</td>
          <td><button class="linkBtn symbol-chip" data-symbol="${escapeHtml(alert.symbol || "")}">${escapeHtml(alert.symbol || "-")}</button></td>
          <td>${escapeHtml(purpleProfileLabel(alert.purple_timeframe))}</td>
          <td>${escapeHtml(alert.entry_kind || "-")}</td>
          <td>${escapeHtml(alert.trade_status || alert.status || "-")}</td>
          <td>${fmt(alert.price)} / ${fmt(alert.yellow_line)}</td>
          <td>${escapeHtml(alert.message || "-")}</td>
        </tr>
      `,
    )
    .join("");
}

function profileList(data) {
  const profiles = data.profiles || [];
  if (!profiles.length) return data.purple_timeframe || "-";
  return profiles.map((profile) => profile.label || profile.purple_timeframe).join(", ");
}

function purpleProfileLabel(value) {
  return { month: "Monthly", week: "Weekly", day: "Daily", all: "All" }[value] || value || "-";
}

function telegramText(status) {
  if (!status) return "not checked";
  if (status.configured === false || (!status.configured && !status.enabled)) return "not configured";
  if (!status.enabled) return "configured; disabled for this scan";
  const errors = status.errors || [];
  if (errors.length) return `${status.sent || 0} sent, ${errors.length} error(s)`;
  return `${status.sent || 0} sent`;
}

function openDuration(openedAt) {
  return durationBetween(openedAt, null);
}

function durationBetween(openedAt, closedAt = null) {
  if (!openedAt) return "-";
  const opened = parseDateTime(openedAt);
  const ended = closedAt ? parseDateTime(closedAt) : new Date();
  if (Number.isNaN(opened.getTime()) || Number.isNaN(ended.getTime())) return "-";
  const minutes = Math.max(0, Math.floor((ended.getTime() - opened.getTime()) / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days) return `${days}d ${hours}h ${rest}m`;
  if (hours) return `${hours}h ${rest}m`;
  return `${rest}m`;
}

function setPurpleBacktestDefaults() {
  const to = new Date();
  const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000);
  const localDate = (value) => {
    const offset = value.getTimezoneOffset() * 60 * 1000;
    return new Date(value.getTime() - offset).toISOString().slice(0, 10);
  };
  if (!$('purpleBtTo').value) $('purpleBtTo').value = localDate(to);
  if (!$('purpleBtFrom').value) $('purpleBtFrom').value = localDate(from);
}

async function runPurpleBacktest() {
  const profiles = ["week"];
  if (!profiles.length) {
    setNotes("Select at least one Purple Touch profile.", true);
    return;
  }
  if (!$('purpleBtFrom').value || !$('purpleBtTo').value) {
    setNotes("Select both From and To dates for the Purple Touch backtest.", true);
    return;
  }
  const symbolsText = $('purpleBtSymbols').value.trim();
  const payload = {
    symbols: symbolsText ? symbolsText.split(",").map((value) => value.trim()).filter(Boolean) : null,
    limit_symbols: Number($('purpleBtLimit').value || 5),
    params: {
      from_date: $('purpleBtFrom').value,
      to_date: $('purpleBtTo').value,
      profiles,
      entry_mode: $('purpleBtEntryMode').value,
      touch_tolerance_percent: Number($('purpleBtDistance').value),
      min_score: Number($('purpleBtMinScore').value),
      stop_mode: $('purpleBtStopMode').value,
      stop_percent: Number($('purpleBtStopPercent').value),
      target_r_multiple: Number($('purpleBtTargetR').value),
      max_holding_bars: Number($('purpleBtMaxHold').value),
      capital: Number($('purpleBtCapital').value),
      risk_per_trade_percent: Number($('purpleBtRiskPercent').value),
      slippage_bps: Number($('purpleBtSlippage').value),
      costs_bps: Number($('purpleBtCosts').value),
    },
  };
  $('purpleBacktestRunBtn').disabled = true;
  $('purpleBacktestStatus').textContent = "Running historical simulation from cached candles...";
  $('purpleBacktestMeta').textContent = "Running";
  setNotes("Running Purple Touch historical simulation. Larger symbol/date selections may take several minutes.");
  try {
    const data = await postApi("/api/krishna-purple-touch-backtest", payload);
    state.lastPurpleBacktest = data;
    renderPurpleBacktest(data);
    $('purpleBacktestDownloadBtn').disabled = !(data.trades || []).length;
    setNotes(`Purple Touch backtest completed with ${data.trade_count || 0} historical trade(s).`);
  } catch (error) {
    $('purpleBacktestMeta').textContent = "Failed";
    $('purpleBacktestStatus').textContent = error.message;
    setNotes(error.message, true);
  } finally {
    $('purpleBacktestRunBtn').disabled = false;
  }
}

function renderPurpleBacktest(data) {
  const metrics = data.metrics || {};
  $('purpleBacktestMeta').textContent = `${fmtInt(data.analyzed_symbols || 0)} symbols / ${fmtInt(data.signal_count || 0)} signals / ${fmtInt(data.trade_count || 0)} trades`;
  $('purpleBacktestStatus').textContent = `Completed. ${fmtInt(data.suppressed_entries || 0)} entry event(s) were suppressed by open-trade/final-entry precedence.`;
  const cards = [
    ["Trades", fmtInt(metrics.trades || 0)],
    ["Win rate", fmtPct(metrics.win_rate_percent)],
    ["Average return", fmtPct(metrics.average_return_percent)],
    ["Average R", metrics.average_r_multiple === null || metrics.average_r_multiple === undefined ? "-" : `${fmt(metrics.average_r_multiple)}R`],
    ["Profit factor", fmt(metrics.profit_factor)],
    ["Expectancy", fmtPct(metrics.expectancy_percent)],
    ["Max drawdown", fmtPct(metrics.max_drawdown_percent)],
    ["Aggregate return", fmtPct(metrics.ending_return_percent)],
    ["Target", metrics.target_r_multiple === null || metrics.target_r_multiple === undefined ? "-" : `${fmt(metrics.target_r_multiple)}R`],
    ["Break-even win rate", fmtPct(metrics.break_even_win_rate_percent)],
    ["Sample", metrics.sample_quality === "useful" ? "30+ trades" : "Small (<30)"],
  ];
  $('purpleBacktestCards').innerHTML = cards
    .map(([label, value]) => `<div class="compact-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
  $('purpleBacktestNotes').innerHTML = [
    ...((data.summary && data.summary.points) || []),
    ...((data.summary && data.summary.limitations) || []),
    ...(data.errors || []).slice(0, 10).map((row) => `${row.symbol || ""} ${row.profile || row.timeframe || ""}: ${row.error}`.trim()),
  ].map((point) => `<div>${escapeHtml(point)}</div>`).join("");
  $('purpleBacktestProfileBody').innerHTML = purpleBacktestPerformanceRows(data.profile_performance || [], "profile", "No profile trades were generated.");
  $('purpleBacktestEntryBody').innerHTML = purpleBacktestPerformanceRows(data.entry_performance || [], "entry_kind", "No entry-type trades were generated.");
  $('purpleBacktestCoverageBody').innerHTML = (data.coverage || []).length
    ? data.coverage.map((row) => `
      <tr>
        <td>${escapeHtml(row.symbol)}</td>
        <td>${escapeHtml(purpleProfileLabel(row.profile))}</td>
        <td><span class="status-badge status-${escapeHtml(row.status)}">${escapeHtml(row.status)}</span></td>
        <td>${fmtDateTime(row.from)}</td>
        <td>${fmtDateTime(row.to)}</td>
        <td>${escapeHtml((row.missing_timeframes || []).join(", ") || "-")}</td>
      </tr>`).join("")
    : emptyTableRow(6, "No coverage rows are available.");
  const trades = data.trades || [];
  $('purpleBacktestTradesBody').innerHTML = trades.length
    ? trades.slice(0, 250).map((trade) => `
      <tr>
        <td>${escapeHtml(trade.symbol)}</td>
        <td>${escapeHtml(purpleProfileLabel(trade.profile))}</td>
        <td>${escapeHtml(trade.entry_kind)}</td>
        <td>${fmtDateTime(trade.entry_time)}</td>
        <td>${fmtDateTime(trade.exit_time)}<div class="cell-note">${escapeHtml(durationMinutes(trade.holding_minutes))}</div></td>
        <td>${fmt(trade.entry_price)} / ${fmt(trade.stop_loss)} / ${fmt(trade.target)}</td>
        <td>${fmt(trade.exit_price)}</td>
        <td class="${pointClass(trade.net_return_percent)}">${fmtPct(trade.net_return_percent)}</td>
        <td class="${pointClass(trade.r_multiple)}">${trade.r_multiple === null || trade.r_multiple === undefined ? "-" : `${fmt(trade.r_multiple)}R`}</td>
        <td>${escapeHtml(trade.exit_reason)}${trade.intrabar_ambiguous ? '<div class="cell-note">stop/target same bar; stop first</div>' : ""}</td>
      </tr>`).join("")
    : emptyTableRow(10, "No historical trades matched these settings.");
}

function purpleBacktestPerformanceRows(rows, key, emptyMessage) {
  if (!rows.length) return emptyTableRow(6, emptyMessage);
  return rows.map((row) => `
    <tr>
      <td>${escapeHtml(key === "profile" ? purpleProfileLabel(row[key]) : row[key])}</td>
      <td>${fmtInt(row.trades)}</td>
      <td>${fmtPct(row.win_rate_percent)}</td>
      <td class="${pointClass(row.average_return_percent)}">${fmtPct(row.average_return_percent)}</td>
      <td class="${pointClass(row.average_r_multiple)}">${row.average_r_multiple === null || row.average_r_multiple === undefined ? "-" : `${fmt(row.average_r_multiple)}R`}</td>
      <td>${fmt(row.profit_factor)}</td>
    </tr>`).join("");
}

function durationMinutes(value) {
  const minutes = Number(value || 0);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = Math.floor(minutes % 60);
  if (days) return `${days}d ${hours}h ${rest}m`;
  if (hours) return `${hours}h ${rest}m`;
  return `${rest}m`;
}

function downloadPurpleBacktestTrades() {
  const rows = state.lastPurpleBacktest?.trades || [];
  downloadCsv("purple_touch_backtest_trades.csv", rows, [
    { label: "Symbol", value: (row) => row.symbol },
    { label: "Profile", value: (row) => row.profile },
    { label: "Entry Type", value: (row) => row.entry_kind },
    { label: "Signal Time", value: (row) => row.signal_time },
    { label: "Entry Time", value: (row) => row.entry_time },
    { label: "Exit Time", value: (row) => row.exit_time },
    { label: "Entry", value: (row) => row.entry_price },
    { label: "Stop", value: (row) => row.stop_loss },
    { label: "Target", value: (row) => row.target },
    { label: "Exit", value: (row) => row.exit_price },
    { label: "Exit Reason", value: (row) => row.exit_reason },
    { label: "Net Return %", value: (row) => row.net_return_percent },
    { label: "R Multiple", value: (row) => row.r_multiple },
    { label: "Holding Minutes", value: (row) => row.holding_minutes },
    { label: "Score", value: (row) => row.score },
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

async function loadNiftyAutoStatus() {
  try {
    const data = await api("/api/nifty/auto/status");
    state.nifty.autoStatus = data;
    renderNiftyAutoStatus(data);
  } catch (error) {
    $("niftyAutoMeta").textContent = "Failed";
  }
}

async function loadNiftyAlerts() {
  try {
    const data = await api("/api/nifty/alerts?limit=50");
    state.nifty.alerts = data.alerts || [];
    renderNiftyAlerts(data);
  } catch (error) {
    $("niftyAlertsMeta").textContent = "Failed";
  }
}

async function loadNiftyLatestData() {
  try {
    const data = await api("/api/nifty/data/latest");
    state.nifty.latestData = data;
    renderNiftyLatestData(data);
  } catch (error) {
    const target = $("niftyDataFreshnessCards");
    if (target) {
      target.innerHTML = `<div class="compact-metric"><span>Data Freshness</span><strong>Failed</strong></div>`;
    }
  }
}

async function startNiftyAutoScan() {
  $("niftyAutoMeta").textContent = "Starting";
  try {
    const data = await postApi("/api/nifty/auto/start", {});
    state.nifty.autoStatus = data;
    renderNiftyAutoStatus(data);
    startNiftyAutoPolling();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function stopNiftyAutoScan() {
  $("niftyAutoMeta").textContent = "Stopping";
  try {
    const data = await postApi("/api/nifty/auto/stop", {});
    state.nifty.autoStatus = data;
    renderNiftyAutoStatus(data);
    stopNiftyAutoPolling();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function runNiftyAutoOnce() {
  $("niftyAutoMeta").textContent = "Running one cycle";
  try {
    const data = await postApi("/api/nifty/auto/run-once", { force: true });
    state.nifty.autoStatus = data;
    renderNiftyAutoStatus(data);
    await loadNiftyAlerts();
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function acknowledgeNiftyAlert(alertId) {
  try {
    await postApi(`/api/nifty/alerts/${alertId}/ack`, {});
    await loadNiftyAlerts();
    await loadNiftyAutoStatus();
  } catch (error) {
    setNotes([error.message], true);
  }
}

function startNiftyAutoPolling() {
  if (state.nifty.autoPollTimer) return;
  state.nifty.autoPollTimer = window.setInterval(() => {
    loadNiftyAutoStatus();
    loadNiftyAlerts();
    loadNiftyContextSnapshots();
    loadNiftyLatestData();
  }, 20000);
}

function stopNiftyAutoPolling() {
  if (!state.nifty.autoPollTimer) return;
  window.clearInterval(state.nifty.autoPollTimer);
  state.nifty.autoPollTimer = null;
}

function renderNiftyAutoStatus(data) {
  const running = Boolean(data.running);
  $("niftyAutoMeta").textContent = `${running ? "Running" : "Stopped"} / market ${data.market_hours ? "open" : "closed"}`;
  $("niftyAutoCards").innerHTML = [
    ["Status", running ? "Running" : "Stopped"],
    ["Market Hours", data.market_hours ? "Open" : "Closed"],
    ["Last Run", fmtDateTime(data.last_job_run)],
    ["Active Alerts", fmtInt(data.active_alerts_count)],
  ]
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("niftyAutoJobBody").innerHTML = (data.recent_jobs || [])
    .slice(0, 12)
    .map((job) => `
      <tr>
        <td>${job.job_name}</td>
        <td><span class="status-badge status-${statusKey(job.status)}">${statusLabel(job.status)}</span></td>
        <td>${fmtDateTime(job.started_at)}</td>
        <td>${job.duration_ms === null || job.duration_ms === undefined ? "-" : `${job.duration_ms} ms`}</td>
        <td>${autoJobSummary(job)}</td>
      </tr>
    `)
    .join("");
}

function renderNiftyLatestData(data) {
  const candles = data.latest_candles || {};
  const option = data.latest_option_snapshot || {};
  const iv = data.latest_iv_observation || {};
  const counts = data.counts || {};
  const candleCounts = counts.candles || {};
  $("niftyDataFreshnessCards").innerHTML = [
    ["Latest 15m Candle", fmtDateTime(candles["15minute"])],
    ["Latest Option Snapshot", option.captured_at ? `#${option.id} / ${fmtDateTime(option.captured_at)}` : "-"],
    ["Latest ATM IV", iv.captured_at ? `${fmt(iv.atm_iv)} / ${fmtDateTime(iv.captured_at)}` : "-"],
    ["Context Snapshots", fmtInt(counts.context_snapshots)],
    ["Alerts", fmtInt(counts.alerts)],
    ["DB Candle Rows", Object.entries(candleCounts).map(([frame, count]) => `${frame}: ${fmtInt(count)}`).join(", ") || "-"],
  ]
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function renderNiftyAlerts(data) {
  const alerts = data.alerts || [];
  $("niftyAlertsMeta").textContent = `${alerts.length} shown / ${fmtInt(data.active_count)} active`;
  $("niftyAlertsBody").innerHTML = alerts
    .map((alert) => `
      <tr class="alert-${statusKey(alert.severity)}">
        <td>${fmtDateTime(alert.created_at)}</td>
        <td><span class="status-badge status-${statusKey(alert.severity)}">${statusLabel(alert.severity)}</span></td>
        <td>${alert.direction || "-"}</td>
        <td>${alert.horizon || alert.mode || "-"}</td>
        <td>${alert.strategy_id || "-"}</td>
        <td>${alert.context_snapshot_id || "-"}</td>
        <td>${fmt(alert.score)}</td>
        <td>${escapeHtml(alert.title)}</td>
        <td>${escapeHtml(alert.message)}</td>
        <td>${escapeHtml((alert.reasons || []).join("; ") || "-")}</td>
        <td>${escapeHtml((alert.risks || []).join("; ") || "-")}</td>
        <td>${fmt(alert.trigger_level)}</td>
        <td>${fmt(alert.invalidation_level)}</td>
        <td>
          ${alert.context_snapshot_id ? `<button class="linkBtn nifty-context-view" data-context-id="${alert.context_snapshot_id}">View Context</button>` : ""}
          ${alert.is_active ? `<button class="linkBtn nifty-alert-ack" data-alert-id="${alert.id}">Acknowledge</button>` : "Ack"}
        </td>
      </tr>
    `)
    .join("");
  document.querySelectorAll(".nifty-alert-ack").forEach((button) => {
    button.addEventListener("click", () => acknowledgeNiftyAlert(Number(button.dataset.alertId)));
  });
  document.querySelectorAll(".nifty-context-view").forEach((button) => {
    button.addEventListener("click", () => viewNiftyContextSnapshot(Number(button.dataset.contextId)));
  });
}

async function runNiftyAlertBacktest() {
  $("niftyAlertBacktestMeta").textContent = "Running";
  const params = new URLSearchParams({
    timeframe: $("niftyAlertBacktestFrame").value,
    limit: $("niftyAlertBacktestLimit").value || "500",
    horizons: "3,5,10,15",
  });
  try {
    const data = await api(`/api/nifty/alerts/backtest?${params.toString()}`);
    state.nifty.alertBacktest = data;
    renderNiftyAlertBacktest(data);
    await loadNiftyAlerts();
  } catch (error) {
    $("niftyAlertBacktestMeta").textContent = "Failed";
    setNotes([error.message], true);
  }
}

function renderNiftyAlertBacktest(data) {
  const overall = (data.metrics && data.metrics.overall) || {};
  $("niftyAlertBacktestMeta").textContent = `${data.evaluated_alerts || 0} alert(s) evaluated / ${data.timeframe}`;
  $("niftyAlertBacktestCards").innerHTML = [
    ["Alerts", fmtInt(data.alert_count)],
    ["Rows", fmtInt(overall.signals)],
    ["Accuracy", fmtPct(overall.accuracy)],
    ["Avg Directional", fmtPct(overall.avg_directional_return)],
    ["Saved Outcomes", fmtInt(data.saved_outcomes)],
  ]
    .map(([label, value]) => `<div class="compact-metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  const byHold = (data.metrics && data.metrics.by_holding_bars) || [];
  $("niftyAlertBacktestMetricBody").innerHTML = byHold
    .map((row) => `
      <tr>
        <td>${row.group} candle(s)</td>
        <td>${fmtInt(row.signals)}</td>
        <td>${fmtPct(row.accuracy)}</td>
        <td>${fmtPct(row.avg_directional_return)}</td>
        <td>${fmtPct(row.avg_max_favorable)}</td>
        <td>${fmtPct(row.avg_max_adverse)}</td>
      </tr>
    `)
    .join("");
  $("niftyAlertBacktestBody").innerHTML = (data.rows || [])
    .slice(0, 50)
    .map((row) => `
      <tr>
        <td>${fmtDateTime(row.alert_time)}</td>
        <td>${row.horizon || "-"}</td>
        <td>${row.strategy_id || "-"}</td>
        <td>${row.direction || "-"}</td>
        <td>${row.holding_bars || "-"}</td>
        <td>${fmt(row.entry_price)}</td>
        <td>${fmt(row.exit_price)}</td>
        <td>${fmtPct(row.forward_return_percent)}</td>
        <td>${fmtPct(row.directional_return_percent)}</td>
        <td>${row.status}${row.reason ? `: ${escapeHtml(row.reason)}` : ""}</td>
      </tr>
    `)
    .join("");
  if ((data.warnings || []).length) setNotes(data.warnings);
}

async function loadNiftyContextSnapshots() {
  try {
    const data = await api("/api/nifty/context-snapshots?limit=10");
    state.nifty.snapshots = data.snapshots || [];
    renderNiftyContextSnapshots(data);
  } catch (error) {
    $("niftySnapshotMeta").textContent = "Failed";
  }
}

async function viewNiftyContextSnapshot(contextId) {
  try {
    const data = await api(`/api/nifty/context-snapshots/${contextId}`);
    const snapshot = data.snapshot || {};
    const points = (snapshot.summary && snapshot.summary.points) || [];
    setNotes([
      `Context #${contextId}: spot ${fmt(snapshot.spot)}, intraday ${snapshot.intraday_bias}, swing ${snapshot.swing_bias}, positional ${snapshot.positional_bias}, option ${snapshot.option_bias}, IV ${snapshot.iv_regime}.`,
      ...points,
    ]);
  } catch (error) {
    setNotes([error.message], true);
  }
}

function renderNiftyContextSnapshots(data) {
  const rows = data.snapshots || [];
  $("niftySnapshotMeta").textContent = `${rows.length} shown`;
  $("niftySnapshotBody").innerHTML = rows
    .map((row) => `
      <tr>
        <td>${row.id}</td>
        <td>${fmtDateTime(row.captured_at)}</td>
        <td>${row.mode || "-"}</td>
        <td>${fmt(row.spot)}</td>
        <td>${row.intraday_bias || "-"}</td>
        <td>${row.swing_bias || "-"}</td>
        <td>${row.positional_bias || "-"}</td>
        <td>${row.option_bias || "-"}</td>
        <td>${row.iv_regime || "-"}</td>
      </tr>
    `)
    .join("");
}

function autoJobSummary(job) {
  if (job.error) return escapeHtml(job.error);
  const result = job.result && job.result.result ? job.result.result : job.result || {};
  if (result.alerts_created !== undefined) {
    return `${result.alerts_created} alert(s), ${result.alerts_suppressed || 0} duplicate(s) suppressed`;
  }
  if (result.candle_sources) {
    return candleSourceText(result.candle_sources);
  }
  if (result.cached_snapshots !== undefined) {
    return `${result.cached_snapshots} cached snapshot(s)`;
  }
  if (result.iv_regime) {
    return `IV ${result.iv_regime}, rank ${fmt(result.iv_rank)}`;
  }
  if (result.deleted !== undefined) {
    return `${result.deleted} old job row(s) removed`;
  }
  return "-";
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
  const date = parseDateTime(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toLocaleString("en-IN", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Kolkata",
  })} IST`;
}

function parseDateTime(value) {
  const raw = String(value).trim();
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const normalized = hasTimezone
    ? raw
    : /^\d{4}-\d{2}-\d{2}$/.test(raw)
      ? `${raw}T00:00:00+05:30`
      : `${raw.replace(" ", "T")}+05:30`;
  return new Date(normalized);
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
    const panel = head.closest(".table-panel");
    const initiallyCollapsed = panel.classList.contains("collapsed");
    button.className = "collapse-btn";
    button.type = "button";
    button.textContent = initiallyCollapsed ? "+" : "-";
    button.title = initiallyCollapsed ? "Expand section" : "Collapse section";
    button.setAttribute("aria-expanded", initiallyCollapsed ? "false" : "true");
    button.setAttribute("aria-controls", `panel-body-${index}`);
    const wrapper = head.querySelector(".panel-head-title") || head;
    wrapper.insertBefore(button, wrapper.firstChild);
    button.addEventListener("click", () => {
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
$("purpleLiveScanBtn").addEventListener("click", () => runPurpleLiveScan());
$("purpleCancelScanBtn").addEventListener("click", cancelPurpleCurrentScan);
$("purpleAutoStartBtn").addEventListener("click", startPurpleAutoMonitor);
$("purpleAutoStopBtn").addEventListener("click", stopPurpleAutoMonitor);
$("purpleCopyBtn").addEventListener("click", copyPurpleSymbols);
$("purpleDownloadBtn").addEventListener("click", downloadPurpleCsv);
$("purpleStep1ProfileFilter").addEventListener("change", () => { state.purplePages.step1 = 1; renderPurpleStep1Results(); });
$("purpleStep1EntryFilter").addEventListener("change", () => { state.purplePages.step1 = 1; renderPurpleStep1Results(); });
$("purpleStep1StatusFilter").addEventListener("change", () => { state.purplePages.step1 = 1; renderPurpleStep1Results(); });
$("purpleStep1DistanceFilter").addEventListener("change", () => { state.purplePages.step1 = 1; renderPurpleStep1Results(); });
$("purpleStep1Sort").addEventListener("change", () => { state.purplePages.step1 = 1; renderPurpleStep1Results(); });
$("purpleCandidateProfileFilter").addEventListener("change", () => { state.purplePages.candidates = 1; renderPurpleResults(); });
$("purpleCandidateEntryFilter").addEventListener("change", () => { state.purplePages.candidates = 1; renderPurpleResults(); });
$("purpleCandidateDistanceFilter").addEventListener("change", () => { state.purplePages.candidates = 1; renderPurpleResults(); });
$("purpleCandidateSort").addEventListener("change", () => { state.purplePages.candidates = 1; renderPurpleResults(); });
[
  ["purpleExitAlertProfileFilter", "exit"],
  ["purpleExitAlertKindFilter", "exit"],
  ["purpleExitAlertSymbolFilter", "exit"],
  ["purpleExitAlertFromFilter", "exit"],
  ["purpleExitAlertToFilter", "exit"],
  ["purpleExitAlertSort", "exit"],
  ["purpleTradeProfileFilter", "trades"],
  ["purpleTradeEntryFilter", "trades"],
  ["purpleTradeStatusFilter", "trades"],
  ["purpleTradeSymbolFilter", "trades"],
  ["purpleTradeFromFilter", "trades"],
  ["purpleTradeToFilter", "trades"],
  ["purpleTradeSort", "trades"],
].forEach(([id, pageKey]) => $(id).addEventListener("change", () => {
  state.purplePages[pageKey] = 1;
  loadPurpleAlerts();
}));
$("purpleStep1Prev").addEventListener("click", () => { state.purplePages.step1 -= 1; renderPurpleStep1Results(); });
$("purpleStep1Next").addEventListener("click", () => { state.purplePages.step1 += 1; renderPurpleStep1Results(); });
$("purpleCandidatePrev").addEventListener("click", () => { state.purplePages.candidates -= 1; renderPurpleResults(); });
$("purpleCandidateNext").addEventListener("click", () => { state.purplePages.candidates += 1; renderPurpleResults(); });
$("purpleExitPrev").addEventListener("click", () => { state.purplePages.exit -= 1; loadPurpleAlerts(); });
$("purpleExitNext").addEventListener("click", () => { state.purplePages.exit += 1; loadPurpleAlerts(); });
$("purpleTradePrev").addEventListener("click", () => { state.purplePages.trades -= 1; loadPurpleAlerts(); });
$("purpleTradeNext").addEventListener("click", () => { state.purplePages.trades += 1; loadPurpleAlerts(); });
[
  "purpleSetupProfileFilter", "purpleSetupStatusFilter", "purpleSetupSymbolFilter",
  "purpleSetupEarlyFilter", "purpleSetupFinalFilter", "purpleSetupSort",
].forEach((id) => $(id).addEventListener("change", () => {
  state.purplePages.setups = 1;
  loadPurpleSetups();
}));
$("purpleSetupPrev").addEventListener("click", () => { state.purplePages.setups -= 1; loadPurpleSetups(); });
$("purpleSetupNext").addEventListener("click", () => { state.purplePages.setups += 1; loadPurpleSetups(); });
$("purpleBacktestRunBtn").addEventListener("click", runPurpleBacktest);
$("purpleBacktestDownloadBtn").addEventListener("click", downloadPurpleBacktestTrades);
$("genericStrategySelect").addEventListener("change", populateStrategyParams);
$("genericBacktestRunBtn").addEventListener("click", runGenericBacktest);
$("backtestRunBtn").addEventListener("click", runKrishnaBacktest);
$("backtestDownloadTradesBtn").addEventListener("click", downloadBacktestTrades);
$("backtestDownloadSignalsBtn").addEventListener("click", downloadBacktestSignals);
$("niftyRunContextBtn").addEventListener("click", runNiftyContext);
$("niftySuggestBtn").addEventListener("click", runNiftySuggestions);
$("niftyAutoStartBtn").addEventListener("click", startNiftyAutoScan);
$("niftyAutoStopBtn").addEventListener("click", stopNiftyAutoScan);
$("niftyAutoRunOnceBtn").addEventListener("click", runNiftyAutoOnce);
$("niftyAlertBacktestBtn").addEventListener("click", runNiftyAlertBacktest);
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
updatePurpleDefaults();
setPurpleBacktestDefaults();
updatePurpleMonitorUi();
renderPurpleProfileProgress();
loadPurpleAlerts();
loadPurpleSetups();
loadPurpleMonitorStatus();
startPurpleMonitorPolling();

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
