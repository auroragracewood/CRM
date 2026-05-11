# Concept · Service layer

> The one architectural rule. Everything else in this CRM only works
> because the service layer enforces it consistently.

## Context

A CRM in 2026 is rarely driven by a single user-interface. Your
salespeople use the web UI. Your marketing automation hits a REST API.
Your AI agent drives it through MCP. Your operator runs CLI commands
during incidents. Your inbound webhook receiver writes deals when
Stripe pings.

If each of those surfaces talks to the database independently, you end
up with five copies of every business rule — slightly different in each
copy, drifting further apart with every quarter. One copy logs audit
rows. One forgets. One enforces the do-not-contact flag. One doesn't.
One sends webhooks. One doesn't. The CRM appears to work but quietly
becomes untrustworthy.

The service layer is the answer to "where does the business logic
live?" — exactly one place — combined with a discipline that every
transport defers to that place.

## Understanding

The service layer is a directory: `backend/services/*.py`. Each file
is one entity (`contacts.py`, `companies.py`, `deals.py`, …). Each
file exposes a small surface of plain Python functions:

```python
contacts.create(ctx, payload)        -> dict
contacts.get(ctx, contact_id)        -> dict
contacts.list_(ctx, q=…, limit=…)    -> dict
contacts.update(ctx, contact_id, …)  -> dict
contacts.delete(ctx, contact_id)     -> dict
contacts.find_by_email(ctx, email)   -> dict | None
```

The first argument is always `ServiceContext`. Return values are plain
dicts (no ORM objects). Errors are raised as `ServiceError(code,
message, details)`.

Each function does six things, atomically:

```
BEGIN
  1. scope check       ─ ctx.can_write() else raise FORBIDDEN
  2. validate payload  ─ shape, format, normalize
  3. pre-write checks  ─ uniqueness, FK existence, business rules
  4. SQL write         ─ INSERT/UPDATE/DELETE
  5. audit.log         ─ before/after JSON, action, surface
  6. webhooks.enqueue  ─ event name, payload
  7. plugins.dispatch  ─ pass conn; hooks run synchronously
COMMIT
```

Any step that raises rolls back the whole transaction. There is no
state where step 4 succeeded but step 5 didn't.

Above the service layer are four transports: `backend/api.py` (REST),
`agent_surface/cli.py` (CLI), `agent_surface/mcp_server.py` (MCP), and
`backend/main.py` (UI routes). Each is a translation layer — request
in → ServiceContext + dict out, response out → format conversion. None
of them contain SQL writes, audit calls, webhook enqueues, or plug-in
dispatches.

## Reason

**Why one layer, why this shape?**

1. **Atomicity of side-effects.** A CRM whose audit log can diverge
   from reality is one you can't trust during a compliance review or a
   security incident. By keeping the audit write inside the same SQLite
   transaction as the data write, the CRM gives you a hard guarantee:
   "if you see it changed, you can see who changed it."

2. **Transport-agnostic correctness.** You can write a 6-line test that
   calls `contacts.create(ctx, payload)` directly. That test verifies
   the rule for every transport that uses it, including ones you
   haven't built yet. A test through HTTP only covers HTTP.

3. **Predictability for agents.** AI agents are bad at "this rule is
   true here but not here." Funneling every mutation through one
   function gives them a single contract to learn. Once they know
   `contacts.create`, they know all five ways to call it.

4. **Plug-in coherence.** A plug-in that reacts to `on_contact_created`
   fires exactly once per real contact creation, regardless of which
   surface initiated it. Without the service layer, you'd need a plug-in
   mounted on each surface separately, with no guarantee they all fire.

5. **Replaceable transports.** When you add a new transport (GraphQL,
   gRPC, Slack slash command, IoT MQTT bridge), the cost is small —
   you're building a translator, not re-implementing the CRM. The
   first version of an MCP server here took a few hours; it would
   have taken weeks if every tool re-implemented its logic.

## Result

What you actually get from following this rule:

