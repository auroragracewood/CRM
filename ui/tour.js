/* In-app guided tour.
 *
 * One tour, ~25 steps, walks the user through every major surface of the
 * CRM. State persists in localStorage so the tour resumes across page
 * navigations.
 *
 * UX rules (per the user's spec):
 *   - There is a "Start tour" button (the ? in the topnav) that begins
 *     or restarts the tour from step 0.
 *   - Every step has a "Skip tour" button that dismisses it for this
 *     session. Re-opening from the ? button brings it back.
 *   - Every step has a "Restart" button that jumps back to step 0.
 *   - Every step has a "Next" button (or "Continue to X" if the next step
 *     lives on a different page).
 *   - First-time visitors to the dashboard get a one-line offer to start
 *     the tour; declining it sets the seen flag so it doesn't re-pester.
 *
 * Adding a step: append an entry to TOUR. Fields:
 *   url      — where the step lives (so navigation can take the user there)
 *   selector — CSS selector to spotlight (optional; falls back to centered)
 *   title    — short header
 *   body     — HTML body (kept readable; trust your own content)
 *   placement — 'auto' (default), 'top', 'bottom', 'left', 'right', 'center'
 */

(function () {
  const TOUR = [
    {
      url: '/', selector: '.pagebar h1', title: 'Welcome to the CRM',
      body: `Quick tour of every part. You can <strong>Skip</strong> or
             <strong>Restart</strong> at any point. The <strong>?</strong>
             icon in the top bar reopens this tour any time.`,
    },
    {
      url: '/', selector: '.stats', title: 'Headline stats',
      body: `These four numbers are your "open work load": active
             contacts and companies, plus open deals and tasks.`,
    },
    {
      url: '/', selector: '.reports-grid', title: 'Dashboard widgets',
      body: `Each card is a saved report. Top intent, dormant high-value,
             overdue tasks, recent forms, pipeline summary, lead sources,
             and recent activity. Click into any of them to see the full
             report.`,
    },
    {
      url: '/contacts', selector: '.pagebar h1', title: 'Contacts',
      body: `People you track. Each row links to a profile. Use the search
             box for live filtering, the "Save current view" form to keep
             a useful filter, and the checkboxes for bulk operations.`,
    },
    {
      url: '/contacts', selector: '#list-filter-q', title: 'Live filter',
      body: `Type here and the table re-renders as you go (180ms debounce).
             Pressing Enter still works for no-JS environments.`,
    },
    {
      url: '/contacts', selector: '.saved-views-bar', title: 'Saved views',
      body: `Save the current filter (name it, optionally share with the
             team), or load a previously-saved one from the dropdown.`,
    },
    {
      url: '/contacts', selector: '#bulk-form', title: 'Bulk operations',
      body: `Check rows to reveal the toolbar above the table. Apply a tag,
             remove a tag, soft-delete, or restore selected rows in one
             POST.`,
    },
    {
      url: '/companies', selector: '.pagebar h1', title: 'Companies',
      body: `Same shape as Contacts, for organizations. Linked to contacts
             via <code>contacts.company_id</code>.`,
    },
    {
      url: '/pipelines', selector: '.pipeline-strip', title: 'Pipelines',
      body: `Each chip is one pipeline (sales / client / sponsor templates,
             or custom). Click to switch. Archive a pipeline with × — its
             deals stay put.`,
    },
    {
      url: '/pipelines', selector: '.kanban', title: 'Kanban',
      body: `Deals sit in stages. Change a deal's stage via its inline
             dropdown. Click a deal title to open its detail page.
             Add a deal directly to a stage from the "+ new deal here"
             field at the bottom of each column.`,
    },
    {
      url: '/pipelines', selector: '.stage-mgmt', title: 'Manage stages',
      body: `Edit positions and names, toggle won/lost flags, or delete a
             stage. Deletion refuses if any deal is on it — move them first.`,
    },
    {
      url: '/tasks', selector: '.task-tabs', title: 'Tasks',
      body: `Open, My open, Overdue, Done, All. Mark complete with ✓; click
             a title to edit. Tasks can be attached to a contact, company,
             deal, or any combination.`,
    },
    {
      url: '/forms', selector: '.pagebar h1', title: 'Forms',
      body: `Public form endpoints at <code>/f/&lt;slug&gt;</code>.
             Submissions auto-create contacts and log timeline entries.
             Click a form name to edit its schema + routing rules.`,
    },
    {
      url: '/segments', selector: '.pagebar h1', title: 'Segments',
      body: `Saved groups of contacts. Dynamic segments evaluate a JSON
             rule tree; static segments are a frozen list. Re-evaluate
             from a segment's detail page; results show on a Members tab.`,
    },
    {
      url: '/reports', selector: '.reports-grid', title: 'Reports',
      body: `Each card is a pure function in <code>services/reports.py</code>.
             Click <strong>Run</strong> for an inline result or <strong>CSV</strong>
             to download. No LLM — fully deterministic.`,
    },
    {
      url: '/tags', selector: '.pagebar h1', title: 'Tags',
      body: `Labels for contacts and companies. Edit name / color / scope
             inline. Deleting a tag detaches it from every record using it
             (count shown in the confirm).`,
    },
    {
      url: '/search', selector: '.searchbar-live', title: 'Search',
      body: `FTS5 across contacts, companies, interactions, and non-private
             notes. Press <strong>⌘K</strong> (or <strong>Ctrl+K</strong>) from
             anywhere to focus the topnav search and get a dropdown of
             top hits.`,
    },
    {
      url: '/plugins', selector: '.pagebar h1', title: 'Plug-ins',
      body: `Drop a Python file in <code>agent_surface/plugins/</code>,
             click Reload, and it picks up event hooks. Click a plug-in's
             name to edit its config JSON and view recent errors.`,
    },
    {
      url: '/connectors', selector: '.pagebar h1', title: 'Connectors (inbound)',
      body: `External systems POST signed JSON to
             <code>/in/&lt;slug&gt;</code>. Each delivery is logged raw;
             routing rules map fields onto contacts and interactions.`,
    },
    {
      url: '/audit', selector: '.pagebar h1', title: 'Audit log',
      body: `Every mutation lands here, in the same SQLite transaction as
             the data write. Filter by object type, action, surface, user,
             or request id. Admin only.`,
    },
    {
      url: '/settings', selector: '.pagebar h1', title: 'Settings',
      body: `Admin areas at the top: profile, users, roles, audit, tags.
             Below: API keys (for agent access), import/export, webhook
             subscriptions.`,
    },
    {
      url: '/settings/users', selector: '.pagebar h1', title: 'Users',
      body: `Add new users (email + password + role) and change anyone's
             built-in role. Click an email to assign additive RBAC roles.`,
    },
    {
      url: '/settings/roles', selector: '.pagebar h1', title: 'Roles & permissions',
      body: `Custom roles with permission strings (<code>contact.read</code>,
             <code>deal.write</code>, etc.). Granted on top of the built-in
             role; your service code chooses which permissions matter.`,
    },
    {
      url: '/me', selector: '.pagebar h1', title: 'My profile',
      body: `Change your email / display name, change password, see your
             active sessions across devices (revoke any you don't recognize),
             and view your RBAC role assignments.`,
    },
    {
      url: '/', selector: '.pagebar h1', title: 'Tour complete',
      body: `That's every page. The <strong>?</strong> icon in the top bar
             reopens this tour any time. Your saved views, bulk ops,
             plug-ins, and webhooks are waiting. Happy CRM-ing.`,
    },
  ];

  const LS = {
    active:    'crm_tour_active',
    step:      'crm_tour_step',
    dismissed: 'crm_tour_dismissed',
    seen:      'crm_tour_seen',
  };

  function getState() {
    return {
      active:    localStorage.getItem(LS.active) === '1',
      step:      parseInt(localStorage.getItem(LS.step) || '0', 10),
      dismissed: localStorage.getItem(LS.dismissed) === '1',
      seen:      localStorage.getItem(LS.seen) === '1',
    };
  }
  function setState(patch) {
    for (const k in patch) {
      const v = patch[k];
      if (v === null || v === undefined || v === false) {
        localStorage.removeItem(LS[k]);
      } else {
        localStorage.setItem(LS[k], v === true ? '1' : String(v));
      }
    }
  }

  function start(stepIndex = 0) {
    setState({ active: true, step: stepIndex, dismissed: false, seen: true });
    render();
  }
  function dismiss() {
    setState({ active: false, dismissed: true });
    teardown();
  }
  function restart() {
    setState({ active: true, step: 0, dismissed: false });
    const s = TOUR[0];
    if (s && s.url && location.pathname !== s.url) {
      location.href = s.url;
    } else {
      teardown();
      render();
    }
  }
  function next() {
    const s = getState();
    const newStep = s.step + 1;
    if (newStep >= TOUR.length) {
      // Finished
      setState({ active: false, dismissed: false });
      teardown();
      const done = document.createElement('div');
      done.className = 'crm-tour-tooltip';
      done.style.position = 'fixed';
      done.style.right = '20px';
      done.style.bottom = '20px';
      done.style.maxWidth = '320px';
      done.innerHTML = '<h4>Tour finished</h4><p>Reopen any time with the ? icon in the top bar.</p>';
      document.body.appendChild(done);
      setTimeout(() => done.remove(), 3500);
      return;
    }
    setState({ step: newStep });
    const step = TOUR[newStep];
    if (step.url && location.pathname !== step.url) {
      location.href = step.url;
    } else {
      teardown();
      render();
    }
  }

  function teardown() {
    document.querySelectorAll('.crm-tour-overlay, .crm-tour-spotlight, .crm-tour-tooltip')
      .forEach(el => el.remove());
  }

  function render() {
    const s = getState();
    if (!s.active || s.dismissed) return;
    if (s.step < 0 || s.step >= TOUR.length) return;
    const step = TOUR[s.step];

    // If we're on the wrong page for this step, render a small floating
    // "Continue tour ›" pill in the corner instead of a tooltip on a
    // wrong element.
    if (step.url && location.pathname !== step.url) {
      teardown();
      const pill = document.createElement('div');
      pill.className = 'crm-tour-tooltip crm-tour-pill';
      pill.style.position = 'fixed';
      pill.style.right = '20px';
      pill.style.bottom = '20px';
      pill.style.maxWidth = '320px';
      pill.style.zIndex = '1100';
      pill.innerHTML = (
        '<h4>Tour paused</h4>' +
        '<p>Continue to <strong>' + step.url + '</strong> to see this step:'
        + ' <em>' + escapeHtml(step.title) + '</em>.</p>'
        + '<div class="actions">'
        + '<button type="button" class="btn secondary" data-act="skip">Skip tour</button>'
        + '<button type="button" class="btn secondary" data-act="restart">Restart</button>'
        + '<button type="button" class="btn" data-act="goto">Continue ›</button>'
        + '</div>'
      );
      document.body.appendChild(pill);
      pill.addEventListener('click', e => {
        const act = (e.target.dataset || {}).act;
        if (act === 'skip')    dismiss();
        if (act === 'restart') restart();
        if (act === 'goto')    location.href = step.url;
      });
      return;
    }

    teardown();
    const target = step.selector ? document.querySelector(step.selector) : null;
    const overlay = document.createElement('div');
    overlay.className = 'crm-tour-overlay';
    document.body.appendChild(overlay);

    const tooltip = document.createElement('div');
    tooltip.className = 'crm-tour-tooltip';
    tooltip.innerHTML = (
      '<div class="crm-tour-progress">Step ' + (s.step + 1) + ' of ' + TOUR.length + '</div>'
      + '<h4>' + escapeHtml(step.title) + '</h4>'
      + '<div class="crm-tour-body">' + step.body + '</div>'
      + '<div class="actions">'
      + '<button type="button" class="btn secondary" data-act="skip">Skip tour</button>'
      + '<button type="button" class="btn secondary" data-act="restart">Restart</button>'
      + '<button type="button" class="btn" data-act="next">'
      + (s.step + 1 < TOUR.length ? 'Next ›' : 'Finish')
      + '</button>'
      + '</div>'
    );
    document.body.appendChild(tooltip);

    // Spotlight + position the tooltip near the target
    if (target) {
      const rect = target.getBoundingClientRect();
      // Spotlight
      const spot = document.createElement('div');
      spot.className = 'crm-tour-spotlight';
      const pad = 8;
      spot.style.top    = (window.scrollY + rect.top - pad) + 'px';
      spot.style.left   = (window.scrollX + rect.left - pad) + 'px';
      spot.style.width  = (rect.width + pad * 2) + 'px';
      spot.style.height = (rect.height + pad * 2) + 'px';
      document.body.appendChild(spot);
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });

      // Place tooltip: prefer below, else above, else centered
      const w = 360;
      tooltip.style.width = w + 'px';
      tooltip.style.position = 'absolute';
      let top  = window.scrollY + rect.bottom + 12;
      let left = window.scrollX + rect.left;
      if (left + w > window.scrollX + window.innerWidth - 16) {
        left = window.scrollX + window.innerWidth - w - 16;
      }
      if (left < window.scrollX + 16) left = window.scrollX + 16;
      tooltip.style.top  = top + 'px';
      tooltip.style.left = left + 'px';
    } else {
      // Centered fallback
      tooltip.style.position = 'fixed';
      tooltip.style.top  = '50%';
      tooltip.style.left = '50%';
      tooltip.style.transform = 'translate(-50%, -50%)';
      tooltip.style.maxWidth = '420px';
    }

    tooltip.addEventListener('click', e => {
      const act = (e.target.dataset || {}).act;
      if (act === 'skip')    dismiss();
      if (act === 'restart') restart();
      if (act === 'next')    next();
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // Wire up the ? button if present
  document.addEventListener('click', e => {
    const t = e.target && e.target.closest('[data-action="start-tour"]');
    if (t) { e.preventDefault(); start(0); }
  });

  // Resume on every page load if active
  window.addEventListener('DOMContentLoaded', () => {
    const s = getState();
    if (s.active && !s.dismissed) {
      render();
      return;
    }
    // First-time offer on the dashboard
    if (!s.seen && location.pathname === '/') {
      const offer = document.createElement('div');
      offer.className = 'crm-tour-tooltip';
      offer.style.position = 'fixed';
      offer.style.right = '20px';
      offer.style.bottom = '20px';
      offer.style.maxWidth = '320px';
      offer.style.zIndex = '1100';
      offer.innerHTML = (
        '<h4>First time here?</h4>'
        + '<p>Take a quick tour of every page. ~2 minutes, dismissible '
        + 'any time.</p>'
        + '<div class="actions">'
        + '<button type="button" class="btn secondary" data-act="no">Skip</button>'
        + '<button type="button" class="btn" data-act="yes">Start tour</button>'
        + '</div>'
      );
      document.body.appendChild(offer);
      offer.addEventListener('click', e => {
        const act = (e.target.dataset || {}).act;
        if (act === 'no')  { setState({ seen: true }); offer.remove(); }
        if (act === 'yes') { offer.remove(); start(0); }
      });
    }
  });

  // Expose for console + the ? button
  window.crmTour = { start, restart, dismiss, getState };
})();
