# CRM

> An open-source, self-hostable customer relations management
> application designed for agent-driven workflows.

**The CRM is the body. The agent is the brain.** The body has nerve
endings — UI, REST API, MCP server, CLI, skills, webhooks, cron,
plug-ins. Whatever agent harness you wire in (Claude Code, OpenClaw,
Codex, custom orchestrators) pulls the levers. The CRM ships no LLM,
no provider keys, no prompt logic.

This repo is the open-source CRM. A private fork at Great Creations
(GCRM) extends it with company-specific customization; the open-source
core stays generic and reusable.

## What's in it

### Core data model (v0 → v4)

- **Contacts, companies, interactions, notes, tags, consent** —
  the basic CRM nouns
- **Pipelines + deals** — opinionated sales/client/sponsor templates
- **Tasks** — assigned, prioritized, due-dated
- **Forms** — public submission endpoints at `/f/{slug}` with routing
- **Scoring** — five rule-based scores per contact with evidence
  trails (no ML black box)
- **Segments** — static or dynamic (JSON rule trees) groups of
  contacts
- **Portals** — `/portal/{token}` self-service URLs for external
  contacts
- **Inbound** — `POST /in/{slug}` HMAC-signed receivers for external
  events
- **Reports** — pure functions in a CATALOG, served as JSON or CSV
- **Plug-ins** — drop a `.py` file in `agent_surface/plugins/`,
  reload, react to events
- **Saved views + RBAC scaffolding** (v4)

### Infrastructure properties

- **Audit log on every mutation** — who did it, through which surface,
  before/after, all under one `request_id`
- **Webhook outbox** — same-transaction enqueue, post-commit delivery,
  HMAC-SHA256 signing, retry with backoff
- **Service-layer architecture** — REST/CLI/MCP/UI all dispatch
  through `backend/services/*.py`. No business logic in transports.
- **Single-company, single-install** — no multi-tenant logic in core
- **Stack:** FastAPI + SQLite + vanilla HTML/JS — no Docker, no
  Postgres, no React, no build step

## Quick install

```bash
git clone <repo-url> crm
cd crm
python -m venv .venv && source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python setup.py                  # prompts for first admin user
uvicorn backend.main:app --reload
# open http://localhost:8000
```

Optional: `python seed_demo.py` to populate the database with
realistic demo data.

Full guide: [docs/02-guides/install.md](docs/02-guides/install.md).

## Surfaces

After install, the same action runs through any of:

| Surface | Use |
| --- | --- |
| **UI** | `http://localhost:8000/` — browser, cookie sessions |
| **REST API** | `/api/*` with `Authorization: Bearer <key>` (~95 endpoints) |
| **CLI** | `python -m agent_surface.cli ...` (18 command groups) |
| **MCP** | `python -m agent_surface.mcp_server` (FastMCP or stdio) |
| **Webhooks** | Subscribe in Settings; HMAC-signed delivery with retries |
| **Inbound** | `POST /in/{slug}` from external systems |
| **Forms** | `POST /f/{slug}` from public websites |
| **Portals** | `/portal/{token}` for external contacts to self-serve |
| **Skills** | Markdown files in `agent_surface/skills/` agents read |

## Five-minute tour

1. `python seed_demo.py` to populate.
2. UI → Dashboard. Top-intent and dormant-high-value widgets are
   populated.
3. UI → Contacts → click a contact. See timeline, tags, scores
   (with "why?" expand), consent, portal access.
4. UI → Pipelines → drag a deal between stages. Watch the audit
   chain via `sqlite3 crm.db "SELECT action, surface FROM audit_log
   ORDER BY ts DESC LIMIT 10"`.
5. UI → Segments → "Fresh leads (7d)" is auto-populated from form
   submissions.
6. Try the same operations via REST/CLI/MCP — see
   [docs/02-guides/first-contact.md](docs/02-guides/first-contact.md)
   for parallel walkthroughs.

## The wiki

The CRM ships with a comprehensive wiki at [docs/](docs/). Start here:

- **[docs/00-start-here.md](docs/00-start-here.md)** — 10-minute
  orientation
- **[docs/README.md](docs/README.md)** — wiki index
- **[AGENTS.md](AGENTS.md)** — operating contract for AI agents
- **[CLAUDE.md](CLAUDE.md)** — Claude Code project conventions
- **[SCHEMATICS.md](SCHEMATICS.md)** — ASCII diagrams of how
  transports, services, storage, and side-effects fit together
- **[Blueprint.md](Blueprint.md)** — product spec
- **[prompt.md](prompt.md)** — the prompt to build this CRM from
  scratch

The wiki is organized as:

