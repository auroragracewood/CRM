/* Toast notifications.
 *
 * Replaces native alert() and provides a clean way to surface "saved",
 * "deleted", "API key copied" etc. without a blocking dialog.
 *
 * Usage:
 *   window.toast("Saved");                        // info
 *   window.toast("Deleted", { type: "warn" });
 *   window.toast("Server error", { type: "error", timeout: 6000 });
 *   window.toast("Copied to clipboard", { type: "success" });
 *
 * If you don't pass a type it's "info". Default timeout 3500ms.
 * Multiple toasts stack at the bottom-right.
 *
 * For ?info=… and ?error=… URL params, topnav.js still renders the inline
 * flash banner at the top — those persist until dismissed. Toasts are for
 * ephemeral feedback during a session.
 */
(function () {
  let container = null;
  function ensureContainer() {
    if (container) return container;
    container = document.createElement("div");
    container.className = "toast-stack";
    document.body.appendChild(container);
    return container;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
  }

  window.toast = function (msg, opts) {
    opts = opts || {};
    const type = opts.type || "info";
    const timeout = opts.timeout || 3500;
    const c = ensureContainer();
    const el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.innerHTML = (
      '<span class="toast-msg">' + escapeHtml(msg) + '</span>'
      + '<button class="toast-x" aria-label="dismiss">×</button>'
    );
    c.appendChild(el);
    // Animate in next frame so the CSS transition can run.
    requestAnimationFrame(() => el.classList.add("toast-in"));
    const remove = () => {
      el.classList.remove("toast-in");
      el.classList.add("toast-out");
      setTimeout(() => el.remove(), 220);
    };
    el.querySelector(".toast-x").addEventListener("click", remove);
    if (timeout > 0) setTimeout(remove, timeout);
    return remove;
  };

  // Convenience: any form with [data-toast-on-submit="<msg>"] toasts on submit.
  document.addEventListener("submit", e => {
    const m = e.target && e.target.getAttribute("data-toast-on-submit");
    if (m) window.toast(m, { type: "success" });
  });

  // Convenience: any <button data-confirm="prompt"> shows a confirm toast.
  // Click flow: 1st click sets armed state + toast; 2nd click within 4s
  // submits the parent form / follows the link. Skip browser confirm() ugliness.
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-confirm]");
    if (!btn) return;
    if (btn.dataset.confirmArmed === "1") return; // let through
    e.preventDefault();
    e.stopPropagation();
    btn.dataset.confirmArmed = "1";
    const dismiss = window.toast(
      btn.getAttribute("data-confirm") + " — click again to confirm.",
      { type: "warn", timeout: 4000 }
    );
    setTimeout(() => { btn.dataset.confirmArmed = ""; }, 4000);
  });
})();
