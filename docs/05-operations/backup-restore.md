# Ops · Backup + restore

> Hot SQLite backups while the server is running, off-machine sync,
> verified restores, and the discipline that turns "I have backups"
> into "I have working backups."

## Context

SQLite backup is deceptively simple. `cp crm.db crm.bak` looks right
but is wrong when WAL is in use — you might miss recent writes, or
copy mid-transaction state. The CRM uses SQLite's online backup API,
which produces a consistent snapshot regardless of in-flight writes.

The deeper problem is restore discipline. A backup you've never
restored is statistically equivalent to no backup. This page covers
both create and restore, with a recurring drill schedule.

## Understanding

The CRM exposes:

```bash
python -m agent_surface.cli backup create [--out PATH]
```

Internally:

```python
src = sqlite3.connect(DB_PATH, isolation_level=None)
dst = sqlite3.connect(out_path)
src.backup(dst)
dst.close()
src.close()
```

The Python `Connection.backup` method holds a brief WAL read lock,
copies pages, and produces a self-contained `.db` file. It's safe to
run while the FastAPI server is writing.

Optional flags:
- `--out PATH` — destination (default: `backups/<unix-ts>.db`)
- `--verify` — open the produced file and run an integrity check
  before declaring success

The output is a regular SQLite file. Restore = `cp` it back over
`crm.db` while the server is stopped.

## Reason

**Why SQLite backup API and not `cp`?**

`cp crm.db crm.bak` can race with the writer; you may end up with a
corrupt file. SQLite backup API is atomic from the caller's POV.

**Why not just `VACUUM INTO`?**

`VACUUM INTO 'backup.db'` works and produces a defragmented copy.
We use the backup API because:
- It's faster (no defrag pass).
- It's the canonical way and well-understood.
- VACUUM holds the writer lock longer.

**Why off-machine + verified?**

- A local backup on the same disk doesn't help if the disk dies.
- An unverified backup may be corrupt or schema-incompatible.
- Verification + integrity check confirms restorability.

## Result

A backup discipline that produces, daily, an off-machine, verified,
restorable copy of `crm.db`. Plus a quarterly restore drill on a
fresh VM to prove it.

## Recipe — daily backup script

```bash
#!/usr/bin/env bash
# /srv/crm/backup.sh
set -euo pipefail

REPO=/srv/crm/app
VENV="$REPO/.venv/bin"
DEST_DIR=/var/backups/crm
REMOTE="b2:my-crm-backups"   # rclone remote
LOG=/var/log/crm/backup.log

ts=$(date +%Y-%m-%dT%H%M%S)
dest_local="$DEST_DIR/crm-${ts}.db"

mkdir -p "$DEST_DIR"

# 1. Hot backup
"$VENV/python" -m agent_surface.cli backup create --out "$dest_local"

# 2. Integrity check
"$VENV/python" - <<EOF
import sqlite3, sys
con = sqlite3.connect("$dest_local")
row = con.execute("PRAGMA integrity_check").fetchone()
con.close()
if row[0] != "ok":
    print("INTEGRITY CHECK FAILED:", row[0]); sys.exit(2)
EOF

# 3. Off-machine sync
rclone copy "$dest_local" "$REMOTE/" --quiet

# 4. Prune local backups older than 14 days
find "$DEST_DIR" -name 'crm-*.db' -mtime +14 -delete

# 5. Ping deadman's-switch
curl -fsS -m 10 --retry 5 -o /dev/null \
  https://hc-ping.com/<your-uuid>

echo "Backup $ts complete: $dest_local → $REMOTE"
```

Cron entry:

```
0 3 * * *   /srv/crm/backup.sh >> /var/log/crm/backup.log 2>&1
```

## Restore — step by step

### Restoring on the same machine (lost data, server alive)

```bash
sudo systemctl stop crm
# Optional: keep the broken one for forensics
sudo -u crm cp /srv/crm/app/crm.db /srv/crm/app/crm.db.preincident
# Restore
sudo -u crm cp /var/backups/crm/crm-2026-05-08T030000.db /srv/crm/app/crm.db
# Remove stale WAL/SHM (the restored DB doesn't need them)
sudo -u crm rm -f /srv/crm/app/crm.db-wal /srv/crm/app/crm.db-shm
sudo systemctl start crm
```

### Restoring on a new machine

```bash
# Set up the new machine following docs/02-guides/install.md
# Skip the python setup.py step.
# Instead:
rclone copy b2:my-crm-backups/crm-2026-05-08T030000.db /srv/crm/app/crm.db
chown crm:crm /srv/crm/app/crm.db
sudo systemctl start crm
```

The migration runner will be idempotent on next start; no action
needed unless your new code is ahead of the backup's schema (in
which case migrations apply forward).

### Restoring a single table (surgical)

Sometimes you want "the contacts table from yesterday, not the whole
DB." Restore to a side-by-side file and pull from there:

```bash
sqlite3 /tmp/restored.db < /var/backups/crm/crm-2026-05-08T030000.db
sqlite3 /srv/crm/app/crm.db
> ATTACH '/tmp/restored.db' AS prev;
> -- e.g., undo a deletion
> INSERT INTO contacts SELECT * FROM prev.contacts WHERE id = ?;
> DETACH prev;
```

