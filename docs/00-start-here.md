# 00 — Start here

Welcome. This file is the 10-minute orientation tour. After reading it
you'll know: what this software is, what it isn't, where to find each
piece, and what your first steps should be depending on why you're here.

## What this is, in one paragraph

A CRM (customer-relationship-management) app for one company per
install. It stores people, companies, interactions, notes, tags,
consent, pipelines, deals, tasks, forms, and segments. It runs as a
single FastAPI process against a single SQLite file. The same core
service-layer functions are exposed through four transports — a browser
UI, a REST API, a local CLI, and an MCP server for AI agents — and
every mutation is audited, emits webhooks, and dispatches plug-in hooks
inside one transaction.

## What this is NOT

- Not multi-tenant (no parent/entity model in core).
- Not an LLM application (no embedded model; agents act on it from
  outside).
- Not a SaaS (you run it yourself).
- Not Postgres-backed (deliberately SQLite; the design assumes a single
  process).
- Not React/Vite/build-step (vanilla HTML + CSS, server-rendered).

## Why these constraints

Single-machine SQLite is a deliberate ceiling, not a temporary
compromise. Every choice that prevented us from running on three
machines instead of one (Postgres, Redis, queue worker, replica)
multiplied operational complexity for no extra capability. The CRM is
explicitly designed for the size of company that fits on one machine
— which is most companies.

## The architectural rule

> **One rule:** every mutation goes through `backend/services/*.py`. The
> four transports are thin shells. They do auth + payload parsing, then
> call a service function. They contain no business logic.

That's it. If you keep that rule in your head, you can read the whole
codebase top-to-bottom and not get lost.

---

## I am a human — how do I run it?

```bash
git clone <this-repo>
cd CRM
python setup.py                  # creates crm.db, runs migrations, prompts for admin
uvicorn backend.main:app --reload
# open http://localhost:8000
```

Full install guide: [02-guides/install.md](02-guides/install.md).

Then walk through:

1. [02-guides/first-contact.md](02-guides/first-contact.md) — make
   your first contact, log an interaction.
2. [02-guides/your-first-pipeline.md](02-guides/your-first-pipeline.md)
   — make a deal pipeline.
3. (optional) `python seed_demo.py` to fill the database with realistic
   demo data so the dashboard, segments, and reports have content.

## I am an AI agent — how do I operate it?

Read `AGENTS.md` first. Then:

1. Pick the right transport — see the table in `AGENTS.md` ("How to
   operate the CRM").
2. Read the skill for the specific action you're about to take —
   `agent_surface/skills/<verb>-<noun>.md`. Skills have example payloads
   and the canonical transport.
3. For exhaustive lookups: `docs/03-reference/api.md`,
   `docs/03-reference/cli.md`, `docs/03-reference/mcp.md`.

## I am a developer extending the CRM — what do I read?

In order:

1. `SCHEMATICS.md` — visual model of how transports → service layer →
   storage → webhooks fit together.
2. `docs/01-concepts/service-layer.md` — the one rule explained in
   depth.
3. `docs/01-concepts/service-context.md` — how identity flows.
4. `docs/01-concepts/audit-and-webhooks.md` — why mutations have side-
   effects.
5. `docs/06-development/adding-an-entity.md` — the 10-step checklist
   when you want to add a new noun (e.g., "projects" or "subscriptions").

## Where each kind of code lives

```
backend/
  main.py            ← FastAPI app + UI routes (HTML responses)
  api.py             ← /api/* JSON router (thin)
  context.py         ← ServiceContext dataclass + helpers
  db.py              ← SQLite connection helper, DB_PATH
  audit.py           ← audit.log(conn, ctx, ...) — one function
  auth.py            ← password hashing, sessions, API keys
  webhooks.py        ← enqueue(conn, event, payload); delivery worker
  migrations.py      ← migration runner
  services/          ← THE business logic (every file is one entity)

agent_surface/
  cli.py             ← argparse-based local CLI (thin)
  mcp_server.py      ← FastMCP (or stdio JSON-RPC) server (thin)
  cron.py            ← in-process scheduler for nightly tasks
  plugins/           ← user-installed plug-ins (drop a .py file here)
  skills/            ← agent-facing skill markdown files

ui/
  *.html             ← server-rendered templates (no framework)
  *.css              ← styles
  static/            ← anything copied verbatim

migrations/
  0001_initial.sql   ← never edit — append new files instead
  0002_v1.sql
  ...

docs/                ← THIS wiki
schema.sql           ← v0 baseline (kept for reference; not authoritative)
Blueprint.md         ← product spec
prompt.md            ← "build this CRM from scratch" prompt for an agent
AGENTS.md            ← AI agent operating contract
CLAUDE.md            ← Claude-Code-specific project conventions
README.md            ← human entry point
SCHEMATICS.md        ← ASCII diagrams
setup.py             ← first-run installer (admin + migrations)
server.py            ← uvicorn launcher
seed_demo.py         ← realistic demo data
deploy.py            ← single-VM deploy helper
```

## The shortest possible feature tour

| feature | where it lives | what it does |
|---------|----------------|--------------|
| Contacts + companies | `services/contacts.py`, `companies.py` | Soft-deleted people + orgs |
| Timeline | `services/interactions.py` | Catch-all event firehose |
| Notes | `services/notes.py` | Visibility-scoped human text |
| Consent | `services/consent.py` | Per-channel grant/withdraw |
| Tags | `services/tags.py` | Reusable scoped labels |
| Pipelines + deals | `services/pipelines.py`, `deals.py` | Stages, value, won/lost |
| Tasks | `services/tasks.py` | Assigned, due dates, priorities |
| Forms | `services/forms.py` | Public `/f/{slug}` submissions |
| Search | `services/search.py` | FTS5 across 4 entities |
| Scoring | `services/scoring.py` | Rule-based, evidence-tracked |
| Segments | `services/segments.py` | Static + dynamic groups |
| Reports | `services/reports.py` | Pure functions in a CATALOG |
| Portals | `services/portals.py` | `/portal/{token}` for outsiders |
| Inbound | `services/inbound.py` | `POST /in/{slug}` HMAC ingest |
| Plug-ins | `services/plugins.py` + `agent_surface/plugins/*.py` | Hook reactions |

## What to do next

- If you only have 10 minutes: stop here. You know enough.
- If you have an hour: read all of `01-concepts/`.
- If you're about to write code: read the concept doc for the area
  you're touching, then `06-development/adding-an-entity.md` if you're
  adding a noun, or `06-development/writing-a-plugin.md` if you're
  reacting to events.
- If you're operating it in production: `05-operations/backup-restore.md`,
  `02-guides/deploying.md`, and `07-troubleshooting/error-codes.md` are
  your friends.

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](../README.md) — human entry point
- [AGENTS.md](../AGENTS.md) — AI agent operating contract
- [CLAUDE.md](../CLAUDE.md) — Claude Code project conventions
- [SCHEMATICS.md](../SCHEMATICS.md) — ASCII architecture diagrams
- [Blueprint.md](../Blueprint.md) — product spec
- [prompt.md](../prompt.md) — build-from-scratch prompt

**Wiki root** (`docs/`)
- [README.md](README.md) — wiki index
- [00-start-here.md](00-start-here.md) **← you are here**

**01 — Concepts** (`docs/01-concepts/`) — why each piece exists
- [service-layer.md](01-concepts/service-layer.md)
- [service-context.md](01-concepts/service-context.md)
- [audit-and-webhooks.md](01-concepts/audit-and-webhooks.md)
- [plugins.md](01-concepts/plugins.md)
- [scoring.md](01-concepts/scoring.md)
- [segments.md](01-concepts/segments.md)
- [portals.md](01-concepts/portals.md)
- [inbound.md](01-concepts/inbound.md)
- [search.md](01-concepts/search.md)

**02 — Guides** (`docs/02-guides/`) — step-by-step how-tos
- [install.md](02-guides/install.md)
- [first-contact.md](02-guides/first-contact.md)
- [your-first-pipeline.md](02-guides/your-first-pipeline.md)
- [import-export.md](02-guides/import-export.md)
- [deploying.md](02-guides/deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](03-reference/data-model.md)
- [api.md](03-reference/api.md)
- [cli.md](03-reference/cli.md)
- [mcp.md](03-reference/mcp.md)
- [plugins.md](03-reference/plugins.md)
- [webhooks.md](03-reference/webhooks.md)
- [errors.md](03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](04-recipes/lead-intake.md)
- [dormant-revival.md](04-recipes/dormant-revival.md)
- [agent-workflows.md](04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](05-operations/backup-restore.md)
- [migrations.md](05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](06-development/adding-an-entity.md)
- [writing-a-plugin.md](06-development/writing-a-plugin.md)
- [writing-a-skill.md](06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](07-troubleshooting/error-codes.md)