- **One audit log that reflects reality.** Forensic queries return
  truth. Diffs are accurate. `request_id` correlates the chain.
- **Webhook subscribers never lie about state.** They see events
  for changes that committed; they never see events for changes that
  rolled back.
- **Tests are short.** Most tests call services directly. The same
  test suite covers all transports.
- **Onboarding is fast.** Any new engineer reads `backend/services/`
  and knows the whole product. Transports are obvious adapters.
- **Refactoring is safe.** Changing `contacts.update` changes
  contact-update behavior for every surface simultaneously, with one
  audit-log shape and one webhook event shape.
- **AI agents converge on correct usage.** They can read one service
  file, one skill file, one error-code table — and not have to learn
  five different error shapes.

## Use case — narrative example

A lead arrives on the public form at `/f/contact-us`. Here's what the
service layer does:

1. **Transport** (`main.py:1283`) parses the form POST, builds a
   `ServiceContext(surface="ui", user_id=PUBLIC, scope="write")`.
2. Calls `forms.submit(ctx, slug="contact-us", payload={...})`.
3. **Service** validates the payload against the form's schema.
4. Routes — looks up an existing contact by email or calls
   `contacts.create(ctx, {...})`. The audit row for the contact creation
   records `surface=ui`, `user_id=PUBLIC`, and the request_id.
5. Calls `interactions.log(ctx, {type: "form_submission", ...})`. The
   interaction's audit row shares the request_id.
6. Calls `tags.attach(...)` for each tag in the form's routing rules.
   Audit rows again.
7. `webhooks.enqueue` fires for `form.submitted`, `contact.created`,
   `interaction.logged`, `tag.attached`. Four webhook events, all
   committed atomically with the data.
8. `plugins.dispatch("on_form_submitted", ...)` fires; the auto-tag
   plug-in reads the body and attaches `topic:copper` and
   `topic:signage`. Two more audit rows, one webhook event each.

Total in the audit log for this one form submission: ~7-9 rows, all
stamped with the same `request_id`. You can replay or audit it later
with a single SQL query.

If step 6's audit insert failed (disk full, say), the entire chain
rolls back — including the contact create. Nothing committed. No
phantom contact. No half-delivered webhook.

## Operations

**Day-to-day operational implications of the service layer:**

- **Running a one-off task?** Use `python -m agent_surface.cli` — it
  imports the same service functions you'd call from the API. Or write
  a small Python script that imports `backend.services.contacts` and
  calls it. Both produce the same audit / webhook / plug-in side-
  effects.
- **Bulk operation?** Each service call is its own transaction. If
  you're inserting 10,000 contacts, that's 10,000 commits — slow for
  SQLite. Either use the `imports` service (chunks at 500 per
  transaction with WAL) or import via `crm-cli import` which already
  chunks.
- **Operation hangs?** Almost always SQLite write contention. Two
  writers waiting on a `BEGIN IMMEDIATE`. Check `PRAGMA
  busy_timeout` (5000ms) — if a writer doesn't get the lock within
  5s, it raises `database is locked`. With WAL + service-layer
  short transactions, this should be rare; if you see it regularly,
  a service function is holding a transaction too long (e.g.,
  doing outbound HTTP inside `with db() as conn:`).
- **Debugging a webhook subscriber not receiving an event?** Query
  `webhook_events` directly — does the row exist? If yes, the service
  layer did its job; check delivery worker logs. If no, the service
  function for that mutation isn't calling `webhooks.enqueue`
  (potential bug, file an issue).
- **Auditing a complaint ("X claims they did Y at 3pm")?**
  `SELECT * FROM audit_log WHERE user_id = ? AND ts BETWEEN ? AND ?`.
  Filter further by `surface` to scope to UI vs API vs CLI.

## Fine-tuning

**Where the rule has tunable knobs:**

