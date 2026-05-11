# Data model reference

31 application tables + 13 FTS5 internal tables. SQLite. PRAGMAs set on every
connection: `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`.

## How to read this doc

Tables are grouped by version. Each entry lists key columns and the most
important indexes/constraints. For exhaustive column lists, see `schema.sql`
+ `migrations/*.sql`.

---

## v0 — identity + foundation

### `schema_versions`
Migration tracker. One row per applied migration.
- `version` PK · `applied_at` · `description`

### `users`
Humans who log into the UI. Cookie-session auth.
- `id`, `email` (UNIQUE), `password_hash` (Argon2id), `display_name`,
  `role` ∈ `admin`/`user`/`readonly`, `created_at`, `updated_at`, `last_login_at`

### `sessions`
Server-side cookie sessions; deletable on logout.
- `id` (random secret, PK), `user_id` → users, `expires_at` (sliding 7d)

### `api_keys`
Bearer tokens for agent access (REST/MCP/CLI when remote).
- `user_id` → users, `key_prefix` (display only), `key_hash` (sha256 of raw),
  `scope` ∈ `read`/`write`/`admin`, `revoked_at` nullable
- Raw key shown ONCE at creation, never persisted.

### `audit_log`
Every service-layer mutation lands here.
- `ts`, `user_id` OR `api_key_id`, `surface` (ui/rest/cli/mcp/cron/plugin/
  webhook/system), `action`, `object_type`, `object_id`, `before_json`,
  `after_json`, `request_id`

### `companies`
Organizations. Soft-delete via `deleted_at`.
- `name`, `slug` (UNIQUE), `website`, `domain` (lowercased on write),
  `industry`, `size`, `location`, `description`, `custom_fields_json`,
  `created_at`, `updated_at`, `deleted_at`

### `contacts`
People. Central entity. Soft-delete via `deleted_at`.
- Core: `full_name`, `first_name`, `last_name`, `email`, `phone`,
  `avatar_url`, `company_id` → companies, `title`, `location`, `timezone`,
  `preferred_channel`, `custom_fields_json`
- v4.1 add: `birthday`, `pronouns`, `language`, `linkedin_url`, `twitter_url`,
  `instagram_url`, `website_url`, `about`, `interests_json`, `source`,
  `referrer`, `best_contact_window`, `do_not_contact` (NOT NULL DEFAULT 0)
- **`uq_contacts_active_email`** partial unique index
  `(email WHERE email IS NOT NULL AND deleted_at IS NULL)` — soft-delete
  frees the email back up.

### `tags`, `contact_tags`, `company_tags`
Reusable labels with scope.
- `tags`: `name` (UNIQUE), `color`, `scope` ∈ `contact`/`company`/`any`
- join tables: `(target_id, tag_id, added_at, added_by)`

### `interactions`
The catch-all timeline event firehose. Append-only.
- `contact_id` and/or `company_id` (at least one required)
- `type` ∈ `email`/`call`/`meeting`/`form_submission`/`page_view`/
  `note_system`/`system`
- `channel`, `title`, `body`, `metadata_json`, `source`, `occurred_at`,
  `created_at`

### `notes`
Visibility-scoped human notes. Separate from interactions for permission
control.
- `visibility` ∈ `public`/`team`/`private`
- **Private notes** are NEVER in FTS5 index, NEVER in webhook payloads;
  admins reveal each one explicitly (`note.private_revealed` audit row).

### `consent`
Per-contact, per-channel records.
- UNIQUE `(contact_id, channel)`
- `status` ∈ `granted`/`withdrawn`/`unknown`, `source`, `proof`,
  `granted_at`, `withdrawn_at`

### `webhooks`, `webhook_events`
Outbound subscriptions + the outbox.
- `webhooks`: `url`, `events_json` (JSON array of event names; `*` matches
  all), `secret` (HMAC-SHA256 signing), `active`
- `webhook_events`: `webhook_id`, `event_type`, `payload_json`,
  `status` ∈ `pending`/`retrying`/`delivered`/`failed`, `attempts`,
  `response_status`, `response_body`, `next_attempt_at`, `delivery_id`
  (UNIQUE; `X-CRM-Delivery-ID` header on send)

---

## v1 — pipelines, deals, tasks, forms, search

### `pipelines`, `pipeline_stages`
- `pipelines`: `name`, `type`, `description`, `archived`
- `pipeline_stages`: `pipeline_id` → pipelines, `name`, `position`,
  `is_won` (bool), `is_lost` (bool)
- Three built-in templates seeded by `pipelines.create_from_template()`:
  `sales`, `client`, `sponsor`.

### `deals`
- `contact_id` → contacts (SET NULL on delete), `company_id` → companies,
  `pipeline_id` → pipelines (RESTRICT), `stage_id` → pipeline_stages
  (RESTRICT)
