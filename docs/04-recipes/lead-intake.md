# Recipe · Lead intake

> End-to-end: a stranger fills out your public contact form, and ten
> seconds later you have a tagged, scored, segment-ready lead with
> a Slack notification waiting for sales.

## Context

Most CRMs claim "lead intake automation" but ship it as an opaque box.
Here, every step is a service-layer call you can read, override, or
extend. By the end of this recipe you'll have:

- A live public form at `/f/contact-us`.
- Auto-creation of a contact on submission.
- Automatic tag attachment based on the form's `interest` field.
- A topic-extraction plug-in firing on the resulting interaction.
- Auto-scoring against the new interaction.
- (Optional) A Slack notification plug-in pinging your team channel.
- The lead landing in a "fresh leads, last 7 days" dynamic segment.

## Understanding

The pieces:

1. **Form** — defined once via `forms.create`. Public URL
   `/f/contact-us`. Schema declares the fields. Routing declares
   what to do with them.
2. **Submission** — POST to `/f/contact-us`. Service `forms.submit`
   parses, validates, calls downstream services.
3. **Contact resolution** — `contacts.find_by_email` or
   `contacts.create`.
4. **Interaction** — `interactions.log` with
   `type=form_submission`, body = the form's message.
5. **Tags** — `tags.attach` for every tag in routing rules and
   per-`interest` mapping.
6. **Plug-ins** — `on_form_submitted` fires (e.g., Slack), and
   `on_interaction_logged` fires (e.g., auto-tag-from-interactions).
7. **Scoring** — `scoring.maybe_recompute(intent)` runs because the
   interaction is new.
8. **Segment** — a pre-existing dynamic segment with rule
   "interaction within 7 days" picks them up on next evaluation.

All of these happen synchronously in one transaction except scoring
(which fires its own short transaction) and the webhook delivery
(asynchronous).

## Reason

**Why a recipe and not a guide?**

Guides teach one feature. Recipes show what happens when features
compose. Lead intake is the canonical compose-of-everything path in a
CRM — it's what makes the difference between a contacts database and a
sales engine.

**Why "topic extraction" via a plug-in instead of in the form
routing?**

Routing is for deterministic rules (this checkbox → that tag). Topic
extraction is fuzzy ("the body mentions copper, signage, lobby").
That's plug-in territory — it can be deterministic (keyword frequency)
or LLM-driven, swappable per install.

## Result

A working pipeline where:

- Any public submission produces an audit chain of ~5-8 rows in 50ms.
- Sales sees the lead in their dashboard widgets within minutes.
- The lead's intent score is non-zero from day one.
- You can drop in plug-ins to add steps without touching core.

## Recipe — step by step

### 1. Create the form

```bash
python -m agent_surface.cli form create \
  --slug contact-us \
  --name "Contact Us" \
  --schema '{
    "fields": [
      {"key":"name",     "type":"text",     "label":"Name", "required":true},
      {"key":"email",    "type":"email",    "label":"Email","required":true},
      {"key":"interest", "type":"select",   "label":"Interest",
                        "options":["signage","sculpture","consulting"]},
      {"key":"message",  "type":"textarea", "label":"Message"}
    ]
  }' \
  --routing '{
    "tags": ["lead", "form:contact-us"],
    "interest_tag_prefix": "interest:",
    "auto_create_contact": true,
    "match_by_email": true,
    "interaction": {
      "type":  "form_submission",
      "title": "Contact form submission",
      "body":  "{{message}}"
    }
  }' \
  --active
```

(Or use the UI: Forms → New form.)

### 2. Embed the form (or use the auto-rendered page)

The CRM auto-renders `/f/contact-us` from the schema — paste the link
on your site. For a custom-styled embed, POST directly:

```html
<form action="https://crm.example.com/f/contact-us" method="POST">
  <input name="name" required>
  <input name="email" type="email" required>
  <select name="interest">
    <option>signage</option>
    <option>sculpture</option>
    <option>consulting</option>
  </select>
  <textarea name="message"></textarea>
  <button type="submit">Send</button>
</form>
```

Or JSON-fetch:

```js
fetch("https://crm.example.com/f/contact-us", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    name: "Greg Johnson",
    email: "greg@hammerbuild.example",
    interest: "signage",
    message: "Need pricing for a 2m copper lobby piece."
  }),
}).then(r => r.json())
```

### 3. Add a pre-existing dynamic segment for "fresh leads"

```bash
python -m agent_surface.cli segment create-dynamic \
  --name "Fresh leads (7d)" --slug "fresh-leads-7d" \
  --rules '{
    "and": [
      {"tag": {"name": "lead"}},
      {"interaction": {"type": "form_submission", "within_days": 7}}
    ]
  }'
```

