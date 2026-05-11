# Troubleshooting · Error codes

> Symptom-first index of what each error code means and what to do.
> Cross-reference of [03-reference/errors](../03-reference/errors.md)
> ordered by how you actually encounter the problem.

## How to use this page

You're here because something went wrong. Find the error code in the
sections below. Each entry has:

- What the code means.
- What probably caused it.
- The first thing to try.
- Where to look next.

Codes are grouped by problem domain — auth, validation, conflicts,
resources, infrastructure.

---

## Auth problems

### `UNAUTHENTICATED` (HTTP 401)

You hit a protected endpoint without valid credentials.

**Most common causes:**
- Missing `Authorization: Bearer <key>` header.
- Typo in the key.
- Cookie session expired (UI users — sliding 7-day TTL).

**Fix:**
- Verify the header is being sent: `curl -v ...` and look at request
  headers.
- For REST agents: regenerate the key, save it carefully, retry.
- For UI: sign in again.

### `API_KEY_REVOKED` (HTTP 401)

The key existed but has been revoked.

**Fix:** generate a new key. Find out who revoked the old one
(audit log: `action='api_key.revoked'`).

### `SESSION_EXPIRED` (HTTP 401)

The cookie session is past its expiry.

**Fix:** sign in again. The cookie should re-issue automatically on
each successful response.

### `FORBIDDEN` (HTTP 403)

You're authenticated but your scope is too narrow.

**Most common causes:**
- Using a `read` key on a write endpoint.
- Trying to reveal a private note as a non-admin.
- Calling an admin-only operation (e.g., `scoring.recompute_all`,
  `plugins.reload`) without admin scope.

**Fix:**
- Use a higher-scope key (issued in Settings → API keys).
- For UI users: ask an admin to upgrade your role.

---

## Validation problems

### `VALIDATION_ERROR` (HTTP 400)

Payload shape is wrong. `details.field` is usually populated.

**Most common causes:**
- Missing required field (e.g., contact create with no name/email).
- Wrong type (string where int expected).
- Bad format (e.g., invalid email).

**Fix:** read `details.field` and fix the payload. If unclear, see
[03-reference/api.md](../03-reference/api.md) for the field list per
endpoint.

### `FORM_VALIDATION_ERROR` (HTTP 400)

A public form submission has invalid fields per the form's schema.

**Fix:** check the form's `schema_json` for `required` and `type`
constraints. Likely a UI bug or a typo'd field name.

### `SEGMENT_RULES_INVALID` (HTTP 400)

Dynamic segment rule tree malformed.

**Fix:** validate the JSON shape against
[01-concepts/segments.md](../01-concepts/segments.md). Common
mistakes: extra keys, wrong operator names (`AND` vs `and`).

### `REPORT_PARAMS_INVALID` (HTTP 400)

The report function rejected your params.

**Fix:** look up the report in `services/reports.py` to see what
params it accepts.

### `SCORE_TYPE_UNKNOWN` (HTTP 400)

You passed a `score_type` that isn't one of the five.

**Fix:** valid values are
`relationship_strength`, `intent`, `fit`, `risk`, `opportunity`.

### `INBOUND_ROUTING_FAILED` (HTTP 400)

Inbound event verified its signature but the routing rules couldn't
parse it.

**Fix:** read the raw body from `inbound_events`. Probably your
routing rule's JSONPath references a field that's not in the actual
payload.

---

## Conflict problems (HTTP 409)

### `CONTACT_EMAIL_EXISTS`

Another active contact has that email.

**Fix:** decide:
- Update the existing one (look it up first: `find_contacts` by
  email; the response `details.contact_id` already tells you).
- Soft-delete the existing one if you want to replace.
- Treat it as a duplicate-merge candidate
  (`POST /api/duplicates/merge`).

### `COMPANY_SLUG_EXISTS`, `FORM_SLUG_EXISTS`, `SEGMENT_SLUG_EXISTS`, `INBOUND_SLUG_EXISTS`

Slug uniqueness violation.

**Fix:** choose a different slug, or update the existing entity if
that's the intent.

### `TAG_EXISTS`

You tried to create a tag with a name already in use.

**Fix:** look up the existing tag (`GET /api/tags?name=...`). Most
agents should call `create_or_get`-style logic that swallows this
error and returns the existing row.

### `DEAL_STAGE_GATE`

