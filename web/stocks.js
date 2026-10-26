/* Live paper observation and historical evidence remain separate. */
const stocks = {data:null, swing:null, loading:false, busy:false, scannerBusy:false,
  pages:{Candidate:1,Result:1,Coverage:1,Decision:1,Trade:1}};
function stockPage(name, rows, render) {
  const pages=Math.max(1,Math.ceil(rows.length/10));
  stocks.pages[name]=Math.max(1,Math.min(stocks.pages[name],pages));
  $("stock"+name+"Pager").innerHTML=`<button type="button" data-stock-page="${name}" data-step="-1" title="Previous page" aria-label="Previous page" ${stocks.pages[name]===1?"disabled":""}>&larr;</button><span>Page ${stocks.pages[name]} / ${pages} (${rows.length} rows)</span><button type="button" data-stock-page="${name}" data-step="1" title="Next page" aria-label="Next page" ${stocks.pages[name]===pages?"disabled":""}>&rarr;</button>`;
  return rows.slice((stocks.pages[name]-1)*10,stocks.pages[name]*10).map(render).join("");
}
async function loadStocks() {
  if(stocks.loading || !["stocks","backtest","pullbacks"].includes(state.activeTab)) return;
  stocks.loading=true;
  try {
    [stocks.data,stocks.swing]=await Promise.all([api("/api/stocks/status"),api("/api/stocks/scanner/status")]);
    renderStocks();renderSwing();
  }
  catch(e) { $("swingAction").textContent=e.message; }
  finally { stocks.loading=false; }
}
function renderStocks() {
  const d=stocks.data;if(!d)return;
  const r=d.report||{},e=escapeHtml;
  $("stockApproval").textContent="Historical evidence / Not strategy approved";
  $("stockApprovalReasons").textContent=(d.approval_reasons||[]).join(" | ");
  $("stockRunState").textContent=`${d.phase} | Last report ${r.as_of||"-"} / ${r.benchmark||"-"}`;
  $("stockProgressText").textContent=`${d.progress || "No completed validation"} | ${d.completed || 0}/${d.total || 0}`;
  $("stockProgress").max=Math.max(1,d.total||1);$("stockProgress").value=d.completed||0;
  for(const id of ["stockValidate","stockDownload","stockAsOf","stockBenchmark"]) $(id).disabled=stocks.busy || d.phase==="running";
  $("stockCancel").disabled=d.phase!=="running";
  $("stockZip").disabled=$("stockCsv").disabled=!d.report;
  $("stockSummary").innerHTML=[["Strategies",2],["Live-data paper scanner","Available"],["Last historical session",r.as_of||"-"],["Stocks",r.universe?.length||0],["Comparisons",r.rows?.length||0],["Screen passes (not approvals)",(r.rows||[]).filter(x=>x.screen==="screen_pass_not_approved").length]].map(([k,v])=>`<div class="compact-metric"><span>${e(k)}</span><strong>${e(String(v))}</strong></div>`).join("");
  $("stockCandidateDate").textContent=`Historical observations only | ${r.as_of||"Not run"}`;
  const candidates=(r.candidates||[]).filter(x=>x.symbol.includes($("stockSymbol").value.trim().toUpperCase()) && ($("stockCandidateFilter").value==="all" || x.setups.length));
  $("stockCandidates").innerHTML=stockPage("Candidate",candidates,x=>`<tr><td>${e(x.symbol)} / ${e(x.frame)}</td><td>${fmtDateTime(x.as_of)}</td><td>${e(x.setups.join(", ")||x.reason)}</td><td>${e(x.side||"-")}</td><td>${fmt(x.close)} / ${fmt(x.ema20)} / ${fmt(x.ema50)}</td><td>${fmt(x.atr14)}</td></tr>`)||'<tr><td colspan="6">No research observations.</td></tr>';
  const rows=(r.rows||[]).filter(x=>[["stockSetup","setup"],["stockSide","side"],["stockFrame","frame"],["stockVariant","variant"]].every(([id,k])=>$(id).value==="all"||$(id).value===x[k]));
  $("stockResults").innerHTML=stockPage("Result",rows,x=>`<tr><td>${e(d.setups[x.setup])} / ${e(x.side)}</td><td>${e(x.frame)} / ${e(d.variants[x.variant])}</td><td>${e(x.window)} / ${x.target_r}R</td><td>${x.covered_symbols}/${x.total_symbols}</td><td>${x.trades} / ${fmt(x.win_rate)}%</td><td>${fmt(x.expectancy_r)}R / ${x.no_losses?"No losses":fmt(x.profit_factor)}</td><td>${fmt(x.stress_expectancy_r)}R</td><td>${fmt(x.drawdown_r)}</td><td>${x.later.trades} / ${fmt(x.later.win_rate)}%</td><td>${e(x.screen.replaceAll("_"," "))}</td></tr>`)||'<tr><td colspan="10">No completed matrix.</td></tr>';
  $("stockCoverageSummary").textContent=`${(r.coverage||[]).length} sources | ${r.benchmark||"-"}`;
  $("stockCoverage").innerHTML=stockPage("Coverage",r.coverage||[],x=>`<tr><td>${e(x.symbol)}</td><td>${e(x.frame)}</td><td>${x.bars}</td><td>${fmtDateTime(x.first)}</td><td>${fmtDateTime(x.last)}</td></tr>`);
  $("stockErrors").textContent=(r.errors||[]).map(x=>`${x.symbol}: ${x.error}`).join(" | ");
  $("stockRules").innerHTML=[...(d.rules||[]),...(r.limitations||[])].map(x=>`<p>${e(x)}</p>`).join("");
}
function renderSwing() {
  const d=stocks.swing;if(!d)return;
  const e=escapeHtml,ready=d.readiness||{},settings=d.settings||{};
  const phase=d.phase==="stopped"?"Stopped":d.phase==="monitoring_exits"?"Monitoring exits":`${d.running?"Running":"Stopped"} / ${(d.phase||"stopped").replaceAll("_"," ")}`;
  $("stockSwingStatus").textContent=`${phase} / Unvalidated paper`;
  $("swingReadiness").textContent=(ready.blockers||[]).join(" | ") || `Instrument masters ready | Telegram ${ready.telegram_configured?"configured":"not configured"} | Orders disabled`;
  $("swingStart").disabled=stocks.scannerBusy||d.running||!ready.ready||!$("swingAcknowledge").checked;
  $("swingStop").disabled=stocks.scannerBusy||!d.running;
  $("swingTelegramTest").disabled=stocks.scannerBusy||!ready.telegram_configured;
  for(const [id,key] of [["swingSetup","setup"],["swingFrame","frame"],["swingBenchmark","benchmark"],["swingVariant","variant"],["swingTarget","target_r"]]) {
    $(id).disabled=!!d.running||stocks.scannerBusy;if(d.running&&settings[key]!=null)$(id).value=String(settings[key]);
  }
  $("swingTelegram").disabled=!!d.running||stocks.scannerBusy||!ready.telegram_configured;
  if(d.running)$("swingTelegram").checked=!!settings.telegram_enabled;
  $("swingProgress").max=Math.max(1,d.total||1);$("swingProgress").value=d.completed||0;
  $("swingMetrics").innerHTML=[["Started",fmtDateTime(d.started_at)],["Last completed",fmtDateTime(d.last_completed)],["New entries stopped",fmtDateTime(d.stopped_at)],["Progress",`${d.completed||0} / ${d.total||0}`],["Current stock",d.current||"-"],["Open trades",d.open_count||0]].map(([label,value])=>`<div class="compact-metric"><span>${e(label)}</span><strong>${e(String(value))}</strong></div>`).join("");
  $("swingRefresh").textContent=`Candle refresh: ${d.refresh?.current||"idle"} | ${d.refresh?.completed||0}/${d.refresh?.total||0} sources | Last success ${fmtDateTime(d.refresh?.last_success)}`;
  $("swingErrors").textContent=(d.errors||[]).join(" | ");
  const checks=(d.decisions||[]).filter(x=>x.symbol.includes($("swingSymbol").value.trim().toUpperCase())&&($("swingDecisionFilter").value==="all"||x.direction));
  $("swingDecisionCount").textContent=`${checks.length} checks / ${d.universe_count||0} F&O stocks`;
  $("stockDecisions").innerHTML=stockPage("Decision",checks,x=>`<tr><td>${e(x.symbol)} / ${e(x.frame)}</td><td>${e(d.setups?.[x.family]||x.family||"-")}</td><td>${e(x.direction||"-")}</td><td>${e(x.reason.replaceAll("_"," "))}</td><td>${e(x.context?.market||"-")} / ${e(x.context?.sector||"-")}</td><td>${fmt(x.indicators?.close)} / ${fmt(x.indicators?.ema20)} / ${fmt(x.indicators?.ema50)}</td><td>${fmtDateTime(x.checked_at)}</td></tr>`)||'<tr><td colspan="7">No completed stock checks.</td></tr>';
  const from=$("swingTradeFrom").value,to=$("swingTradeTo").value;
  const trades=(d.trades||[]).filter(t=> {
    const at=(t[$("swingTradeBasis").value+"_time"]||"").slice(0,10);
    return t.symbol.includes($("swingTradeSymbol").value.trim().toUpperCase())&&
      ($("swingTradeStatus").value==="all"||t.status===$("swingTradeStatus").value)&&
      ($("swingTradeSetup").value==="all"||t.family===$("swingTradeSetup").value)&&
      (!from||(at&&at>=from))&&(!to||(at&&at<=to));
  });
  $("swingTradeCount").textContent=from&&to&&from>to?"From must not be after To":`${trades.length} shown / ${(d.trades||[]).length} stored`;
  $("stockTrades").innerHTML=stockPage("Trade",from&&to&&from>to?[]:trades,t=> {
    let result=paperResult(t.net_r,"R",t,"After assumed costs");
    if(t.status==="open") {
      const q=t.last_quote||{},age=(Date.now()-parseDateTime(q.quote_time))/1000;
      const points=q.price>0&&age>=-3&&age<=30?(q.price-t.entry_price)*(t.direction==="bullish"?1:-1):null;
      result=paperResult(points,"points",t,"Unrealized gross stock spot");
    }
    return `<tr><td>${e(t.symbol)}<div class="cell-note">${e(t.id)}</div></td><td>${e(d.setups?.[t.family]||t.family)} / ${e(t.timeframe)}</td><td>${e(t.status)} / ${e(t.direction)}</td><td>${result}</td><td>${fmt(t.entry_price)}<br>${fmtDateTime(t.entry_time)}</td><td>${fmt(t.exit_price)}<br>${fmtDateTime(t.exit_time)}</td><td>${formatSeconds(t.open_seconds??(Date.now()-parseDateTime(t.entry_time))/1000)}</td><td>${fmt(t.stop_level)} / ${fmt(t.target_level)}</td><td>${vixCell(t.india_vix_entry)}</td><td>${e(t.exit_reason||"-")}</td></tr>`;
  })||'<tr><td colspan="10">No matching swing trades.</td></tr>';
  $("swingDelivery").innerHTML=(d.delivery||[]).map(r=>`<tr><td>${e(r.symbol)}</td><td>${e(r.trade_id)}</td><td>${e(r.event_kind)}</td><td>${fmtDateTime(r.created_at)}</td><td>${e(r.status)}</td><td>${e(r.error||"-")}</td></tr>`).join("");
}
async function swingControl(action) {
  if(stocks.scannerBusy)return;
  stocks.scannerBusy=true;renderSwing();$("swingAction").textContent=`${action} requested`;
  try {
    const payload=action==="start"?{acknowledge:$("swingAcknowledge").checked,setup:$("swingSetup").value,
      frame:$("swingFrame").value,benchmark:$("swingBenchmark").value,variant:$("swingVariant").value,
      target_r:Number($("swingTarget").value),telegram_enabled:$("swingTelegram").checked}:{};
    const data=await postApi(`/api/stocks/scanner/${action}`,payload);
    if(action==="test-telegram")$("swingAction").textContent=data.sent?"Connection test sent":data.error||"Delivery failed";
    else{stocks.swing=data;$("swingAction").textContent=action==="stop"?"New entries stopped; open exits remain monitored":"Paper scanner started";}
  }catch(error){$("swingAction").textContent=error.message;}
  finally{stocks.scannerBusy=false;renderSwing();}
}
async function stockRun(download=false) {
  if(stocks.busy)return;
  if(download && !confirm("Download Daily and hourly history for all F&O stocks and context indices? This may take several minutes."))return;
  stocks.busy=true;renderStocks();$("stockAction").textContent="Starting validation...";
  try { stocks.data=await postApi("/api/stocks/validate",{as_of:$("stockAsOf").value,benchmark:$("stockBenchmark").value,download});$("stockAction").textContent=""; }
  catch(e) { $("stockAction").textContent=e.message; }
  finally { stocks.busy=false;renderStocks(); }
}
$("stockAsOf").value=new Date(Date.now()-86400000).toLocaleDateString("sv-SE",{timeZone:"Asia/Kolkata"});
$("stockValidate").addEventListener("click",()=>stockRun());
$("stockDownload").addEventListener("click",()=>stockRun(true));
$("stockCancel").addEventListener("click",async()=>{try{await postApi("/api/stocks/cancel",{});$("stockAction").textContent="Cancellation requested; current broker request may finish first.";}catch(e){$("stockAction").textContent=e.message;}});
$("stockOpenValidation").addEventListener("click",()=>{activateBacktestView("validation");activateTab("backtest");});
for(const id of ["stockSetup","stockSide","stockFrame","stockVariant","stockCandidateFilter","stockSymbol"]) $(id).addEventListener(id==="stockSymbol"?"input":"change",()=>{Object.keys(stocks.pages).forEach(k=>stocks.pages[k]=1);renderStocks();});
for(const id of ["tab-stocks","tab-backtest","tab-pullbacks"]) $(id).addEventListener("click",event=>{const b=event.target.closest("[data-stock-page]");if(b&&!b.disabled){stocks.pages[b.dataset.stockPage]+=Number(b.dataset.step);renderStocks();renderSwing();}});
for(const [id,format] of [["stockZip","zip"],["stockCsv","csv"]]) $(id).addEventListener("click",async()=>{
  try { const from=$("stockFrom").value,to=$("stockTo").value;if(from&&to&&from>to)throw Error("From must not be after To");const query=new URLSearchParams({format});if(from)query.set("from_date",from);if(to)query.set("to_date",to);await downloadArchive(`/api/stocks/export?${query}`,`stock-research.${format}`);$("stockExportStatus").textContent="Downloaded"; }
  catch(e){$("stockExportStatus").textContent=e.message;}
});
for(const [id,action] of [["swingStart","start"],["swingStop","stop"],["swingTelegramTest","test-telegram"]]) $(id).addEventListener("click",()=>swingControl(action));
$("swingAcknowledge").addEventListener("change",renderSwing);
for(const id of ["swingSymbol","swingDecisionFilter","swingTradeSymbol","swingTradeStatus","swingTradeSetup","swingTradeBasis","swingTradeFrom","swingTradeTo"]) $(id).addEventListener(id.endsWith("Symbol")?"input":"change",()=>{stocks.pages.Decision=stocks.pages.Trade=1;renderSwing();});
$("swingTradeClear").addEventListener("click",()=>{for(const id of ["swingTradeSymbol","swingTradeFrom","swingTradeTo"])$(id).value="";$("swingTradeStatus").value=$("swingTradeSetup").value="all";stocks.pages.Trade=1;renderSwing();});
for(const [id,format] of [["swingExportZip","zip"],["swingExportCsv","csv"]]) $(id).addEventListener("click",async()=>{
  try{const range=auditRange("swingExportFrom","swingExportTo");await downloadArchive(`/api/stocks/scanner/export?${range.query}&format=${format}`,`stock-swing-${range.label}.${format}`);$("swingExportStatus").textContent="Downloaded";}
  catch(error){$("swingExportStatus").textContent=error.message;}
});
$("swingExportFrom").value=new Date().toLocaleDateString("sv-SE",{timeZone:"Asia/Kolkata"});
$("stockAdvancedResearch").appendChild($("stockCustomWorkspace"));
$("stockCustomWorkspace").hidden=false;
$("stockValidationIntro").appendChild($("stockValidationSummary"));
$("stockValidationSummary").hidden=false;
loadStocks();setInterval(loadStocks,5000);