### 4. (Optional) Slack notification plug-in

`agent_surface/plugins/slack_lead_notify.py`:

```python
import os, requests

NAME = "slack_lead_notify"
VERSION = "0.1.0"
DESCRIPTION = "Posts to Slack when a lead form is submitted"

SLACK_URL = os.environ.get("SLACK_INCOMING_WEBHOOK_URL")

def on_form_submitted(ctx, form, submission, contact, conn):
    if form["slug"] != "contact-us" or not SLACK_URL:
        return
    text = (f":wave: New lead from contact form: "
            f"{submission['payload'].get('name')} "
            f"({submission['payload'].get('email')}) — "
            f"interest: {submission['payload'].get('interest','?')}")
    try:
        requests.post(SLACK_URL, json={"text": text}, timeout=2)
    except Exception:
        raise   # dispatcher catches + logs
```

Reload:

```bash
python -m agent_surface.cli plugin reload
```

### 5. Re-evaluate the segment (nightly cron does this automatically)

```bash
python -m agent_surface.cli segment evaluate --slug fresh-leads-7d
```

### 6. Watch it happen

Submit a test form:

```bash
curl -sX POST https://crm.example.com/f/contact-us \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Lead","email":"test@example.com",
       "interest":"signage",
       "message":"Looking for pricing on copper signage."}'
```

In the CRM:
- Contacts page → Test Lead exists with tags `lead`, `form:contact-us`,
  `interest:signage`.
- Test Lead's contact page → Timeline has a `form_submission` entry
  with the message as body.
- If auto-tag-from-interactions is enabled, additional `topic:copper`,
  `topic:signage` tags appear (pink).
- Scores card → `intent` has a value with evidence including "Recent
  form submission".
- Segments page → Fresh leads (7d) now contains Test Lead.
- Slack (if configured) → notification posted.

### 7. Inspect the audit chain

```sql
SELECT ts, action, object_type, object_id, surface
FROM audit_log
WHERE request_id IN (
  SELECT request_id FROM audit_log
  WHERE object_type='form_submission'
  ORDER BY id DESC LIMIT 1
)
ORDER BY ts;
```

Returns:
- form_submission.received (surface=ui)
- contact.created
- tag.created (×3 if first-run for these tags)
- tag.attached (×3)
- interaction.logged
- (plug-in) tag.created/attached for topic:*
- (plug-in) interaction.logged for "Auto-tagged with: copper, signage"
- scoring.updated

That's the full chain, atomic, replayable.

## Operations

### Monitoring lead flow

A dashboard widget runs `report run --name top_intent_now` or queries:

```sql
SELECT COUNT(*) FROM contacts c
JOIN contact_tags ct ON ct.contact_id = c.id
JOIN tags t ON t.id = ct.tag_id
WHERE t.name = 'lead' AND c.created_at > strftime('%s','now','-24 hours');
```

Drop in a 24h / 7d / 30d count.

### Spam handling

The public form is a target. Mitigations:
- Form has a hidden `honeypot` field; submissions with non-empty
  value are silently 200'd but discarded.
- Rate-limit by IP at the reverse proxy: 60/min/IP for `/f/*`.
- Tag spammy submissions (a plug-in `on_form_submitted` that runs a
  basic heuristic) and segment them out.

### Disabling temporarily

```bash
python -m agent_surface.cli form update --id 1 --active 0
```

The form returns 410 Gone. Old links still work for `GET` (showing a
maintenance message); POSTs are rejected.

## Fine-tuning

### Routing dynamics

`routing.interest_tag_prefix: "interest:"` means every value of the
`interest` field becomes a tag — so `interest:signage` is auto-created
if missing. Use this for any select/multiselect field.

For tags that should be color-coded:

```json
"tags": [
  {"name": "lead",                "color": "#c47a4a"},
  {"name": "form:contact-us",     "color": "#738c5e"}
]
```

(Plain string forms also work; default color used.)

### Routing dependencies

Use template variables to reference submission values:

```json
"interaction": {
  "title": "Form: {{interest}}",
  "body":  "{{message}}\n\n— {{name}} <{{email}}>"
}
```

### Captcha / hCaptcha integration

For high-value forms:
1. Add hidden `h-captcha-response` field to the schema.
2. Write a plug-in `on_form_submitted` that calls hCaptcha verify and
   raises if it fails (the dispatcher catches but audits the rejection).
3. OR validate at the transport: a small middleware that checks the
   captcha field before the form-submit route runs.

### Welcome email plug-in

