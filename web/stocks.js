/* Research-only controls never start a live scanner. */
const stocks = {data:null, loading:false, busy:false, pages:{Candidate:1,Result:1,Coverage:1}};
function stockPage(name, rows, render) {
  const pages=Math.max(1,Math.ceil(rows.length/10));
  stocks.pages[name]=Math.max(1,Math.min(stocks.pages[name],pages));
  $("stock"+name+"Pager").innerHTML=`<button type="button" data-stock-page="${name}" data-step="-1" title="Previous page" aria-label="Previous page" ${stocks.pages[name]===1?"disabled":""}>&larr;</button><span>Page ${stocks.pages[name]} / ${pages} (${rows.length} rows)</span><button type="button" data-stock-page="${name}" data-step="1" title="Next page" aria-label="Next page" ${stocks.pages[name]===pages?"disabled":""}>&rarr;</button>`;
  return rows.slice((stocks.pages[name]-1)*10,stocks.pages[name]*10).map(render).join("");
}
async function loadStocks() {
  if(stocks.loading || !["stocks","backtest"].includes(state.activeTab)) return;
  stocks.loading=true;
  try { stocks.data=await api("/api/stocks/status"); renderStocks(); }
  catch(e) { $("stockAction").textContent=e.message; }
  finally { stocks.loading=false; }
}
function renderStocks() {
  const d=stocks.data;if(!d)return;
  const r=d.report||{},e=escapeHtml;
  $("stockApproval").textContent="Research only / Live approval blocked";
  $("stockRunState").textContent=`${d.phase} | Last report ${r.as_of||"-"} / ${r.benchmark||"-"}`;
  $("stockProgressText").textContent=`${d.progress || "No completed validation"} | ${d.completed || 0}/${d.total || 0}`;
  $("stockProgress").max=Math.max(1,d.total||1);$("stockProgress").value=d.completed||0;
  for(const id of ["stockValidate","stockDownload","stockAsOf","stockBenchmark"]) $(id).disabled=stocks.busy || d.phase==="running";
  $("stockCancel").disabled=d.phase!=="running";
  $("stockZip").disabled=$("stockCsv").disabled=!d.report;
  $("stockSummary").innerHTML=[["Strategies",2],["Live scanners approved",0],["Last historical end",r.as_of||"-"],["Stocks",r.universe?.length||0],["Comparisons",r.rows?.length||0],["Screen passes (not approvals)",(r.rows||[]).filter(x=>x.screen==="screen_pass_not_approved").length]].map(([k,v])=>`<div class="compact-metric"><span>${e(k)}</span><strong>${e(String(v))}</strong></div>`).join("");
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
$("stockScreenRun").addEventListener("click",()=>scanOpportunity($("stockScreenPattern").value));
for(const id of ["stockSetup","stockSide","stockFrame","stockVariant","stockCandidateFilter","stockSymbol"]) $(id).addEventListener(id==="stockSymbol"?"input":"change",()=>{Object.keys(stocks.pages).forEach(k=>stocks.pages[k]=1);renderStocks();});
for(const id of ["tab-stocks","tab-backtest"]) $(id).addEventListener("click",event=>{const b=event.target.closest("[data-stock-page]");if(b&&!b.disabled){stocks.pages[b.dataset.stockPage]+=Number(b.dataset.step);renderStocks();}});
for(const [id,format] of [["stockZip","zip"],["stockCsv","csv"]]) $(id).addEventListener("click",async()=>{
  try { const from=$("stockFrom").value,to=$("stockTo").value;if(from&&to&&from>to)throw Error("From must not be after To");const query=new URLSearchParams({format});if(from)query.set("from_date",from);if(to)query.set("to_date",to);await downloadArchive(`/api/stocks/export?${query}`,`stock-research.${format}`);$("stockExportStatus").textContent="Downloaded"; }
  catch(e){$("stockExportStatus").textContent=e.message;}
});
loadStocks();setInterval(loadStocks,5000);