Service-level invariant blocked the move (e.g., "Proposal stage
requires value_cents > 0").

**Fix:** read the message; satisfy the precondition; retry.

### `PIPELINE_ARCHIVED`

You tried to create a deal on an archived pipeline.

**Fix:** unarchive the pipeline OR move the deal to a different
pipeline. Check `pipelines.archived` first via `GET /api/pipelines`.

### `IDEMPOTENCY_KEY_MISMATCH`

Same idempotency key, but the principal or action differs from the
original recorded call.

**Fix:** use a different key. Keys must be unique per `(principal,
action)`.

---

## Not-found problems (HTTP 404)

### `CONTACT_NOT_FOUND`, `COMPANY_NOT_FOUND`, `DEAL_NOT_FOUND`, etc.

The id doesn't exist OR the entity is soft-deleted.

**Fix:**
- Verify the id. Often a typo or a stale id from before a delete.
- If you intentionally want a soft-deleted record, pass
  `include_deleted=true` (where supported).
- Use `find_*` to look up by name/email/slug.

### `REPORT_NOT_FOUND`

The report name isn't in `services/reports.py:CATALOG`.

**Fix:** check available reports with `GET /api/reports` (or `report
list` CLI). Spelling is case-sensitive.

### `PLUGIN_NOT_FOUND`

The plug-in id is unknown. Common after deleting the .py file but
not running `plugin reload`.

**Fix:** `plugin list` to see what's actually registered.

---

## Gone (HTTP 410)

### `FORM_INACTIVE`

You hit `/f/{slug}` for a form whose `active=0`.

**Fix:** the form owner deactivated it. Either re-activate (Forms →
Active toggle) or use a different form slug.

### `INBOUND_INACTIVE`

Same idea for an inbound endpoint.

**Fix:** re-activate in Connectors page.

### `PORTAL_TOKEN_REVOKED`

The token is revoked or expired.

**Fix:** issue a new token. Don't try to "un-revoke" — that's
deliberately not supported.

---

## Rate / availability problems

### `RATE_LIMITED` (HTTP 429)

You hit a rate limit. Most commonly per-API-key or per-inbound-
endpoint.

**Fix:**
- Back off (the response includes a `Retry-After` header).
- Move bulk work to the import CLI (no rate limit).
- Issue more keys and parallelize.

### `SEARCH_DISABLED` (HTTP 503)

`SEARCH_ENABLED=0` in env. Search endpoints return 503.

**Fix:** re-enable in env if you need it, OR remove the search
predicate from your dynamic segment.

### `INTERNAL_ERROR` (HTTP 500)

Uncaught exception in a service. A bug.

**Fix:**
- Check the server logs (`journalctl -u crm -n 200`).
- Reproduce locally; add a test.
- File an issue with the traceback + request_id.

---

## Plug-in problems

### `plugin.error` audit rows piling up

A plug-in keeps throwing.

**Fix:**
- Read `plugins.last_error` for the most recent message.
- Read audit rows: `WHERE action='plugin.error' AND object_id=<plugin_id>`.
- Fix the plug-in code; reload.
- If you can't fix immediately, `plugin disable --id N` to stop
  noise.

### `PLUGIN_DISABLED`

Tried to call a disabled plug-in directly.

**Fix:** enable it via `plugin enable --id N` if intended, or use
a different plug-in.

---

## Infrastructure problems

### `database is locked` (SQLite, not a ServiceError code)

Two writers contended for the writer lock.

**Most common causes:**
- A long-running transaction (e.g., a plug-in doing HTTP inside the
  parent tx).
- An unclosed `with db() as conn:` block in custom code.

**Fix:**
- Investigate which service is slow (logs, audit timestamps).
- Move slow work to deferred-work pattern (see
  [01-concepts/plugins.md](../01-concepts/plugins.md)).
- As a one-off, retry the failed write.

### WAL file ballooning

`crm.db-wal` is 1+ GB.

**Cause:** a long-running reader holding old WAL frames OR a
checkpoint that never runs.

**Fix:**
- Stop the long reader (find it via process inspection).
- `PRAGMA wal_checkpoint(TRUNCATE)` from sqlite3 CLI.
- If chronic: tune `wal_autocheckpoint`.

### Webhook deliveries failing

`webhook_events.status='failed'` rows.

**Most common causes:**
- Receiver URL changed or is down.
- Receiver returns non-2xx because of body parsing error.
- HMAC secret on receiver doesn't match.

**Fix:**
- Inspect `response_status` + `response_body` of failed rows.
- Verify the receiver is reachable: `curl -i <webhook.url>`.
- If you rotated the secret on the CRM, also rotate on the receiver.
- Re-queue failed rows by setting `status='pending'`.

### Migration won't apply

```
sqlite3.OperationalError: NOT NULL constraint failed: contacts.do_not_contact
```

A previous migration added a NOT NULL column with a default, but a
row was inserted via a code path that didn't supply it.

**Fix:**
- Audit the service code that inserts into this table; ensure
  defaults match the schema.
- For the broken migration: write a SECOND migration that
  back-fills + adjusts.

---

## Generic checklist when things go sideways

1. **Look at audit_log filtered by request_id.** The full chain
   tells you what succeeded and what didn't.
2. **Check `plugins.last_error`.** Plug-ins can hide errors that
   look like core problems.
3. **Check `webhook_events`.** If a subscriber is broken, the CRM
   keeps marching but the outside world is out of sync.
4. **Read the server logs** (`journalctl -u crm -n 200`). Stack
   traces reveal bugs.
5. **Verify environment.** ENV vars (`CRM_DB_PATH`, plug-in keys),
   filesystem permissions, Python version.
6. **Reproduce locally with the smallest possible case.** If it
   reproduces, you have a bug. If not, the prod state differs
   somehow.

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
- [error-codes.md](error-codes.md) **← you are here**
