# CRM

> An open-source, self-hostable customer relations management application designed for agent-driven workflows.

**The CRM is the body. The agent is the brain.** The body has nerve endings — UI, REST API, MCP server, CLI, skills, webhooks, cron, plug-ins. Whatever agent harness you wire in (Claude Code, OpenClaw, Codex, Hermes, anything else) pulls the levers. The CRM ships no LLM, no provider keys, no prompt logic.

## What's in it

- **Contacts, companies, interactions, notes, tags, consent** — the basic CRM nouns
- **One firehose** — every meaningful event lands in `interactions`
- **Audit log on every mutation** — who did it, through which surface, before/after
- **Webhook outbox** — same-transaction enqueue, post-commit delivery, HMAC-SHA256 signing, retry log
- **Service-layer architecture** — REST/CLI/MCP/UI all dispatch through `backend/services/*.py`
- **Single-company, single-install** — no multi-tenant logic, no parent/subsidiary model
- **Stack:** FastAPI + SQLite + vanilla HTML/JS — no Docker, no Postgres, no build step

## Five-minute install

```bash
git clone <repo> crm && cd crm
python -m pip install -r requirements.txt
python setup.py                  # prompts for admin email + password, generates first API key
python server.py                 # or: start.bat on Windows
```

Open `http://127.0.0.1:8765/` and sign in. Save the API key from `setup.py` — it's the only time it's shown.

## Surfaces

After install, the same action runs through any of:

| Surface | Use |
| --- | --- |
| **UI** | `http://127.0.0.1:8765/` — browser, cookie-session auth |
| **REST API** | `POST /api/contacts` with `Authorization: Bearer crm_...` |
| **CLI** | `python -m agent_surface.cli contact create --name X --email Y` (local-only) |
| **MCP server** | `python -m agent_surface.mcp_server` (stdio; point Claude Code at it) |
| **Webhooks** | Subscribe in Settings; events fire via outbox with retry + HMAC signing |
| **Skills** | Markdown files in `agent_surface/skills/` an agent reads to learn the levers |

## Data model snapshot

15 v0 tables, all single-company:

```
schema_versions   users        sessions     api_keys
audit_log         companies    contacts     tags
contact_tags      company_tags interactions notes
consent           webhooks     webhook_events
```

Plus 6 reserved for v1 migrations: `pipelines`, `pipeline_stages`, `deals`, `tasks`, `forms`, `form_submissions`.

`interactions.type` enum: `email | call | meeting | form_submission | page_view | note_system | system`

## Privacy & security baseline

- Argon2id-hashed passwords (bcrypt fallback)
- 7-day sliding-window cookie sessions (`HttpOnly`, `SameSite=Lax`, `Secure` in production)
- CSRF tokens on every UI mutation
- API keys: stored as SHA-256 hashes, raw key shown ONCE at creation, revocable
- SQLite WAL + `busy_timeout=5000` for concurrent surface safety
- Foreign keys ON, partial unique index on `contacts.email` for active rows
- Audit log records the acting principal (`user_id` OR `api_key_id`) and surface
- **Private notes never appear in webhook payloads.** Admins see them only via explicit "Reveal" — every reveal writes `note.private_revealed` to audit_log.

## Roadmap

- **v0** (now): contacts, companies, interactions, notes, tags, consent, audit, webhooks, all surfaces, basic admin UI
- **v1**: pipelines + deals, forms + lead routing, tasks, consent enforcement, FTS5 search, bulk import/export, duplicate detection
- **v2**: scoring (rule-based, no LLM), segments, reports
- **v3+**: connectors (Gmail, Outlook, common forms), plug-in loading, granular RBAC, retry-signed webhooks

## License

MIT. Anyone can use, fork, ship.

## Mandatory non-goals

- No multi-tenant / parent-entity / subsidiary logic in core
- No LLM / provider-specific code in core
- No Docker, no build steps, no frontend framework
- No business logic in transports — REST/CLI/MCP/UI all dispatch through `backend/services/*.py`
