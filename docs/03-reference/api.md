# REST API reference

All routes are mounted under `/api/`. JSON in, JSON out. The browser UI
uses its own `/contacts`, `/companies`, etc. routes (HTML responses) —
this doc covers only the JSON API.

## Auth

Every request must authenticate via one of:

1. **Bearer API key** (canonical for agents):
   `Authorization: Bearer <raw-key>`
2. **Cookie session** (used by the browser UI; sent automatically when
   logged in).

Missing or invalid auth → `401 authentication required`.

Generate keys at **Settings → API keys** in the UI. The raw key is
displayed exactly once at creation — store it then; it's not recoverable.

Key scopes:

| scope | can read | can write | can admin |
|-------|----------|-----------|-----------|
| `read`  | yes | no  | no  |
| `write` | yes | yes | no  |
| `admin` | yes | yes | yes |

Cookie-session scope derives from `users.role`: admin → admin scope,
readonly → read scope, user → write scope.

## Response shapes

**Success** (verb-dependent shape, always `ok: true`):

```json
{ "ok": true, "contact": { "id": 5, "full_name": "Maya Sato", ... } }
```

**Error**:

```json
{
  "ok": false,
  "error": {
    "code": "CONTACT_EMAIL_EXISTS",
    "message": "Another active contact already has email 'maya@blueriver.media'",
    "details": { "contact_id": 12 },
    "request_id": "f3a8…"
  }
}
```

Pass `X-Request-Id: <uuid>` to thread your own correlation ID through
the audit log. Otherwise the server generates one and echoes it back.

## Error code → HTTP status

| code | status |
|------|--------|
| `VALIDATION_ERROR`            | 400 |
| `FORBIDDEN`                   | 403 |
| `CONTACT_NOT_FOUND`, `COMPANY_NOT_FOUND`, `NOTE_NOT_FOUND`, `PIPELINE_NOT_FOUND`, `DEAL_NOT_FOUND`, `TASK_NOT_FOUND`, `FORM_NOT_FOUND`, `SEGMENT_NOT_FOUND`, `REPORT_NOT_FOUND`, `PORTAL_TOKEN_NOT_FOUND`, `INBOUND_ENDPOINT_NOT_FOUND`, `PLUGIN_NOT_FOUND`, `SAVED_VIEW_NOT_FOUND`, `API_KEY_NOT_FOUND`, `USER_NOT_FOUND` | 404 |
| `CONTACT_EMAIL_EXISTS`, `COMPANY_SLUG_EXISTS`, `TAG_EXISTS`, `FORM_SLUG_EXISTS`, `SEGMENT_SLUG_EXISTS`, `INBOUND_SLUG_EXISTS` | 409 |
| (default) | 400 |

---

## Endpoints

### Identity

| method | path | purpose |
|--------|------|---------|
| GET | `/api/me` | Inspect the calling identity (user_id, scope, surface) |

### Contacts

| method | path | purpose |
|--------|------|---------|
| POST   | `/api/contacts`                  | Create contact |
| GET    | `/api/contacts`                  | List/search contacts (`q`, `company_id`, `limit`, `offset`) |
| GET    | `/api/contacts/{id}`             | Get one contact |
| PUT    | `/api/contacts/{id}`             | Update (partial) |
| DELETE | `/api/contacts/{id}`             | Soft-delete |
| POST   | `/api/contacts/{id}/score`       | Recompute all score types for this contact |
| GET    | `/api/contacts/{id}/scores`      | List all persisted scores + evidence |
| GET    | `/api/contacts/{id}/timeline`    | Combined interactions (+ optional `?include_notes=1`) |
| GET    | `/api/contacts/{id}/notes`       | Notes (private notes hidden unless admin scope) |
| POST   | `/api/contacts/{id}/tags/{tagId}`   | Attach tag |
| DELETE | `/api/contacts/{id}/tags/{tagId}`   | Detach tag |
| GET    | `/api/contacts/{id}/consent`     | All consent records |
| POST   | `/api/contacts/{id}/portal-tokens`  | Issue a portal token |
| GET    | `/api/contacts/{id}/portal-tokens`  | List portal tokens for this contact |

