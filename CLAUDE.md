# CLAUDE.md — orientation for AI agents working ON this codebase

If you are coding *on* the CRM repo (not driving it via MCP from outside), read
this first. It's the shortest path to making correct changes.

## The product in one line

A self-hosted, single-company CRM whose every action is callable through UI,
REST, MCP, CLI, skills, webhooks, and cron. No LLM inside core; AI is external
(or a plug-in). Drop-in extensible via the plug-in framework.

## The architectural rule that matters more than any other

**Surfaces are transports, not features.** Every action lives in
`backend/services/*.py`. REST endpoints in `backend/api.py`, CLI commands in
`agent_surface/cli.py`, MCP tools in `agent_surface/mcp_server.py`, and UI
route handlers in `backend/main.py` all dispatch through services. If you find
yourself writing the same validation in two places, you've broken the rule.

Every service function takes a `ServiceContext` as its first argument; that
object carries the acting user/api-key, the surface name (ui/rest/cli/mcp/
cron/plugin/webhook/system), and a request_id for correlation across audit +
webhook tables.

## Stack & install

- **Python 3.10+ / FastAPI / SQLite / vanilla HTML+JS templates.** No Docker,
  no Postgres, no React, no build step.
- `pip install -r requirements.txt` then `python setup.py` (interactive
  first-run wizard) then `python server.py`.
- `python seed_demo.py` populates the DB with realistic demo data.
- The migration runner (`backend/migrations.py`) discovers files in
  `migrations/*.sql` and applies in order. Each migration ends with an
  insert into `schema_versions` guarded by `WHERE NOT EXISTS` so re-runs
  are safe.

## Layout

```
CRM/
├── README.md, CLAUDE.md, LICENSE      — orientation
├── server.py, start.bat               — launch
├── setup.py                           — first-run wizard
├── seed_demo.py                       — populates DB with demo data
├── deploy.py                          — reference deploy script
├── schema.sql                         — v0 schema (first-run base)
├── migrations/0002…0007.sql           — incremental migrations
├── crm.db                             — SQLite (gitignored)
├── backend/
│   ├── main.py                        — FastAPI app + UI routes
│   ├── db.py, context.py, auth.py     — foundation
│   ├── audit.py, webhooks.py          — cross-cutting (always inside tx)
│   ├── migrations.py                  — runner
│   ├── api.py                         — REST endpoints (thin, → services)
│   └── services/                      — THE SOURCE OF TRUTH
│       ├── contacts.py, companies.py, interactions.py, notes.py, tags.py,
│       │   consent.py, auth_keys.py            (v0)
│       ├── pipelines.py, deals.py, tasks.py, forms.py, search.py,
│       │   duplicates.py, imports.py           (v1)
│       ├── scoring.py, segments.py, reports.py (v2)
│       ├── portals.py, inbound.py              (v3)
│       └── plugins.py, saved_views.py          (v4)
├── ui/
│   ├── styles.css, *.html             — vanilla, no build
├── agent_surface/
│   ├── cli.py, mcp_server.py, cron.py — agent transports
│   ├── skills/                        — markdown skill files
│   ├── plugins/                       — drop-in Python plug-ins
│   ├── connectors/, prompts/          — extension scaffolds
└── docs/
    ├── data-model.md, api.md, cli.md, mcp.md, plugins.md, webhooks.md
```

## Build + verify

```bash
python -m pip install -r requirements.txt
python setup.py --non-interactive --admin-email a@b.c --admin-password test1234
python seed_demo.py
python -m tests.run_all           # full acceptance suite (8 files, ~20s)
python server.py                  # browse http://127.0.0.1:8765/
```

### Test suite layout

Eight acceptance files under `tests/`, run individually OR all together via
`python -m tests.run_all`. Each file is self-contained (temp DB, own setup).
The runner runs them as subprocesses in priority order — if a baseline file
fails (migrations, M1), the failure surfaces first so you debug at the
right layer.

| File | Coverage |
|---|---|
| `test_migrations.py` | runner contract + post-migration table presence + idempotency |
| `test_milestone1.py` | M1 (contacts × 4 surfaces) + M2 (companies/interactions/notes/tags/consent) |
| `test_v1plus.py` | v1+ services (pipelines/deals/tasks/forms/segments/saved_views/scoring/plugins) + plug-in end-to-end proof |
| `test_services_coverage.py` | auth_keys / users / roles / search (FTS5) / duplicates / imports / inbound (HMAC) / portals / reports |
| `test_error_paths.py` | every documented error code raises correctly across 13 services |
| `test_transports.py` | REST + CLI + MCP for deals & tasks (transport wiring pinned) |
| `test_webhook_delivery.py` | full webhook pipeline: enqueue → HMAC sign → 200=delivered, 5xx=retrying with backoff, exhaust=failed, event-type filter |
| `test_contact_lifecycle.py` | soft-delete → email reuse → restore (proves the partial unique email index works as intended) |