CAUTION: this bypasses the service layer. The change has no audit row,
no webhook, no plug-in dispatch. For real recovery, prefer a fresh
contact + manual interaction log explaining what happened.

## Operations

### Verify backup health daily

The script's `PRAGMA integrity_check` covers the file. To verify it
opens with the current code:

```bash
"$VENV/python" - <<EOF
import sqlite3
con = sqlite3.connect("$dest_local")
v = con.execute("SELECT MAX(version) FROM schema_versions").fetchone()
print("schema_version:", v[0])
EOF
```

If the version differs from current production, that's not corruption
— that's just a backup made before a migration. Still restorable.

### Quarterly restore drill

Stand up a throwaway VM. Restore the most recent backup. Run:

```bash
python -m agent_surface.cli contact list --limit 5
python -m agent_surface.cli report run --name pipeline_overview
```

If both return reasonable shapes, the backup is good. Tear the VM
down. Schedule the next drill.

### Retention

| layer | retention |
|-------|-----------|
| local on the server | 14 days (script prunes) |
| off-machine bucket  | 90 days hot, 1 year cold (rclone lifecycle) |
| annual snapshot     | indefinite (manually pulled into long-term storage) |

Adjust to your compliance posture.

### Pre-migration backup

The deploy script should take a fresh backup BEFORE running
migrations:

```bash
"$VENV/python" -m agent_surface.cli backup create \
  --out "/var/backups/crm/pre-migration-$(date +%s).db"
"$VENV/python" -m backend.migrations
```

If a migration fails partway, you can restore to "pre-migration"
state immediately.

## Fine-tuning

### Reducing backup size

The audit_log table is the dominant size. For installs where audit
history can be archived separately:

1. Have a separate `audit_log_archive` table.
2. Nightly, move rows older than 90 days into the archive.
3. Back up the main file daily; back up the archive weekly.

### Incremental backup via WAL shipping

For RPO (recovery-point objective) under one day:

- Configure WAL with `journal_size_limit` not too small.
- After each daily full backup, ship subsequent WAL files to remote
  storage (rclone every N minutes).
- Restore = full backup + replayed WAL frames.

Operationally heavier; only worth it if RPO < 1 day matters.

### Backups during heavy writes

The backup API plays nice with WAL — readers and writers continue. A
backup of a 1 GB DB takes ~5 seconds. Schedule for low-traffic hours
just to be polite, not because it's required.

### Encryption

Off-machine bucket should be either:
- Server-side encrypted (B2/S3 SSE on by default in 2026).
- Client-side encrypted via rclone's `crypt` remote.

Either way, holding both the bucket credentials AND the encryption
key gives full access — manage accordingly.

### Checksumming

Add to backup script:

```bash
sha256sum "$dest_local" > "$dest_local.sha256"
rclone copy "$dest_local.sha256" "$REMOTE/"
```

On restore, verify hash before opening. Catches in-transit corruption
that integrity check might miss.

## Maximizing potential

1. **Backup-driven dev environments.** Pull last night's backup,
   anonymize PII (or use as-is in a non-prod environment), spin up
   a clone for testing. Devs work against realistic data without
   sharing production credentials.

2. **Restore-tested CI.** A CI job that, on a tag push, downloads
   the most recent backup, runs migrations on it, ensures the server
   boots. Catches "we forgot to migrate" before it bites prod.

3. **Audit-only backups.** A weekly export of `audit_log` to
   long-term cold storage. Even if the rest of the data evolves,
   the audit history of past events is preserved forever (a
   compliance superpower).

4. **Backup verification dashboard.** Track `time_since_last_
   successful_backup` as a metric. Page on > 30h. Pin to status
   page.

5. **Multi-region replication via app-layer.** Two CRM installs;
   each subscribes to the other's webhooks for `contact.created`
   etc. Eventually consistent but very disaster-resistant. Out of
   scope for default config, achievable as a fork.

6. **Backups in Git LFS (small installs).** A 10 MB DB committed
   daily to a private git repo gives you full history + Git's
   integrity model + free hosting (GitHub LFS). Wasteful at scale,
   delightful at small scale.

## Anti-patterns

- **`cp crm.db backup.db` while server is running.** WAL frame
  inconsistency. Use the backup API.
- **Backups on the same disk and nowhere else.** Disk dies →
  everything dies.
- **No verification.** A corrupted backup discovered during a real
  recovery is the worst possible time to discover it.
- **Storing backups indefinitely without a rotation policy.**
  Storage costs creep; GDPR right-to-erasure becomes impossible.
- **Backing up `crm.db-wal` and `crm.db-shm`.** Don't. They're
  derivatives. Just `crm.db` (from the backup API).
- **Encrypting backups without storing the key safely.** A backup
  you can't decrypt is no backup.

## Where to look in code

- `agent_surface/cli.py:654` — `backup create` command
- `backend/db.py` — DB_PATH resolution + PRAGMA settings

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
- [backup-restore.md](backup-restore.md) **← you are here**
- [migrations.md](migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
