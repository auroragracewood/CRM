/* Live list-page filter. Used by /contacts and /companies.
 *
 * Setup:
 *   <input id="list-filter-q" data-endpoint="/api/contacts"
 *          data-columns="full_name,email,phone,title" data-row-link="/contacts">
 *   <tbody id="list-rows">…server-rendered rows…</tbody>
 *
 * As the user types: debounce 180ms, fetch the JSON list endpoint with
 * ?q=..., replace the tbody. Column set comes from data-columns. Each
 * row's first cell is wrapped in a link to data-row-link + /<id>.
 */
(function () {
  const input = document.getElementById('list-filter-q');
  const tbody = document.getElementById('list-rows');
  if (!input || !tbody) return;

  const endpoint = input.dataset.endpoint;
  const columns = (input.dataset.columns || '').split(',').map(s => s.trim()).filter(Boolean);
  const rowLink = input.dataset.rowLink || '';
  const totalEl = document.getElementById('list-total');
  const statusEl = document.getElementById('list-status');

  if (!endpoint || !columns.length) return;

  let timer = null, inflight = null, lastQ = input.value;

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function renderRow(item) {
    const cells = columns.map((col, i) => {
      const v = item[col];
      const inner = escapeHtml(v == null ? '' : v);
      if (i === 0 && rowLink && item.id != null) {
        return `<td><a href="${escapeHtml(rowLink)}/${item.id}">${inner || '(unnamed)'}</a></td>`;
      }
      return `<td>${inner}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }

  function emptyRow() {
    return `<tr><td colspan="${columns.length}" class="empty"
              style="padding:20px;text-align:center;color:var(--fg-muted)">
              No matches</td></tr>`;
  }

  async function doFilter(q) {
    if (q === lastQ) return;
    lastQ = q;

    // Keep the URL in sync so refresh + share works.
    const url = new URL(window.location);
    if (q) url.searchParams.set('q', q);
    else   url.searchParams.delete('q');
    window.history.replaceState(null, '', url);

    if (inflight) inflight.abort();
    inflight = new AbortController();
    if (statusEl) statusEl.textContent = 'filtering…';

    try {
      const u = endpoint + '?q=' + encodeURIComponent(q) + '&limit=200';
      const r = await fetch(u, { credentials: 'same-origin', signal: inflight.signal });
      if (!r.ok) {
        if (statusEl) statusEl.textContent = 'error ' + r.status;
        return;
      }
      const json = await r.json();
      const items = json.items || [];
      tbody.innerHTML = items.length ? items.map(renderRow).join('') : emptyRow();
      if (totalEl) totalEl.textContent = (json.total != null ? json.total : items.length) + ' total';
      if (statusEl) statusEl.textContent = '';
    } catch (e) {
      if (e.name !== 'AbortError' && statusEl) statusEl.textContent = 'error';
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => doFilter(input.value), 180);
  });
})();