```python
# agent_surface/plugins/welcome_email.py
import os, requests
NAME = "welcome_email"
VERSION = "0.1.0"
DESCRIPTION = "Sends a welcome email to new form leads"
RESEND_KEY = os.environ.get("RESEND_API_KEY")

def on_form_submitted(ctx, form, submission, contact, conn):
    if form["slug"] != "contact-us" or not contact or not RESEND_KEY:
        return
    requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}"},
        json={
            "from": "noreply@example.com",
            "to":   contact["email"],
            "subject": "Thanks for reaching out",
            "text":    f"Hi {contact['full_name']},\n\nWe got your message...",
        },
        timeout=5,
    )
```

## Maximizing potential

1. **Form per campaign.** `partner-bizcard-2026`, `summer-art-fair`,
   `referral-friend-of-a-friend`. The `source` tag attribution
   gives you per-campaign analytics for free.

2. **Auto-create the deal too.** Plug-in `on_form_submitted` that
   creates a deal in stage[0] of your "Inbound Leads" pipeline when
   `interest != consulting`. Sales sees a card on the pipeline
   instead of just a contact.

3. **Multi-step intake flow.** A second form `qualify-lead` that
   only shows up if the contact has `lead` tag. Submitting it
   moves the deal to stage[1] and adds qualifying fields.

4. **LLM-based interest classification.** When the `interest` select
   is too coarse, a plug-in reads `body` and assigns one of N
   pre-defined buckets via an LLM call. Replace the routing's
   coarse `interest:*` with a finer-grained tag.

5. **Auto-assign to the right salesperson.** Plug-in that, on
   `on_form_submitted`, looks at the contact's `location` and the
   team's territory rules, then sets `contact.assigned_to`. Sales
   wakes up to leads pre-routed to them.

6. **Public form as the only entry path.** Disable contact creation
   for non-admins. All new contacts come through forms. Audit and
   provenance are uniform.

7. **A/B test forms.** Two slugs, differing only in copy. Compare
   submission rate and downstream conversion. The same lead-intake
   plumbing runs both.

## Anti-patterns

- **Stuffing logic into routing JSON.** If you find yourself
  inventing routing predicates ("if interest=signage and message
  contains 'urgent', then..."), that's a plug-in.
- **Sending the Slack notification before the contact is created.**
  Wait for `on_form_submitted` (post-contact) not pre-write. You
  don't want to ping the team about a submission that failed.
- **Trusting form data unverified.** Run `do_not_contact` and email
  validation in the form-submit service before any routing fires.
  Routing should never override DNC.
- **Treating every form submission as a "new" contact.**
  `match_by_email: true` (default) reuses the existing contact —
  important for repeat submitters; you want a richer interaction
  history, not duplicate contacts.

## Where to look in code

- `backend/services/forms.py` — submit, routing engine
- `backend/main.py:1283` — `POST /f/{slug}` route
- `backend/main.py:1158` — `GET /f/{slug}` auto-render
- `migrations/0002_v1.sql` — `forms`, `form_submissions` schema

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](../../README.md) — human entry point
- [AGENTS.md](../../AGENTS.md) — AI agent operating contract
- [CLAUDE.md](../../CLAUDE.md) — Claude Code project conventions
- [SCHEMATICS.md](../../SCHEMATICS.md) — ASCII architecture diagrams
- [Blueprint.md](../../Blueprint.md) — product spec
- [prompt.md](../../prompt.md) — build-from-scratch prompt

**Wiki root** (`docs/`)
- [README.md](../README.md) — wiki index
- [00-start-here.md](../00-start-here.md) — 10-minute orientation

**01 — Concepts** (`docs/01-concepts/`) — why each piece exists
- [service-layer.md](../01-concepts/service-layer.md)
- [service-context.md](../01-concepts/service-context.md)
- [audit-and-webhooks.md](../01-concepts/audit-and-webhooks.md)
- [plugins.md](../01-concepts/plugins.md)
- [scoring.md](../01-concepts/scoring.md)
- [segments.md](../01-concepts/segments.md)
- [portals.md](../01-concepts/portals.md)
- [inbound.md](../01-concepts/inbound.md)
- [search.md](../01-concepts/search.md)

**02 — Guides** (`docs/02-guides/`) — step-by-step how-tos
- [install.md](../02-guides/install.md)
- [first-contact.md](../02-guides/first-contact.md)
- [your-first-pipeline.md](../02-guides/your-first-pipeline.md)
- [import-export.md](../02-guides/import-export.md)
- [deploying.md](../02-guides/deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](../03-reference/data-model.md)
- [api.md](../03-reference/api.md)
- [cli.md](../03-reference/cli.md)
- [mcp.md](../03-reference/mcp.md)
- [plugins.md](../03-reference/plugins.md)
- [webhooks.md](../03-reference/webhooks.md)
- [errors.md](../03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](lead-intake.md) **← you are here**
- [dormant-revival.md](dormant-revival.md)
- [agent-workflows.md](agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
