---
verb: backup
noun: database
canonical_transport: cli
mcp_tool: (CLI only)
cli: backup create
rest: (none)
required_scope: admin (CLI runs as the resolved user)
related: []
---

# Back up the database

Creates a consistent, restorable copy of `crm.db` using SQLite's
online backup API. Safe to run while the server is processing
requests.

## Required fields
None.

## Optional fields
- `--out PATH` — destination path (default `backups/<unix_ts>.db`)
- `--verify` — open the produced file and run PRAGMA integrity_check

## Example

```bash
python -m agent_surface.cli backup create \
  --out /var/backups/crm/crm-$(date +%F).db --verify
```

Output:

```json
{
  "ok": true,
  "backup": "/var/backups/crm/crm-2026-05-11.db",
  "size_bytes": 2147483648
}
```

## How it works

Internally uses `Connection.backup`:

```python
src = sqlite3.connect(DB_PATH)
dst = sqlite3.connect(out_path)
src.backup(dst)
dst.close(); src.close()
```

This holds a brief WAL read lock, copies pages, and produces a
self-contained `.db` file. Writers continue during the copy.

## Verifying

The `--verify` flag runs `PRAGMA integrity_check` on the new file.
If it returns anything other than `"ok"`, the backup is corrupt.

## Off-machine sync

The CLI just creates the local file. You're responsible for shipping
it off-machine. Recommended:

```bash
# After backup create:
rclone copy /var/backups/crm/crm-$(date +%F).db b2:my-crm-backups/
```

See [docs/05-operations/backup-restore.md](../../docs/05-operations/backup-restore.md)
for the full daily backup script + cron setup.

## Restoring

```bash
sudo systemctl stop crm
cp /var/backups/crm/crm-2026-05-11.db /srv/crm/app/crm.db
rm -f /srv/crm/app/crm.db-wal /srv/crm/app/crm.db-shm
sudo systemctl start crm
```

## Audit

Backups are not audited — they don't mutate the database. If you want
to track who took backups, add a custom audit row via a small wrapper
script.

## Anti-patterns

- `cp crm.db backup.db` while the server is running. Wrong — WAL
  inconsistency. Use the backup API.
- Storing backups only on the same disk as the production DB.
- Not verifying. A corrupt backup discovered during a real restore
  is the worst possible time.