### Companies

| method | path |
|--------|------|
| POST   | `/api/companies` |
| GET    | `/api/companies` (`q`, `limit`, `offset`) |
| GET    | `/api/companies/{id}` |
| PUT    | `/api/companies/{id}` |
| DELETE | `/api/companies/{id}` (soft-delete) |
| GET    | `/api/companies/{id}/timeline` |

### Timeline + notes

| method | path | purpose |
|--------|------|---------|
| POST   | `/api/interactions` | Log an interaction (`type`, `contact_id` and/or `company_id`, `title`, `body`, ...) |
| POST   | `/api/notes` | Create note (`visibility` ∈ `team`/`public`/`private`) |
| POST   | `/api/notes/{id}/reveal` | Admin-only: read a `private` note (writes a `note.private_revealed` audit row) |

### Tags + consent

| method | path |
|--------|------|
| POST   | `/api/tags` |
| GET    | `/api/tags` |
| POST   | `/api/consent` |

### Pipelines + deals

| method | path |
|--------|------|
| POST   | `/api/pipelines` |
| POST   | `/api/pipelines/from-template` (`name`, `template` ∈ `sales`/`client`/`sponsor`) |
| GET    | `/api/pipelines` |
| GET    | `/api/pipelines/{id}` |
| POST   | `/api/pipelines/{id}/stages` |
| POST   | `/api/pipelines/{id}/archive` |
| POST   | `/api/deals` |
| GET    | `/api/deals` (`pipeline_id`, `stage_id`, `status`, `assigned_to`, `limit`, `offset`) |
| GET    | `/api/deals/{id}` |
| PUT    | `/api/deals/{id}` (moving to a won/lost stage auto-flips `status`) |
| DELETE | `/api/deals/{id}` |

### Tasks

| method | path |
|--------|------|
| POST   | `/api/tasks` |
| GET    | `/api/tasks` (`status`, `assigned_to`, `contact_id`, `deal_id`, `due_before`, `priority`, `limit`) |
| GET    | `/api/tasks/{id}` |
| PUT    | `/api/tasks/{id}` |
| POST   | `/api/tasks/{id}/complete` |
| DELETE | `/api/tasks/{id}` |

### Forms

| method | path | purpose |
|--------|------|---------|
| POST   | `/api/forms` | Create form (`slug`, `schema`, `routing`) |
| GET    | `/api/forms` |  |
| GET    | `/api/forms/{id}` |  |
| PUT    | `/api/forms/{id}` |  |
| GET    | `/api/forms/{id}/submissions` | Most recent N submissions |

Note: the **public** form-submit endpoint is `POST /f/{slug}` (not under
`/api/`) — that's the URL you give to website visitors. It accepts
either form-encoded or JSON.

### Search + duplicates

| method | path | purpose |
|--------|------|---------|
| GET    | `/api/search?q=...&kind=contact&limit=20` | FTS5 cross-entity search (excludes private notes) |
| GET    | `/api/duplicates` | Likely duplicate contacts (email, normalized name) |
| POST   | `/api/duplicates/merge` | Merge `from_id` into `to_id` (timeline + tags + notes follow; the loser is soft-deleted) |

### Bulk

| method | path | purpose |
|--------|------|---------|
| GET    | `/api/export/{kind}.csv` | `kind` ∈ `contacts`/`companies`/`deals`/`tasks`/`interactions` |
| GET    | `/api/reports/{name}.csv` | Same report as JSON but CSV |

Imports are CLI-only (`crm import`) — the API doesn't accept multipart
file upload by design (keep service-layer simple, agents send
JSON per row instead).

### Scoring

| method | path |
|--------|------|
| POST   | `/api/scoring/recompute-all` (admin scope) |
| GET    | `/api/scoring/top?score_type=opportunity&limit=20` |

### Segments