- `title`, `value_cents`, `currency` (iso, lowercase), `probability` (0-100),
  `expected_close`, `status` ∈ `open`/`won`/`lost`/`nurture`, `next_step`,
  `notes`, `assigned_to` → users, `closed_at` (stamped when status →
  won/lost)
- Moving stages where `is_won`/`is_lost` auto-flips `status` + sets
  `closed_at`.

### `tasks`
- `contact_id`/`company_id`/`deal_id` → respective parents (CASCADE)
- `assigned_to` → users (SET NULL)
- `title`, `description`, `due_date`, `priority` ∈ `low`/`normal`/`high`/
  `urgent`, `status` ∈ `open`/`in_progress`/`done`/`cancelled`,
  `created_by`, `completed_at` (stamped on done; cleared on re-open)

### `forms`, `form_submissions`
- `forms`: `slug` (UNIQUE; public URL `/f/{slug}`), `name`, `schema_json`
  (field defs), `routing_json` (parse + tag rules), `redirect_url`,
  `active`
- `form_submissions`: `form_id`, `payload_json`, `contact_id` (resolved or
  null), `ip`, `user_agent`, `source`

### `idempotency_keys`
For agent retries.
- PK `(key, principal, action)`, `result_json`, `expires_at`

### `search_index` (FTS5 virtual table)
Cross-entity search. Kept in sync by 9 triggers on contacts/companies/
interactions/notes.
- Columns: `kind` (`contact`/`company`/`interaction`/`note`), `ref` (source
  row id), `title`, `body`
- Tokenizer: `porter unicode61`
- **Private notes are excluded** by the trigger guard
  `WHERE visibility != 'private'`.

---

## v2 — scoring, segments, reports

### `contact_scores`
Rule-based scores with evidence trail.
- PK `(contact_id, score_type)` where `score_type` ∈
  `relationship_strength`/`intent`/`fit`/`risk`/`opportunity`
- `score` 0..100, `evidence_json` (list of `{reason, delta}` entries),
  `computed_at`

### `segments`, `segment_members`
- `segments`: `name`, `slug` (UNIQUE), `type` ∈ `static`/`dynamic`,
  `rules_json` (filter tree for dynamic), `last_evaluated_at`,
  `member_count`
- `segment_members`: PK `(segment_id, contact_id)` (CASCADE on either side)

### `(reports has no table)`
Reports are pure functions in `services/reports.py`, dispatched by name
via the `CATALOG` dict.

---

## v3 — portals + inbound

### `portal_tokens`
Self-service URLs for external contacts.
- `token` (UNIQUE), `contact_id` → contacts (CASCADE),
  `scope` ∈ `client`/`applicant`/`sponsor`/`member`, `expires_at`,
  `revoked_at`, `last_used_at`

### `inbound_endpoints`, `inbound_events`
External systems POST events into the CRM.
- `inbound_endpoints`: `slug` (UNIQUE; public URL `/in/{slug}`), `name`,
  `shared_secret` (HMAC-SHA256 verify), `routing_json`, `active`,
  `last_received_at`
- `inbound_events`: every POST is logged raw before parsing. `status` ∈
  `received`/`parsed`/`contact_linked`/`error`. Links to resolved
  `contact_id` + `interaction_id` on success.

---

## v4 — plug-ins, saved views, RBAC

### `plugins`, `plugin_hooks`
Plug-in registry. The actual Python modules live in
`agent_surface/plugins/*.py`.
- `plugins`: `name` (UNIQUE), `version`, `description`, `enabled`,
  `config_json`, `last_error` (caught exceptions logged here)
- `plugin_hooks`: `plugin_id` → plugins (CASCADE), `hook_name`, `priority`

### `saved_views`
Per-user (or shared) stored filter+sort+columns for list pages.
- `user_id` → users, `entity` ∈ `contact`/`company`/`deal`/`task`/
  `interaction`, `name`, `slug`, `config_json`, `shared` (visible to
  all users when 1)

### `roles`, `role_permissions`, `user_roles`
Granular RBAC scaffolding (additive — users.role still works for the
built-in 3 roles).
- `roles`: `name` (UNIQUE), `built_in` flag. Seeded with admin/user/readonly.
- `role_permissions`: PK `(role_id, permission)`. Permissions are simple
  action strings like `contact.read`, `deal.write`.
- `user_roles`: PK `(user_id, role_id)`. Multi-role assignment.

---

## Conventions

- All timestamps are UNIX seconds (INTEGER).
- All JSON columns are TEXT containing valid JSON.
- All foreign keys use ON DELETE CASCADE/SET NULL/RESTRICT as appropriate
  (see schema.sql for the actual constraints — RESTRICT is used where
  data loss would surprise you).
- Service-layer functions always take `ServiceContext` as first arg.
- Service-layer functions never trust caller-supplied user_id / api_key_id
  — they come from `ctx`.

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
- [data-model.md](data-model.md) **← you are here**
- [api.md](api.md)
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
