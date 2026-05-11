# Guide · Your first contact

> Five minutes from a fresh install to a contact in the system with a
> logged interaction, a tag, and a consent record. Touches every layer
> of the service stack so you see what's actually happening.

## Context

The fastest way to understand a CRM is to create one contact and walk
them through the basic lifecycle: appearance, conversation, opt-in,
tagging. This guide does that in four different ways — UI, REST,
CLI, and MCP — to make the surface-equivalence concrete. After this
you'll know that yes, every transport really does call the same
service-layer functions.

## Understanding

A "contact" in this CRM is a row in the `contacts` table. A row
becomes useful through accumulation:

- `contacts.create` — the row exists.
- `interactions.log` — the row has a timeline entry.
- `tags.attach` — the row is grouped with similar rows.
- `consent.record` — the row's permission state is known.
- `scoring.compute_for_contact` — the row has comparable scores.

Each of those is a service-layer call. Each writes an audit row.
Each enqueues at least one webhook event. Each dispatches plug-in
hooks.

This guide assumes you've completed [install](install.md) and have
the server running on `localhost:8000` with at least one admin user.

## Reason

**Why walk through four surfaces in one guide?**

Because the architectural promise of this CRM ("same behavior
everywhere") is abstract until you do it yourself. After running the
same operation through UI, REST, CLI, and MCP, you'll never again
have to ask "wait, will it also work via X?" — you'll know.

**Why these specific four actions and not more?**

They cover the four canonical concerns of CRM data:

1. **Identity** (create) — does this person exist in our records?
2. **Activity** (interaction) — what's our history with them?
3. **Categorization** (tag) — what kind of person are they?
4. **Permission** (consent) — what are we allowed to do?

Deals, tasks, forms, portals are valuable but secondary — they exist
to act on contacts whose identity/activity/category/permission are
already established.

## Result

After this guide you'll have:

- One contact named "Maya Sato" (or whoever) in the system.
- One logged interaction on her timeline.
- One tag attached to her.
- One consent record for the email channel.
- Recomputed scores reflecting all of the above.
- An audit log with 5+ rows tracing what just happened, all tagged
  with the same `request_id` if you set one.

## Use case — four ways to do the same thing

### Path 1 — through the UI

1. Open `http://localhost:8000` and sign in.
2. Click **Contacts** in the topnav → **New contact**.
3. Fill in:
   - Full name: Maya Sato
   - Email: maya@blueriver.media
   - Phone: +1 604-555-0188
   - Title: Marketing Director
4. Save. You land on Maya's contact page.
5. Scroll to **Timeline** → fill the small form:
   - Type: meeting
   - Title: Coffee chat
   - What happened: "Talked through their fall editorial calendar.
     Interested in copper-themed feature."
6. Click **Log interaction**. The row appears below.
7. Scroll to **Tags** → top of page → add tag `vip` (create the tag
   inline if it doesn't exist; pick a color).
8. Scroll to **Consent** card → record `email · granted · source:
   manual`.
9. Refresh. Scores card has populated.

### Path 2 — through the REST API

Generate an API key first: Settings → API keys → New key. Save the
raw key string.

```bash
KEY="sk_live_..."
BASE="http://localhost:8000"
REQ="firstcontact-rest-$(date +%s)"
```

Create the contact:

```bash
curl -sX POST $BASE/api/contacts \
  -H "Authorization: Bearer $KEY" \
  -H "X-Request-Id: $REQ" \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Maya Sato",
       "email":"maya@blueriver.media",
       "phone":"+1 604-555-0188",
       "title":"Marketing Director"}'
# {"ok":true,"contact":{"id":2, ...}}
```

Note the returned `id`. Suppose it's `2`.

```bash
CONTACT_ID=2

curl -sX POST $BASE/api/interactions \
  -H "Authorization: Bearer $KEY" \
  -H "X-Request-Id: $REQ" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"meeting\",\"contact_id\":$CONTACT_ID,
       \"title\":\"Coffee chat\",
       \"body\":\"Talked through their fall editorial calendar.\"}"

curl -sX POST $BASE/api/tags \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"vip","color":"#c47a4a","scope":"contact"}'
# {"ok":true,"tag":{"id":1,...}}

curl -sX POST $BASE/api/contacts/$CONTACT_ID/tags/1 \
  -H "Authorization: Bearer $KEY" \
  -H "X-Request-Id: $REQ"

curl -sX POST $BASE/api/consent \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"contact_id\":$CONTACT_ID,\"channel\":\"email\",
       \"status\":\"granted\",\"source\":\"manual\"}"

curl -sX POST $BASE/api/contacts/$CONTACT_ID/score \
  -H "Authorization: Bearer $KEY" \
  -H "X-Request-Id: $REQ"
```

Verify in the UI: Maya's page now shows the timeline entry, tag,
consent, and scores — same as if you'd done it through the UI.

### Path 3 — through the CLI

```bash
python -m agent_surface.cli contact create \
  --name "Maya Sato" --email "maya@blueriver.media" \
  --phone "+1 604-555-0188" --title "Marketing Director"
# {"ok":true,"contact":{"id":2,...}}

python -m agent_surface.cli interaction log \
  --type meeting --contact-id 2 \
  --title "Coffee chat" \
  --body "Talked through their fall editorial calendar."

python -m agent_surface.cli tag create --name vip --color "#c47a4a" --scope contact
python -m agent_surface.cli tag attach --tag-id 1 --contact-id 2

python -m agent_surface.cli consent record \
  --contact-id 2 --channel email --status granted --source manual

python -m agent_surface.cli score contact --id 2
```

### Path 4 — through MCP

Assuming you've configured an MCP client (Claude Code, Cursor, etc.)
to point at `agent_surface/mcp_server.py`:

```python
# From an MCP-driven agent:
contact = create_contact(
    name="Maya Sato",
    email="maya@blueriver.media",
    phone="+1 604-555-0188",
    title="Marketing Director",
)

log_interaction(
    type="meeting",
    contact_id=contact["id"],
    title="Coffee chat",
    body="Talked through their fall editorial calendar.",
)

tag = create_tag(name="vip", color="#c47a4a", scope="contact")
tag_contact(contact_id=contact["id"], tag_id=tag["id"])

record_consent(
    contact_id=contact["id"],
    channel="email",
    status="granted",
    source="manual",
)

score_contact(contact_id=contact["id"])
```

Same audit chain. Same webhook events. Same plug-in dispatches.

## Operations

### Reading what just happened

In `crm.db`:

```sql
SELECT id, ts, surface, action, object_type, object_id
FROM audit_log
WHERE request_id = 'firstcontact-rest-...'   -- the one you set
ORDER BY ts;
```

You'll see the full chain:
- `contact.created` (object_type=contact)
- `interaction.logged` (object_type=interaction)
- `tag.created` (object_type=tag, only if it was new)
- `tag.attached` (object_type=contact_tag)
- `consent.recorded` (object_type=consent)
- `score.recomputed` (object_type=contact)

Plus possibly `plugin.*` rows from the auto-tag plug-in if it
extracted topics from your interaction body.

### Verifying webhook events

```sql
SELECT id, event_type, status, attempts
FROM webhook_events
ORDER BY id DESC LIMIT 10;
```

Each event you triggered is here. If you have no subscriptions
configured, the table is empty by design.

### Repeating with `seed_demo.py`

If you want a thicker dataset to play with, run:

```bash
python seed_demo.py
```

It does this guide's steps for 5 contacts at once, plus deals,
tasks, and a form. Idempotent on contacts (existing emails are
skipped).

## Fine-tuning

### Doing it idempotently

Pass an `Idempotency-Key` header on each REST call:

```bash
curl -sX POST $BASE/api/contacts \
  -H "Idempotency-Key: maya-first-create-$REQ" \
  ...
```

A second call with the same header returns the original result
instead of trying to create again (would 409 on email collision
otherwise).

### Choosing a `request_id` strategy

For one-off CLI work, the auto-generated UUID is fine. For agent
runs, pre-decide a request_id format:

```
<run_kind>-<contact_slug>-<unix_ts>
onboard-acme-1715000000
import-csv-batch-42
hot-lead-sweep-1715000000
```

Searchable in audit by `request_id LIKE 'onboard-%'` etc.

### Plug-in side effects

If you have the auto-tag-from-interactions plug-in enabled (it ships
with the demo and is enabled by default), the `meeting` interaction
you logged will produce `topic:*` tags on the contact in addition
to the manual `vip` tag. Verify on Maya's page → Tags card.

To disable it:

```bash
python -m agent_surface.cli plugin disable --id 1
```

(or whichever id `auto_tag_from_interactions` has.)

### Time travel

If you need a backdated interaction (e.g., importing historical
data), pass `occurred_at`:

```bash
python -m agent_surface.cli interaction log \
  --type call --contact-id 2 \
  --title "Pre-existing call" \
  --body "..." \
  --occurred-at 1700000000
```

(Wire the `--occurred-at` flag into the parser if it isn't there
yet; the service function already accepts it.)

## Maximizing potential

1. **Use this guide as an onboarding ritual.** Have every new
   teammate (engineer, salesperson, ops) walk through it. They'll
   internalize the model in 10 minutes rather than reading docs
   for an hour.

2. **Use it as a smoke test.** Run the four paths as part of CI
   against a fresh `crm.db`. If any one fails, transport has
   diverged from service.

3. **Build a "first contact" plug-in starter.** Drop a plug-in that
   listens to `on_contact_created` and sends you a Slack note for
   your own contacts only. The CRM becomes your personal CRM
   without much work.

4. **Use scoring evidence to refine the rules.** After 20 contacts
   you've logged interactions for, look at the evidence trails in
   `contact_scores.evidence_json`. The rules will reveal patterns:
   are recency deltas right? Is `seniority` over-weighted? Tune the
   rules then bulk-recompute.

5. **Capture this run as a fixture.** Export `crm.db` after this
   guide as `tests/fixtures/seed-after-firstcontact.db`. Use it
   as a baseline for any test that needs "a system with a single
   reasonable contact."

## Anti-patterns

- **Doing it once and forgetting it.** This is the cheapest test
  of the system's invariants. Rerun after every significant change
  to service code.
- **Skipping `consent.record` "because it's just demo data."**
  The consent surface is load-bearing; don't normalize skipping it.
- **Hand-rolling the contact in SQL.** Bypasses validation, audit,
  webhooks, and plug-ins. Always go through the service layer.
- **Sharing the admin's API key for every transport.** Each operator
  gets their own key. Audit by `api_key_id` becomes meaningful only
  if keys aren't shared.

## Where to look in code

- `backend/services/contacts.py` — `create`, `update`, `find_by_email`
- `backend/services/interactions.py` — `log`
- `backend/services/tags.py` — `create`, `attach`, `detach`
- `backend/services/consent.py` — `record`, `list_for_contact`
- `backend/services/scoring.py` — `compute_for_contact`
- `ui/contacts.html`, `ui/contact.html` — UI surface
- `backend/api.py:146-200` — REST surface for contacts
- `agent_surface/cli.py:88-150` — CLI surface
- `agent_surface/mcp_server.py:92-160` — MCP surface

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
- [install.md](install.md)
- [first-contact.md](first-contact.md) **← you are here**
- [your-first-pipeline.md](your-first-pipeline.md)
- [import-export.md](import-export.md)
- [deploying.md](deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](../03-reference/data-model.md)
- [api.md](../03-reference/api.md)
- [cli.md](../03-reference/cli.md)
- [mcp.md](../03-reference/mcp.md)
- [plugins.md](../03-reference/plugins.md)
- [webhooks.md](../03-reference/webhooks.md)
- [errors.md](../03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](../04-recipes/lead-intake.md)
- [dormant-revival.md](../04-recipes/dormant-revival.md)
- [agent-workflows.md](../04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
