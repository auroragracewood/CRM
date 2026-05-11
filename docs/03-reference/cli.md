# CLI reference

The CLI is a **local** operator surface — it dials the SQLite database
directly through the same service layer the REST API uses. There is no
network hop and no API key. Run it on the same machine as the repo +
`crm.db`. For remote automation, use REST or MCP instead.

## Invoke

```
python -m agent_surface.cli <group> <action> [flags...]
```

There is no `crm-cli` executable on PATH by default (no setuptools
console-script entry yet); always run via `python -m`.

Every command prints a single JSON document to stdout. Errors print an
error object and exit non-zero.

## Choosing the acting user

The CLI builds a `ServiceContext` on every command — every action is
performed AS some user. Pick which one at the **top level** (before the
subcommand):

```
python -m agent_surface.cli --as-email me@example.com contact create --name "Maya Sato"
python -m agent_surface.cli --as-user-id 1            contact list
```

If neither flag is given, the CLI falls back to "the lowest-numbered
admin in the users table." Setup creates that admin for you, so this
works out of the box on a fresh install.

`--as-user-id` and `--as-email` MUST appear **before** the group name,
not after — Python's argparse rejects them in the subcommand position.

## Command groups

| group | actions |
|-------|---------|
| `contact`    | `create` · `get` · `list` · `update` · `delete` |
| `company`    | `create` · `get` · `list` · `update` · `delete` |
| `interaction`| `log` · `list` |
| `note`       | `create` · `list` · `reveal` |
| `tag`        | `create` · `list` · `attach` |
| `consent`    | `record` · `list` |
| `pipeline`   | `create` · `from-template` · `list` · `get` · `add-stage` · `archive` |
| `deal`       | `create` · `get` · `list` · `update` · `delete` |
| `task`       | `create` · `get` · `list` · `update` · `complete` · `delete` |
| `search`     | (single positional action — runs FTS5 across all entities) |
| `duplicates` | `find` · `merge` |
| `import`     | (single positional action — CSV import) |
| `export`     | (single positional action — CSV export) |
| `score`      | `contact` · `get` · `recompute-all` · `top` |
| `segment`    | `create-static` · `create-dynamic` · `list` · `members` · `evaluate` · `delete` |
| `report`     | `list` · `run` |
| `portal`     | `issue` · `list` · `revoke` |
| `inbound`    | `create` · `list` · `events` · `delete` |
| `plugin`     | `list` · `reload` · `enable` · `disable` |
| `view`       | `create` · `list` · `delete` |
| `backup`     | `create` |

That's 18 groups, 60+ actions.

## Common flag patterns

All ID args are `--id` (or `--<entity>-id` when the action references
multiple entities, e.g. `interaction log --contact-id 5`).

- Listing: `--q`, `--limit`, `--offset` where supported.
- IDs are integers.
- Booleans use `action="store_true"`: e.g. `--include-archived`,
  `--is-won`, `--overdue`.
- Timestamps are unix seconds (integer).
- Money is `--value-cents` (integer cents) + `--currency` (lowercase
  ISO).

## Examples

```bash
# Create a contact
python -m agent_surface.cli contact create \
  --name "Maya Sato" --email "maya@blueriver.media" \
  --phone "+1 604-555-0188" --title "Marketing Director"

# List contacts whose name or email contains "maya"
python -m agent_surface.cli contact list --q maya --limit 10

# Log a meeting against contact 5
python -m agent_surface.cli interaction log \
  --type meeting --contact-id 5 \
  --title "Coffee chat" \
  --body "Talked through their fall editorial calendar."

# Add a tag and attach it
python -m agent_surface.cli tag create --name vip --color "#c47a4a" --scope contact
python -m agent_surface.cli tag attach --tag-id 1 --contact-id 5

# Spin up a Q4 sales pipeline from the built-in template
python -m agent_surface.cli pipeline from-template --name "Q4 Sales" --template sales

# Create a deal in the second stage of pipeline 1
python -m agent_surface.cli deal create \
  --title "Acme cafe rebrand" \
  --pipeline-id 1 --stage-id 2 \
  --contact-id 7 --company-id 3 \
  --value-cents 1800000 --currency cad --probability 60

# Recompute scores for one contact, then show top 10 opportunities
python -m agent_surface.cli score contact --id 5
python -m agent_surface.cli score top --score-type opportunity --limit 10

# Run a named report (sees the same catalog as /api/reports)
python -m agent_surface.cli report list
python -m agent_surface.cli report run --name pipeline_overview

# Issue a self-service portal token to contact 5, valid 60 days
python -m agent_surface.cli portal issue --contact-id 5 --scope client --expires-in-days 60

# Reload plug-ins after editing one
python -m agent_surface.cli plugin reload

# Take a hot backup of the SQLite DB (uses sqlite backup API; safe while running)
python -m agent_surface.cli backup create --out backups/manual.db
```

## CSV import

```bash
python -m agent_surface.cli import \
  --kind contacts \
  --path leads.csv \
  --on-duplicate skip   # or: update | merge
```

- `--kind` is currently `contacts` (companies is also supported; check
  `--help`).
- The CSV header row is required. Columns map by header name; unknown
  columns are ignored.
- `--on-duplicate update` overwrites the existing active contact with
  the same email.
- `--on-duplicate merge` invokes the duplicates service to merge timeline
  + tags + notes from the new row into the existing one.

## CSV export

```bash
python -m agent_surface.cli export --kind contacts --out contacts.csv
```

Same `kind` set as `/api/export/{kind}.csv`.

## Backups

`backup create` calls `sqlite3.Connection.backup()` against a fresh
connection — this is safe while the FastAPI server is running and holds
WAL locks. The output file is a complete, atomic SQLite copy.

```bash
python -m agent_surface.cli backup create --out backups/$(date +%F).db
```

If `--out` is omitted, defaults to `backups/<unix-ts>.db`.

## How the CLI surfaces in the audit log

Every CLI mutation writes an `audit_log` row with `surface = 'cli'`,
the resolved `user_id`, and the action name (e.g. `contact.created`).
That's how UI users can see what the CLI changed.

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
- [cli.md](cli.md) **← you are here**
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
That's how UI users can see what the CLI changed.
