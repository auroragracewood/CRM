# Guide · Deploying

> Take the CRM from "runs on my laptop" to "runs on a real server
> with TLS, daily backups, and a chance of surviving a reboot."

## Context

The CRM is a single-machine product. There is no multi-server
deployment story — by design. The recommended deployment is:

- One VM (or bare-metal machine).
- The CRM process managed by systemd (or equivalent).
- A reverse proxy (Caddy / nginx / Cloudflare Tunnel) for TLS.
- A daily SQLite backup to off-machine storage.
- A monitoring agent / heartbeat / uptime check.

This guide walks through that setup in concrete terms. The goal is
not "infinitely scalable" but "reliable enough that you don't lose
data and don't worry about the server."

## Understanding

A deployed CRM has these moving parts:

```
   internet
      │
      ▼
   reverse proxy        ─── TLS, hostname routing, rate-limit
   (caddy / nginx)
      │  (plain HTTP on localhost)
      ▼
   uvicorn worker        ─── ASGI server hosting backend.main:app
      │                       managed by systemd (autorestart)
      ▼
   crm.db (SQLite WAL)   ─── on local disk, regular file
      │
      └──► daily backup ──► off-machine storage (S3 / rclone / sftp)
```

No Postgres, no Redis, no Kafka, no Docker (optional but not
required), no Kubernetes.

## Reason

**Why one VM instead of "cloud native"?**

The CRM is sized for a single company; vertical scaling on one VM
covers 90% of installs. A single 2-vCPU 4-GB VM at any provider
($10-15/mo) handles ~1M contacts comfortably. Going horizontal would
require swapping SQLite for Postgres and adding a coordination layer
— a 10x complexity tax for no value at this scale.

**Why systemd and not Docker?**

- systemd is on every Linux box; no extra runtime.
- One service file replaces docker-compose, image building, registry
  pushes.
- Autorestart, logging, status are uniform with the rest of the
  system.

Docker is fine if you prefer it; the deployment is unchanged in spirit
(reverse proxy → uvicorn in container → mounted SQLite volume).

**Why reverse proxy at all?**

- TLS termination — uvicorn does TLS but a proxy is more battle-tested.
- Multi-app hosting — you'll add other services on the same box; the
  proxy routes by hostname.
- Buffering — slow clients don't tie up uvicorn workers.

**Why backup to off-machine storage?**

The only catastrophic failure mode for a single-machine deploy is
losing the machine. A backup on the same disk doesn't help. Off-
machine is mandatory; daily is the minimum cadence.

## Result

After following this guide:

- Your CRM is live at a public hostname over HTTPS.
- The server process auto-restarts on crash and on reboot.
- A daily backup runs at 03:00 local time.
- You have a runbook for rolling out updates (`git pull` + migrate +
  restart).

## Use case — Linux VM deployment with Caddy

Assumes:
- A fresh Ubuntu 24.04 VM.
- DNS A record pointing `crm.example.com` at the VM's IP.
- Repo cloned at `/srv/crm/`.

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git caddy rclone
```

### 2. Venv + install

```bash
sudo useradd -m -d /srv/crm -s /bin/bash crm
sudo -u crm bash -c '
  cd /srv/crm
  git clone <repo-url> app
  cd app
  python3.11 -m venv .venv
  .venv/bin/pip install -r requirements.txt
'
```

### 3. First-run setup

```bash
sudo -u crm /srv/crm/app/.venv/bin/python /srv/crm/app/setup.py
# answer prompts for admin user
```

### 4. systemd unit

`/etc/systemd/system/crm.service`:

```
[Unit]
Description=CRM
After=network.target

[Service]
Type=simple
User=crm
WorkingDirectory=/srv/crm/app
Environment="CRM_DB_PATH=/srv/crm/app/crm.db"
ExecStart=/srv/crm/app/.venv/bin/uvicorn backend.main:app \
          --host 127.0.0.1 --port 8001 --workers 1
Restart=on-failure
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Note: **one uvicorn worker**. SQLite WAL allows concurrent reads
across workers but only one writer at a time; multi-worker setups
contend on writes. One worker is fine for this scale.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crm
sudo systemctl status crm
```

### 5. Caddy

`/etc/caddy/Caddyfile`:

```
crm.example.com {
    reverse_proxy 127.0.0.1:8001
    encode gzip zstd
    log {
        output file /var/log/caddy/crm.access.log
    }
}
```

```bash
sudo systemctl reload caddy
```

Caddy auto-provisions a Let's Encrypt cert. Open
`https://crm.example.com` and verify.

