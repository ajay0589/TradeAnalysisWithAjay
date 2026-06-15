const state = {
  symbols: [],
  lastAnalysis: null,
  bulkJobId: null,
  bulkPollTimer: null,
};

const $ = (id) => document.getElementById(id);

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

function fmt(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return value.toFixed(2);
  return String(value);
}

function fmtInt(value) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("en-IN");
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
  return params;
}

async function api(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function postApi(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
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
  list.innerHTML = "";
  data.symbols.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.symbol;
    option.label = row.name || row.symbol;
    list.appendChild(option);
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
  const windowDays = job.window && job.window.days ? `${job.window.days} days` : "";
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
  try {
    const params = scanParams();
    params.set("type", type);
    const data = await api(`/api/scan?${params.toString()}`);
    $("scanTitle").textContent = `${capitalize(type)} candidates`;
    $("scanMeta").textContent = `${data.results.length} shown / ${data.matched_symbols} matched / ${data.available_symbols} ready / ${data.timeframe_label}`;
    renderScan(data.results);
    setNotes(`${data.strategy}. Verify option chain, liquidity, event risk, and risk/reward before trade.`);
  } catch (error) {
    setNotes([error.message], true);
  }
}

function renderAnalysis(data) {
  $("biasValue").textContent = data.decision.bias;
  $("biasValue").className = data.decision.bias;
  $("scoreValue").textContent = data.decision.score;
  $("strategyValue").textContent = data.setup.strategy;
  $("decisionValue").textContent = data.decision.decision;
  $("structureMeta").textContent = `${data.chart.label} / ${data.chart.candle_count} candles`;

  renderMultiTimeframe(data.multi_timeframe);
  renderOptionGuide(data.option_trade_guide);
  renderCoverage(data.analysis_summary);
  $("structureBody").innerHTML = [
    structureRow(data.chart.label, data.chart.technical, data.chart.structure),
    structureRow("60m", data.hourly.technical, data.hourly.structure),
  ].join("");

  renderRelativeStrength(data.relative_strength);
  renderOptionChain(data.option_chain);
  renderSnapshotStatus(data.option_snapshot);
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
  const missing = summary.rows.filter((row) => ["missing", "failed", "not_analyzed", "not_requested"].includes(row.status)).length;
  $("coverageMeta").textContent = `${summary.timeframe_label} / ${analyzed} analyzed / ${pulled} pulled / ${missing} skipped or missing`;
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

function structureRow(frame, technical, structure) {
  if (!technical || !structure) {
    return `<tr><td>${frame}</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>`;
  }
  return `
    <tr>
      <td>${frame}</td>
      <td>${fmt(technical.close)}</td>
      <td>${technical.trend}</td>
      <td>${structure.trend}</td>
      <td>${fmt(structure.support)}</td>
      <td>${fmt(structure.resistance)}</td>
      <td>${fmt(structure.invalidation)}</td>
    </tr>
  `;
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
      (row) => `
        <tr>
          <td><button class="linkBtn" data-symbol="${row.symbol}">${row.symbol}</button></td>
          <td>${row.score}</td>
          <td class="${row.bias}">${row.bias}</td>
          <td>${row.strategy}</td>
          <td>${fmt(row.close)}</td>
          <td>${row.timeframe}: ${row.daily_trend} / ${row.daily_structure}</td>
          <td>${fmt(row.support)}</td>
          <td>${fmt(row.resistance)}</td>
          <td>${row.option_zone || "-"}</td>
          <td>${row.stock_vs_nifty}</td>
        </tr>
      `
    )
    .join("");

  document.querySelectorAll(".linkBtn").forEach((button) => {
    button.addEventListener("click", () => {
      $("symbolInput").value = button.dataset.symbol;
      analyze();
    });
  });
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

$("analyzeBtn").addEventListener("click", analyze);
$("checkZerodhaBtn").addEventListener("click", checkZerodhaStatus);
$("updateZerodhaTokenBtn").addEventListener("click", updateZerodhaToken);
$("bulkDownloadBtn").addEventListener("click", startBulkDownload);
$("bulkMonth").addEventListener("change", adjustBulkDaysForHigherFrames);
$("bulkWeek").addEventListener("change", adjustBulkDaysForHigherFrames);
$("sectorUploadBtn").addEventListener("click", uploadSectorCsv);
$("refreshFiiDiiBtn").addEventListener("click", () => loadFiiDii(true));
$("saveReportBtn").addEventListener("click", saveReport);
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

Promise.all([loadZerodhaLoginUrl(), checkZerodhaStatus(), loadSymbols(), loadSectorStatus(), loadFiiDii(false)])
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
