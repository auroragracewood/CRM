# AGENTS.md — Operating contract for AI agents

This file is the entry point for AI agents (Claude Code, MCP-driven
harnesses, custom orchestrators) that need to operate this CRM. Read it
in full BEFORE issuing any tool call against the system.

If you are a human, the right file is `README.md`. If you are Claude
Code specifically, also read `CLAUDE.md` for project conventions on top
of this.

---

## Identity of this software

**Name:** CRM.

**One-line:** A single-company FastAPI + SQLite CRM where every action
lives in a service-layer module and is exposed identically through
REST, CLI, MCP, and UI.

**It is NOT** a multi-tenant SaaS. It is NOT an LLM application — there
is no embedded model. AI agents act on it from the outside through the
transports below.

## The architectural rule you must not break

> Every mutation goes through `backend/services/*.py`. Transports are
> thin shells that build a `ServiceContext` and call the service. They
> contain no business logic.

If you find yourself writing SQL, calling `audit.log`, or enqueuing a
webhook from a REST handler, CLI command, MCP tool, or UI route — STOP.
That code belongs in `backend/services/`. The service-layer guarantees
that:

1. Validation runs in the same transaction as the write.
2. The audit row, webhook outbox row, and plug-in hooks share that
   transaction's atomicity — they all commit or all abort.
3. Every surface gets the same behavior, including the same error codes.

A transport that bypasses the service layer breaks all three guarantees
silently. Don't do it.

## How to operate the CRM

You have four transports. Pick the right one:

| transport | when to use | how to call |
|-----------|-------------|-------------|
| **MCP**    | You're a Claude/Cursor/etc. agent with an MCP client | `agent_surface/mcp_server.py` — tools are listed in `docs/03-reference/mcp.md` |
| **REST**   | You're a remote agent or another service | `/api/*` with `Authorization: Bearer <key>` |
| **CLI**    | You're operating locally on the same machine as the DB | `python -m agent_surface.cli ...` |
| **UI**     | A human is driving | Don't drive the UI from an agent unless you're testing |

All four hit the same service functions. Same validation, same audit
rows, same webhook events, same error codes.

## Authentication

- **MCP**: Identity comes from the local user record OR the
  `--as-email` / `--as-user-id` arg. No keys.
- **REST**: `Authorization: Bearer <raw-key>`. Keys are issued in the
  UI at Settings → API keys. The raw key is shown ONCE; if you didn't
  save it, you have to rotate.
- **CLI**: Same as MCP — local user resolution. No network auth.
- **UI**: Cookie sessions (`crm_session`, HttpOnly, SameSite=Lax,
  7-day sliding). Agents shouldn't use cookies — use a key.

Scopes: `read`, `write`, `admin`. Admin scope is required for:

- Reading private notes
- Reloading plug-ins
- Bulk recomputing scores
- Revealing audit `before_json` for sensitive fields

## What happens when you call a service

Every successful mutation does six things in a single transaction:

```
┌─ Service function called with ctx + payload
│
├─ 1. Scope check (ctx.can_read / ctx.can_write)
├─ 2. Payload validated → ServiceError if not
├─ 3. Row written (INSERT/UPDATE/DELETE)
├─ 4. audit.log(conn, ctx, action, before, after)
├─ 5. webhooks.enqueue(conn, event_name, payload)
├─ 6. plugins.dispatch(hook_name, ctx, ..., conn)
│
└─ COMMIT (all or nothing)
```

If any step raises, the whole thing rolls back. The audit row, webhook,
and plug-in side-effects do NOT exist if the data write did not commit.

## Error model

Every service raises `ServiceError(code, message, details)`. Transports
translate that:

- REST returns `{"ok": false, "error": {...}}` with the HTTP status from
  the mapping in `backend/api.py:52-76`.
- CLI prints the error object to stdout and exits non-zero.
- MCP returns `{"error": {"code", "message", "details"}}`.
- UI flashes an alert and re-renders the form with values preserved.

Codes you'll see most often:

| code | meaning | what to do |
|------|---------|------------|
| `VALIDATION_ERROR` | Payload is malformed | Fix the payload; check `details.field` |
| `FORBIDDEN` | Your scope is too narrow | Use an admin key or escalate |
| `*_NOT_FOUND` | Object doesn't exist or is soft-deleted | Verify the ID |
| `*_EXISTS` | Unique constraint hit | Use the existing object instead of creating new |
| `IDEMPOTENT_REPLAY` | Same idempotency-key was seen before | Use the stored result |

Full table: `docs/03-reference/errors.md`.

## Idempotency

For agent retries, pass an idempotency key:

- REST: header `Idempotency-Key: <opaque-string>`
- MCP: arg `idempotency_key="<opaque-string>"`

The CRM stores `(key, principal, action)` → `result_json` in the
`idempotency_keys` table. A second call with the same triple returns
the original response instead of writing again.

## Audit trail

