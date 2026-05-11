You are a senior full-stack software architect and implementation engineer.

Build **CRM** — a self-hosted, open-source customer relations management application.

---

## Product summary

CRM is a single-company self-hosted CRM whose every action is callable through multiple surfaces — UI, REST API, MCP server, CLI, skills, webhooks, cron, plug-ins, connectors. The goal: an agent harness (Claude Code, OpenClaw, Codex, Hermes, or any future system) can pull every lever the human UI exposes, and more.

**The CRM ships no LLM, no provider keys, no prompt logic.** It is the body; the agent is the brain. The CRM exposes operations; any agent operates them.

---

## Hard constraints

- **Stack:** Python 3.10+ / FastAPI, SQLite (single-file db), vanilla HTML+JS templates, cookie sessions, bearer API keys. **No** Docker, Postgres, React, build steps, or non-Python deployment dependencies.
- **Single company per install.** No multi-tenant logic, no parent/subsidiary model in core. Each install serves one company. Adding entity-aware logic for forks must be additive, not foundational.
- **Service layer is mandatory.** All four surfaces (REST, CLI, MCP, UI) route through `backend/services/*.py`. That is where validation, audit writes, and event firing live. Surfaces are *transports*; never reimplement business logic in a transport. This is the rule that makes "surface parity" real.
- **Surface parity:** every action exists as REST endpoint AND MCP tool AND CLI command before it gets a UI page. UI is one of several clients of the same logic.
- **Deterministic vs creative split.** CRM stores, exposes, validates, enforces, logs, fires. Agents summarize, decide, draft, analyze, recommend. Do not put creative/agent logic in CRM code.
- **One firehose:** every event lands in `interactions` table. Timeline, audit input, agent context all share one source.
- **Audit from day one.** Every mutation writes `audit_log` (user_id OR api_key_id, action, object_type, object_id, before, after, ts).
- **Self-host:** clone → `python setup.py` → `start.bat` → browser. Five-minute setup, max.

---

## In scope for v0

- Auth (humans via cookie sessions; agents via bearer API keys)
- Contacts, companies, interactions, notes, tags — full CRUD
- Audit log of every mutation (with before/after)
- REST API for all entities
- MCP server with matching named tools
- CLI mirroring the API (`python cli.py contact create --name X --email Y`)
- Minimal admin UI (login, dashboard, contacts list+profile, companies list+profile, settings)
- 5 starter skill files (markdown) — `create-contact`, `log-interaction`, `find-contact`, `add-note`, `tag-contact`
- One outbound webhook (`contact.created`)
- `setup.py` first-run wizard, `start.bat` launcher, `deploy.py` helper
- Docs for every surface

---

## Out of scope for v0

- Pipelines, deals, tasks → **v1**
- Forms, leads, lead routing → **v1**
- Consent enforcement → **v1** (table exists at v0, no enforcement yet)
- Bulk import/export → **v1**
- Duplicate detection → **v1**
- Scoring, segments, reports → **v2**
- Portals, integrations, advanced reporting → **v3+**
- LLM features of any kind → **never in core**
- Multi-tenant logic, parent/entity model → **never in core**

---

## Data model

Two tiers. **14 tables at v0 install**, **6 more added by v1 migrations.** All single-company. No `tenant_id` or `entity_id` anywhere.

### v0 install (in `schema.sql`)

```
schema_versions   users             api_keys
audit_log         contacts          companies
tags              contact_tags      company_tags
interactions      notes             consent
webhooks          webhook_events
```

`consent` is included at v0 so we can start recording it from day one. Enforcement (blocking sends without consent) is v1.

### v1 migrations

```
pipelines  pipeline_stages  deals  tasks  forms  form_submissions
```

No v1 tables in v0 schema. Every table that exists at v0 is one the v0 service layer writes to.

### Tables of note

**`interactions` is the catch-all event table.** Every meaningful action — email, call, meeting, form submission, page view, system event — lands here with `type`, `channel`, `title`, `body`, `metadata_json`, `occurred_at`, `source`.

