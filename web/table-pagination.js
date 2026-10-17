/* Existing server-side pagers remain authoritative; other tables use ten rows. */
(() => {
  const managed = new Set(["niftyTradesBody", "indexScannerTrades", "labTrades", "purpleStep1Body", "purpleBody", "purpleSetupBody", "purpleExitAlertsBody", "purpleTradesBody"]);
  const states = new WeakMap();
  function paginate(table) {
    const body = table.tBodies[0];
    if (!body || table.hasAttribute("data-no-pagination") || table.dataset.managedPagination || managed.has(body.id)) return;
    let s = states.get(table);
    if (!s) {
      const pager = document.createElement("div");
      pager.className = "table-pagination compact-pagination auto-pagination";
      pager.innerHTML = '<button type="button" title="Previous page" aria-label="Previous page">&#8592;</button><span role="status"></span><button type="button" title="Next page" aria-label="Next page">&#8594;</button>';
      (table.parentElement.matches(".wide-table,.lab-scroll,.table-wrap,.table-scroll,.ledger-scroll") ? table.parentElement : table).after(pager);
      s = {page: 1, pager, first: null}; states.set(table, s);
      pager.firstElementChild.addEventListener("click", () => { s.page--; paginate(table); });
      pager.lastElementChild.addEventListener("click", () => { s.page++; paginate(table); });
    }
    const rows = [...body.rows], pages = Math.max(1, Math.ceil(rows.length / 10));
    // A new filter or result set starts on page one; polling preserves the page.
    const first = rows[0]?.cells[0]?.textContent;
    if (s.first !== first) s.page = 1;
    s.first = first;
    s.page = Math.max(1, Math.min(s.page, pages));
    rows.forEach((row, i) => row.hidden = i < (s.page - 1) * 10 || i >= s.page * 10);
    s.pager.hidden = rows.length <= 10;
    s.pager.querySelector("span").textContent = `Page ${s.page} of ${pages} / ${rows.length} rows`;
    s.pager.firstElementChild.disabled = s.page === 1;
    s.pager.lastElementChild.disabled = s.page === pages;
  }
  const observer = new MutationObserver(records => {
    const tables = new Set();
    for (const r of records) {
      const parent = r.target.nodeType === 1 ? r.target : r.target.parentElement;
      const table = parent?.closest("table");
      if (table) tables.add(table);
      r.addedNodes.forEach(n => { if (n.nodeType === 1) { if (n.matches("table")) tables.add(n); n.querySelectorAll("table").forEach(t => tables.add(t)); } });
      r.removedNodes.forEach(n => { if (n.nodeType === 1) { const removed = n.matches("table") ? [n] : [...n.querySelectorAll("table")]; removed.forEach(t => { if (!t.isConnected) states.get(t)?.pager.remove(); }); } });
    }
    tables.forEach(t => { if (t.isConnected) paginate(t); });
  });
  document.querySelectorAll("table").forEach(paginate);
  observer.observe(document.body, {childList: true, subtree: true, characterData: true});
})();
