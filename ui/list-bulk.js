/* Shared bulk-action toolbar for list pages (contacts, companies, ...).
 *
 * Looks for:
 *   <form id="bulk-form" action="/contacts/bulk" method="post">
 *     <table>
 *       <thead><tr><th><input type="checkbox" id="bulk-select-all"></th>…</tr></thead>
 *       <tbody><tr><td><input type="checkbox" name="ids" value="42"></td>…</tr></tbody>
 *     </table>
 *     <div id="bulk-toolbar">…buttons…</div>
 *   </form>
 *
 * Toolbar shows/hides based on whether any row checkbox is checked.
 * Header checkbox toggles all visible rows. The form submits ids as a
 * repeated 'ids' form field (FastAPI receives list[int]).
 */
(function () {
  const form = document.getElementById('bulk-form');
  const toolbar = document.getElementById('bulk-toolbar');
  const selectAll = document.getElementById('bulk-select-all');
  const countEl = document.getElementById('bulk-selected-count');
  if (!form || !toolbar) return;

  function rowCbs() {
    return form.querySelectorAll('tbody input[type=checkbox][name="ids"]');
  }
  function update() {
    const checked = Array.from(rowCbs()).filter(c => c.checked);
    if (checked.length > 0) {
      toolbar.classList.add('open');
      toolbar.style.display = '';
    } else {
      toolbar.classList.remove('open');
      toolbar.style.display = 'none';
    }
    if (countEl) countEl.textContent = checked.length;
    if (selectAll) {
      const all = rowCbs();
      selectAll.checked = all.length > 0 && checked.length === all.length;
      selectAll.indeterminate = checked.length > 0 && checked.length < all.length;
    }
  }
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      rowCbs().forEach(c => { c.checked = selectAll.checked; });
      update();
    });
  }
  form.addEventListener('change', e => {
    if (e.target && e.target.matches('input[type=checkbox][name="ids"]')) update();
  });
  // Confirm destructive actions
  form.addEventListener('submit', e => {
    const action = (form.querySelector('select[name="action"]') || {}).value;
    if (action === 'delete') {
      const n = Array.from(rowCbs()).filter(c => c.checked).length;
      if (!window.confirm(`Delete ${n} selected row(s)? This is reversible (soft-delete).`)) {
        e.preventDefault();
      }
    }
  });
  // Initial state
  update();
})();
