# Ops · Migrations

> Schema evolves; the migration runner makes that safe. Every change
> is a new append-only file; nothing ever edits an existing migration.

## Context

A CRM's schema changes constantly: new fields, new entities, renamed
columns, new indexes. Without a migration discipline, the schema on
your laptop, on staging, and on production drift apart. Reasoning
about behavior across environments becomes impossible.

The CRM uses a small, no-framework migration runner: a directory of
`migrations/NNNN_*.sql` files applied in numeric order, with applied
versions tracked in `schema_versions`. No Alembic, no Django south,
no Liquibase. Just SQL files and a Python loop.

## Understanding

### Files

`migrations/0001_initial.sql` defines the v0 baseline. Subsequent
files (`0002_v1.sql`, `0003_v1_fts.sql`, ...) define incremental
changes.

Each file is one or more SQL statements. They run in a single
transaction; if any statement fails, the whole file rolls back and
the migration is NOT recorded as applied. Fix the SQL; retry.

### The runner

`backend/migrations.py` provides:

```python
def run_all(conn) -> list[int]:
    """Apply every migration whose version > current. Returns applied versions."""
```

Invoked:
- Automatically by `setup.py` on first run.
- Automatically by `server.py` on every start.
- Manually by `python -m backend.migrations`.

### Tracking

```
schema_versions
  version       INTEGER PK
  applied_at    INTEGER  unix seconds
  description   TEXT     usually the filename stem
```

The runner picks `MAX(version)` as "current" and looks for files with
higher numbers.

## Reason

**Why append-only files?**

- An edited migration changes meaning between environments. If laptop
  applies v=5 with the old text and prod applies v=5 with the edited
  text, you have two different schemas labeled the same.
- Append-only is the simplest invariant that prevents this. New
  changes = new file.

**Why SQL and not Python migrations?**

- Pure SQL is universally readable. Any DBA can audit it.
- SQL migrations match the storage layer's vocabulary; Python ones
  add an unnecessary translation.
- Python migrations tend to evolve into "data backfills" — those
  belong in a separate, idempotent backfill script (often a one-time
  CLI command), not the schema runner.

**Why automatic on every start?**

- Reduces "did I remember to migrate?" anxiety.
- Failed migrations halt the start, so you can't deploy code that
  references new columns until the migration has succeeded.
- For staging/dev/CI this is the right default. For production, it's
  fine as long as your deploy pipeline pre-runs migrations explicitly
  (`python -m backend.migrations` before `systemctl restart`).

## Result

A schema you can reason about across environments. Every deploy
either:
- Succeeds (migrations applied, code starts).
- Fails atomically (migration rolled back, old code keeps running).

## Recipe — writing a new migration

### 1. Pick the next number

`ls migrations/ | sort | tail -1` → say `0007_richer_contacts.sql`.
Your new file is `0008_<descriptive_name>.sql`.

### 2. Write the SQL

```sql
-- migrations/0008_add_contact_locale.sql
-- Adds a locale column for i18n on contacts.

ALTER TABLE contacts
  ADD COLUMN locale TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_locale ON contacts(locale)
  WHERE locale IS NOT NULL;
```

Note:
- Use `CREATE ... IF NOT EXISTS` for new tables/indexes to keep the
  migration safe to re-run if its `schema_versions` insert ever
  failed.
- Avoid `DROP TABLE` and `RENAME` unless you've thought through every
  consumer of the table.
- `ALTER TABLE ... ADD COLUMN` is cheap in SQLite.

### 3. Service code

Update the relevant service to accept the new column in its
`_FIELDS` tuple:

```python
# backend/services/contacts.py
_FIELDS = (
    ..., "locale",
)
```

Update validation if needed (e.g., the locale must be a valid BCP-47
tag).

### 4. UI

If users should set the field, add an input to `ui/contact.html` and
handle it in the contact-update route in `backend/main.py`.

### 5. Run

```bash
python -m backend.migrations
# Applied: 0008_add_contact_locale.sql
```

Or just `uvicorn backend.main:app --reload` — startup runs migrations.

### 6. Verify

```bash
sqlite3 crm.db "PRAGMA table_info(contacts);" | grep locale
# 32|locale|TEXT|0||0
```

### 7. Commit

```bash
git add migrations/0008_add_contact_locale.sql \
        backend/services/contacts.py \
        ui/contact.html \
        backend/main.py
git commit -m "feat: add contact.locale field"
```

## Operations

### Deploying with migrations

Recommended deploy script (excerpt):

```bash
cd /srv/crm/app
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m backend.migrations
sudo systemctl restart crm
```

If the migration fails, the old server keeps running with the old
schema. Investigate, fix, redeploy.

### Inspecting migration history

```bash
sqlite3 crm.db "SELECT version, applied_at, description
                FROM schema_versions ORDER BY version;"
```

### Dry-running

The runner currently has no dry-run mode. To approximate: apply to a
copy of the DB first.

```bash
cp crm.db crm.db.dryrun
CRM_DB_PATH=$PWD/crm.db.dryrun .venv/bin/python -m backend.migrations
```

If it succeeds and the resulting schema is what you expect, apply
to real DB.

### Rolling back

There is no down-migration. To roll back:

1. Restore from the pre-migration backup
   (see [backup-restore](backup-restore.md)).
