/* Read-only navigation; all experiment starts require an explicit acknowledgement. */
const expiryUI = {view:"overview", data:null, loading:false, busy:false, request:0, pages:{Trade:1,Decision:1,Event:1,Manual:1}};
const expiryEscape = value => escapeHtml(String(value ?? "-"));
function expiryDates() {
  const from_date=$("expiryFrom").value, to_date=$("expiryTo").value || from_date;
  if(!from_date || from_date>to_date) throw new Error("Choose a valid From / To date range.");
  return {from_date,to_date};
}
function expiryPage(name, rows, render) {
  const count=Math.max(1,Math.ceil(rows.length/10));
  expiryUI.pages[name]=Math.min(count,Math.max(1,expiryUI.pages[name]));
  const page=expiryUI.pages[name];
  $("expiry"+name+"Pager").innerHTML=`<button type="button" data-expiry-page="${name}" data-step="-1" aria-label="Previous page" ${page===1?"disabled":""}>&larr;</button> Page ${page} of ${count} / ${rows.length} rows <button type="button" data-expiry-page="${name}" data-step="1" aria-label="Next page" ${page===count?"disabled":""}>&rarr;</button>`;
  return rows.slice((page-1)*10,page*10).map(render).join("");
}
function expiryView(view) {
  expiryUI.view=view;Object.keys(expiryUI.pages).forEach(k=>expiryUI.pages[k]=1);
  document.querySelectorAll("[data-expiry-view]").forEach(b=>{b.classList.toggle("active",b.dataset.expiryView===view);b.setAttribute("aria-selected",String(b.dataset.expiryView===view));});
  document.querySelectorAll("[data-expiry-panel]").forEach(p=>p.hidden=p.dataset.expiryPanel!==(["NIFTY","SENSEX"].includes(view)?"trades":view));
  renderExpiry();
}
async function loadExpiry() {
  if(state.activeTab!=="expiry" || expiryUI.loading || expiryUI.busy)return;
  expiryUI.loading=true;const request=++expiryUI.request;
  try {
    const query=new URLSearchParams(expiryDates()).toString();
    const data=await api(`/api/expiry/status?${query}`);
    if(request===expiryUI.request && query===new URLSearchParams(expiryDates()).toString()) {
      expiryUI.data=data;renderExpiry();
      if($("expiryAction").textContent===expiryUI.error)$("expiryAction").textContent="";
      expiryUI.error=null;
    }
  }catch(e){expiryUI.error=e.message;$("expiryAction").textContent=e.message;}
  finally{expiryUI.loading=false;}
}
async function expiryCommand(action,setup=null) {
  if(expiryUI.busy)return;
  expiryUI.busy=true;++expiryUI.request;
  $("expiryAction").textContent="Request in progress...";
  try {
    const body=action==="replay"?expiryDates():{setup,acknowledge:$("expiryAck").checked,telegram_enabled:$("expiryTelegram").checked};
    await postApi(`/api/expiry/${action}`,body);
    $("expiryAction").textContent=action==="stop"?"New entries stopped; open paper exits continue.":action==="replay"?"Replay completed.":"Paper experiments enabled.";
  }catch(e){$("expiryAction").textContent=e.message;}
  finally{expiryUI.busy=false;await loadExpiry();}
}
function expiryMetricRows(rows) {
  const e=expiryEscape;
  return rows.map(r=>`<tr><td>${e(r.setup)}</td><td>${r.open} / ${r.closed}</td><td>${fmt(r.win_rate)}</td><td>${fmt(r.net_pnl)}</td><td>${fmt(r.expectancy)}</td><td>${fmt(r.profit_factor)}</td><td>${fmt(r.drawdown)}</td><td>${r.excluded}</td></tr>`).join("");
}
function renderExpiry() {
  const d=expiryUI.data;if(!d)return;
  const e=expiryEscape,view=expiryUI.view;
  $("expiryStatus").textContent=`${d.running_count}/8 enabled / Unvalidated paper`;
  $("expiryStart").disabled=d.running_count===8;$("expiryStop").disabled=d.running_count===0;
  $("expiryTelegram").disabled=d.running_count>0;
  $("expiryCsv").href=`/api/expiry/export?${new URLSearchParams(expiryDates())}&format=csv`;
  $("expiryZip").href=`/api/expiry/export?${new URLSearchParams(expiryDates())}&format=zip`;
  $("expiryStates").innerHTML=Object.entries(d.states).map(([key,s])=>`<tr><td>${e(key)}<div class="cell-note">${e(d.setups[key.split(":")[1]])}</div></td><td>${e(s.phase)}<div class="cell-note">${e(s.reason)}</div></td><td>${fmtDateTime(s.started_at)}<br>${fmtDateTime(s.stopped_at)}</td><td>${fmtDateTime(s.last_checked)}<br>${fmtDateTime(s.last_bar)}</td><td><button type="button" data-expiry-action="${s.enabled?"stop":"start"}" data-setup="${e(key)}">${s.enabled?"Stop entries":"Start"}</button></td></tr>`).join("");
  $("expiryFeed").innerHTML=`<table class="readable-table"><thead><tr><th>Index</th><th>Status</th><th>Expiry / ATM</th><th>Last refresh (IST)</th><th>Samples</th><th>Issue</th></tr></thead><tbody>${Object.entries(d.feeds).map(([s,f])=>`<tr><td>${e(s)}</td><td>${e(f.phase)}</td><td>${e(f.pair?.expiry)} / ${fmt(f.pair?.strike)}</td><td>${fmtDateTime(f.last_refresh)}</td><td>${f.samples}</td><td>${e(f.error||f.stream_error)}</td></tr>`).join("")}</tbody></table>`;
  $("expiryRules").innerHTML=`<strong>${e(d.version)}</strong><ul>${d.rules.map(r=>`<li>${e(r)}</li>`).join("")}</ul><pre>${e(JSON.stringify(d.policy,null,2))}</pre>`;
  $("expiryIndexTitle").textContent=`${view} Paper Spreads`;
  const ledger=$("expiryLedger").value==="portfolio"?(d.portfolio_trades||[]):d.trades;
  const selected=ledger.filter(t=>t.symbol===view && ($("expirySetup").value==="all"||t.setup.endsWith(":"+$("expirySetup").value)) && ($("expiryTradeStatus").value==="all"||t.status===$("expiryTradeStatus").value));
  $("expiryTrades").innerHTML=expiryPage("Trade",selected,t=>`<tr><td>${e(t.id)}<br>${e(t.setup)}</td><td>${e(t.direction)} / ${e(t.status)}</td><td>${e(t.long.tradingsymbol)}<br>${e(t.short.tradingsymbol)}</td><td>${e(t.long.expiry)} / ${t.quantity}</td><td>${fmtDateTime(t.entry_time)}<br>${fmt(t.debit)}</td><td>${fmtDateTime(t.exit_time)}<br>${fmt(t.exit_fill?.value)}</td><td>${formatSeconds(t.open_seconds ?? Math.max(0,(Date.parse(d.now)-Date.parse(t.entry_time))/1000))}</td><td>${fmt(t.status==="open"?t.mark_pnl:t.net_pnl)} / ${fmt(t.net_r)}<div class="cell-note">${t.status==="open"?"Mark; not realized":"Net with assumed costs"}</div></td><td>${fmt(t.stop_level)}</td><td>${e(t.portfolio_reason)}<div class="cell-note">${e(t.monitoring_interrupted||t.pending_exit||t.exit_reason)}</div></td><td>${vixCell(t.india_vix_entry)}</td></tr>`)||'<tr><td colspan="11">No trades for these filters.</td></tr>';
  const prior=$("expiryManualTrade").value;
  $("expiryManualTrade").innerHTML=d.trades.filter(t=>t.symbol===view).map(t=>`<option value="${e(t.id)}">${e(t.id)}</option>`).join("");
  if(d.trades.some(t=>t.id===prior))$("expiryManualTrade").value=prior;
  const manual=d.manual_fills.filter(r=>d.trades.some(t=>t.id===r.trade_id && t.symbol===view));
  $("expiryManualRows").innerHTML=`<table class="readable-table"><thead><tr><th>Trade</th><th>Kind</th><th>Time (IST)</th><th>Qty</th><th>Long / short fill</th><th>Charges INR</th></tr></thead><tbody>${expiryPage("Manual",manual,r=>`<tr><td>${e(r.trade_id)}</td><td>${e(r.kind)}</td><td>${fmtDateTime(r.time)}</td><td>${r.quantity}</td><td>${fmt(r.long_price)} / ${fmt(r.short_price)}</td><td>${fmt(r.charges)}</td></tr>`)}</tbody></table>`;
  $("expiryMetrics").innerHTML=expiryMetricRows(d.metrics);
  const p=d.portfolio;
  $("expiryPortfolio").innerHTML=[["Selected-period net INR",fmt(p.net_pnl)],["Open / closed",`${p.open} / ${p.closed}`],["Today's net mark INR",p.valuation_incomplete?"Incomplete":fmt(p.current_daily_pnl)],["Entry circuit",p.halted?"Halted":"Not halted"],["Excluded",p.excluded]].map(([k,v])=>`<div class="compact-metric"><span>${e(k)}</span><strong>${e(v)}</strong></div>`).join("");
  $("expiryReplayStatus").textContent=`${d.validation.phase}: ${d.validation.reason||""} | ${d.validation.samples||0} samples | ${d.validation.unresolved_open||0} unresolved`;
  $("expiryReplayRows").innerHTML=`<table class="readable-table"><thead><tr><th>Setup</th><th>Open / closed</th><th>Win %</th><th>Net INR</th><th>Expectancy</th><th>PF</th><th>Drawdown</th><th>Excluded</th></tr></thead><tbody>${expiryMetricRows(d.validation.metrics||[])}</tbody></table>`;
  $("expiryRejections").textContent=Object.entries(d.rejection_counts).map(([k,v])=>`${k}: ${v}`).join(" | ");
  $("expiryDecisions").innerHTML=expiryPage("Decision",d.decisions,r=>`<tr><td>${fmtDateTime(r.checked_at)}</td><td>${e(r.setup)}</td><td>${e(r.reason)}</td><td>${fmt(r.signal_delay_seconds)}s</td><td><details><summary>Values</summary><pre>${e(JSON.stringify(r,null,2))}</pre></details></td></tr>`);
  const events=[...d.events,...d.delivery].sort((a,b)=>String(b.time||b.created_at).localeCompare(String(a.time||a.created_at)));
  $("expiryEvents").innerHTML=expiryPage("Event",events,r=>`<details><summary>${fmtDateTime(r.time||r.created_at)} | ${e(r.event||r.event_kind)} | ${e(r.status||r.setup)}</summary><pre>${e(JSON.stringify(r,null,2))}</pre></details>`);
}
$("expiryFrom").value=$("expiryTo").value=labSessionDate();
$("expiryStart").addEventListener("click",()=>expiryCommand("start"));
$("expiryStop").addEventListener("click",()=>expiryCommand("stop"));
$("expiryReplay").addEventListener("click",()=>expiryCommand("replay"));
$("tab-expiry").addEventListener("click",event=>{
  const b=event.target.closest("button");if(!b||b.disabled)return;
  if(b.dataset.expiryView)expiryView(b.dataset.expiryView);
  if(b.dataset.expiryAction)expiryCommand(b.dataset.expiryAction,b.dataset.setup);
  if(b.dataset.expiryPage){expiryUI.pages[b.dataset.expiryPage]+=Number(b.dataset.step);renderExpiry();}
});
for(const id of ["expirySetup","expiryTradeStatus","expiryLedger"])$(id).addEventListener("change",()=>{expiryUI.pages.Trade=1;renderExpiry();});
for(const id of ["expiryFrom","expiryTo"])$(id).addEventListener("change",()=>{++expiryUI.request;Object.keys(expiryUI.pages).forEach(k=>expiryUI.pages[k]=1);loadExpiry();});
$("expiryManualForm").addEventListener("submit",async event=>{
  event.preventDefault();if(expiryUI.busy)return;expiryUI.busy=true;
  try {
    await postApi("/api/expiry/manual",{trade_id:$("expiryManualTrade").value,kind:$("expiryManualKind").value,
      time:$("expiryManualTime").value+":00+05:30",quantity:Number($("expiryManualQty").value),long_price:Number($("expiryManualLong").value),short_price:Number($("expiryManualShort").value),charges:Number($("expiryManualCharges").value)});
    $("expiryAction").textContent="User-reported fill recorded separately from paper results.";
  }catch(e){$("expiryAction").textContent=e.message;}finally{expiryUI.busy=false;await loadExpiry();}
});
setInterval(loadExpiry,3000);