### 6. Daily backup

`/srv/crm/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail
ts=$(date +%Y-%m-%dT%H%M%S)
dest="/var/backups/crm/crm-${ts}.db"
mkdir -p /var/backups/crm

# Use SQLite's online backup API via the CLI
/srv/crm/app/.venv/bin/python -m agent_surface.cli backup create \
  --out "$dest"

# Off-machine sync (configure rclone first: rclone config)
rclone copy "$dest" b2:my-crm-backups/ --quiet

# Prune local backups older than 14 days
find /var/backups/crm -mtime +14 -delete
```

Crontab for the `crm` user:

```
0 3 * * *  /srv/crm/backup.sh >> /var/log/crm/backup.log 2>&1
```

### 7. Health check

A small endpoint exists at `/healthz` (returns 200 with
`{"ok":true}`). Add an uptime monitor (Uptime Robot, Healthchecks.io)
that hits it every 5 minutes.

### 8. Rolling out updates

```bash
sudo -u crm bash -c '
  cd /srv/crm/app
  git pull
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python -m backend.migrations
'
sudo systemctl restart crm
sudo systemctl status crm
```

Migrations run before restart; if a migration fails, the old version
continues running uninterrupted.

## Operations

### Logs

```bash
journalctl -u crm -f          # follow CRM logs
journalctl -u caddy -f        # follow proxy logs
tail -f /var/log/crm/backup.log
```

### Rotating logs

systemd's journal handles its own rotation. Caddy's access log can
be rotated by `logrotate`:

```
/var/log/caddy/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        systemctl reload caddy
    endscript
}
```

### Restarting cleanly

```bash
sudo systemctl restart crm
```

The CRM finishes in-flight requests, drains the webhook delivery
worker for ~3 seconds, then exits. systemd starts a new process.
Brief downtime (~1-2 seconds) is expected.

### Zero-downtime updates

Not supported in the single-worker design. If you need it:
- Run two CRM workers behind a load balancer.
- Requires SQLite WAL is bulletproof for concurrent writers (mostly
  yes for our pattern — short transactions — but you'll see
  occasional `database is locked` errors under load).
- Or: migrate to Postgres. Out of scope for this CRM.

### Monitoring

- `/healthz` for uptime.
- Audit log row count as a "is anything happening?" gauge:
  `SELECT COUNT(*) FROM audit_log WHERE ts > strftime('%s','now','-5 minutes')`.
- Webhook delivery health: `SELECT status, COUNT(*) FROM
  webhook_events WHERE created_at > ... GROUP BY status`.
- Disk space: SQLite WAL can grow if a long-running reader keeps it
  pinned. Alert at 80% disk.

### Restoring from backup

```bash
sudo systemctl stop crm
cp /var/backups/crm/crm-2026-05-08T030000.db /srv/crm/app/crm.db
# (optionally) restore WAL — usually safe to delete:
rm -f /srv/crm/app/crm.db-wal /srv/crm/app/crm.db-shm
sudo systemctl start crm
```

See [05-operations/backup-restore](../05-operations/backup-restore.md)
for verification steps.

## Fine-tuning

### Choosing a VM size

- **<10k contacts, <5 active users:** 1 vCPU, 1 GB RAM. The CRM
  process uses ~80 MB; SQLite WAL adds buffer.
- **<100k contacts, <20 users:** 2 vCPU, 2 GB RAM.
- **<1M contacts:** 4 vCPU, 4-8 GB RAM. Most usage stays well under
  this.

Disk: 10x the database size for WAL/temp/backup-staging room.

### TLS without Let's Encrypt

If your environment can't reach Let's Encrypt (air-gapped corp):

- Caddy supports DNS-01 challenges via DNS provider plugins.
- Or use an internal CA + Caddy's `tls /path/to/cert /path/to/key`.

### Cloudflare Tunnel deployment

If you don't want a public IP, use Cloudflare Tunnel:

```bash
sudo apt install cloudflared
cloudflared tunnel login
cloudflared tunnel create crm
cloudflared tunnel route dns crm crm.example.com
sudo cp ~/.cloudflared/config.yml /etc/cloudflared/
sudo cloudflared service install
```

