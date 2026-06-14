const state = {
  symbols: [],
};

const $ = (id) => document.getElementById(id);

function fmt(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return value.toFixed(2);
  return String(value);
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
  $("dataStatus").textContent = `${data.total} F&O stocks tracked`;

  const list = $("symbolList");
  list.innerHTML = "";
  data.symbols.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.symbol;
    option.label = row.name || row.symbol;
    list.appendChild(option);
  });
}

async function analyze() {
  const symbol = $("symbolInput").value.trim().toUpperCase();
  if (!symbol) return;

  setNotes("Loading analysis...");
  const params = new URLSearchParams({
    symbol,
    option_chain: $("optionChainToggle").checked ? "true" : "false",
    previous_snapshot: $("previousSnapshot").value.trim(),
    refresh: $("refreshToggle").checked ? "true" : "false",
  });
  chartParams().forEach((value, key) => params.set(key, value));
  try {
    const data = await api(`/api/analyze?${params.toString()}`);
    renderAnalysis(data);
    setNotes((data.warnings || []).concat(data.decision.warnings || []));
  } catch (error) {
    setNotes([error.message], true);
  }
}

async function scan(type) {
  setNotes(`Loading ${type} scan...`);
  try {
    const params = chartParams();
    params.set("type", type);
    const data = await api(`/api/scan?${params.toString()}`);
    $("scanTitle").textContent = `${capitalize(type)} candidates`;
    $("scanMeta").textContent = `${data.results.length} shown / ${data.available_symbols} ready / ${data.timeframe_label}`;
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

  $("structureBody").innerHTML = [
    structureRow(data.chart.label, data.chart.technical, data.chart.structure),
    structureRow("60m", data.hourly.technical, data.hourly.structure),
  ].join("");

  renderRelativeStrength(data.relative_strength);
  renderOptionChain(data.option_chain);
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

$("analyzeBtn").addEventListener("click", analyze);
$("checkZerodhaBtn").addEventListener("click", checkZerodhaStatus);
$("updateZerodhaTokenBtn").addEventListener("click", updateZerodhaToken);
$("symbolInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") analyze();
});
document.querySelectorAll("[data-scan]").forEach((button) => {
  button.addEventListener("click", () => scan(button.dataset.scan));
});

Promise.all([loadZerodhaLoginUrl(), checkZerodhaStatus(), loadSymbols()])
  .then(() => {
    const first = state.symbols.find((row) => row.has_daily);
    if (first) {
      $("symbolInput").value = first.symbol;
      analyze();
    }
    return scan("neutral");
  })
  .catch((error) => setNotes([error.message], true));