If you change anything in the core service layer, run `python -m tests.run_all`
before committing — total wall-clock is ~20 seconds and it catches schema
drift, error-code regressions, transport-wiring breaks, and webhook contract
violations in one shot.

## Database — what's there

31 tables. See `docs/data-model.md` for the full reference. The headline
relationships:

- **`contacts`** is the central entity. `companies` is the org they work for.
  Both soft-delete (`deleted_at`). Partial unique index on
  `contacts.email WHERE deleted_at IS NULL` so soft-deleted emails free up.
- **`interactions`** is the firehose. Every meaningful event (email, call,
  meeting, form_submission, page_view, note_system, system) lands here.
  Timeline = `SELECT … FROM interactions WHERE contact_id=? ORDER BY occurred_at DESC`.
- **`notes`** are separate from `interactions` because they have a
  `visibility` scope (`public`/`team`/`private`). Private notes are NEVER in
  FTS5 index, NEVER in webhook payloads.
- **`audit_log`** records every mutation: who (user_id OR api_key_id),
  surface, action, object, before, after, request_id.
- **`webhooks` + `webhook_events`** = outbox pattern. Service mutations
  insert webhook_events INSIDE the same transaction; dispatcher delivers
  after commit with HMAC-SHA256 signing + retry. **A webhook failure NEVER
  rolls back the original mutation.**
- **`search_index`** is the FTS5 virtual table. 9 triggers on contacts/
  companies/interactions/notes keep it in sync. Private notes excluded.
- **`plugins` + `plugin_hooks`** = the v4 extensibility registry. Hooks fire
  inside service-layer transactions; plug-in writes commit atomically with
  the host mutation.

## Cross-cutting rules every service follows

1. **Validation** at the top, raise `ServiceError(code, message, details?)`
   on bad input. Standard error codes live in `_STATUS` in `api.py`.
2. **Single transaction** per service call. Acquire one `with db() as conn`,
   do everything inside it.
3. **Audit log** every mutation: `audit.log(conn, ctx, action="...",
   object_type="...", object_id=..., before=..., after=...)`.
4. **Webhook outbox** every mutation worth notifying about:
   `webhooks.enqueue(conn, "<event_name>", payload, redact_keys=[...])`.
5. **Plug-in hooks** after audit + webhooks:
   `_plugins.dispatch("on_<event>", ctx, ..., conn)`. Plug-in exceptions
   are caught upstream and logged to `plugins.last_error`.
6. **No business logic in transports.** REST/CLI/MCP/UI handlers should be
   ~6 lines: build ctx → call service → render result. Period.

## Common error codes

`VALIDATION_ERROR`, `FORBIDDEN`, `CONTACT_NOT_FOUND`, `CONTACT_EMAIL_EXISTS`,
`COMPANY_NOT_FOUND`, `COMPANY_SLUG_EXISTS`, `NOTE_NOT_FOUND`, `TAG_EXISTS`,
`API_KEY_NOT_FOUND`, `PIPELINE_NOT_FOUND`, `DEAL_NOT_FOUND`,
`TASK_NOT_FOUND`, `USER_NOT_FOUND`, `FORM_NOT_FOUND`, `FORM_SLUG_EXISTS`,
`SEGMENT_NOT_FOUND`, `SEGMENT_SLUG_EXISTS`, `REPORT_NOT_FOUND`,
`PORTAL_TOKEN_NOT_FOUND`, `INBOUND_ENDPOINT_NOT_FOUND`,
`INBOUND_SLUG_EXISTS`, `PLUGIN_NOT_FOUND`, `SAVED_VIEW_NOT_FOUND`.

## Adding a new entity (e.g., "products") — checklist

1. `migrations/0008_products.sql` — `CREATE TABLE` + indexes + the
   `INSERT INTO schema_versions` guard at the bottom.
2. `backend/services/products.py` — `create/get/list_/update/delete`
   functions, each `(ctx, payload_or_id) → dict`. Each writes audit,
   enqueues webhooks, dispatches plug-in hooks.
