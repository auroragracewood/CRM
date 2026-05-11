# Concept · Audit log + webhook outbox

> Every mutation in this CRM leaves two side-effect rows — one for
> internal accountability, one for external propagation — written in
> the same transaction as the data change.

## Context

If a CRM cannot answer "who did this and when?" reliably, it cannot be
used for anything serious. Salespeople blame each other. Compliance
audits fail. Customer service can't reconstruct a complaint. Sales
operations can't tell whether a number was "always 50" or "fixed from
500 yesterday."

If a CRM cannot reliably tell external systems "this changed", every
downstream integration becomes a polling loop that hammers the database
and still misses fast updates. Subscribers get out of sync. The CRM
becomes the source of subtle data drift.

These two concerns — internal traceability and external propagation —
are the same engineering problem at heart: "every mutation must leave
a durable record that something downstream can read." We solve them
with the same pattern (the transactional outbox), differently named for
their two consumers (audit log for humans, webhook events for
integrators).

## Understanding

Two tables. Both written inside the service-layer transaction.

### `audit_log`

One row per service-layer mutation. Insert-only (no DELETEs, no UPDATEs).

```
id              INTEGER PK
ts              INTEGER  unix seconds
user_id         INTEGER  who acted (always set; system actions use user_id=1)
api_key_id      INTEGER  which key, or NULL for cookie/CLI/MCP/system
surface         TEXT     ui|rest|cli|mcp|cron|plugin|webhook|system
action          TEXT     'contact.created', 'deal.stage_moved', ...
object_type     TEXT     'contact','deal','webhook','note','plugin', ...
object_id       INTEGER  the row's id
before_json     TEXT     JSON of the row state before, NULL for CREATE
after_json      TEXT     JSON of the row state after,  NULL for DELETE
request_id      TEXT     UUID; joins multiple rows of one logical request
```

### `webhook_events`

One row per matching subscription per mutation. Mutable (status, attempts).

```
id              INTEGER PK
webhook_id      INTEGER  → webhooks.id
event_type      TEXT     'contact.created'
payload_json    TEXT     JSON; this is the receiver's request body
status          TEXT     pending|retrying|delivered|failed
attempts        INTEGER  delivery attempt count
response_status INTEGER  last HTTP code from receiver
response_body   TEXT     last response body (truncated 8KB)
next_attempt_at INTEGER  unix seconds; worker waits until this
delivery_id     TEXT     UUID; sent as X-CRM-Delivery-ID
created_at      INTEGER  unix seconds
```

## Reason

**Why this pattern and not alternatives?**

Three competing designs and why we rejected them:

1. **Audit-row-after-commit.** Faster (the audit row isn't in the hot
   path), but creates moments where the data committed but the audit
   row didn't. Forensic queries miss writes. Compliance fails. Hard NO.

2. **Synchronous webhook delivery.** Service function calls `requests.
   post()` to the subscriber inside its transaction. If the subscriber
   is slow, the writer holds the SQLite write lock — every other writer
   waits. If the subscriber is down, the writer fails — but the data
   write rolled back, so the change "didn't happen" from the CRM's
   point of view but a partial write reached the subscriber. Hard NO.

3. **Asynchronous outbox written AFTER commit, in the same handler.**
   Common pattern, ALMOST right. Fails when the process dies between
   commit and outbox insert. The data committed; the outbox row never
   existed; the subscriber never hears about it. Hard NO for a CRM,
   though acceptable for less-critical systems.

