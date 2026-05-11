# CRM — Customer Relations Management

> An open-source, self-hostable CRM that ships as a complete tool surface for agent-driven workflows.

This is an open-source CRM. Generic. Single-company. Self-hosted. Designed so an AI agent harness (Claude Code, OpenClaw, Codex, or anything else) can pull every lever the human UI exposes — and more.

The CRM is the body. The agent is the brain. The body has nerve endings — API, MCP, CLI, webhooks, cron, skills, plug-ins, prompts, connectors, scripts, markdown. Whatever harness you wire in pulls the levers. The CRM ships **no LLM, no provider keys, no prompt logic**. It exposes operations; any agent operates them.

---

## Status (v4.1 — 2026-05-11)

This Blueprint was written as the planning document at v0. Most of what it describes as future ("v1", "v2", "v3+") is now shipped. Read it as a design rationale, not a roadmap. The current state of the codebase is:

- **v0 (shipped)** — contacts, companies, interactions, notes, tags, consent, audit, webhooks, all four surfaces (REST/CLI/MCP/UI)
- **v1 (shipped)** — pipelines + deals, forms, tasks, FTS5 search, bulk import/export, duplicate detection
- **v2 (shipped)** — rule-based scoring (5 score types with evidence trails), segments (static + dynamic), reports catalog
- **v3 (shipped)** — portal tokens (`/portal/{token}` self-service URLs), inbound endpoints (`/in/{slug}` HMAC-signed), connector UI
- **v4 (shipped)** — plug-in framework with hook dispatch, saved views, RBAC scaffolding
- **v4.1 (shipped)** — richer contact model (birthday, pronouns, language, socials, about, interests, source, referrer, preferences), demo seed script, expanded report widgets, first AI plug-in (auto-tag-from-interactions)

**31 application tables** across 7 migration files. ~95 REST endpoints, 60+ CLI actions, ~40 MCP tools. Full operational documentation in `docs/` (the wiki) + `AGENTS.md` + `SCHEMATICS.md`.

When the text below tags something `(v1)` or `(v0)` or describes a feature as "deferred", that's historical. Section 9 below describes the build phases as planned; refer to the wiki and `README.md` for what actually exists.

---

## 1. One-line definition

A self-hosted CRM whose every action is callable through multiple surfaces — UI, REST API, MCP server, CLI, skills, webhooks, cron — so humans and agents share the same plumbing.

---

## 2. Build principles

1. **Tool surface first.** Every UI action exists as a REST endpoint, MCP tool, and CLI command. The UI is one of several clients of the same logic.
2. **One service layer, many surfaces.** All four surfaces (REST, CLI, MCP, UI) call functions in `backend/services/*.py`. That layer is where validation, audit writes, and event firing live. Surfaces are *transports*, not *features*. This is the rule that makes surface parity real instead of aspirational.
3. **No AI inside.** The CRM is provider-agnostic. Agent harnesses connect from outside. No LLM dependencies in core, no prompt logic, no key management.
4. **Deterministic vs creative split.** CRM stores, exposes, validates, enforces, logs, fires. Agent summarizes, decides, drafts, analyzes, recommends, chains actions. Do not blur this line in the public CRM. That separation is what makes the design durable.
5. **One firehose.** Every meaningful event lands in one `interactions` table. Timeline, audit input, and agent context all share one source.
6. **Single-company by default.** No multi-tenant logic. No parent/subsidiary model. Each install serves one company. If a fork needs multi-entity, it's additive.
7. **Boring stack.** FastAPI + SQLite + vanilla JS templates. Same pattern as Aurora-Gracewood. No Docker, no Postgres, no React, no build step, no new tooling decisions.
8. **Self-host first.** Clone the repo, run `setup.py`, you're running in five minutes.
9. **Audit and recovery from day one.** Every mutation writes audit log. Soft-delete on long-lived records (contacts, companies). Webhook delivery has retry log. API keys are revocable.

---

## 3. What the full CRM is designed to do

