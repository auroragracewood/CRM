# Concept · Inbound

> Public HMAC-signed POST receivers at `/in/{slug}` that let external
> systems push events into the CRM as first-class interactions,
> contacts, or whatever the routing rules say.

## Context

A modern CRM is never an island. Stripe wants to tell it about new
customers. n8n wants to push form submissions. Calendly wants to
report new meetings. Your home-grown internal tools want to ping it
when a doc is signed. Every one of these systems can POST a webhook —
but the CRM needs a place to receive, verify, parse, and route those
webhooks safely.

The naive approach: write a custom FastAPI route for every integration.
Days per source, hard to monitor, no shared audit. The slightly better
approach: a generic "webhook receiver" page that turns the JSON into
an interaction. We do that, with a few important properties:

- HMAC signature verification per receiver (you can rotate one
  secret without affecting others).
- Per-receiver routing rules (parse fields, attach tags, create
  contacts as needed).
- Raw-body logging FIRST, so even if parsing fails you still have the
  request to debug.
- A standard interaction shape produced regardless of source.

## Understanding

Two tables:

```
inbound_endpoints
  id              INTEGER PK
  slug            TEXT UNIQUE         used in /in/{slug}
  name            TEXT
  shared_secret   TEXT                used for HMAC-SHA256 verification
  routing_json    TEXT                parse + tag rules
  active          BOOLEAN
  last_received_at INTEGER
  created_at      INTEGER
  created_by      INTEGER  → users(id)

inbound_events
  id              INTEGER PK
  endpoint_id     INTEGER  → inbound_endpoints(id) ON DELETE CASCADE
  ts              INTEGER  unix seconds
  raw_body        TEXT     verbatim request body (no parsing applied)
  raw_headers     TEXT     JSON of request headers
  signature_ok    BOOLEAN  did HMAC verify?
  status          TEXT     'received' | 'parsed' | 'contact_linked' | 'error'
  error           TEXT     parse error message if any
  contact_id      INTEGER  resolved contact (if routing produced one)
  interaction_id  INTEGER  the resulting interaction row
```

One service: `backend/services/inbound.py`. The public route:
`POST /in/{slug}` in `backend/main.py`.

## Reason

**Why a generic receiver and not custom routes per source?**

- **Reusability.** Stripe and n8n and Calendly all POST JSON.
  Differences are in the schema, not the protocol. One receiver +
  per-endpoint routing covers all three.
- **Operational consistency.** Every inbound event goes through the
  same observability path (raw log, signature verify, parse, route)
  so debugging is the same regardless of source.
- **No code change per integration.** Adding a new source = creating
  a new endpoint row + routing rules. No deploy.

**Why log raw body BEFORE parsing?**

Because if parsing fails, you need to see what arrived. With
parsing-then-store, a malformed payload is just gone. With
store-then-parse, you can investigate forever. Disk is cheap;
mystery debug sessions are expensive.

**Why HMAC and not just secret-in-URL or API key?**

- Secret-in-URL: visible in proxy logs, browser history. Bad.
- API key in header: better, but the body could still be tampered
  with by an attacker who can replay or intercept.
- HMAC of the body with a shared secret: proves the sender knew the
  secret AND that the body wasn't modified in transit. Industry
  standard for webhooks (Stripe, GitHub, etc.). Cheap to implement
  on both sides.

**Why route via JSON rules and not Python per-endpoint?**

Same reasoning as segments: JSON is serializable, surface-agnostic,
LLM-generatable. The 80% case (extract email, set type, attach tags)
is expressible in declarative JSON. The 20% case is a plug-in.

## Result

What inbound gives you:

- A single URL pattern any external system can POST to.
- HMAC-verified, auditable ingestion with the raw request preserved.
- Auto-creation or linking of contacts based on routing rules.
- A standardized interaction shape regardless of source.
- A queryable history of every inbound POST, even failed ones.
- A plug-in hook (`on_inbound_received`) for custom logic per source.

## Use case 1 — Stripe customer.created → CRM contact

Stripe POSTs JSON to `/in/stripe`. The endpoint's routing rules:

```json
{
  "match_event_field": "type",
  "match_event_value": "customer.created",
  "extract": {
    "email":      "data.object.email",
    "full_name":  "data.object.name",
    "external_id":"data.object.id"
  },
  "auto_create_contact": true,
  "tag_with":   ["source:stripe", "stripe_customer"],
  "interaction": {
    "type":  "system",
    "title": "Stripe customer created",
    "body":  "Stripe customer {{external_id}} ({{email}}) created."
  }
}
```

When Stripe POSTs:
1. Raw body + headers stored.
2. HMAC verified against `shared_secret`.
3. JSON parsed; `type` matches; routing applies.
4. Email extracted → `contacts.find_by_email` → if missing,
   `contacts.create` (audit row + webhook).
5. Tags attached.
6. Interaction logged (audit row + webhook).
7. `inbound_events` row updated to `status='contact_linked'`,
   `contact_id` and `interaction_id` populated.
8. Returns 200 to Stripe.

Stripe sees one HTTP 200; the CRM has a new contact, a tagged record,
a timeline entry, and a full debug trail.

## Use case 2 — n8n nodes posting form submissions

You have a marketing form on a partner site. n8n receives the form,
maps fields, and POSTs to `/in/partner-form`. The routing extracts
email + name + topic interest + free-text body. Same flow. You get
contacts coming in from arbitrary external systems without writing
CRM-side code per source.

## Use case 3 — your own internal scripts

A cron on another machine POSTs a "deal stalled" event when one of
your home-grown systems detects it. Routing creates a `system`
interaction noting "stall detected." Plug-in
`on_inbound_received(source='internal-stall-detector', ...)`
adjusts the contact's `risk` score by recomputing.

## Operations

### Creating an endpoint

UI: Connectors page → New connector. Form lets you pick a slug, name,
generate a secret, paste routing JSON.

REST:
```bash
curl -X POST http://localhost:8000/api/inbound-endpoints \
  -H "Authorization: Bearer <admin-key>" \
  -d '{
    "slug":"stripe",
    "name":"Stripe events",
    "routing": {...},
    "active": true
  }'
# returns {"endpoint": {"id":..., "shared_secret":"...", ...}}
```

The `shared_secret` is returned ONCE (like an API key); the CRM
stores its hash + display digest. Save the raw secret on the sender
side.

CLI:
```bash
python -m agent_surface.cli inbound create --slug stripe --name "Stripe events" \
  --routing-file routing.json
```

### Receiving

The sender POSTs to `/in/{slug}` with header
`X-Signature: sha256=<hex>` where `hex = hmac.new(secret, body,
sha256).hexdigest()`.

If the sender uses Stripe's signing convention
(`Stripe-Signature: t=...,v1=...`), the CRM's Stripe-specific
endpoint type knows how to parse it. Configure the
`signature_scheme` field on the endpoint row: `simple` (default) or
`stripe-v1`.

### Inspecting

UI: Connectors page → connector → Events tab → recent events with
status.

REST: `GET /api/inbound-endpoints/{id}/events?limit=100`.

CLI: `inbound events --id N`.

Each event shows status, raw body (truncated), parsed contact_id /
interaction_id, error if any.

### Re-running a failed event

If parsing failed (`status='error'`), you can fix the routing rules
and replay the event:

```bash
python -m agent_surface.cli inbound replay --event-id 234
```

The event is re-processed using current routing. The original row
stays (audit); a new row records the replay.

### Rotating a secret

```bash
python -m agent_surface.cli inbound rotate-secret --id 5
```

Returns the new secret. The old one stops working immediately. Have
the sender configured to accept BOTH for a transition window if the
source can't be updated atomically.

### Deactivating

UI / REST / CLI / MCP: flip `active=false`. The endpoint returns 404
on hits. Useful when investigating runaway senders without losing
audit history.

## Fine-tuning

### Routing rule shape

The default rule grammar (handled by `_apply_routing` in
`inbound.py`):

```json
{
  "match_event_field":   "...",     // JSONPath into body; optional gate
  "match_event_value":   "...",
  "extract":             {...},     // {field_name: JSONPath}
  "auto_create_contact": true,
  "match_by_email":      true,
  "tag_with":            [...],
  "interest_tag_prefix": "interest:",
  "interaction": {
    "type":  "...",
    "title": "template",
    "body":  "template",
    "channel": "..."
  },
  "default_consent": {"channel": "email", "status": "granted"}
}
```

Add rule types by editing `inbound.py`. Each rule node is a small
pure function from `(body, headers) -> side_effects_list`.

