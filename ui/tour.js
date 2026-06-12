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
      url: '/', selector: '.pagebar h1', title: 'Welcome — what is a CRM?',
      body: `
        <p class="lede">A <strong>Customer Relationship Management</strong> tool is a single
        place to remember every person, company, conversation, deal, and
        follow-up tied to your work — so nothing falls through the cracks.</p>
        <div class="dummies">
          <strong>If you're new to CRMs:</strong> think of this like a smart
          contact list that also remembers what you talked about, what you
          promised, when to follow up, and how a deal is going. Less
          "address book," more "memory for your work life."
        </div>
        <p><strong>This tour covers every page</strong> — about 30 short stops.
        You can <strong>Skip</strong> or <strong>Restart</strong> at any point.
        The <strong>?</strong> in the top-right reopens this anytime.</p>`,
    },
    {
      url: '/', selector: '.stats', title: 'Dashboard — headline numbers',
      body: `
        <p>Four big numbers showing your current load: active <strong>Contacts</strong>,
        <strong>Companies</strong>, <strong>Open deals</strong> and <strong>Open tasks</strong>.</p>
        <div class="dummies">
          <strong>For dummies:</strong> these are the "is my world busy or empty?"
          numbers. Look here first when you start your day.
        </div>
        <p><strong>Use them to:</strong></p>
        <ul>
          <li>See if you've got a deal backlog piling up</li>
          <li>Check task count before Monday — if 47 are open you probably
              overcommitted last week</li>
          <li>Spot if contacts/companies are growing or stagnant</li>
        </ul>`,
    },
    {
      url: '/', selector: '.reports-grid', title: 'Dashboard widgets',
      body: `
        <p>Each card is a live mini-report: top-intent leads, dormant high-value
        contacts, overdue tasks, recent form submissions, pipeline summary,
        lead sources, recent activity feed.</p>
        <div class="dummies">
          <strong>For dummies:</strong> these are like notification widgets on a phone
          home-screen — but for your business. They surface what needs attention.
        </div>
        <p><strong>Try clicking:</strong></p>
        <ul>
          <li><strong>Top intent</strong> to call your hottest leads first</li>
          <li><strong>Dormant high-value</strong> to re-engage a quiet whale</li>
          <li><strong>Recent forms</strong> to greet today's new sign-ups</li>
        </ul>
        <p class="tip"><strong>Tip:</strong> any number you click here takes you to the full report — same numbers, more rows.</p>`,
    },
    {
      url: '/contacts', selector: '.pagebar h1', title: 'Contacts — the people page',
      body: `
        <p>The list of every person you're tracking. Click a row to open
        their profile (interactions, notes, tags, deals).</p>
        <div class="dummies">
          <strong>For dummies:</strong> imagine your phone's contacts list, but each
          person also has a journal of every conversation you've had with them.
        </div>
        <p><strong>Use this to:</strong></p>
        <ul>
          <li>Look up "what was that customer's email again?" in 2 seconds</li>
          <li>Add a new lead you just met at a meetup</li>
          <li>Find everyone who hasn't been contacted in 90 days</li>
          <li>Pull a list of contacts at a specific company</li>
        </ul>`,
    },
    {
      url: '/contacts', selector: '#list-filter-q', title: 'Live filter',
      body: `
        <p>Type and the table re-renders as you type (180ms debounce). No page reload, no "search" button needed.</p>
        <div class="dummies">
          <strong>For dummies:</strong> you don't have to remember someone's exact name.
          Type the first few letters, part of their company, even part of
          their job title — anything indexed and you'll see them.
        </div>
        <p><strong>Try:</strong></p>
        <ul>
          <li>Type "@gmail" to see all Gmail-domain contacts</li>
          <li>Type a city to find local leads</li>
          <li>Type a job title to filter by role</li>
        </ul>`,
    },
    {
      url: '/contacts', selector: '.saved-views-bar', title: 'Saved views',
      body: `
        <p>Filtered + sorted a list a useful way? Click "Save current view,"
        name it, and load it from the dropdown next time. Optionally share
        with the team.</p>
        <div class="dummies">
          <strong>For dummies:</strong> like saving a YouTube playlist of the
          search results, so you don't have to redo the filtering every Monday.
        </div>
        <p><strong>Real views worth saving:</strong></p>
        <ul>
          <li>"Hot leads this week" (tag = hot, last-contact &lt; 7 days)</li>
          <li>"Customers in EU"</li>
          <li>"My to-call list"</li>
        </ul>`,
    },
    {
      url: '/contacts', selector: '#bulk-form', title: 'Bulk operations',
      body: `
        <p>Check any rows and the toolbar appears above. Tag, untag, delete,
        or restore many at once in a single request.</p>
        <div class="dummies">
          <strong>For dummies:</strong> like selecting a bunch of emails in Gmail
          and hitting "Mark as read." Saves 100 clicks when you have 100 rows.
        </div>
        <p><strong>Useful for:</strong></p>
        <ul>
          <li>Tagging 30 contacts as "webinar-attendee-2026" after an event</li>
          <li>Soft-deleting test data without leaving the page</li>
          <li>Quickly assigning a "follow-up-q3" tag across a list</li>
        </ul>`,
    },
    {
      url: '/contacts', selector: '.list-table tr:nth-child(2)', title: 'Contact detail page',
      body: `
        <p>Click any row's <strong>name</strong> to open the contact's profile.
        The detail page shows: their info, a <strong>timeline</strong> of every
        interaction, attached <strong>notes</strong> (with visibility scopes —
        private / team / shared), <strong>tags</strong>, related deals & tasks,
        and consent records.</p>
        <div class="dummies">
          <strong>For dummies:</strong> this is the page that makes the CRM more
          than a contact list. Every email, call, meeting note, and follow-up
          for that person lives here. You'll never forget a context again.
        </div>
        <p><strong>What lands automatically:</strong></p>
        <ul>
          <li>Form submissions become interactions on the form-author's profile</li>
          <li>Webhook events you wire from external systems</li>
          <li>Notes you type stay tagged with who wrote them and when</li>
        </ul>
        <p class="tip"><strong>Tip:</strong> open one now, then click <em>Next</em>.</p>`,
    },
    {
      url: '/companies', selector: '.pagebar h1', title: 'Companies',
      body: `
        <p>Organizations the contacts work at. Same list pattern as Contacts
        (live filter, saved views, bulk ops). Linked to people via
        <code>contacts.company_id</code>.</p>
        <div class="dummies">
          <strong>For dummies:</strong> if Contacts is "people," Companies is "where
          they work." Tracking the company separately means when Jane changes
          jobs, you keep her history AND the company's history intact.
        </div>
        <p><strong>Useful for:</strong></p>
        <ul>
          <li>Pulling every contact at "Acme Corp" before a meeting</li>
          <li>Tracking the company-level relationship (one contract, many
              people)</li>
          <li>Reporting by company size, industry, or country</li>
        </ul>`,
    },
    {
      url: '/pipelines', selector: '.pipeline-strip', title: 'Pipelines — what deals are happening',
      body: `
        <p>Each chip is one pipeline. Sales, client-success, sponsor, custom — multiple
        in parallel. Click a chip to switch boards. Archive with × (deals stay,
        the chip just hides).</p>
        <div class="dummies">
          <strong>For dummies:</strong> a pipeline is "the path a deal walks" from first
          contact to close. Different sales motions = different pipelines.
        </div>
        <p><strong>Examples of distinct pipelines:</strong></p>
        <ul>
          <li><strong>Sales:</strong> New → Qualified → Proposal → Won / Lost</li>
          <li><strong>Onboarding:</strong> Signed → Kickoff → Live → Steady-state</li>
          <li><strong>Partnerships:</strong> Intro → Pitched → Agreement → Active</li>
        </ul>`,
    },
    {
      url: '/pipelines', selector: '.kanban', title: 'Kanban board',
      body: `
        <p>Deals sit in stage columns. Move a deal by changing its stage in the
        dropdown. Click a deal title to open it. Use "+ new deal here" at the
        bottom of any column to add one directly to that stage.</p>
        <div class="dummies">
          <strong>For dummies:</strong> like a Trello board — each card is a deal,
          and you "drag" it across columns as it progresses. Cards on the right
          are closer to closing.
        </div>
        <p><strong>Daily rituals:</strong></p>
        <ul>
          <li>Glance at the leftmost column — your new leads</li>
          <li>Check the rightmost open column — deals about to close</li>
          <li>Notice "stuck" cards that haven't moved in weeks — they need attention or to be marked lost</li>
        </ul>`,
    },
    {
      url: '/pipelines', selector: '.stage-mgmt', title: 'Manage stages',
      body: `
        <p>Rename, reorder, toggle won/lost flags, or delete a stage. Deletion
        refuses if any deal sits on it — move them first.</p>
        <div class="dummies">
          <strong>For dummies:</strong> stages are the columns on the Kanban. You set
          up your own — there's no "right" set. Most pipelines have 4–6 stages.
        </div>
        <p><strong>Common adjustments:</strong></p>
        <ul>
          <li>Add a "Negotiation" stage if proposals are getting bogged down</li>
          <li>Mark "Closed Won" with the <em>won</em> flag so reports count revenue right</li>
          <li>Delete "Maybe?" if it just became a parking lot</li>
        </ul>`,
    },
    {
      url: '/pipelines', selector: '.kanban', title: 'Deal detail page',
      body: `
        <p>Click any deal title in the kanban to open its detail page. The deal
        page shows: title, value, expected close, status, owner, timeline of
        activities, notes, attached tasks, and stage history.</p>
        <div class="dummies">
          <strong>For dummies:</strong> this is the deal's own profile, like a contact
          but for a sales opportunity. Every email, call, and decision about
          that deal lives in one place.
        </div>
        <p><strong>Use it to:</strong></p>
        <ul>
          <li>Pull up full context 60 seconds before a sales call</li>
          <li>Log the outcome of a call (creates a timeline entry)</li>
          <li>Set the next follow-up task without leaving the page</li>
        </ul>
        <p class="tip"><strong>If you have no deals yet:</strong> add one in any
        column ("+ new deal here"), then click it. Or just hit Next.</p>`,
    },
    {
      url: '/tasks', selector: '.task-tabs', title: 'Tasks — your to-do list',
      body: `
        <p>Tabs: <strong>Open</strong>, <strong>My open</strong>,
        <strong>Overdue</strong>, <strong>Done</strong>, <strong>All</strong>.
        Click ✓ to complete; click a title to edit. Tasks attach to any combo
        of contact, company, and deal.</p>
        <div class="dummies">
          <strong>For dummies:</strong> a to-do list, but each task knows what it's
          about. "Call Jane" is tied to Jane's contact card; finishing it logs
          on her timeline automatically.
        </div>
        <p><strong>Try it for:</strong></p>
        <ul>
          <li>"Send proposal" attached to a deal (auto-completes once you mark won)</li>
          <li>"Quarterly check-in" tied to a contact</li>
          <li>"Renew contract" attached to a company with a due-date alarm</li>
        </ul>`,
    },
    {
      url: '/forms', selector: '.pagebar h1', title: 'Forms — public lead capture',
      body: `
        <p>Each form is a public URL at <code>/f/&lt;slug&gt;</code>. Submissions
        auto-create contacts and log timeline entries. Edit any form's schema
        + routing rules by clicking its name.</p>
        <div class="dummies">
          <strong>For dummies:</strong> a "Contact Us" form on a website. You embed
          the form (or link it), people fill it in, and they land in your CRM
          as real contacts — no copy-paste, no scrolling spreadsheet.
        </div>
        <p><strong>Real forms to build:</strong></p>
        <ul>
          <li>Newsletter signup (single field: email)</li>
          <li>Demo request (name, company, use case → opens a deal)</li>
          <li>Event RSVP (auto-tags <em>event-2026-q3-attendee</em>)</li>
        </ul>`,
    },
    {
      url: '/segments', selector: '.pagebar h1', title: 'Segments — saved groups',
      body: `
        <p>Two kinds:
          <strong>Static</strong> = a frozen list of contacts you picked.
          <strong>Dynamic</strong> = a rule that recomputes membership ("tag
          contains X and last-contact within 30 days").</p>
        <div class="dummies">
          <strong>For dummies:</strong> like a Spotify playlist. Static = "the songs
          I picked." Dynamic = "all songs by this artist released this year"
          (grows by itself).
        </div>
        <p><strong>Useful segments:</strong></p>
        <ul>
          <li>"Trial users who haven't paid" (dynamic — rule-based)</li>
          <li>"VIP customers" (static — you chose them)</li>
          <li>"Users in EU subject to GDPR" (dynamic — region-based)</li>
        </ul>`,
    },
    {
      url: '/duplicates', selector: '.pagebar h1', title: 'Duplicates — clean up doubles',
      body: `
        <p>Finds suspected duplicates by email, phone, name, and name+company.
        Review groups and merge — the keeper inherits all interactions, notes,
        tags, deals, and tasks from the others.</p>
        <div class="dummies">
          <strong>For dummies:</strong> over time, the same person sneaks into your
          CRM twice (form submission with a typo, friend forwarded, etc).
          This page finds those doubles so your data stays clean.
        </div>
        <p><strong>Run it when:</strong></p>
        <ul>
          <li>You just imported a CSV — likely duplicates from prior data</li>
          <li>Quarterly hygiene — even five minutes here saves headaches</li>
          <li>Before a big marketing send (so people don't get the email twice)</li>
        </ul>`,
    },
    {
      url: '/reports', selector: '.reports-grid', title: 'Reports — pre-built analytics',
      body: `
        <p>Each card is a deterministic report (no AI). Click <strong>Run</strong>
        for an inline result or <strong>CSV</strong> to download.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the "stats dashboard" of the CRM. Common
          questions ("who hasn't bought in 6 months?") are pre-built, so you
          don't need to write SQL.
        </div>
        <p><strong>Reports here right now:</strong></p>
        <ul>
          <li>Dormant high-value, top intent now, pipeline velocity</li>
          <li>Conversion funnel, deal pipeline summary, lead sources</li>
          <li>Tag distribution (which labels are most/least used)</li>
        </ul>`,
    },
    {
      url: '/tags', selector: '.pagebar h1', title: 'Tags — flexible labels',
      body: `
        <p>Labels you attach to contacts and companies. Edit name / color /
        scope inline. Deleting a tag detaches it from every record using it
        (the confirm shows the count).</p>
        <div class="dummies">
          <strong>For dummies:</strong> like hashtags. Use them when a record
          belongs to a category that doesn't fit neatly into a field —
          "icp," "vip," "event-attendee," "wrong-fit."
        </div>
        <p><strong>Tag systems that work:</strong></p>
        <ul>
          <li>Lifecycle: <em>lead, mql, sql, customer, churned</em></li>
          <li>Source: <em>referral, website, event, cold-outbound</em></li>
          <li>Topic interests: <em>topic:billing, topic:integrations</em></li>
        </ul>`,
    },
    {
      url: '/search', selector: '.searchbar-live', title: 'Search — find anything fast',
      body: `
        <p>Full-text search (FTS5) across contacts, companies, interactions, and
        non-private notes. Hit <strong>⌘K</strong> / <strong>Ctrl+K</strong> from
        anywhere to focus the topnav search and see a live dropdown of top hits.</p>
        <div class="dummies">
          <strong>For dummies:</strong> like Spotlight on Mac or Cmd+Shift+P in
          editors. Type anything that might be in any record. Forget which
          page the info was on — just search.
        </div>
        <p><strong>Tries to find:</strong></p>
        <ul>
          <li>"Stripe" → companies and contacts mentioning Stripe</li>
          <li>"pricing demo" → any interaction or note with those words</li>
          <li>An email fragment, a phone digit, a city, anything</li>
        </ul>`,
    },
    {
      url: '/plugins', selector: '.pagebar h1', title: 'Plug-ins — extend without forking',
      body: `
        <p>Drop a Python file in <code>agent_surface/plugins/</code>, click
        <strong>Reload</strong>, and the plug-in registers its hooks. Click
        a plug-in's name to edit its config or view errors.</p>
        <div class="dummies">
          <strong>For dummies:</strong> like browser extensions, but for your CRM.
          You install little add-ons that listen for events ("a new contact was
          created") and do something useful in response — auto-tag, auto-score,
          auto-create-task.
        </div>
        <p><strong>Real plug-ins shipped:</strong></p>
        <ul>
          <li><em>auto-tag-from-interactions</em> — extracts topics from notes</li>
          <li><em>deal-stage-automation</em> — fires kickoff tasks on wins</li>
          <li><em>example-fit-score</em> — computes ICP score from tags</li>
        </ul>`,
    },
    {
      url: '/connectors', selector: '.pagebar h1', title: 'Connectors — pull data in',
      body: `
        <p>External systems POST signed JSON to <code>/in/&lt;slug&gt;</code>.
        Each delivery is logged raw; routing rules map fields onto contacts
        and interactions.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the "data ingress." When another tool
          (Calendly, Zapier, Slack, your own app) wants to send you data,
          they POST to a URL here, and the CRM converts it into a contact
          or interaction.
        </div>
        <p><strong>Wire these:</strong></p>
        <ul>
          <li>Calendly booking → auto-creates contact + meeting interaction</li>
          <li>Zapier "new Stripe customer" → upsert contact + tag <em>customer</em></li>
          <li>Custom website event "demo viewed" → interaction on contact</li>
        </ul>`,
    },
    {
      url: '/audit', selector: '.pagebar h1', title: 'Audit log — who did what',
      body: `
        <p>Every mutation lands here in the same SQLite transaction as the data
        write. Filter by object type, action, surface, user, or request id.
        Admin only.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the "rewind tape" of your CRM. If somebody
          asks "who deleted Jane?" or "when did this deal change to Lost?", the
          answer is here. Also a paper trail for compliance.
        </div>
        <p><strong>Real reasons to check it:</strong></p>
        <ul>
          <li>A contact has weird data — who edited it last?</li>
          <li>Compliance audit needs a list of who accessed which records</li>
          <li>Debugging: a webhook event fired but the data didn't update?</li>
        </ul>`,
    },
    {
      url: '/settings', selector: '.pagebar h1', title: 'Settings — the admin dock',
      body: `
        <p>The settings page bundles every administrative section: profile,
        users, roles, audit, tags, plus API keys, import/export, and webhook
        subscriptions.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the "control panel" of the CRM. You won't
          come here every day, but when you onboard a teammate or wire up an
          integration, this is where you start.
        </div>
        <p><strong>Most-used sections:</strong></p>
        <ul>
          <li><strong>API keys</strong> — for letting code / agents read+write</li>
          <li><strong>Webhooks</strong> — for sending events out to other apps</li>
          <li><strong>Import/export</strong> — CSV in / CSV out</li>
        </ul>`,
    },
    {
      url: '/settings/users', selector: '.pagebar h1', title: 'Users — your teammates',
      body: `
        <p>Add new users (email + password + built-in role) and change anyone's
        role. Click an email to assign additive RBAC roles on top.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the list of everyone who can log in. Most
          teams have 3–10 entries here. You'll add a teammate, give them a role
          (admin / user / read-only), they sign in with the password you set.
        </div>
        <p><strong>Standard pattern:</strong></p>
        <ul>
          <li>1 admin (you)</li>
          <li>A few <em>user</em> roles for teammates who write data</li>
          <li>A read-only role for stakeholders / auditors who only view</li>
        </ul>`,
    },
    {
      url: '/settings/roles', selector: '.pagebar h1', title: 'Roles & permissions',
      body: `
        <p>Custom roles with permission strings (<code>contact.read</code>,
        <code>deal.write</code>, etc.). Granted on top of the built-in role;
        your service code chooses which permissions matter.</p>
        <div class="dummies">
          <strong>For dummies:</strong> fine-grained access. The built-in roles
          (admin/user/read-only) work for most teams, but if you want
          "Sales can edit deals but not contacts," this is where you build it.
          Skip if you're a one-person team.
        </div>
        <p><strong>Examples worth creating:</strong></p>
        <ul>
          <li><em>Sales</em> — deal.* + task.* + contact.read</li>
          <li><em>Marketing</em> — contact.* + form.* + segment.*</li>
          <li><em>Bookkeeper</em> — deal.read + report.read only</li>
        </ul>`,
    },
    {
      url: '/me', selector: '.pagebar h1', title: 'Your profile',
      body: `
        <p>Change your email / display name / password. See your active
        sessions across devices (revoke any you don't recognize) and your
        RBAC role assignments.</p>
        <div class="dummies">
          <strong>For dummies:</strong> the "me" page. Most people only visit here
          once or twice — when they change their password or laptop. Worth
          checking the sessions list once in a while for unfamiliar entries.
        </div>
        <p><strong>Security habits:</strong></p>
        <ul>
          <li>Rotate password every few months (or use a manager)</li>
          <li>Revoke old sessions from devices you no longer use</li>
          <li>Verify your email is current — password resets go there</li>
        </ul>`,
    },
    {
      url: '/', selector: '.pagebar h1', title: 'Tour complete',
      body: `
        <p class="lede">That's every major page.</p>
        <p>The <strong>?</strong> in the top bar reopens this tour anytime — at
        any step. The framework also remembers where you were if you skip mid-tour.</p>
        <div class="dummies">
          <strong>Next moves if you're new:</strong>
          <ul>
            <li>Add your first 5 real contacts (or import a CSV)</li>
            <li>Create one pipeline that matches your actual sales flow</li>
            <li>Set up one webhook OR one form to bring data in automatically</li>
            <li>Live in the dashboard for a week — that builds your CRM habit</li>
          </ul>
        </div>
        <p>Happy CRM-ing.</p>`,
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
  function prev() {
    const s = getState();
    if (s.step <= 0) return;
    const newStep = s.step - 1;
    setState({ step: newStep });
    const step = TOUR[newStep];
    if (step.url && location.pathname !== step.url) {
      location.href = step.url;
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
        + '<button type="button" class="btn secondary" data-act="back"'
        +   (s.step === 0 ? ' disabled' : '')
        +   '>‹ Back</button>'
        + '<button type="button" class="btn" data-act="goto">Continue ›</button>'
        + '</div>'
      );
      document.body.appendChild(pill);
      pill.addEventListener('click', e => {
        const act = (e.target.dataset || {}).act;
        if (act === 'skip')    dismiss();
        if (act === 'restart') restart();
        if (act === 'back')    prev();
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
      + '<button type="button" class="btn secondary" data-act="back"'
      +   (s.step === 0 ? ' disabled' : '')
      +   '>‹ Back</button>'
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
      if (act === 'back')    prev();
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