| method | path |
|--------|------|
| POST   | `/api/segments` (type `static` or `dynamic`; dynamic needs `rules`) |
| GET    | `/api/segments` |
| GET    | `/api/segments/{id}` |
| GET    | `/api/segments/{id}/members` |
| POST   | `/api/segments/{id}/evaluate` (re-run dynamic rules + persist members) |
| DELETE | `/api/segments/{id}` |

### Reports

| method | path |
|--------|------|
| GET    | `/api/reports` (catalog: name + description for each pre-built report) |
| GET    | `/api/reports/{name}` (`?param1=...` query string passed to the report fn) |

Built-in report names: `pipeline_overview`, `task_load`,
`top_intent_now`, `dormant_high_value`, `consent_coverage`,
`recent_activity`, `won_lost_summary`, plus whatever plug-ins register.

### Portals

| method | path |
|--------|------|
| POST   | `/api/contacts/{id}/portal-tokens` |
| GET    | `/api/contacts/{id}/portal-tokens` |
| POST   | `/api/portal-tokens/{id}/revoke` |

### Inbound connectors

| method | path |
|--------|------|
| POST   | `/api/inbound-endpoints` |
| GET    | `/api/inbound-endpoints` |
| GET    | `/api/inbound-endpoints/{id}` |
| GET    | `/api/inbound-endpoints/{id}/events` |
| DELETE | `/api/inbound-endpoints/{id}` |

The **public** inbound endpoint is `POST /in/{slug}` with header
`X-Signature: sha256=<hex>` (HMAC-SHA256 of raw body using
`shared_secret`).

### Plug-ins

| method | path |
|--------|------|
| GET    | `/api/plugins` |
| POST   | `/api/plugins/reload` (admin scope; rescans `agent_surface/plugins/`) |
| POST   | `/api/plugins/{id}/enable` |
| POST   | `/api/plugins/{id}/disable` |

### Saved views

| method | path |
|--------|------|
| POST   | `/api/saved-views` (`entity`, `name`, `config`, `shared`) |
| GET    | `/api/saved-views/{entity}` |
| PUT    | `/api/saved-views/{id}` |
| DELETE | `/api/saved-views/{id}` |

---

## Webhooks (outbound)

Configure delivery URLs under **Settings → Webhooks**. Every service-layer
mutation enqueues into `webhook_events`; a worker delivers them in order.

Each delivery has these headers:

- `X-CRM-Event` — event name (e.g. `contact.created`)
- `X-CRM-Delivery-ID` — unique per attempt; safe to dedupe on
- `X-CRM-Signature` — `sha256=<hex>`, HMAC of raw body with the webhook's
  `secret`

See `docs/webhooks.md` for the full event catalog and payload shapes.

## Inbound (`POST /in/{slug}`)

Receive events from external systems (Stripe, n8n, your own scripts).
The receiver:

1. Logs the raw request to `inbound_events`.
2. Verifies the HMAC signature.
3. Applies the endpoint's `routing_json` (parse fields, attach to
   contact by email/external_id, attach tags).
4. Logs an `interaction` of type `form_submission` or `system`.

Status codes are deliberately limited: `200` (accepted), `400`
(parse/route failure — still logged), `401` (bad signature),
`404` (slug not found / inactive).

## A minimal end-to-end agent example

```bash
# Create a contact, then log an interaction against it
KEY="sk_live_..."   # your raw API key

curl -sX POST http://localhost:8000/api/contacts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Test Lead","email":"test@example.com"}'

# Response: {"ok":true,"contact":{"id":42,...}}

curl -sX POST http://localhost:8000/api/interactions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"type":"email","contact_id":42,"title":"Reply received","body":"They want a quote for copper signage."}'

# The `auto-tag-from-interactions` plug-in (if enabled) reads that body
# and attaches `topic:copper`, `topic:signage` tags to contact 42.
```

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
- [api.md](api.md) **← you are here**
- [cli.md](cli.md)
- [mcp.md](mcp.md)
- [plugins.md](plugins.md)
- [webhooks.md](webhooks.md)
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