```
docs/
├─ 00-start-here.md           ← read this first
├─ 01-concepts/               ← WHY each piece exists (9 docs)
├─ 02-guides/                 ← step-by-step how-tos (5 docs)
├─ 03-reference/              ← exhaustive lookup (7 docs)
├─ 04-recipes/                ← end-to-end workflows (3 docs)
├─ 05-operations/             ← run-it-in-production (2 docs)
├─ 06-development/            ← extending the CRM (3 docs)
└─ 07-troubleshooting/        ← what to do when things break
```

Every doc carries the full wiki map at the bottom — reading any
single page makes you aware of every other.

## The one architectural rule

> Every mutation goes through `backend/services/*.py`. Transports
> (REST, CLI, MCP, UI) are thin shells that build a `ServiceContext`,
> parse a payload, and call a service. They contain no business
> logic.

See [docs/01-concepts/service-layer.md](docs/01-concepts/service-layer.md)
for why this matters and what it guarantees.

## Privacy & security baseline

- Argon2id-hashed passwords (bcrypt fallback)
- 7-day sliding-window cookie sessions (`HttpOnly`, `SameSite=Lax`,
  `Secure` in production)
- CSRF tokens on every UI mutation
- API keys: SHA-256 hashed at rest, raw key shown ONCE at creation,
  revocable, scoped (`read` / `write` / `admin`)
- SQLite WAL + `busy_timeout=5000` for concurrent surface safety
- Foreign keys ON, partial unique index on `contacts.email` for
  active rows
- Audit log records `user_id`, `api_key_id`, `surface`, and
  `request_id` on every mutation
- **Private notes never appear in webhook payloads, search index,
  or non-admin reads.** Admins see them only via explicit
  `notes.reveal_private`, which itself writes an audit row.
- HMAC-SHA256 signing on every outbound webhook + verification on
  every inbound endpoint

Full security model: [AGENTS.md](AGENTS.md) and
[docs/01-concepts/audit-and-webhooks.md](docs/01-concepts/audit-and-webhooks.md).

## Roadmap & version history

- **v0** (shipped): contacts, companies, interactions, notes, tags,
  consent, audit, webhooks, all four surfaces, basic admin UI
- **v1** (shipped): pipelines + deals, forms + lead routing, tasks,
  FTS5 search, bulk import/export, duplicate detection
- **v2** (shipped): scoring (5 score types), segments (static +
  dynamic), reports catalog
- **v3** (shipped): portals (self-service tokens), inbound endpoints
  (`POST /in/{slug}` with HMAC), connector framework
- **v4** (shipped): plug-in framework with hook dispatch, saved views,
  granular RBAC scaffolding
- **v4.1** (shipped): richer contact model (birthday, pronouns,
  language, socials, about, interests, source, referrer, consent
  preferences), demo seed data, expanded reports

## Project layout

```
backend/                       ─ FastAPI app + service layer
  main.py                      ─ HTML/UI routes
  api.py                       ─ /api/* JSON router
  services/                    ─ THE business logic
  context.py                   ─ ServiceContext dataclass
  db.py                        ─ SQLite connection + PRAGMAs
  audit.py                     ─ audit.log()
  webhooks.py                  ─ outbox + delivery worker
  auth.py                      ─ passwords, sessions, API keys
  migrations.py                ─ migration runner

agent_surface/                 ─ agent-facing transports + assets
  cli.py                       ─ argparse-based local CLI
  mcp_server.py                ─ FastMCP / stdio JSON-RPC server
  cron.py                      ─ in-process scheduler
  plugins/                     ─ user-installed plug-ins
  skills/                      ─ agent skill files (.md)

ui/                            ─ server-rendered HTML templates + CSS

migrations/                    ─ append-only schema evolution

docs/                          ─ the wiki

schema.sql                     ─ v0 reference (not authoritative)
setup.py                       ─ first-run installer
server.py                      ─ uvicorn launcher
seed_demo.py                   ─ realistic demo data
deploy.py                      ─ single-VM deploy helper

README.md                      ─ this file
AGENTS.md                      ─ AI agent operating contract
CLAUDE.md                      ─ Claude Code conventions
SCHEMATICS.md                  ─ ASCII architecture diagrams
Blueprint.md                   ─ product spec
prompt.md                      ─ "build this from scratch" prompt
```

## License

MIT. Anyone can use, fork, ship.

## Mandatory non-goals (by design, not omission)

- No multi-tenant / parent-entity logic in core
- No embedded LLM / provider-specific code in core
- No Docker, no build steps, no frontend framework
- No business logic in transports — REST/CLI/MCP/UI all dispatch
  through `backend/services/*.py`

If you need any of those, fork and add them in your private fork
(this is what GCRM does for Great Creations). The open-source core
stays small and generic.

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](README.md) **← you are here**
- [AGENTS.md](AGENTS.md) — AI agent operating contract
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
