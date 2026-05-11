# CRM wiki

This directory is the **knowledgebase** for this CRM. It is part of the
software, not an afterthought — an AI agent or human operator can only
use the CRM safely after reading the relevant pages here.

## Audience

The wiki serves two readers at once:

- **AI agents** picking up the repo cold (Claude Code, MCP-driven
  agents, custom harnesses). They need predictable structure, exact
  paths, contract-level guarantees.
- **Humans** running, deploying, or extending the CRM. They need
  narrative, motivation, and worked examples.

Each page is written so both can use it. If a page is more useful to
one audience than the other, it says so at the top.

## Top-level orientation

Read these first, in order, before anything else:

| file | who reads it | what it answers |
|------|--------------|-----------------|
| [`../README.md`](../README.md)         | humans  | What is this thing? Why does it exist? |
| [`../AGENTS.md`](../AGENTS.md)         | agents  | What is the contract for operating this thing? |
| [`../CLAUDE.md`](../CLAUDE.md)         | Claude Code agents | Project-specific rules for one-shot work |
| [`../SCHEMATICS.md`](../SCHEMATICS.md) | both    | How the pieces fit together (ASCII diagrams) |
| [`../Blueprint.md`](../Blueprint.md)   | both    | The product spec (versioned roadmap) |
| [`../prompt.md`](../prompt.md)         | agents  | The build-from-scratch prompt |
| [`00-start-here.md`](00-start-here.md) | both    | 10-minute orientation tour |

## Wiki sections

```
docs/
├─ 00-start-here.md             ← read this second
├─ 01-concepts/                 ← WHY each piece exists
├─ 02-guides/                   ← step-by-step HOW-TOs for common tasks
├─ 03-reference/                ← exhaustive lookup tables (data model, API, CLI, MCP, ...)
├─ 04-recipes/                  ← end-to-end workflows tying multiple features together
├─ 05-operations/               ← run-it-in-production tasks (backup, migrate, harden)
├─ 06-development/              ← extending the CRM (add an entity, write a plug-in, ship a skill)
└─ 07-troubleshooting/          ← what to do when something breaks
```

### 01 — Concepts (why does this exist?)

| page | summary |
|------|---------|
| [service-layer](01-concepts/service-layer.md)     | The single rule that ties REST/CLI/MCP/UI together |
| [service-context](01-concepts/service-context.md) | How identity + scope + surface travel with each call |
| [audit-and-webhooks](01-concepts/audit-and-webhooks.md) | Every mutation leaves a trail and an outbox event |
| [plugins](01-concepts/plugins.md)                 | Hook-driven extensibility without modifying core code |
| [scoring](01-concepts/scoring.md)                 | Rule-based contact scores with evidence trails |
| [segments](01-concepts/segments.md)               | Static + dynamic groups of contacts |
| [portals](01-concepts/portals.md)                 | Self-service URLs for external contacts |
| [inbound](01-concepts/inbound.md)                 | HMAC-signed `POST /in/{slug}` ingest |
| [search](01-concepts/search.md)                   | FTS5 across contacts, companies, interactions, notes |

### 02 — Guides (step-by-step)

| page | summary |
|------|---------|
| [install](02-guides/install.md)               | Get the CRM running on a fresh machine |
| [first-contact](02-guides/first-contact.md)   | Create your first contact + log an interaction |
| [your-first-pipeline](02-guides/your-first-pipeline.md) | Spin up a sales pipeline + a deal |
| [import-export](02-guides/import-export.md)   | Bulk move contacts/companies/deals in and out |
| [deploying](02-guides/deploying.md)           | Single-VM deploy, reverse proxy, TLS, backups |

### 03 — Reference (lookup)

| page | summary |
|------|---------|
| [data-model](03-reference/data-model.md)   | 31 tables — columns, indexes, relationships |
| [api](03-reference/api.md)                 | ~95 REST endpoints under `/api/` |
| [cli](03-reference/cli.md)                 | 18 command groups, 60+ actions |
| [mcp](03-reference/mcp.md)                 | ~40 MCP tools (FastMCP + JSON-RPC fallback) |
| [plugins](03-reference/plugins.md)         | Plug-in framework contract |
| [webhooks](03-reference/webhooks.md)       | Outbound event catalog + signing |
| [errors](03-reference/errors.md)           | Every error code, when it fires, how to handle |

### 04 — Recipes (end-to-end workflows)

| page | summary |
|------|---------|
| [lead-intake](04-recipes/lead-intake.md)         | From form submission to scored, segmented lead |
| [dormant-revival](04-recipes/dormant-revival.md) | Find high-value contacts gone cold; nudge them back |
| [agent-workflows](04-recipes/agent-workflows.md) | Common patterns when an external agent drives the CRM |

### 05 — Operations

| page | summary |
|------|---------|
| [backup-restore](05-operations/backup-restore.md) | Hot backups; restore-into-staging; verify |
| [migrations](05-operations/migrations.md)         | Migration runner, schema_versions, write-your-own |

### 06 — Development

| page | summary |
|------|---------|
| [adding-an-entity](06-development/adding-an-entity.md) | The 10-step checklist for a new noun |
| [writing-a-plugin](06-development/writing-a-plugin.md) | Hook reference + worked example |
| [writing-a-skill](06-development/writing-a-skill.md)   | Skill file format + how agents discover them |

### 07 — Troubleshooting

| page | summary |
|------|---------|
| [error-codes](07-troubleshooting/error-codes.md) | What each error means and what to do |

## Conventions used in this wiki

- **Code examples are runnable.** Copy them, change values, run them.
- **Paths are absolute from repo root** unless explicitly relative.
- **"Service-layer" is sacred** — it's repeated everywhere because it's
  the one architectural rule that's load-bearing. If you ever feel
  tempted to put business logic in a transport (REST handler, CLI
  command, MCP tool, UI route), STOP and read
  [01-concepts/service-layer.md](01-concepts/service-layer.md).
- **No emojis** in any wiki file unless the user explicitly asked.
- **No headers padded with horizontal rules** for visual weight — just
  the heading and the content.

## Keeping the wiki alive

A wiki that drifts from the code is worse than no wiki. When you touch:

| if you change... | also update... |
|------------------|----------------|
| `schema.sql` or any migration | `docs/03-reference/data-model.md` |
| any `backend/api.py` route    | `docs/03-reference/api.md` |
| any `agent_surface/cli.py` parser | `docs/03-reference/cli.md` |
| any `agent_surface/mcp_server.py` tool | `docs/03-reference/mcp.md` |
| any `webhooks.emit_*` call (new event)  | `docs/03-reference/webhooks.md` |
| any plug-in hook name | `docs/03-reference/plugins.md` + `docs/01-concepts/plugins.md` |
| any new `ServiceError` code  | `docs/03-reference/errors.md` + `docs/07-troubleshooting/error-codes.md` |

Treat doc drift as a bug.

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
- [README.md](README.md) **← you are here**
- [00-start-here.md](00-start-here.md) — 10-minute orientation

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
