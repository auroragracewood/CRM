/* Themed confirmation modal.
 *
 * Use 1: opt forms in by adding data-confirm="message". On submit, the
 *   user sees a styled modal asking for confirmation; only on OK does
 *   the form actually submit.
 *
 *   <form method="post" action="/foo/delete" data-confirm="Delete this thing?">
 *     ...
 *   </form>
 *
 * Use 2: window.crmConfirm(message) returns a Promise<boolean> for
 *   custom async flows.
 */
(function () {
  function buildModal(message) {
    return new Promise(resolve => {
      const backdrop = document.createElement('div');
      backdrop.className = 'crm-modal-backdrop';
      const modal = document.createElement('div');
      modal.className = 'crm-modal';
      backdrop.appendChild(modal);

      const heading = document.createElement('h3');
      heading.textContent = 'Confirm';
      modal.appendChild(heading);

      const p = document.createElement('p');
      p.textContent = message;
      modal.appendChild(p);

      const actions = document.createElement('div');
      actions.className = 'actions';
      modal.appendChild(actions);

      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn secondary';
      cancel.textContent = 'Cancel';
      actions.appendChild(cancel);

      const ok = document.createElement('button');
      ok.type = 'button';
      ok.className = 'btn danger';
      ok.textContent = 'OK';
      actions.appendChild(ok);

      function close(answer) {
        backdrop.remove();
        document.removeEventListener('keydown', onKey);
        resolve(answer);
      }
      function onKey(e) {
        if (e.key === 'Escape') close(false);
        else if (e.key === 'Enter') close(true);
      }
      backdrop.addEventListener('click', e => {
        if (e.target === backdrop) close(false);
      });
      cancel.addEventListener('click', () => close(false));
      ok.addEventListener('click', () => close(true));
      document.addEventListener('keydown', onKey);

      document.body.appendChild(backdrop);
      ok.focus();
    });
  }

  window.crmConfirm = buildModal;

  // Intercept any form with data-confirm
  document.addEventListener('submit', async function (e) {
    const form = e.target;
    if (!form || !form.matches('form[data-confirm]')) return;
    if (form.dataset.confirmed === '1') return;  // already confirmed; let submit through
    e.preventDefault();
    const ok = await buildModal(form.dataset.confirm);
    if (ok) {
      form.dataset.confirmed = '1';
      form.submit();
    }
  });
})();
