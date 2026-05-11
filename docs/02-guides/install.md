# Guide · Install

> Get the CRM running on a fresh machine in under five minutes. This
> is the recommended path for first contact with the system.

## Context

Installation is the moment most projects lose new users. The CRM is
designed so install is one command + one prompt + one URL. No
container runtime, no Postgres tuning, no Redis, no npm build. Python
3.11+ and SQLite (already in Python's stdlib) are the only
dependencies.

The "install" phase covers four things:

1. Get the source code on the box.
2. Install Python dependencies into a virtual environment.
3. Run the setup script — creates `crm.db`, applies migrations,
   prompts for an admin user.
4. Launch the server.

The total disk footprint is ~30 MB for the venv and ~200 KB for the
empty database.

## Understanding

The install steps are scripted in `setup.py`. What it does:

```
setup.py:
  1. checks Python version (>=3.11)
  2. creates crm.db if missing
  3. applies every migration in migrations/*.sql in order
  4. prompts for first admin (email + display name + password)
  5. writes that admin into the users table with role='admin'
  6. prints a "you can now run uvicorn" message
```

After setup, two files exist alongside the source:
- `crm.db` — the SQLite database
- `crm.db-wal`, `crm.db-shm` — WAL journal files (appear on first run)

The server entry point is `backend.main:app`. Run it with uvicorn (the
recommended ASGI server) or any other ASGI runner.

## Reason

**Why one setup script and not a wizard?**

The setup is small enough to be a script. A wizard implies
configurability, but the only configurable thing here is the admin
user — and that's a single set of prompts. Anything more would be
ceremony.

**Why prompt for the admin during setup instead of an env var?**

- Most installers won't have an env var ready and would be confused
  by a "no admin configured" error on first run.
- The interactive prompt forces a strong password (length + complexity
  checks).
- The audit trail of "who installed the system" starts with this
  user.

For automated installs (CI, IaC), pass `--admin-email`,
`--admin-display-name`, `--admin-password-stdin` to `setup.py`. No
prompts.

**Why uvicorn and not gunicorn / hypercorn / something else?**

Any ASGI server works. Uvicorn is recommended for local dev because
of its `--reload` flag. For production, see
[deploying](deploying.md) — usually uvicorn behind a reverse proxy.

## Result

After this guide you have:

- A running FastAPI server on `http://localhost:8000`.
- A SQLite database file at `./crm.db`.
- One admin user, ready to log in.
- All migrations applied; the database is at the latest schema
  version.
- The UI accessible in your browser.

## Use case — typical first-time install

### 1. Clone

```bash
git clone <repo-url> CRM
cd CRM
```

### 2. Make a venv

```bash
python -m venv .venv
source .venv/bin/activate         # macOS/Linux
.venv\Scripts\activate            # Windows PowerShell
```

If `python` resolves to Python 2 on your system, use `python3`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies (kept short on purpose):
- `fastapi` — web framework
- `uvicorn[standard]` — ASGI server
- `argon2-cffi` — password hashing
- `python-multipart` — form handling
- `httpx` — outbound HTTP for webhook delivery + plug-in calls
- `mcp` (optional) — FastMCP support; falls back to stdio JSON-RPC if
  absent

### 4. Run setup

```bash
python setup.py
```

You'll be prompted:

```
First admin email: me@example.com
Display name: Andrew
Password: ********
Confirm password: ********
```

Output:

```
Created crm.db
Applied migration 0001_initial.sql
Applied migration 0002_v1.sql
Applied migration 0003_v1_fts.sql
Applied migration 0004_v2.sql
Applied migration 0005_v3.sql
Applied migration 0006_v4.sql
Applied migration 0007_richer_contacts.sql
Created admin user me@example.com (id=1)

Run:  uvicorn backend.main:app --reload
```

### 5. Launch

```bash
uvicorn backend.main:app --reload
```

Open `http://localhost:8000` and sign in with your admin email and
password.

### 6. (Optional) Populate demo data

```bash
python seed_demo.py
```

Creates 5 contacts, 3 companies, a Q4 sales pipeline with 3 deals,
4 tasks, 8 interactions, notes, consent, scores, a portal token, and
a contact-us form. Useful for poking at every page with real-looking
content.

## Operations

### Re-running setup

`setup.py` is idempotent for migrations (it tracks applied versions
in `schema_versions`) but NOT for the admin prompt. Running it again
will prompt for another admin user. To avoid double-prompting, pass
`--skip-admin` if an admin already exists:

```bash
python setup.py --skip-admin
```

The migration-runner part is also exposed as `crm.migrate`:

```bash
python -m backend.migrations
```

Useful in CI to apply migrations without touching the admin flow.

### Verifying install

```bash
python -m agent_surface.cli contact list
# {"items": [], "total": 0, "limit": 50, "offset": 0}
```

If that prints a JSON shape, every layer (SQLite, migrations, service
layer, CLI transport) is wired up correctly.

```bash
curl http://localhost:8000/api/me
# 401 — expected; you have no API key yet
```

A 401 is the right answer here. To get a 200, generate an API key
under Settings → API keys and use it.

### Choosing a port

`uvicorn --host 0.0.0.0 --port 8765` — change as needed. The CRM
makes no assumption about port; only `seed_demo.py` and example docs
say `:8000`.

### Where the DB lives

`crm.db` is at the repo root by default. Override with:

```bash
CRM_DB_PATH=/var/lib/crm/crm.db uvicorn backend.main:app
```

The directory must be writable by the server process — SQLite WAL
needs `-wal` and `-shm` sidecar files in the same directory.

### Pinning Python

The CRM is tested on 3.11 and 3.12. 3.10 mostly works; 3.13 is fine
but newer than what we test on. Avoid 3.9.

## Fine-tuning

### Skipping the venv

Possible but not recommended. System Python often has conflicting
versions of `fastapi` or `argon2-cffi` from other tools. The venv
gives a clean room.

### Reproducible installs with a lockfile

```bash
pip install pip-tools
pip-compile requirements.in -o requirements.lock
pip-sync requirements.lock
```

Commit the lockfile. Future installs are bit-for-bit reproducible.

### Pre-creating the admin from a script

```bash
python setup.py \
  --admin-email me@example.com \
  --admin-display-name "Andrew" \
  --admin-password-stdin <<< "$(cat /tmp/admin_pw)"
```

Useful in IaC. The password file should be sourced from a secret
manager.

### Migrating an existing database to a new install

The CRM is a single-machine product — `crm.db` is portable.

```bash
# stop the server on the old box
scp crm.db newbox:CRM/
# on newbox:
python -m backend.migrations    # applies any new migrations
uvicorn backend.main:app
```

For real disaster recovery, see
[05-operations/backup-restore](../05-operations/backup-restore.md).

### Disabling demo data

The demo seed lives in a separate script (`seed_demo.py`) — it's
never run automatically. Production installs simply don't run it.

## Maximizing potential

1. **Wrap setup in a one-liner.** Ship a tiny `install.sh`:

   ```bash
   curl -fsSL https://your.domain/install.sh | bash
   ```

   Does clone + venv + pip + setup with sensible defaults. New users
   are up in 60 seconds.

2. **Pre-baked Docker image** (if your users insist). Not the default
   path, but a `Dockerfile` of about 20 lines that runs setup at
   image-build time. The image carries the empty migrated `crm.db`;
   first launch only prompts for admin if absent.

3. **Cloud-init script** for VM provisioners (Hetzner, DO, EC2).
   Sets up Python, clones, runs setup with env-supplied admin
   credentials, configures systemd, enables the firewall. ~50 lines
   of bash.

4. **Multiple installs per machine.** Each install is one directory
   + one `crm.db`. Run two server processes on different ports
   pointing at different DB files. Useful for staging vs production
   on the same box.

5. **Reproducible upgrades.** Treat the CRM as a versioned product.
   When pulling new code: `git pull && python -m backend.migrations &&
   systemctl restart crm`. The migration runner is the upgrade
   protocol.

6. **Backups baked into setup.** Have `setup.py` print "remember to
   set up `agent_surface/cron.py:backup_daily`" — actually do it,
   pointing at a cloud bucket via rclone. New users get backups out
   of the box.

## Anti-patterns

- **Editing `schema.sql` after install.** It's a v0 reference, not
  authoritative. The migration files are. Add new schema in a new
  migration file.
- **Running multiple servers against the same `crm.db`.** SQLite WAL
  supports concurrent readers, but the server is designed for one
  writer process. Two writers can deadlock briefly.
- **Putting `crm.db` on a network filesystem** (NFS, SMB). SQLite +
  network FS is a known recipe for corruption. Use a local disk.
- **Shipping the `.venv` directory in git.** It's huge and machine-
  specific. `.gitignore` already excludes it.
- **Sharing the admin password.** Each operator should have their own
  user (admin or otherwise). The first admin creates the second
  through the UI.

## Where to look in code

- `setup.py` — first-run installer
- `backend/migrations.py` — migration runner
- `migrations/*.sql` — schema evolution
- `requirements.txt` — pinned dependencies
- `server.py` — convenience launcher (`python server.py` ≈ uvicorn)

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
- [install.md](install.md) **← you are here**
- [first-contact.md](first-contact.md)
- [your-first-pipeline.md](your-first-pipeline.md)
- [import-export.md](import-export.md)
- [deploying.md](deploying.md)

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
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