3. `backend/api.py` — register REST endpoints under `/api/products`. Add
   any new error codes to `_STATUS`.
4. `agent_surface/cli.py` — add a `product` subcommand group.
5. `agent_surface/mcp_server.py` — add MCP tools in BOTH the FastMCP
   block AND the stdio fallback `_do()` dispatcher.
6. `ui/products.html`, `ui/product.html` — vanilla templates with
   `{{placeholder}}` substitution.
7. `backend/main.py` — UI routes + add "Products" to the topnav `items` list.
8. `agent_surface/skills/<verb>-product.md` — skill markdown for agents.
9. `docs/data-model.md`, `docs/api.md`, `docs/mcp.md`, `docs/cli.md`,
   `docs/skills.md` — update.
10. Extend the test suite — add the new entity to `tests/test_v1plus.py` (or
    a new file picked up by `tests/run_all.py`). Also exercise its
    documented error codes in `tests/test_error_paths.py`. If it gains REST
    handlers, add a block to `tests/test_transports.py`.

Once you have it working through one surface, the others should be
~30 minutes of mirror code each.

## Plug-ins

Drop a `.py` file in `agent_surface/plugins/`. Required: `NAME` constant.
Optional: any hook function from `plugins.KNOWN_HOOKS` as a top-level
callable. See `agent_surface/plugins/README.md` and these working examples:

- `example_fit_score.py` — implements `compute_fit_score` (return-value hook)
  and `on_contact_created` (side-effect hook). Tag-pattern-based ICP scoring.
- `auto_tag_from_interactions.py` — listens for `on_interaction_logged`,
  extracts topic tags from title+body using either Claude (if
  `ANTHROPIC_API_KEY` is set) or a heuristic keyword extractor. Auto-tags
  the contact with `topic:<word>` labels.
- `deal_stage_automation.py` — listens for `on_deal_stage_changed`. On a
  won-stage transition, creates a "Kickoff + thank-you" task (priority=high)
  on the deal's contact. On a lost-stage transition, creates a "Postmortem"
  task (priority=normal). Logs a system interaction on the contact for every
  stage change. Demonstrates **the 5-arg hook signature** (ctx, deal,
  from_stage, to_stage, conn) and cross-service mutation inside a hook's
  transaction (writes to `tasks` + `interactions` atomically with the parent
  deal update). `test_v1plus.py` block [9/9] exercises this end-to-end.

**Important pattern**: hooks receive the same `conn` the triggering service is
using. Use it directly with `conn.execute(...)` rather than calling another
service function (which would open a second connection — risk of deadlocks
on SQLite, and the nested service's own webhook/audit logic fires mid-flight
on the parent transaction). All three example plug-ins pattern this correctly.

## In-app guided tour

`ui/tour.js` (~750 lines) implements a 28-step walk-through of every page.
A `?` icon in the topnav opens it; state persists across page navigation
via `localStorage` (`crm_tour_active`, `crm_tour_step`, `crm_tour_dismissed`,
`crm_tour_seen`). The tour spotlights an element via CSS box-shadow trick,
positions a tooltip with Skip/Restart/Next actions, and shows a "Continue ›"
pill when the user has navigated away from the step's intended URL.

Every step's body is structured the same way:
1. **Lead paragraph** — what this page/feature does in plain English.
2. **"For dummies" callout** — a `.dummies` box with a non-CRM analogy
   ("like a Trello board," "like browser extensions," "like Spotlight on Mac").
3. **"Use it for:" bullet list** — 2-3 concrete, real-world use cases.
4. **Optional `.tip` line** — power-user note in italic.

To add or edit content: edit the `TOUR` array in `ui/tour.js`. Each entry is
`{url, selector, title, body}`. The first-time offer on the dashboard prompts
new users; the `?` icon in the topnav is permanent.

## Sticky constraints — don't break these

- **No LLM/AI provider code in core.** Plug-ins can call OpenAI/Anthropic;
  core stays provider-agnostic.
- **No multi-tenant / parent-entity / subsidiary logic in core.** Forks
  can add it. Public CRM stays single-company.
- **No Docker, no build steps, no frontend framework.**
- **No business logic in transports.** Service layer is the single source.
- **Private notes never go in webhook payloads, never go in the FTS5
  index, only readable via explicit admin reveal (audited).**

## Where the spec lives

`Blueprint.md` and `prompt.md` describe the intended architecture. Read
them when scope feels unclear. They have been updated through v4.1 to
match the shipped code.
