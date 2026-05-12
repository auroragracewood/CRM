/* Topnav global search.
 *
 * Reads the input inside .topsearch, debounces 150ms, hits /api/search,
 * renders a dropdown of grouped results. Cmd/Ctrl+K focuses, Esc clears
 * and closes, ArrowUp/Down + Enter navigate.
 *
 * Per-bucket cap is 5 so the dropdown stays short; for the full result
 * set, hitting Enter on the input (no result highlighted) submits the
 * <form action="/search">, taking the user to the dedicated page.
 */
(function () {
  const wrap = document.querySelector('.topsearch');
  if (!wrap) return;

  const input = wrap.querySelector('input[type=search]');
  if (!input) return;

  // Build the dropdown container ONCE.
  const panel = document.createElement('div');
  panel.className = 'topsearch-panel';
  panel.style.display = 'none';
  wrap.appendChild(panel);
  wrap.classList.add('topsearch-live');

  const BUCKET_ORDER = [
    ['contact',     'Contacts'],
    ['company',     'Companies'],
    ['interaction', 'Interactions'],
    ['note',        'Notes'],
  ];
  const PER_BUCKET = 5;

  let timer = null, inflight = null, highlight = -1, hits = [];

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function renderSnippet(s) {
    if (!s) return '';
    return escapeHtml(s)
      .replace(/&lt;mark&gt;/g, '<mark>')
      .replace(/&lt;\/mark&gt;/g, '</mark>');
  }

  function close() {
    panel.style.display = 'none';
    highlight = -1;
    hits = [];
  }

  function open() {
    panel.style.display = 'block';
  }

  function render(json) {
    const buckets = json.buckets || {};
    hits = [];
    const sections = [];
    for (const [key, label] of BUCKET_ORDER) {
      const bucketHits = (buckets[key] || []).slice(0, PER_BUCKET);
      if (!bucketHits.length) continue;
      const rows = bucketHits.map(h => {
        const idx = hits.length;
        hits.push(h);
        return (
          `<a class="topsearch-hit" data-idx="${idx}" href="${escapeHtml(h.url || '#')}">
             <span class="hit-label">${escapeHtml(h.label || '(untitled)')}</span>
             <span class="hit-body">${renderSnippet(h.body || h.title || '')}</span>
           </a>`
        );
      }).join('');
      sections.push(
        `<div class="topsearch-bucket">
           <div class="bucket-label">${escapeHtml(label)}</div>
           ${rows}
         </div>`
      );
    }
    if (!sections.length) {
      panel.innerHTML = '<div class="topsearch-empty">No matches</div>';
    } else {
      panel.innerHTML = sections.join('') +
        `<div class="topsearch-footer">
           <span class="muted">${json.total || hits.length} total</span>
           <a href="/search?q=${encodeURIComponent(input.value)}">See all →</a>
         </div>`;
    }
    open();
  }

  async function doSearch(q) {
    if (!q.trim()) { close(); return; }
    if (inflight) inflight.abort();
    inflight = new AbortController();
    try {
      const r = await fetch('/api/search?q=' + encodeURIComponent(q) + '&limit=40',
        { credentials: 'same-origin', signal: inflight.signal });
      if (!r.ok) return;
      const json = await r.json();
      render(json);
    } catch (e) {
      if (e.name !== 'AbortError') console.error(e);
    }
  }

  function setHighlight(i) {
    const items = panel.querySelectorAll('.topsearch-hit');
    items.forEach(el => el.classList.remove('highlight'));
    if (i >= 0 && i < items.length) {
      items[i].classList.add('highlight');
      items[i].scrollIntoView({ block: 'nearest' });
      highlight = i;
    } else {
      highlight = -1;
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => doSearch(input.value), 150);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      input.value = '';
      close();
      input.blur();
      e.preventDefault();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight(Math.min(highlight + 1, hits.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight(Math.max(highlight - 1, -1));
    } else if (e.key === 'Enter') {
      if (highlight >= 0 && hits[highlight] && hits[highlight].url) {
        window.location.href = hits[highlight].url;
        e.preventDefault();
      }
      // Otherwise let the <form> submit to /search?q=... naturally.
    }
  });

  // Click outside closes
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) close();
  });

  // Cmd/Ctrl+K focuses
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });

  // Show a hint placeholder
  if (!input.placeholder.includes('⌘K')) {
    input.placeholder = input.placeholder + '  (⌘K / Ctrl+K)';
  }
})();