The bullets below describe the *target* product across all versions. Section 9 splits this into per-version milestones; not all of these exist at v0.

- Track **contacts** (people) and **companies** (organizations they work for)
- Log every **interaction** (email, call, meeting, form submission, page view, note-system, system) into one timeline
- Capture **leads** through forms (public POST endpoints at `/f/{slug}`)
- Move opportunities through **pipelines** with customizable stages and **deals**
- Assign **tasks** to users — owners, due dates, completion
- **Tag** anything with anything
- Track **consent** per channel per contact
- Fire **webhooks** via an outbox pattern (see §6)
- Run **cron** jobs (digests, reports, cleanup, integrations)
- **Audit** every mutation with before/after JSON
- **Search** via FTS5 across contacts, companies, interactions, and non-private notes
- **Score** contacts on 5 dimensions with evidence trails
- Build **segments** (static + dynamic JSON rule trees)
- Issue **portal tokens** for external contacts to see their own data
- Receive **inbound** events from external systems at `/in/{slug}` with HMAC signing
- **Plug-ins** drop-in to react to events and contribute to fit scoring

---

## 4. What it does NOT do (and why)

- **No multi-tenant / SaaS layer** — each install is one company. Simpler schema, faster ship.
- **No parent/subsidiary model in core** — too specific to be a generic feature. Forks can add it; the core doesn't ship it.
- **No LLM features in core** — that's the agent harness's job (or a plug-in's). We provide levers; they decide what to pull.
- **No Docker, no Postgres, no React, no build step** — boring stack on purpose.
- **No relationship graph visualization** — additive once interaction data is rich enough to mine; not core.

(At v0 we also deferred portals, advanced segmentation, scoring, and plug-ins. All four shipped in v2–v4; see the Status banner at top.)

---

## 5. The surfaces

Every action operates through ALL of these:

| Surface | Purpose | Location |
| --- | --- | --- |
| **UI** | Humans browsing in a browser | `ui/*.html`, served by `backend/main.py` |
| **REST API** | Programmatic JSON access | `backend/api.py` → `/api/*` |
| **MCP server** | Agent-native tool calls | `agent_surface/mcp_server.py` |
| **CLI** | Shell-driven automation | `agent_surface/cli.py` |
| **Skills** | Markdown an agent reads to learn levers | `agent_surface/skills/*.md` |
| **Webhooks** | Outbound event notifications | `backend/webhooks.py` |
| **Cron** | Scheduled jobs | `agent_surface/cron.py` |
| **Plug-ins** | Drop-in Python extensions | `agent_surface/plugins/` |
| **Connectors** | Adapters for incoming data | `agent_surface/connectors/` |
| **Prompts** | Optional external-agent prompt templates (CRM never loads or executes them) | `agent_surface/prompts/` |

Each surface is independently usable. A user can drive the CRM entirely through one surface (e.g., MCP-only from Claude Code) or any mix.

**Surface parity rule:** every action exists as REST endpoint AND MCP tool AND CLI command before it gets a UI page. UI is built last for each feature.

---

## 6. Data model (v0 → v4.1)

**31 application tables** across 7 migration files. All single-company, no entity/tenant layer. Every mutation writes to `audit_log`. Authoritative reference: [`docs/03-reference/data-model.md`](docs/03-reference/data-model.md).

Tables grouped by introducing version:

**v0** (15 tables in `migrations/0001_initial.sql`):
`schema_versions`, `users`, `sessions`, `api_keys`, `audit_log`, `contacts`, `companies`, `tags`, `contact_tags`, `company_tags`, `interactions`, `notes`, `consent`, `webhooks`, `webhook_events`.

**v1** (6 tables — `migrations/0002_v1.sql` + `0003_v1_fts.sql`):
`pipelines`, `pipeline_stages`, `deals`, `tasks`, `forms`, `form_submissions`, plus the FTS5 virtual table `search_index` + 9 triggers, plus `idempotency_keys`.

**v2** (3 tables — `migrations/0004_v2.sql`):
`contact_scores`, `segments`, `segment_members`.

**v3** (3 tables — `migrations/0005_v3.sql`):
`portal_tokens`, `inbound_endpoints`, `inbound_events`.

**v4** (5 tables — `migrations/0006_v4.sql`):
`plugins`, `plugin_hooks`, `saved_views`, `roles`, `role_permissions`, `user_roles`.

**v4.1** (no new tables — `migrations/0007_richer_contacts.sql` adds columns to `contacts`):
birthday, pronouns, language, linkedin_url, twitter_url, instagram_url, website_url, about, interests_json, source, referrer, best_contact_window, do_not_contact.

### SQLite discipline

Every SQLite connection sets:

```
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

WAL lets UI / CLI / MCP read the same file concurrently. `busy_timeout` prevents random "database is locked" failures when multiple surfaces hit the file at once.

- Indexes on every foreign key + `email`, `slug`, `occurred_at`.
- Versioned migrations from day 1 — a `schema_versions` table records applied migrations; `setup.py` runs unapplied ones in order.
- **FTS5 is deferred to v0 Milestone 2.** Milestone 1 uses simple indexed lookups on `email` and `name LIKE`. When added in Milestone 2, FTS5 covers `contacts.name`, `companies.name`, `interactions.title+body`, and `notes.body`, kept in sync via SQLite triggers.
- JSON columns (`metadata_json`, `custom_fields_json`, `payload_json`) only where flexibility is genuinely needed. Avoid JSON for anything queryable on a hot path — promote to a column.

### Locked schema decisions

- **`interactions.type` is a string enum**, not a separate lookup table. v0 values: `email`, `call`, `meeting`, `form_submission`, `page_view`, `note_system`, `system`. Adding values is a docs update, not a migration.
- **`api_keys.scope` is a single column** with values `read`, `write`, `admin`. Three buckets. Granular RBAC is a v3 problem.
- **Soft-delete only on `contacts` and `companies`** (nullable `deleted_at` column). Not on `interactions` (immutable event log; deletes corrupt the timeline). Not on `tasks` (short-lived). Default `WHERE deleted_at IS NULL` in service-layer queries; deleted records remain in DB and audit.
- **`audit_log` records the acting principal** — `user_id` for cookie-session humans, `api_key_id` for agents. Both nullable; at least one must be set.

### Pre-coding policy decisions

These need to be decided BEFORE writing code, because they shape the schema and service-layer logic:

**Interactions:**
- Required at insert: `contact_id` OR `company_id` (at least one), `type`, `occurred_at`.
- Optional: `title`, `body`, `channel`, `metadata_json`, `source`.
- `metadata_json` is for surface-specific extra context (e.g., for `page_view` type: `{"url": "...", "session_id": "..."}`). UI displays type-aware templates; agents parse the type to know the schema.
- System events (e.g., "contact merged from X") go in `interactions` with `type = 'system'` so they show in the timeline.

**Notes:**
- Visibility scopes: `public` (everyone with read access on the contact), `team` (admins + author), `private` (author only).
- **Admin reveal is explicit, not implicit.** Private notes are hidden by default *even for admins*. The UI shows a "Reveal private note" button; clicking it loads the body AND writes an `audit_log` row with `action = 'note.private_revealed'`. There is no silent admin override.
- Admins **cannot export** private notes through any surface.
- Agent access (API key): `read` scope sees `public` notes only. `write` scope sees `public` + `team`. `admin` scope sees all *except* private (which requires the same explicit reveal mechanism, audited per fetch). Private notes are never sent via webhook.

**API keys:**
- Belong to users, not the company. Each key has a single owning user.
- Raw key shown ONCE at creation, then only the hash + truncated prefix is visible.
- Revocable — `revoked_at` column, set on revoke; queries filter out revoked keys.
- Every API mutation writes `api_key_id` to `audit_log`.

### ServiceContext

Every service function takes a `ctx` object as its first argument. This makes audit, permission, and surface-aware debugging deterministic — no more guessing who did what through which transport.

```python
@dataclass
class ServiceContext:
    user_id: int | None        # set when called via cookie session or for system jobs
    api_key_id: int | None     # set when called via REST/MCP/CLI through an API key
    role: str                  # 'admin' | 'user' | 'readonly' | 'system'
    scope: str                 # 'read' | 'write' | 'admin' (from api_key.scope or user role)
    surface: str               # 'ui' | 'rest' | 'cli' | 'mcp' | 'cron' | 'plugin' | 'webhook'
    request_id: str            # uuid for correlation across audit, logs, webhook_events
```

Service signatures:

```
contacts.create(ctx, payload) -> Contact
contacts.update(ctx, contact_id, payload) -> Contact
contacts.delete(ctx, contact_id) -> None        # soft-delete
interactions.log(ctx, payload) -> Interaction
notes.create(ctx, contact_id, body, visibility) -> Note
notes.list_for_contact(ctx, contact_id) -> list[Note]   # filters by ctx.scope
```

Transports build the `ctx` once at request boundary and pass it through. Services never reach into HTTP / CLI / MCP request objects directly.

### Webhook outbox pattern

Webhook dispatch uses an outbox pattern. Service-layer mutations create `webhook_events` rows **inside the same transaction** as the data change. Delivery happens *after commit* with retry logging. A webhook failure must NEVER roll back the original CRM mutation.

Per-mutation flow inside a service function:

1. BEGIN
2. Insert / update the target row.
3. Insert audit_log row.
4. Insert any `system`-type interaction (e.g., "contact merged from X").
5. Insert webhook_events row(s) for subscribers of this event.
6. COMMIT.
7. *(After commit)* Dispatcher worker reads webhook_events with `status=pending`, attempts delivery, updates `status` + `attempts` + `response`.

A broken webhook URL produces a log entry and retries; it does not break contact creation.

### CLI is a local operator surface

The CLI calls the service layer **directly** against the local SQLite database. It is not a network client. It must run on the same machine (or have filesystem access to the same `crm.db`).

Remote automation should use **REST API** or **MCP server**. The CLI's purpose is local administration, scripts, agent subprocess workflows, and bulk operations on the same host.

### Identity & auth
- **users** — humans logging in. email, password_hash, role, created_at
- **api_keys** — per-user agent tokens. hash, scopes, last_used_at
- **audit_log** — every mutation: user_id, action, object_type, object_id, before, after, ts

### Core records
- **contacts** — name, email, phone, avatar, company_id, location, timezone, custom_fields_json
- **companies** — name, website, domain, industry, size, location, custom_fields_json

### Labels
- **tags** — name, color, scope (contact/company/all)
- **contact_tags**, **company_tags** — join tables

### The timeline firehose
- **interactions** — contact_id, company_id, type (email/call/meeting/form/view/note/system), channel, title, body, metadata_json, occurred_at, source

### Notes (separate from interactions for permission scope)
- **notes** — contact_id, company_id, body, visibility (public/team/private), created_by, created_at

### Pipelines (v1)
- **pipelines** — name, type
- **pipeline_stages** — pipeline_id, name, order
- **deals** — contact_id, company_id, pipeline_id, stage_id, title, value, currency, expected_close, status

### Work (v1)
- **tasks** — contact_id, company_id, deal_id, assigned_to, title, description, due_date, status, completed_at

### Lead capture (v1)
- **forms** — name, slug, schema_json, routing_rules_json
- **form_submissions** — form_id, payload_json, contact_id (resolved or null), created_at

### Privacy (v0 table, v1 enforcement)
- **consent** — contact_id, channel (email/sms/phone/marketing/...), status, source, granted_at, withdrawn_at

### Outbound integration
- **webhooks** — url, event_types_json, secret, active
- **webhook_events** — event_type, payload_json, target_url, status, attempts, sent_at, response

The schema is designed so adding entity-aware logic later in a fork is purely additive: a new `entities` table + a `contact_entities` join + nullable `entity_id` on relevant tables. No core schema needs to change.

---

## 6b. Operational and security baseline

These rules are part of v0. They are small enough to bake in now and expensive to retrofit later.

### Auth & sessions
- **Passwords** are hashed with **Argon2id** (preferred) or **bcrypt/passlib** as fallback. Raw passwords are never stored. `setup.py` creates the first admin password from input and stores only the hash.
- **Human UI sessions** expire after **7 days of inactivity**. Logout invalidates the session immediately. Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` in production.
- **CSRF**: every mutating UI form action requires a CSRF token tied to the user's session. REST API bearer-key requests do not use CSRF (bearer auth is the protection). Cookie-authenticated browser mutations always do.

### Configuration & secrets
Environment variables (read at startup, never committed):
```
CRM_SECRET_KEY            session signing + CSRF
CRM_DB_PATH               default ./crm.db
CRM_BASE_URL              for webhook source & email links
CRM_ENV                   dev | prod
CRM_COOKIE_SECURE         true | false (auto-true when CRM_ENV=prod)
CRM_WEBHOOK_TIMEOUT_SECONDS    default 5
CRM_WEBHOOK_MAX_RETRIES        default 5 (exponential backoff)
```

`.gitignore` includes `.env`, `crm.db`, `*.db`, `logs/`, `__pycache__/`, and any generated key files. Never commit local databases or secrets.

### Webhook signing
Outbound webhook payloads are signed with **HMAC-SHA256** using the per-webhook secret. Required headers:
```
X-CRM-Event           e.g. contact.created
X-CRM-Timestamp       unix seconds (used in signature)
X-CRM-Signature       hex HMAC-SHA256(secret, "{timestamp}.{body}")
X-CRM-Delivery-ID     uuid for idempotency on receiver side
```
- Delivery timeout: 5 seconds (configurable).
- Non-2xx responses are retried with exponential backoff up to `CRM_WEBHOOK_MAX_RETRIES`.
- **Private notes never appear in webhook payloads.**

### Standard error response
Every REST and MCP error uses one shape:
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
Agents (and humans) can branch on `code` reliably; `request_id` correlates against `audit_log` / app logs.

### Pagination
All list endpoints accept `limit` and `offset`. Default `limit=50`, maximum `limit=200`. UI list pages paginate from day one (no infinite-scroll tricks at v0).

### Email normalization & uniqueness
- Email is stored **lowercased and trimmed**. Service layer applies this on the way in.
- Email may be NULL.
- Active contacts (`deleted_at IS NULL`) should not share the same email. Enforced via a **partial unique index**:
  ```sql
  CREATE UNIQUE INDEX uq_contacts_active_email
    ON contacts (email)
    WHERE email IS NOT NULL AND deleted_at IS NULL;
  ```
- v1 may relax this with explicit duplicate-handling tooling.

### Idempotency (optional in v0, recommended)
`POST` create endpoints may accept an optional `Idempotency-Key` header. If the same key is reused by the same principal for the same action within a window (24h default), the original result is returned instead of creating a duplicate. Useful because agents retry on transient errors. Stored in a small `idempotency_keys` table; lookup before write.

### Backup
A first-class CLI command for self-hosting survival:
```
python agent_surface/cli.py backup create [--out <path>]
```
Copies the SQLite DB to a timestamped file (uses SQLite's online backup API so live writes don't corrupt the snapshot). No fancy schedule at v0 — that's a cron job in v1.

### Extension folders at v0
`agent_surface/cron.py`, `agent_surface/plugins/`, `agent_surface/connectors/`, `agent_surface/prompts/` exist at v0 with **README stubs only**. No real cron jobs, connectors, or plug-ins are implemented until their planned phase. This keeps the structure future-ready without violating "no placeholders."

### Acceptance test for Milestone 1
A script (`tests/test_milestone1.py` or `scripts/acceptance_milestone1.py`) automates the milestone proof for the API / CLI / database / audit / webhook surfaces. UI portion can stay manual. Milestone 1 is not done until the script is green.

---

## 7. Tech stack

- **Backend**: Python 3.10+ / FastAPI
- **Database**: SQLite (single file: `crm.db`)
- **Templates**: vanilla HTML + lightweight inline JS (same pattern as Aurora-Gracewood realm pages)
- **Auth**: cookie sessions for humans (HttpOnly, Secure, SameSite=Lax); bearer API keys for agents
- **Deploy**: `deploy.py` script (clone of the Aurora pattern); `start.bat` for local dev
- **No** Docker, Postgres, React, build steps, or external services

Why this stack: it's the one proven in Aurora-Gracewood. Same patterns mean less to learn, less to maintain, faster shipping. SQLite scales further than people expect — comfortable up to thousands of contacts with light concurrency. Postgres is the migration path, not the starting line.

---

## 8. Repo structure

```
CRM/
├── README.md                      install + quickstart for self-hosters
├── LICENSE                        MIT
├── CLAUDE.md                      guide for AI agents working ON the codebase
├── server.py                      FastAPI entry point
├── schema.sql                     DDL — applied on first run
├── setup.py                       first-run wizard (creates db, admin user)
├── deploy.py                      self-deploy helper
├── start.bat                      dev launcher
├── requirements.txt
│
├── backend/
│   ├── main.py                    routes + HTML rendering
│   ├── db.py                      SQLite connection helper
│   ├── auth.py                    cookie sessions + API keys
│   ├── api.py                     REST endpoints (thin — dispatches to services)
│   ├── webhooks.py                outbound event dispatch + retry
│   ├── audit.py                   mutation logging helper
│   ├── context.py                 ServiceContext dataclass + helpers for ctx creation
│   └── services/                  the shared core — all surfaces route through here
│       ├── contacts.py            create/update/find/soft-delete/list contacts
│       ├── companies.py
│       ├── interactions.py        log + retrieve timeline
│       ├── notes.py               visibility-scoped reads/writes (+ private reveal)
│       ├── tags.py
│       ├── consent.py             record/list consent states (enforcement in v1)
│       └── auth_keys.py           api-key lifecycle
│
├── ui/
│   ├── login.html, dashboard.html
│   ├── contacts.html, contact.html
│   ├── companies.html, company.html
│   ├── settings.html
│   ├── styles.css                 re-skinnable at fork time
│   └── app.js
│   # (v1 will add: pipelines.html, deals.html, tasks.html, forms.html)
│
├── agent_surface/
│   ├── mcp_server.py              MCP server exposing CRM tools
│   ├── cli.py                     command-line interface
│   ├── cron.py                    scheduled job declarations
│   ├── skills/                    markdown skill files for any agent
│   ├── prompts/                   reusable prompt templates
│   ├── connectors/                incoming data adapters
│   └── plugins/                   drop-in Python extensions
│
└── docs/
    ├── data-model.md
    ├── api.md
    ├── mcp.md
    ├── cli.md
    ├── skills.md
    ├── webhooks.md
    ├── cron.md
    ├── plugins.md
    └── deploy.md
```

---

## 9. Build phases

> All phases through v4.1 are shipped. The plan below was written at v0; the actual execution roughly matched it. Listed here for design rationale, not as a roadmap. For "what exists now", see the Status banner at top + `README.md`.


### v0 — milestone 1 (the proof)

**One sentence:** *A contact can be created through UI, REST, CLI, and MCP; the mutation lands in audit_log; the contact is visible in SQLite; and a `contact.created` webhook fires via the outbox pattern.*

Everything else in v0 is repetition. If milestone 1 holds, the architecture is real. If not, fix it before adding more entities.

Milestone 1 deliberately keeps search to basic indexed lookups (`email`, `name LIKE`). **FTS5 is deferred to milestone 2** so the first build stays light.

### v0 — milestone 2 (everything else)
Goal: a usable single-user CRM with all surfaces stubbed and parity across them.
Adds: companies, interactions, notes (with reveal mechanism), tags, FTS5 across the four searchable tables, `docs/interactions.md` (which freezes the `metadata_json` shape per `interaction.type`), all remaining skill files, deploy.py.

- Schema + `db.py`
- Auth (cookie + API key)
- Contacts: full CRUD
- Companies: full CRUD
- Interactions: log + timeline view
- Notes: with visibility scope
- Tags
- Audit log on every mutation
- REST API for all above
- MCP server with matching tools
- CLI mirroring the API
- 5 starter skill files (`create-contact.md`, `log-interaction.md`, `find-contact.md`, `add-note.md`, `tag-contact.md`)
- Admin UI (login, dashboard, contacts list+profile, companies list+profile, settings)
- One outbound webhook (`contact.created`)
- `setup.py` first-run wizard
- `deploy.py` helper + `start.bat`
- Docs for every surface

### v1 — pipelines, forms, leads
Goal: end-to-end lead capture and deal flow.

- Pipelines + stages + deals
- Tasks
- Forms with public submission endpoints
- Lead routing rules
- More webhooks
- Consent enforcement (block sends when consent missing)
- Bulk import / export (CSV)
- Duplicate detection

### v2 — relationship intelligence (no LLM required)
Goal: make the data useful with simple heuristics.

- Interaction-based scoring (intent, recency, engagement) — rules-based
- Segments (dynamic + static)
- Saved searches
- Reports (CSV-exportable)
- Plug-in API for custom scoring logic
- Cron-driven digests

### v3+ — polish for agent-driven workflows
Goal: every common workflow has a skill, prompt, or connector.

- More skills covering edge cases
- Connectors (Gmail, Outlook, common form builders, CSV-of-the-month)
- Webhook signing + retry logic
- Granular RBAC (additive — only added if a customer needs it)
- Plug-in marketplace pattern (optional)

---

## 10. Documentation principle

Each surface gets a dedicated doc targeted at the audience that uses it:

- `docs/api.md` → integrators (humans reading)
- `docs/mcp.md` → agents (agents reading their own docs)
- `docs/skills.md` → agents (markdown procedures)
- `docs/cli.md` → operators (shell scripting)
- `docs/deploy.md` → self-hosters
- `docs/data-model.md` → anyone modifying the schema

An agent reading `skills/*.md` and `docs/mcp.md` should be able to operate the CRM without ever reading the source code.

---

## 11. Deployment principle

`setup.py` + `start.bat` is the install.

1. `git clone <repo>`
2. `pip install -r requirements.txt`
3. `python setup.py` — creates `crm.db`, prompts for admin email/password, generates first API key
4. `start.bat` — runs FastAPI on a port
5. Open browser → login → working CRM

Five minutes. No Docker, no DB server, no external service.

For production self-hosters: `deploy.py` is provided as a reference for the Aurora pattern (Cloudflare Tunnel + FastAPI on a home box). Users can adapt or replace it.

---

## 12. What an agent can do, day one

After install, point Claude Code / OpenClaw / Codex / Hermes at the CRM via MCP (or REST + API key). The agent can:

- Create, update, find, soft-delete contacts and companies
- Log interactions (and read full timelines)
- Add notes (visibility-scoped; private requires explicit reveal)
- Tag things
- Record consent
- Subscribe to webhooks (delivered via outbox)
- Read skill markdown + `docs/mcp.md` to learn the levers

Cron-driven recurring jobs arrive in **v1**.

Everything else the agent does — insight summarization, drafting messages, segmentation logic, decision-making — is the agent's job, not the CRM's. The CRM ensures the agent can read and write everything it needs to.

That's the whole design.

---

## 13. License

MIT. Open source. Anyone can use, fork, or ship.

---

## 14. The one-sentence definition

**CRM is an open-source, self-hostable, single-company customer relations management application whose every action is operable through UI, REST API, MCP server, CLI, skills, webhooks, cron, and plug-ins — so any AI agent harness can serve as its nervous system without anything LLM-specific being baked into the CRM itself.**