- **Transaction granularity.** Each service function is one
  transaction. If you have a higher-level workflow (e.g., "create
  deal and assign three tasks"), choose: call three services
  sequentially (three transactions, partial-success possible) OR
  write a new service `deals.create_with_tasks` that does both in
  one transaction.
- **Plug-in synchronicity.** Plug-in hooks run inside the parent
  transaction by default. If a plug-in does slow I/O, move it to a
  deferred-work pattern: have the plug-in write to a small queue
  table, with a background task draining it asynchronously. This
  preserves atomicity (the queue row commits with the data) while
  not blocking the parent.
- **Webhook fan-out.** A subscription with `events_json="*"` fans
  out every event. If you have many of those, your `webhook_events`
  table grows fast. Solutions: cap subscriptions, run a vacuum job
  daily to delete `delivered` rows older than N days, or build
  per-subscriber event filters on top of `*`.
- **ServiceContext extension.** If you need to carry more identity
  beyond the six fields (e.g., a tenant_id for a multi-tenant fork),
  add fields to the dataclass in `backend/context.py`. Every service
  function picks them up via `ctx.<field>`.
- **Validation strictness.** `_validate_create` in each service is
  the single point to tighten or loosen field requirements. Add a
  format check here and it applies to every surface immediately.

## Maximizing potential

**How to push the service layer beyond the basics:**

1. **Treat services as the public SDK.** If you ship a Python client
   for your CRM, the client is literally `from backend.services
   import contacts` running over a network. Better: extract the
   service module signatures into an `interface.pyi` and generate a
   client from it.

2. **Compose services into workflows.** Build `services/workflows.py`
   for multi-entity operations: `workflows.onboard_client(ctx, ...)`
   that creates the contact, the company, the deal, three tasks, and
   the welcome portal token in one transaction. Workflows are
   services that call services — they keep the atomic guarantee.

3. **Treat the audit log as a stream.** Tail `audit_log` into a
   downstream system (analytics warehouse, ML feature store) with a
   simple polling tail by `id`. You get a free event stream without
   adding Kafka.

4. **Use the service signatures to auto-generate transports.** REST
   handlers, MCP tool definitions, and CLI argparse can all be
   generated from service function signatures + annotations. The
   current code is hand-written for readability, but the structure
   is ready for codegen.

5. **Sandbox high-risk services behind a `dry_run=True` flag.** Add
   it to functions like `imports.run` or `scoring.recompute_all` so
   agents can preview the changes (returns counts + sample rows) before
   actually writing. This is a cheap superpower for agent reliability.

6. **Service-layer testing as the contract.** A `tests/test_services_
   *.py` file that exercises one function with every documented input
   shape becomes the contract test for that entity. Transports get
   smoke tests for their translation layer only.

## Anti-patterns to avoid

- **Bypassing services from inside a route handler.** "It's just one
  field, the audit row isn't needed" — that field IS the thing the
  audit log exists for.
- **Reaching into the DB from a plug-in.** Plug-ins should call other
  service functions, not write raw SQL. Otherwise plug-in changes
  don't fire their OWN downstream audit/webhook/plug-in chains.
- **Long transactions.** Don't `with db() as conn:` and then do an
  HTTP call inside. SQLite will hold the writer lock the whole time.
- **Mixing concerns in one service function.** `contacts.create_and_
  invite_to_portal` is a workflow, not a service primitive. Keep
  `contacts.create` doing one thing.

## Where to look in code

| concern | file |
|---------|------|
| The services | `backend/services/*.py` |
| ServiceError | `backend/services/contacts.py` (defined once, re-imported) |
| ServiceContext | `backend/context.py` |
| audit.log         | `backend/audit.py` |
| webhooks.enqueue  | `backend/webhooks.py` |
| plugins.dispatch  | `backend/services/plugins.py` |
| REST translator | `backend/api.py` |
| CLI translator  | `agent_surface/cli.py` |
| MCP translator  | `agent_surface/mcp_server.py` |
| UI translator   | `backend/main.py` |

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
- [service-layer.md](service-layer.md) **← you are here**
- [service-context.md](service-context.md)
- [audit-and-webhooks.md](audit-and-webhooks.md)
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
