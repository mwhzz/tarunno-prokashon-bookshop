/* Keep dynamically loaded report and ledger tables scrollable on small screens. */
(() => {
  function prepareTables(root) {
    const tables = [];
    if (root instanceof HTMLTableElement) tables.push(root);
    if (root.querySelectorAll) tables.push(...root.querySelectorAll('table'));
    for (const table of tables) {
      if (table.closest('.app-table-scroll,.bc-table-scroll,.inventory-table-scroll,.cart-table-wrap,.preview-table-wrap,.preview-table-scroll')) continue;
      const columns = table.tHead?.rows[0]?.cells.length || table.rows[0]?.cells.length || 0;
      if (columns < 5) continue;
      const wrapper = document.createElement('div');
      wrapper.className = 'app-table-scroll';
      wrapper.tabIndex = 0;
      wrapper.setAttribute('role', 'region');
      wrapper.setAttribute('aria-label', 'তালিকা; ছোট স্ক্রিনে পাশে স্ক্রল করুন');
      table.classList.add(columns >= 6 ? 'app-wide-table' : 'app-medium-table');
      table.before(wrapper);
      wrapper.append(table);
    }
  }
  function init() {
    const content = document.getElementById('content');
    if (!content) return;
    prepareTables(content);
    const observer = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) prepareTables(node);
      }
    });
    observer.observe(content, {childList:true, subtree:true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