### Per-source signature schemes

The CRM ships with `simple` (header `X-Signature: sha256=hex`) and
`stripe-v1`. Add new ones by registering a verifier:

```python
SIGNATURE_VERIFIERS["github-v1"] = lambda body, headers, secret: ...
```

Set the endpoint's `signature_scheme` accordingly.

### Idempotency at the receiver

External senders sometimes retry. Configure the endpoint with
`dedupe_by_path` (a JSONPath into the body that identifies the
event uniquely, e.g., `id` or `event_id`). The receiver checks a
small ring buffer of recent ids; duplicates short-circuit to
status `received` without re-processing.

### Rate-limiting per endpoint

A misbehaving sender can flood. Each endpoint has `rate_per_min`
(default 600). Excess returns 429 and is logged.

### Filtering at the receiver

For high-volume sources where you only want a subset, configure
`accept_only`:

```json
{
  "accept_only": [
    {"path": "type", "in": ["customer.created", "customer.updated"]}
  ]
}
```

Events not matching are stored (status='received') but not parsed.
Saves the parsing budget for events you care about.

### Outbound from inbound

`on_inbound_received(ctx, endpoint, event, conn)` plug-in hook
fires after parsing. Plug-ins can:

- Trigger webhook outbox events ("we received an inbound from
  Stripe").
- Call other services to enrich (look up the contact's company by
  domain, attach industry tag).
- Push to Slack ("new lead from partner form").

## Maximizing potential

1. **The CRM as a webhook hub.** Other internal services treat the
   CRM's inbound endpoints as their normalizer. Send raw events; let
   the CRM tag, dedupe, link to contacts, and emit clean events
   downstream via webhooks. The CRM becomes the canonical event bus.

2. **Bidirectional Stripe sync.** Inbound Stripe customer/subscription
   events update CRM contacts and consent. Outbound CRM
   contact.updated events POST to your Stripe-syncer service. Same
   shape both ways.

3. **Form-to-CRM funnel without a backend.** A static-site form
   submits directly to `/in/contact-us` (with the secret obfuscated
   or with a serverless function proxying). No backend needed beyond
   the CRM.

4. **Inbound-driven scoring.** Each inbound event hits scoring's
   recency rule; the contact's `intent` score jumps. Score reads
   like a real-time signal even without polling.

5. **Calendar integration.** Calendly / Google Calendar webhook on
   meeting scheduled → inbound endpoint → meeting interaction
   logged → scoring updated → task auto-created via plug-in.

6. **Multi-tenant inbound.** Each customer of your platform gets
   their own endpoint slug (e.g., `/in/tenant-{tenant_id}-stripe`).
   Routing rules can read the path and assign to the tenant's data
   silo. (Outside our current single-tenant scope but feasible if
   you fork.)

7. **Cross-CRM bridge.** Inbound from a partner's CRM whenever a
   shared deal changes. Outbound webhook to their CRM on your
   changes. Both stay in sync without coupling.

## Anti-patterns

- **Disabling signature verification.** Even for "trusted internal"
  senders. The cost of HMAC is microseconds; the cost of a forged
  inbound is debugging hell.
- **Routing rules that branch into the LLM space.** Routing should
  be deterministic. If you need "interpret this free-text and figure
  out who it's about," that's a plug-in, not a routing rule.
- **Returning detailed error messages to the sender.** The 4xx body
  should say "verification failed" or "invalid event", not "missing
  field X in body". Verbose errors help attackers shape their
  payloads.
- **Auto-creating contacts without any signal.** A POST to inbound
  with junk fields should produce an interaction or an error, not
  a contact with `full_name=""`. Validate first.

## Where to look in code

- `backend/services/inbound.py` — endpoint CRUD, event ingestion,
  routing engine
- `backend/main.py:1786` — `POST /in/{slug}` route
- `migrations/0005_v3.sql` — `inbound_endpoints` + `inbound_events`
  schema

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
- [service-layer.md](service-layer.md)
- [service-context.md](service-context.md)
- [audit-and-webhooks.md](audit-and-webhooks.md)
- [plugins.md](plugins.md)
- [scoring.md](scoring.md)
- [segments.md](segments.md)
- [portals.md](portals.md)
- [inbound.md](inbound.md) **← you are here**
- [search.md](search.md)

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