2. Or write a forward migration that undoes the change
   (e.g., `0008_drop_contact_locale.sql`). Forward-only undo is safer
   for shared environments.

### Out-of-order migrations

Don't. Always sequential. If two devs both write migration 0008
locally, the second to merge renames theirs to 0009. Git conflicts
catch the collision early.

### Squashing

For installs that don't deploy historical migrations from v0 (e.g.,
greenfield boxes that start from a recent snapshot), you can collapse
old migrations into a new "baseline":

1. Generate a fresh DB by applying all migrations through N.
2. Dump its schema: `sqlite3 crm.db .schema > new_initial.sql`.
3. Replace `0001_initial.sql` with this dump.
4. Delete or archive `0002_*` through `000N_*`.
5. New installs apply only the squashed initial. Existing installs
   keep applying incrementally (don't re-squash on their box).

In practice we DON'T squash often — it sacrifices history for a small
performance win on cold installs.

## Fine-tuning

### Long-running migrations

`ALTER TABLE ... ADD COLUMN` is instant in SQLite (metadata only).
`CREATE INDEX` on a large table scans every row — can take seconds
to minutes. Run during low-traffic hours.

If you need ONLINE schema changes (large CRMs):

- Use the canonical pattern: create new table → copy data → swap.
- Done in a single migration file, but expect downtime during the
  copy phase.
- For very large installs, do the copy in batches via a separate
  backfill script + a small final migration that swaps the tables.

### Data migrations

When schema change requires data transformation (e.g., parsing a
text field into structured columns):

1. Migration adds the new columns/tables.
2. A separate one-time backfill script reads existing rows, computes
   the new values, writes them in chunks.
3. Run the backfill AFTER the migration.
4. The backfill is idempotent (resumable; safe to re-run).

Avoid putting heavy data transforms inside the migration .sql file —
they're hard to debug and not chunked.

### Cross-environment differences

Most installs run identical schemas. If you have install-specific
features (custom plug-in tables, etc.), keep them in a `migrations-
local/` directory that the runner picks up after the main directory:

```python
def run_all(conn):
    apply_dir("migrations/", conn)
    if os.environ.get("CRM_LOCAL_MIGRATIONS"):
        apply_dir("migrations-local/", conn, version_offset=10_000)
```

Local migrations start at 10000 to avoid colliding with future main
ones.

### FTS5 schema changes

If you change the FTS5 virtual table, you must DROP and recreate it.
Triggers must be dropped and recreated too. The reindex (rebuilds
content) is the cost. Do this in a single migration; protect with a
"check FTS5 is supported" guard.

## Maximizing potential

1. **Migrations as documentation.** A new dev reading
   `migrations/*.sql` in order sees the entire schema evolution
   with its motivations (use comments at the top of each file).

2. **CI: apply migrations to last release's DB.** Pull yesterday's
   prod backup → restore in CI → run migrations → boot the server →
   smoke test. Catches "schema/code mismatch" before deploy.

3. **Migration changelog generator.** Parse migration filenames →
   produce a markdown changelog → publish with the release. Users
   know exactly what schema-level changes a version brings.

4. **Backward-compat by feature flag.** Add a column, but read it
   only when a feature flag is on. Keeps old code paths viable
   during the rollout window.

5. **Hermetic migration tests.** Spin up a fresh DB in tmpdir, apply
   migrations, run a tiny smoke test (insert one row per table).
   In CI, this guarantees migrations don't break each other.

6. **Migration linter.** A tiny script that grep-checks
   `migrations/*.sql` for `DROP TABLE`, `RENAME`, or `ALTER COLUMN`
   patterns and shouts in CI if you didn't acknowledge them in the
   commit message.

## Anti-patterns

- **Editing an already-applied migration.** Forbidden. Write a new
  one to fix.
- **Renaming a migration file after deploy.** schema_versions stores
  the description; renaming breaks the implicit contract.
- **Skipping `schema_versions` updates.** The runner does it for you
  — don't write SQL that touches schema_versions directly.
- **`DROP COLUMN` (not supported by old SQLite).** Use the
  copy-table pattern: new table → copy data → drop old.
- **Putting business logic in migrations.** No `IF user_count > 100
  THEN ...`. Migrations describe schema; business rules live in
  services.
- **Manually editing the database in production.** Always via a
  migration or via service calls. Direct SQL changes are unrepeatable
  and leave the schema inconsistent across environments.

## Where to look in code

- `backend/migrations.py` — the runner
- `migrations/0001_initial.sql` — baseline
- `migrations/000?_*.sql` — every incremental change
- `setup.py` — invokes runner on first install
- `server.py` — invokes runner on every boot

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
- [data-model.md](../03-reference/data-model.md)
- [api.md](../03-reference/api.md)
- [cli.md](../03-reference/cli.md)
- [mcp.md](../03-reference/mcp.md)
- [plugins.md](../03-reference/plugins.md)
- [webhooks.md](../03-reference/webhooks.md)
- [errors.md](../03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](../04-recipes/lead-intake.md)
- [dormant-revival.md](../04-recipes/dormant-revival.md)
- [agent-workflows.md](../04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](backup-restore.md)
- [migrations.md](migrations.md) **← you are here**

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