The transactional outbox (this CRM's choice) writes both the audit
row and the webhook event row INSIDE the same transaction as the data.
If the process dies before commit, nothing happened. If after, all
three rows are durable and a separate worker — which can restart freely
— is responsible for picking up and delivering. The subscriber is
guaranteed to learn about every committed change, eventually.

This pattern is well-known; it's the same one used by every serious
event-sourced system. We get it for free because SQLite gives us
atomic multi-table writes within a single transaction.

## Result

What audit + outbox give you:

- **Truthful audit answers.** Compliance investigation succeeds. "Did
  Maya delete that contact?" → SELECT in 5ms. The answer is yes/no,
  not "probably."
- **Replayable history.** With `before_json` + `after_json` on every
  row, you can compute the state of any object at any past timestamp
  by walking the audit log forward.
- **Reliable webhook integrators.** Subscribers who dedupe on
  `X-CRM-Delivery-ID` and process events idempotently get an at-least-
  once stream they can trust.
- **Free event bus.** Your data warehouse can `SELECT * FROM
  audit_log WHERE id > last_seen_id LIMIT 1000` and ingest the CRM's
  change stream without adding Kafka.
- **Cheap rollback.** Bad import? Find the request_id in audit, walk
  the rows newest-to-oldest, reverse each with the `before_json`
  state.

## Use case 1 — investigating a complaint

A client emails: "Our company name is wrong in the CRM, but I never
changed it. Who did?"

```sql
SELECT ts, user_id, api_key_id, surface, action, before_json, after_json
FROM audit_log
WHERE object_type='company' AND object_id=42 AND action='company.updated'
ORDER BY ts DESC LIMIT 10;
```

Returns the last 10 updates with full before/after diffs. You can see
the name change, who did it, on which surface, and on what date —
within seconds.

If you also want every write across the same logical request (maybe
the change cascaded?):

```sql
SELECT * FROM audit_log
WHERE request_id = '<the-request-id-from-above>'
ORDER BY ts;
```

## Use case 2 — Stripe → CRM via webhooks (inbound)

You configure Stripe to fire `customer.subscription.created` at
`/in/stripe`. The CRM's inbound endpoint:

1. Logs raw POST into `inbound_events`.
2. Verifies Stripe's signature.
3. Calls `contacts.find_by_email` (audit row).
4. Calls `interactions.log({type:"system",...})` (audit row + webhook
   event for `interaction.logged`).
5. If you subscribe to `interaction.logged`, your billing dashboard
   subscribes back — getting a clean, signed, retryable HTTP POST every
   time a Stripe event lands.

You now have a one-way Stripe → CRM bridge AND a one-way CRM →
dashboard bridge. Both backed by the outbox pattern. Both observable
in audit. Both retryable.

## Use case 3 — your AI agent stamps `request_id`

Your agent runs a chain of 12 API calls to onboard a client (create
company, create contact, attach tags, create deal, create tasks, send
portal token email). All 12 calls send `X-Request-Id: onboard-
<client-slug>-2026-05-11`.

Two days later, the client emails: "did you set up the portal access
for our compliance officer?". You run:

```sql
SELECT action, object_type, object_id, ts
FROM audit_log
WHERE request_id LIKE 'onboard-acme-%'
ORDER BY ts;
```

— and see every step the agent took, including any failures (caught
plug-in errors leave `plugin.error` rows). You can answer with
certainty.

## Operations

**Day-to-day operational use:**

### Reading the audit log

- UI: Settings → Audit log (admin scope). Filter by object, user,
  surface, date, request_id.
- REST: `GET /api/audit` with the same filters as query params.
- CLI: there is no dedicated CLI command yet — you read `audit_log`
  directly via sqlite shell when working at the command line.

### Auditing sensitive `before_json`/`after_json`

Some fields are masked when written (password_hash, api_key_hash,
webhook secret, inbound shared_secret, consent proof if marked
sensitive). Admins with `audit.read_sensitive` scope can call
`GET /api/audit/{id}/reveal` to see the unmasked form. THIS read is
itself audited — `audit.read_sensitive` rows in audit_log.

### Webhook delivery health

Settings → Webhooks shows per-subscription delivery stats:
- pending count
- retrying count
- last successful delivery time
- failure rate over last hour / day / week

If a subscriber has been failing for a long time, you'll see
`status='failed'` rows. They're not retried automatically beyond 8
attempts. You can re-queue a failed delivery via Settings or via:

```bash
python -m agent_surface.cli webhook requeue --id <delivery_id>
```

(Note: this command is on the v4.1 roadmap; if not present yet, use
sqlite directly: `UPDATE webhook_events SET status='pending',
attempts=0, next_attempt_at=strftime('%s','now') WHERE id=?`.)

### Rotating a webhook secret

If a subscriber's secret was leaked: rotate the `webhooks.secret`
field. New deliveries are signed with the new secret. Pending
retries still in `webhook_events` will be signed at delivery time,
so they pick up the new secret too — but the receiver must accept
both old and new during the transition.

### Vacuuming old webhook_events

`webhook_events.status='delivered'` rows accumulate. A cron job in
`agent_surface/cron.py` deletes delivered rows older than 30 days by
default. Tune via `WEBHOOK_RETENTION_DAYS` env var. Auditing of past
deliveries beyond that point is via `audit_log`
(`action='webhook.delivered'` rows are still there).

### Vacuuming audit_log

Audit rows are NEVER deleted in normal operation. The CRM is built so
the audit log is the source of truth for "what happened." If you must
prune (legal requirement, e.g., right-to-erasure), use the dedicated
`audit.redact_user(ctx, user_id)` service which masks but does not
delete — preserving counts + structure for compliance.

## Fine-tuning

### Tuning audit_log size

For very high-volume installs:

- Move masked-but-large fields out of `before_json`/`after_json` to
  reference IDs. E.g., instead of dumping the full `interactions.body`
  on every update, store a hash + length. Tradeoff: less self-
  contained audit; you need the row to be still readable.
- Run `VACUUM` periodically (SQLite reclaims free space).
- Switch to a per-table audit fan-out: `audit_log_contacts`,
  `audit_log_deals`, etc. We've avoided this because join queries get
  ugly, but it's an option for >100M-row scale.

### Tuning webhook delivery

- **Concurrency**: the delivery worker uses one task per process. For
  high fan-out, raise `WEBHOOK_WORKERS=N` in env to start N parallel
  tasks. They lease rows via `UPDATE ... WHERE status='pending' AND
  lease_until < ?`.
- **Backoff schedule**: defined in `backend/webhooks.py` as
  `_BACKOFF_BASE = 30s, attempts cap = 8`. Change those constants
  for faster / slower retries.
- **Per-subscription rate-limit**: cap deliveries per second per
  subscription via `webhooks.rate_per_sec` field. Useful when one
  subscriber is fragile.

### Tuning sensitive-field masking

`backend/audit.py` reads `_SENSITIVE_FIELDS` to decide what to mask.
Add fields to the set. Restart server. New audit rows mask them; old
ones don't — by design, we never rewrite history.

### Subscribing internally

Your own services can subscribe to webhooks by pointing the URL at
your own service's HTTP endpoint. Treat the audit log + webhook outbox
as the CRM's eventbus and your service as a downstream consumer. This
is how you'd build a cross-service search index, an analytics
materialized view, or a notification fan-out.

## Maximizing potential

1. **Stream audit_log into a warehouse.** Polling `WHERE id > ?` is
   enough. You get a free CDC pipeline. Build Looker / Metabase /
   Hex dashboards directly off it.

2. **Build a "time machine" UI page.** Given an `object_type` +
   `object_id`, render the row's state at every audit timestamp by
   walking `before_json`/`after_json` chains forward. Users love
   this; it surfaces value the data was always producing but never
   showing.

3. **Anomaly detection on the audit log.** Train a small model (even
   a deterministic rule set) to flag unusual patterns: a user
   deleting 50 contacts in 5 minutes, a surface=cli action at 3 AM
   from a usually-9-to-5 admin, a webhook subscription rate-limit
   hitting from one key. Feed alerts into Slack.

4. **Webhook-driven realtime UIs.** Subscribe a small SSE relay to
   your own webhooks. Push events to connected browsers. The UI
   updates in realtime without polling. Total infra cost: the relay
   process. Total CRM cost: zero — the events already exist.

5. **Replay-based test fixtures.** Take a real production audit
   stream for a request_id you want to test, replay it into a clean
   test DB. You now have a regression test that mirrors a real user
   flow without writing fixtures by hand.

6. **Per-request-id "explain" view.** Build an internal page that,
   given a request_id, lays out the audit chain visually — a tree
   where service calls branch into plug-in dispatches branch into
   nested service calls. Invaluable for debugging plug-in interactions.

7. **Replay outbox into a different subscriber.** Spin up a new
   subscriber (e.g., a new analytics service) and replay the last
   30 days of `webhook_events` into it before "going live." You're
   already at parity from day one.

## Anti-patterns

- **Deleting audit rows.** Don't. Not for cleanup, not for compaction,
  not for "right to erasure" (use `audit.redact_user` which masks).
  Treat audit_log as append-only forever.
- **Writing webhook URLs into payloads.** The CRM signs the body, not
  the URL; receivers verify signatures, not delivery paths. Don't
  rely on receiving from a specific URL — rotate as needed.
- **Receivers without dedup.** At-least-once means you WILL get
  duplicates. Always dedupe on `X-CRM-Delivery-ID`.
- **Audit_log as a queue.** It's a journal, not a queue. If you need
  reliable async work triggered by mutations, use webhooks (the
  outbox) or write a dedicated task table.
- **Outbox-row-from-transport.** Never write `webhook_events` from a
  REST handler / CLI / UI route. Always from the service layer.
  Otherwise transports diverge again.

## Where to look in code

- `backend/audit.py` — `audit.log(conn, ctx, ...)`, field masking
- `backend/webhooks.py` — `enqueue(conn, event, payload)`, delivery
  worker, signature computation
- `backend/services/*.py` — every service calls both
- `migrations/0001_initial.sql` — `audit_log` + `webhooks` schema
- `migrations/0001_initial.sql` — `webhook_events` schema

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
- [audit-and-webhooks.md](audit-and-webhooks.md) **← you are here**
- [plugins.md](plugins.md)
- [scoring.md](scoring.md)
- [segments.md](segments.md)
- [portals.md](portals.md)
- [inbound.md](inbound.md)
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