**`notes` is separate from `interactions`** specifically because notes need visibility scope (public/team/private) that interactions don't.

## Locked schema decisions

- Every SQLite connection sets `PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;`. WAL lets surfaces read concurrently; busy_timeout prevents random "database is locked" errors.
- Indexes on every foreign key + `email`, `slug`, `occurred_at`.
- Versioned migrations from day 1: a `schema_versions` table records what's been applied; `setup.py` runs unapplied migrations in order.
- FTS5 deferred to **milestone 2** (over `contacts.name`, `companies.name`, `interactions.title+body`, `notes.body`, kept in sync via triggers). Milestone 1 uses simple indexed lookups on `email` / `name LIKE`.
- `interactions.type` is a string enum (`email`, `call`, `meeting`, `form_submission`, `page_view`, `note_system`, `system`). Not a separate table. `docs/interactions.md` freezes the `metadata_json` shape per type.
- `api_keys.scope` is a single column with values `read`, `write`, `admin`. Three buckets. No granular RBAC at v0.
- Soft-delete (`deleted_at` column) on `contacts` and `companies` only. Service-layer queries default to `WHERE deleted_at IS NULL`. Other tables hard-delete.
- `audit_log` records the acting principal: `user_id` for cookie-session humans, `api_key_id` for agents. Both nullable; at least one must be set.
- API keys: belong to a user, raw key shown ONCE at creation, revocable (`revoked_at`), every API mutation writes `api_key_id` to audit_log.
- **Admin reveal of private notes is explicit and audited.** Private notes are hidden by default even for admins. UI shows a "Reveal private note" button; clicking it loads the body AND writes `audit_log` with `action='note.private_revealed'`. No silent override. Admins cannot export private notes through any surface.
- Note visibility for agents (API key): `read` scope sees `public` only; `write` sees `public` + `team`; `admin` sees all *except* private (which requires the same explicit reveal, audited per fetch). Private notes never appear in webhook payloads.

## Operational & security baseline (mandatory at v0)

### Auth & sessions
- Passwords hashed with **Argon2id** (preferred) or **bcrypt** (fallback). Never store raw passwords. `setup.py` creates the first admin password from interactive input and persists only the hash.
- Human UI sessions expire after **7 days of inactivity**. Logout invalidates immediately. Cookies are `HttpOnly`, `SameSite=Lax`, `Secure` in production.
- **CSRF tokens required on every mutating UI form action.** REST API bearer-key requests do not use CSRF (bearer auth is sufficient). Cookie-authenticated browser mutations always do.

### Configuration via environment
```
CRM_SECRET_KEY            session signing + CSRF tokens
CRM_DB_PATH               default ./crm.db
CRM_BASE_URL              for webhook source & email links
CRM_ENV                   dev | prod
CRM_COOKIE_SECURE         true | false (auto-true when CRM_ENV=prod)
CRM_WEBHOOK_TIMEOUT_SECONDS    default 5
CRM_WEBHOOK_MAX_RETRIES        default 5 (exponential backoff)
```
`.gitignore` includes: `.env`, `crm.db`, `*.db`, `logs/`, `__pycache__/`, generated key files. Never commit secrets or local databases.

### Webhook signing
- Outbound payloads signed with **HMAC-SHA256** using the per-webhook secret.
- Required headers: `X-CRM-Event`, `X-CRM-Timestamp`, `X-CRM-Signature`, `X-CRM-Delivery-ID`.
- Signature: `hex HMAC-SHA256(secret, "{timestamp}.{body}")`.
- Delivery timeout 5s. Retries with exponential backoff up to `CRM_WEBHOOK_MAX_RETRIES`.
- **Private notes never appear in webhook payloads.**

### Standard error response
Every REST and MCP error response uses this shape:
```json
{
  "ok": false,
  "error": {
    "code": "CONTACT_NOT_FOUND",
    "message": "Contact not found.",
    "details": {},
    "request_id": "..."
  }
}
```