`config.yml`:

```
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: crm.example.com
    service: http://localhost:8001
  - service: http_status:404
```

No reverse proxy needed; Cloudflare handles TLS and edge.

### Multiple environments on one box

Two systemd units, two directories, two SQLite files, two ports:

```
/srv/crm-prod/    → unit crm-prod   → port 8001 → crm.example.com
/srv/crm-staging/ → unit crm-stage  → port 8002 → staging.crm.example.com
```

The data is fully isolated per directory.

### Hardening

- Restrict SSH to keys + non-root + fail2ban.
- UFW: `ufw allow 22,80,443/tcp; ufw enable`.
- Run CRM as a dedicated unprivileged user (the unit above does).
- Restrict outbound: the CRM only needs DNS + ACME + your backup
  destination + webhook subscribers. Whitelist accordingly.
- Set `Environment="ANTHROPIC_API_KEY=..."` in the unit if you use
  LLM plug-ins. Don't commit env files; use systemd's
  `EnvironmentFile=` + `chmod 600`.
- Audit log retention — see
  [01-concepts/audit-and-webhooks](../01-concepts/audit-and-webhooks.md).

### Performance tuning

- SQLite `PRAGMA cache_size = -262144` (256 MB cache). Tune in
  `backend/db.py` if working sets are large.
- `PRAGMA mmap_size = 2147483648` (2 GB mmap) for read-heavy
  workloads. Test before committing — mmap has tradeoffs.
- WAL checkpoint thresholds — usually defaults are fine.
- Caddy gzip + brotli for HTTP compression — Caddyfile snippet
  above.

## Maximizing potential

1. **Treat deploy as part of the codebase.** Commit `crm.service`,
   `backup.sh`, `Caddyfile` to `/deploy` in the repo. CI can drift-
   check by SSHing in and `diff`-ing live vs repo.

2. **Restore drills.** Once a month, on a separate VM, restore the
   most recent backup, verify it boots, run the CLI's `contact
   list`. Most "I have a backup" stories crumble at first restore
   attempt.

3. **Stream logs to a real log aggregator.** Promtail → Loki, or
   `journalbeat` → Elasticsearch. Suddenly you can query
   "which API key did the most writes last week?" without SSHing
   in.

4. **Heartbeat into a deadman's-switch service.** If your daily
   backup script doesn't ping Healthchecks.io after success, you
   get alerted within the day. Silent failure is the killer.

5. **Failover replica via WAL shipping.** SQLite WAL files can be
   shipped to a hot-standby box that replays them. Not trivial; if
   you genuinely need this, you've outgrown the design and should
   consider migration to Postgres.

6. **Snapshots at the VM level** in addition to logical backups.
   VM snapshots are point-in-time; logical backups are clean
   restorable artifacts. Both have value.

7. **Run an `agent_surface.cli` shell on the server** for emergency
   ops. SSH in, `sudo -u crm bash`, `cd /srv/crm/app`, `source
   .venv/bin/activate`, you have a full operator console with audit
   trail under `surface='cli'`.

## Anti-patterns

- **Running `uvicorn --workers 4`.** SQLite single-writer rule. One
  worker. If you outgrow it, move to Postgres.
- **Running the CRM in a Docker container with the DB on a mounted
  volume on a network filesystem.** Compounding problems. Use a
  local volume + container, or skip Docker.
- **No backups.** "I'll do it next week." There is no next week
  worth the risk. Set up backup-on-day-one or don't deploy.
- **Pointing DNS before TLS is working.** Caddy needs DNS to
  provision the cert. But: test with `caddy run` first, then make
  it permanent.
- **Restarting on every code change in production.** Don't deploy
  from dev. Use a build pipeline (even a trivial one: tag + pull +
  restart).
- **Sharing the API key with every operator.** Issue per-operator
  keys. Revocation becomes possible.

## Where to look in code

- `server.py` — convenience uvicorn launcher
- `backend/main.py` — FastAPI app
- `backend/db.py` — DB path resolution, PRAGMA settings
- `deploy.py` — install/deploy automation helper (varies per setup)
- `setup.py` — first-run installer

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
- [install.md](install.md)
- [first-contact.md](first-contact.md)
- [your-first-pipeline.md](your-first-pipeline.md)
- [import-export.md](import-export.md)
- [deploying.md](deploying.md) **← you are here**

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