Every service-layer mutation writes one row to `audit_log` with:
`(ts, user_id, api_key_id, surface, action, object_type, object_id,
before_json, after_json, request_id)`.

If you set `X-Request-Id` on REST calls, that ID flows through. Use it
to correlate one agent run across many writes.

## What "the AI" is and isn't

This CRM has NO embedded LLM. There is no built-in chat, no AI
suggestions baked into the data model.

What it has:

- **MCP tools** so an external LLM can drive it.
- **Plug-ins** so deterministic Python (or your own LLM call) can
  react to events.
- **Skills** (`agent_surface/skills/*.md`) so agents can discover
  high-level actions with example payloads.
- **A consistent shape across surfaces** so an agent that learned
  REST also knows the CLI and MCP.

If you want LLM-driven behavior, you write a plug-in that calls your
LLM of choice when a hook fires (e.g., `on_interaction_logged` →
classify topic → attach tag). See
[`docs/06-development/writing-a-plugin.md`](docs/06-development/writing-a-plugin.md)
for the contract.

## Discovery: how to find your way around

1. `docs/README.md` — wiki index
2. `docs/00-start-here.md` — 10-minute tour
3. `SCHEMATICS.md` — ASCII diagrams of how transports → service-layer →
   storage → webhooks → external systems
4. `agent_surface/skills/*.md` — high-level actions you can take
5. `docs/03-reference/*.md` — exhaustive lookup

If you don't know which transport to use for a task, look in the skill
file for that action — every skill names the canonical transport.

## Things you must NEVER do

- **Do NOT** modify `schema.sql` directly. Write a new migration file in
  `migrations/` and let the migration runner pick it up.
- **Do NOT** delete from `audit_log` for any reason.
- **Do NOT** add hidden tables that bypass the service layer.
- **Do NOT** put business logic in a transport (REST handler, CLI cmd,
  MCP tool, UI route).
- **Do NOT** drop the `deleted_at` column from anything. Soft-delete is
  load-bearing for the partial unique index on `contacts.email`.
- **Do NOT** call LLM APIs from inside core services. Plug-ins only.
- **Do NOT** ship `crm.db` to a remote machine — it's a single-machine
  store. Use logical export/import instead.

## Things you SHOULD do

- Pass `X-Request-Id` on every REST call.
- Use idempotency keys on writes you might retry.
- Read the relevant skill file before acting on an unfamiliar action.
- When you make a structural change, update the relevant wiki page in
  the same commit. Stale docs are a bug.
- When you discover a behavior that surprised you, add a line to the
  relevant concept doc explaining it.

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](README.md) — human entry point
- [AGENTS.md](AGENTS.md) **← you are here**
- [CLAUDE.md](CLAUDE.md) — Claude Code project conventions
- [SCHEMATICS.md](SCHEMATICS.md) — ASCII architecture diagrams
- [Blueprint.md](Blueprint.md) — product spec
- [prompt.md](prompt.md) — build-from-scratch prompt

**Wiki root** (`docs/`)
- [README.md](docs/README.md) — wiki index
- [00-start-here.md](docs/00-start-here.md) — 10-minute orientation

**01 — Concepts** (`docs/01-concepts/`) — why each piece exists
- [service-layer.md](docs/01-concepts/service-layer.md)
- [service-context.md](docs/01-concepts/service-context.md)
- [audit-and-webhooks.md](docs/01-concepts/audit-and-webhooks.md)
- [plugins.md](docs/01-concepts/plugins.md)
- [scoring.md](docs/01-concepts/scoring.md)
- [segments.md](docs/01-concepts/segments.md)
- [portals.md](docs/01-concepts/portals.md)
- [inbound.md](docs/01-concepts/inbound.md)
- [search.md](docs/01-concepts/search.md)

**02 — Guides** (`docs/02-guides/`) — step-by-step how-tos
- [install.md](docs/02-guides/install.md)
- [first-contact.md](docs/02-guides/first-contact.md)
- [your-first-pipeline.md](docs/02-guides/your-first-pipeline.md)
- [import-export.md](docs/02-guides/import-export.md)
- [deploying.md](docs/02-guides/deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](docs/03-reference/data-model.md)
- [api.md](docs/03-reference/api.md)
- [cli.md](docs/03-reference/cli.md)
- [mcp.md](docs/03-reference/mcp.md)
- [plugins.md](docs/03-reference/plugins.md)
- [webhooks.md](docs/03-reference/webhooks.md)
- [errors.md](docs/03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](docs/04-recipes/lead-intake.md)
- [dormant-revival.md](docs/04-recipes/dormant-revival.md)
- [agent-workflows.md](docs/04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](docs/05-operations/backup-restore.md)
- [migrations.md](docs/05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](docs/06-development/adding-an-entity.md)
- [writing-a-plugin.md](docs/06-development/writing-a-plugin.md)
- [writing-a-skill.md](docs/06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](docs/07-troubleshooting/error-codes.md)