### Pagination
All list endpoints accept `limit` and `offset`. Default `limit=50`, maximum `limit=200`. UI list pages paginate from day one.

### Email normalization & uniqueness
- Stored **lowercase, trimmed**. Service layer normalizes on the way in.
- May be NULL.
- Partial unique index enforces no duplicates among active contacts:
  ```sql
  CREATE UNIQUE INDEX uq_contacts_active_email
    ON contacts (email)
    WHERE email IS NOT NULL AND deleted_at IS NULL;
  ```

### Idempotency
`POST` create endpoints may accept an optional `Idempotency-Key` header. Same key + same principal + same action within 24h returns the original result instead of creating a duplicate. Stored in a small `idempotency_keys` lookup table (added to v0 schema if used in Milestone 1; otherwise v1).

### Backup
Ship a backup command at v0:
```
python agent_surface/cli.py backup create [--out <path>]
```
Uses SQLite's online backup API to a timestamped file. Live writes do not corrupt the snapshot.

### Extension folders at v0
`agent_surface/cron.py`, `agent_surface/plugins/`, `agent_surface/connectors/`, `agent_surface/prompts/` exist with **README stubs only**. Do not implement real cron jobs, connectors, or plug-ins until their planned phase. This is the one allowed exception to "no placeholders" — empty extension scaffolds.

### Milestone 1 acceptance test
Write `tests/test_milestone1.py` (or `scripts/acceptance_milestone1.py`) that programmatically verifies the proof: contact created through REST, CLI, MCP; audit_log row written; webhook event delivered (or recorded as pending in webhook_events with mock receiver). UI portion can stay manual. **Milestone 1 is not done until this script is green.**

---

## ServiceContext

Every service function takes a `ServiceContext` as its first argument:

```python
@dataclass
class ServiceContext:
    user_id: int | None        # cookie-session user OR system jobs
    api_key_id: int | None     # set when called via REST/MCP/CLI through an API key
    role: str                  # 'admin' | 'user' | 'readonly' | 'system'
    scope: str                 # 'read' | 'write' | 'admin'
    surface: str               # 'ui' | 'rest' | 'cli' | 'mcp' | 'cron' | 'plugin' | 'webhook'
    request_id: str            # uuid for correlation across audit/logs/webhook_events
```

Service signatures:

```
contacts.create(ctx, payload) -> Contact
contacts.update(ctx, contact_id, payload) -> Contact
contacts.delete(ctx, contact_id) -> None   # soft-delete
interactions.log(ctx, payload) -> Interaction
notes.create(ctx, contact_id, body, visibility) -> Note
notes.list_for_contact(ctx, contact_id) -> list[Note]
```

Transports build `ctx` once at the request boundary and pass it through. Services never reach into HTTP/CLI/MCP request objects directly.

## Webhook outbox pattern

Webhook dispatch uses an outbox pattern. Service-layer mutations create `webhook_events` rows **inside the same transaction** as the data change. Delivery happens *after commit* with retry logging. **A webhook failure must NEVER roll back the original CRM mutation.**

Per-mutation flow inside a service:

1. BEGIN
2. Insert / update target row.
3. Insert `audit_log` row.
4. Insert system-type `interactions` row when relevant.
5. Insert `webhook_events` row(s) for subscribers of this event.
6. COMMIT.
7. *After commit* — dispatcher reads `webhook_events WHERE status='pending'`, delivers, updates `status` + `attempts` + `response`.

## CLI is local-only

The CLI calls the service layer **directly** against the local SQLite database. It is **not** a network client. It must run on the same machine (or have filesystem access to the same `crm.db`).

Remote automation should use **REST API** or **MCP server**. The CLI is for local administration, scripts, and agent subprocess workflows on the same host.

---

## Surface architecture

Every action operates through:

1. **UI** — `ui/*.html`, rendered by `backend/main.py`. Vanilla HTML + inline JS, no build step.
2. **REST API** — `backend/api.py` → `/api/*`. JSON in/out. Cookie or API-key auth.
3. **MCP server** — `agent_surface/mcp_server.py` exposes named tools (`create_contact`, `log_interaction`, etc.).
4. **CLI** — `agent_surface/cli.py contact create ...`. Subprocess-safe; agents can shell out.
5. **Skills** — markdown in `agent_surface/skills/` an agent reads to learn the levers.
6. **Webhooks** — outbound, signed, retry-capable; delivered via the outbox pattern (see below).
7. **Cron** — declared in `agent_surface/cron.py`.
8. **Plug-ins** (`agent_surface/plugins/`), **connectors** (`agent_surface/connectors/`), **prompts** (`agent_surface/prompts/`) — extension surfaces. **`prompts/` contains optional external-agent prompt templates only. The CRM never loads, executes, or depends on them at runtime.**

---

## Repo structure

```
GCRM/
├── README.md
├── LICENSE                  (MIT)
├── CLAUDE.md                (guide for agents working on this codebase)
├── server.py                (FastAPI entry)
├── schema.sql               (DDL, applied on first run)
├── setup.py                 (first-run wizard)
├── deploy.py                (self-deploy helper, Aurora pattern)
├── start.bat                (dev launcher)
├── requirements.txt
│
├── backend/
│   ├── main.py              (UI routes + HTML rendering — calls services/)
│   ├── db.py                (SQLite connection helper + PRAGMA setup)
│   ├── auth.py              (sessions + API keys)
│   ├── api.py               (REST endpoints — thin, dispatches to services/)
│   ├── webhooks.py          (outbound dispatch + retry log)
│   ├── audit.py             (mutation logger)
│   ├── context.py           (ServiceContext dataclass + ctx-builder helpers)
│   └── services/            (the shared core — REST/CLI/MCP/UI all call these)
│       ├── contacts.py
│       ├── companies.py
│       ├── interactions.py
│       ├── notes.py
│       ├── tags.py
│       ├── consent.py
│       └── auth_keys.py
│
├── ui/
│   ├── login.html, dashboard.html
│   ├── contacts.html, contact.html
│   ├── companies.html, company.html
│   ├── settings.html
│   ├── styles.css, app.js
│
├── agent_surface/
│   ├── mcp_server.py
│   ├── cli.py
│   ├── cron.py
│   ├── skills/
│   ├── prompts/
│   ├── connectors/
│   └── plugins/
│
└── docs/
    ├── data-model.md, api.md, mcp.md, cli.md,
    ├── skills.md, webhooks.md, cron.md,
    ├── plugins.md, deploy.md
```

---

## Build order (v0)

### Milestone 1 — the proof

The architecture is real when *one contact can be created through UI, REST, CLI, and MCP; the mutation lands in audit_log; the contact is visible in SQLite; and a `contact.created` webhook fires.* Stop and verify before continuing.

1. `schema.sql` — DDL for the **14 v0 tables only**, plus indexes + `schema_versions`. **No v1 tables.** **No FTS5 in Milestone 1** — use indexed `email` / `name LIKE` lookups. FTS5 lands in Milestone 2.
2. `backend/db.py` — connection helper with `PRAGMA foreign_keys = ON`, migration runner
3. `backend/auth.py` — cookie sessions + API key validation (hash check + revocation + scope)
4. `backend/audit.py` — mutation logger (writes `user_id` or `api_key_id`, action, object, before, after, ts)
5. `backend/webhooks.py` — dispatch + retry log + signing
6. `backend/services/contacts.py` — create/get/update/list/soft-delete. Validates, writes audit, fires `contact.created`.
7. `backend/api.py` — REST endpoints for contacts. Thin: dispatch to `services.contacts`.
8. `agent_surface/cli.py` — `contact create`, `contact get`, `contact list`. Calls `services.contacts`.
9. `agent_surface/mcp_server.py` — `create_contact`, `get_contact`, `find_contacts`. Calls `services.contacts`.
10. `agent_surface/skills/create-contact.md` — markdown describing the lever.
11. `ui/contacts.html` + `ui/contact.html` — minimal admin UI. Calls `services.contacts` directly.
12. `setup.py` — first-run wizard (creates db, applies migrations, creates admin user, generates first API key shown once).
13. **Verify milestone 1 acceptance test passes** before moving on.

