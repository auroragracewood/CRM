# Webhooks reference

> See [01-concepts/audit-and-webhooks](../01-concepts/audit-and-webhooks.md)
> for the architectural model. This page is the catalog + delivery
> contract.

## Subscribing

UI: Settings → Webhooks → New webhook. Fields:
- URL (the receiver endpoint; HTTPS recommended)
- Events: comma-separated, or `*` for all
- Secret: generated for you; copy it — used for HMAC signing
- Active toggle

REST: `POST /api/settings/webhooks` (admin scope) with:

```json
{
  "url":    "https://your-receiver/crm-hook",
  "events": ["contact.created","contact.updated","deal.won"],
  "secret": "auto-generate or supply your own"
}
```

CLI: not yet wired (use REST or UI).

## Delivery

The delivery worker runs as an asyncio task inside the FastAPI process.
Every 5 seconds it scans for `webhook_events WHERE status IN
('pending','retrying') AND next_attempt_at <= now` and POSTs them.

### Request

```
POST <url> HTTP/1.1
Content-Type:        application/json
X-CRM-Event:         contact.created
X-CRM-Delivery-ID:   <uuid>
X-CRM-Signature:     sha256=<hex hmac of body using secret>
User-Agent:          crm-webhook/1.0

{ ...the event payload... }
```

### Verifying

```python
import hmac, hashlib
def verify(body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    sent = signature_header[7:]
    calc = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, calc)
```

Verify BEFORE trusting the body. Use `hmac.compare_digest` (constant-
time) to avoid timing attacks.

### Retry schedule

| attempt | wait before |
|---------|-------------|
| 1 | 0 (immediate) |
| 2 | 30 s         |
| 3 | 60 s         |
| 4 | 2 min        |
| 5 | 4 min        |
| 6 | 8 min        |
| 7 | 16 min       |
| 8 | 32 min       |
| (then) | status=failed |

A 2xx response stops retries. Any non-2xx triggers the next attempt.

### Idempotency

The same `X-CRM-Delivery-ID` is sent on every retry of the same
delivery. Dedupe on it.

---

## Event catalog

| event | payload root | fired when |
|-------|--------------|------------|
| `contact.created`        | `{"contact": {...}}`                  | a contact is created |
| `contact.updated`        | `{"contact": {...}, "before": {...}}` | a contact is updated |
| `contact.deleted`        | `{"contact_id": N}`                   | a contact is soft-deleted |
| `contact.merged`         | `{"into": {...}, "from": {...}}`      | duplicates merge runs |
| `company.created`        | `{"company": {...}}`                  | company create |
| `company.updated`        | `{"company": {...}, "before": {...}}` | company update |
| `company.deleted`        | `{"company_id": N}`                   | company soft-delete |
| `interaction.logged`     | `{"interaction": {...}}`              | timeline entry written |
| `note.created`           | `{"note": {...}}`                     | non-private note created |
| `note.private_revealed`  | `{"note_id": N, "by_user_id": N}`     | admin reveals private |
| `tag.attached`           | `{"contact_id": N, "tag": {...}}`     | tag → contact |
| `tag.detached`           | `{"contact_id": N, "tag": {...}}`     | tag removal |
| `consent.granted`        | `{"consent": {...}}`                  | grant recorded |
| `consent.withdrawn`      | `{"consent": {...}}`                  | withdraw recorded |
| `deal.created`           | `{"deal": {...}}`                     | deal created |
| `deal.updated`           | `{"deal": {...}, "before": {...}}`    | deal updated |
| `deal.stage_changed`     | `{"deal": {...}, "before_stage_id": N}` | stage move |
| `deal.won`               | `{"deal": {...}}`                     | move to is_won stage |
| `deal.lost`              | `{"deal": {...}}`                     | move to is_lost stage |
| `deal.reopened`          | `{"deal": {...}}`                     | won/lost → open |
| `deal.deleted`           | `{"deal_id": N}`                      | hard delete |
| `task.created`           | `{"task": {...}}`                     | task created |
| `task.updated`           | `{"task": {...}, "before": {...}}`    | task updated |
| `task.completed`         | `{"task": {...}}`                     | status → done |
| `task.deleted`           | `{"task_id": N}`                      | hard delete |
| `form.submitted`         | `{"form": {...}, "submission": {...}, "contact_id": N}` | public form POST |
| `inbound.received`       | `{"endpoint_slug": "...", "event_id": N, "contact_id": N}` | inbound parsed |
| `portal_token.issued`    | `{"contact_id": N, "scope": "..."}`   | portal token created |
| `portal_token.used`      | `{"contact_id": N, "token_id": N}`    | first hit only |
| `portal_token.revoked`   | `{"token_id": N}`                     | revoke |
| `segment.evaluated`      | `{"segment_id": N, "added": N, "removed": N, "total": N}` | re-eval ran |
| `webhook.delivery_failed`| `{"webhook_id": N, "event_id": N}`    | after final retry |
| `plugin.enabled`         | `{"plugin": {...}}`                   | plug-in toggled on |
| `plugin.disabled`        | `{"plugin": {...}}`                   | plug-in toggled off |
| `plugin.error`           | `{"plugin": {...}, "hook": "...", "error": "..."}` | caught exception |
| `audit.sensitive_revealed`| `{"audit_id": N, "by_user_id": N}`   | admin reveal |

Subscribers using `*` get every event including future ones. Prefer
explicit lists for production.

## Anti-replay

The CRM treats `X-CRM-Delivery-ID` as the dedup key. If you receive
the same delivery ID twice within a reasonable window (24h),
process it ONCE.

## Suggested receiver

```python
import json, hmac, hashlib

CRM_SECRET = "..."   # the secret you saved on subscribe

def crm_webhook(headers, body):
    sig = headers.get("X-CRM-Signature", "")
    if not sig.startswith("sha256="):
        return 401
    expected = hmac.new(CRM_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig[7:], expected):
        return 401

    delivery_id = headers["X-CRM-Delivery-ID"]
    if seen_recently(delivery_id):
        return 200   # idempotent reprocess; tell CRM we got it

    event = headers["X-CRM-Event"]
    payload = json.loads(body)
    dispatch(event, payload)
    remember(delivery_id, ttl_hours=24)
    return 200
```

## Where to look in code

- `backend/webhooks.py` — enqueue, delivery worker, signing
- `backend/services/*.py` — every mutation calls
  `webhooks.enqueue(conn, "<event>", payload)`
- `migrations/0001_initial.sql` — `webhooks` + `webhook_events` schema

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
- [webhooks.md](webhooks.md) **← you are here**
- [errors.md](errors.md)

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
