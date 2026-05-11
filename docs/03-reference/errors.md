# Errors reference

Every service function raises `ServiceError(code, message, details)`
on failure. Transports translate consistently — REST returns the
HTTP status from the table below; CLI prints + exits non-zero; MCP
returns an `{"error": {...}}` shape; UI flashes an alert.

## ServiceError shape

```python
class ServiceError(Exception):
    code: str           # machine-readable, e.g. "CONTACT_NOT_FOUND"
    message: str        # human-readable
    details: dict       # optional structured info (field, conflicting_id, ...)
```

In responses:

```json
{
  "ok": false,
  "error": {
    "code":    "CONTACT_EMAIL_EXISTS",
    "message": "Another active contact already has email 'maya@blueriver.media'",
    "details": {"contact_id": 12},
    "request_id": "abc-123-..."
  }
}
```

## Code → status table

| code | HTTP | meaning |
|------|------|---------|
| `VALIDATION_ERROR`             | 400 | Payload shape/format wrong |
| `IDEMPOTENT_REPLAY`            | 200 | Idempotency key seen before; returning stored result |
| `FORBIDDEN`                    | 403 | ctx.scope too narrow |
| `UNAUTHENTICATED`              | 401 | (transport-level) no valid bearer/cookie |
| `RATE_LIMITED`                 | 429 | per-key or per-endpoint rate hit |
| `CONTACT_NOT_FOUND`            | 404 | id missing or soft-deleted |
| `CONTACT_EMAIL_EXISTS`         | 409 | another active contact has this email |
| `COMPANY_NOT_FOUND`            | 404 |  |
| `COMPANY_SLUG_EXISTS`          | 409 |  |
| `NOTE_NOT_FOUND`               | 404 |  |
| `NOTE_PRIVATE_FORBIDDEN`       | 403 | non-admin tried to read private note |
| `TAG_NOT_FOUND`                | 404 |  |
| `TAG_EXISTS`                   | 409 |  |
| `CONSENT_NOT_FOUND`            | 404 |  |
| `PIPELINE_NOT_FOUND`           | 404 |  |
| `PIPELINE_STAGE_NOT_FOUND`     | 404 |  |
| `PIPELINE_ARCHIVED`            | 409 | tried to create deal on archived pipeline |
| `DEAL_NOT_FOUND`               | 404 |  |
| `DEAL_STAGE_GATE`              | 409 | service-level invariant failed (e.g., "Proposal needs value") |
| `TASK_NOT_FOUND`               | 404 |  |
| `USER_NOT_FOUND`               | 404 |  |
| `API_KEY_NOT_FOUND`            | 404 |  |
| `API_KEY_REVOKED`              | 401 | (transport) presented a revoked key |
| `SESSION_EXPIRED`              | 401 | (transport) cookie expired |
| `FORM_NOT_FOUND`               | 404 |  |
| `FORM_SLUG_EXISTS`             | 409 |  |
| `FORM_INACTIVE`                | 410 | public `/f/{slug}` hit on disabled form |
| `FORM_VALIDATION_ERROR`        | 400 | public form fields don't match schema |
| `SEARCH_DISABLED`              | 503 | `SEARCH_ENABLED=0` set |
| `SEGMENT_NOT_FOUND`            | 404 |  |
| `SEGMENT_SLUG_EXISTS`          | 409 |  |
| `SEGMENT_RULES_INVALID`        | 400 | rule tree malformed |
| `SCORE_TYPE_UNKNOWN`           | 400 | unrecognized score_type string |
| `REPORT_NOT_FOUND`             | 404 | report name not in CATALOG |
| `REPORT_PARAMS_INVALID`        | 400 | report-specific params validation |
| `PORTAL_TOKEN_NOT_FOUND`       | 404 |  |
| `PORTAL_TOKEN_REVOKED`         | 410 | hit on revoked or expired token |
| `INBOUND_ENDPOINT_NOT_FOUND`   | 404 |  |
| `INBOUND_SLUG_EXISTS`          | 409 |  |
| `INBOUND_SIGNATURE_INVALID`    | 401 | HMAC verify failed on /in/{slug} |
| `INBOUND_INACTIVE`             | 410 | endpoint exists but `active=0` |
| `INBOUND_ROUTING_FAILED`       | 400 | event stored, but routing rules couldn't parse it |
| `PLUGIN_NOT_FOUND`             | 404 |  |
| `PLUGIN_DISABLED`              | 409 | tried to call a disabled plug-in directly |
| `SAVED_VIEW_NOT_FOUND`         | 404 |  |
| `DUPLICATES_MERGE_INVALID`     | 400 | merge target invalid (same id, deleted, etc.) |
| `IDEMPOTENCY_KEY_MISMATCH`     | 409 | same key, different principal/action |
| `INTERNAL_ERROR`               | 500 | uncaught service error; bug |

## Common scenarios

### A REST POST returns 409 CONTACT_EMAIL_EXISTS

Another active contact has this email. Options:
- `GET /api/contacts?q=<email>` to find it; update the existing one.
- POST with `--on-duplicate update` semantics (import path).
- Soft-delete the old one first if you intend to replace.

### A CLI command exits with FORBIDDEN

Your acting user's scope is `read` or `readonly`. Pass
`--as-email <admin>` or get a write-scope API key.

### A webhook subscriber sees `webhook.delivery_failed`

Your endpoint returned non-2xx 8 times. Inspect
`webhook_events.response_status` and `response_body` to find why.
Fix the receiver. Re-queue manually:

```sql
UPDATE webhook_events SET status='pending', attempts=0,
       next_attempt_at=strftime('%s','now')
WHERE id = ?;
```

### A plug-in repeatedly errors

Read `plugins.last_error` and the audit log for
`action='plugin.error', object_id=<plugin_id>`. Fix the bug; reload
plug-ins.

### A migration fails mid-run

The migration's transaction rolls back. `schema_versions` is NOT
updated. Next start retries the same migration. Fix the SQL; retry.

## Logging

Every ServiceError raised inside a service function is logged at WARN
level with:

```
service.error code=<code> action=<action> user_id=<id>
              api_key_id=<id> request_id=<id>
```

Caught (plug-in) errors are logged at ERROR with the stack.

Uncaught exceptions become 500 `INTERNAL_ERROR` and log at CRITICAL.
These are bugs; fix and add a test.

## Where to look in code

- `backend/services/contacts.py:36` — `ServiceError` class definition
- `backend/api.py:52-76` — status code mapping
- each service file — raises specific codes near the point of failure

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
- [data-model.md](data-model.md)
- [api.md](api.md)
- [cli.md](cli.md)
- [mcp.md](mcp.md)
- [plugins.md](plugins.md)
- [webhooks.md](webhooks.md)
- [errors.md](errors.md) **← you are here**

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