### Milestone 2 — repeat the pattern

14. Companies (same 6-step pattern: service → REST → CLI → MCP → skill → UI)
15. Interactions (log + retrieve + timeline view)
16. Notes (with visibility scope)
17. Tags
18. `deploy.py` helper
19. Docs for every surface (REST endpoints, MCP tools, CLI commands, skill files, webhooks, cron, deploy)

**Discipline for each entity:** service → REST → CLI → MCP → skill → UI, in that order. The service-layer function is written first; everything else is a transport that calls it.

---

## Code style

- **Match Aurora-Gracewood's patterns.** Single-file FastAPI backend with HTML rendered from Python f-strings (or simple templates); vanilla JS inline in templates; `deploy.py` script pattern for self-deploy; cookie sessions with HttpOnly/Secure/SameSite=Lax.
- **Complete files.** No placeholders, no "...rest of the function here" comments. Every file is runnable as written.
- **No comments explaining WHAT the code does.** Comment only for non-obvious WHY (constraint, invariant, workaround for a specific bug).
- **No emojis in code or comments.**
- **Tests** for CRUD round-trips on every entity and for the auth boundary.

---

## Documentation expectations

- Every REST endpoint documented in `docs/api.md` with example request + response.
- Every MCP tool documented in `docs/mcp.md` with example call.
- Every CLI command documented in `docs/cli.md` with example.
- Every skill in `agent_surface/skills/` is self-contained — an agent reading the markdown alone should know how to invoke the right tool with the right arguments.

---

## Mandatory non-goals

- **Do not** add multi-tenant, parent/entity, subsidiary, or organization-hierarchy logic to core.
- **Do not** include any LLM/AI/provider-specific code (no `openai` import, no `anthropic` import, no API keys for model providers).
- **Do not** add Docker, build steps, or non-Python deployment dependencies.
- **Do not** introduce React, Vue, Svelte, or any frontend framework requiring a build step.
- **Do not** refactor Aurora-Gracewood patterns — copy them.
- **Do not** put business logic in transports. REST endpoints, CLI commands, MCP tools, and UI route handlers all call functions in `backend/services/*.py`. If you find yourself writing the same validation in two places, you've broken the rule.
- **Do not** build a dashboard before contacts/companies/interactions are working end-to-end through all surfaces. Dashboards built on empty data become fake decoration.
- **Do not** put creative/agent behavior in CRM code (no "summarize this contact," no "suggest a follow-up"). The CRM provides levers; agents pull them.

---

## What success looks like

After running `python setup.py` and `start.bat`, a fresh user:

1. Logs in as the admin they just created.
2. Creates a contact through the UI.
3. Generates an API key in settings.
4. From a separate terminal, runs `python cli.py contact create --name "Test" --email "t@example.com"` and sees the new contact in the UI.
5. Points Claude Code (or any MCP client) at `agent_surface/mcp_server.py` and creates a contact via MCP.
6. Logs an interaction against that contact via REST API.
7. Sees the interaction in the contact's timeline in the UI.
8. Subscribes a webhook URL to `contact.created` and watches a new contact fire the event.

All five surfaces (UI, REST, CLI, MCP, webhook) producing and observing the same data, against the same SQLite file, with full audit trails.

---

## Begin

Start by writing `schema.sql`, `backend/db.py`, and `backend/auth.py`. Then iterate per the build order. After each entity, write the API + CLI + MCP + skill + UI in sequence so all surfaces stay aligned.
