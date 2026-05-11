# MCP reference

The CRM ships an MCP server at `agent_surface/mcp_server.py`. It
exposes ~40 tools that map 1:1 onto service-layer functions. Same
validation, same audit, same webhooks, same plug-ins.

## Connecting

The server speaks two protocols:

1. **FastMCP** (if `pip install mcp` is present) — recommended.
2. **stdio JSON-RPC fallback** — works without the `mcp` package.

For Claude Code's `mcp.json`:

```json
{
  "mcpServers": {
    "crm": {
      "command": "python",
      "args": ["-m", "agent_surface.mcp_server"],
      "cwd": "/srv/crm/app"
    }
  }
}
```

The server runs locally — it dials the SQLite DB directly. The MCP
client should be on the same machine as `crm.db`. For remote MCP
access, run the CRM behind a proxy that translates remote MCP into
local invocations, or use REST instead.

## Identity resolution

Each MCP call resolves an acting user:

- If `CRM_AS_EMAIL` env var is set, use that user.
- Else if `CRM_AS_USER_ID` env var is set, use that user.
- Else fall back to the first admin in `users` (id ASC).

Set the env var when launching the MCP server, not per call. The
identity is per-process.

## Result shapes

Every tool returns either:

```json
{"ok": true, "...": ...}
```

or

```json
{"error": {"code": "...", "message": "...", "details": {...}}}
```

Error codes match the REST/CLI error model — see
[errors](errors.md).

---

## Tools

### Identity

- `me()` → calling identity (user, role, scope, surface).

### Contacts

- `create_contact(name, email=None, phone=None, title=None, ...)` → contact
- `get_contact(contact_id)` → contact
- `find_contacts(q="", limit=50, offset=0)` → list
- `update_contact(contact_id, ...)` → contact
- `delete_contact(contact_id)` → {id, deleted_at}

### Companies

- `create_company(name, slug="", website="", domain="", industry="", location="")` → company
- `get_company(company_id)` → company
- `find_companies(q="", limit=50, offset=0)` → list

### Interactions + notes

- `log_interaction(type, contact_id=None, company_id=None, title=None, body=None, channel=None, source=None, occurred_at=None)` → interaction
- `get_timeline(contact_id=None, company_id=None, limit=50, offset=0)` → list
- `add_note(body, contact_id=None, company_id=None, visibility="team")` → note
- `list_notes(contact_id)` → list (private filtered unless admin)

### Tags + consent

- `create_tag(name, color="", scope="any")` → tag
- `tag_contact(contact_id, tag_id)` → {ok}
- `record_consent(contact_id, channel, status, source=None, proof=None)` → consent

### Pipelines + deals

- `create_pipeline_from_template(name, template)` → pipeline (with stages)
- `list_pipelines(include_archived=False)` → list
- `get_pipeline(pipeline_id)` → pipeline
- `create_deal(title, pipeline_id, stage_id, contact_id=None, company_id=None, value_cents=None, currency=None, probability=None, status=None)` → deal
- `update_deal(deal_id, stage_id=None, status=None, value_cents=None, ...)` → deal
- `list_deals(pipeline_id=None, stage_id=None, status=None, assigned_to=None, contact_id=None, company_id=None, limit=100)` → list

### Tasks

- `create_task(title, contact_id=None, company_id=None, deal_id=None, assigned_to=None, due_date=None, priority="normal", description=None)` → task
- `list_tasks(status=None, assigned_to=None, contact_id=None, deal_id=None, overdue=False, limit=100)` → list
- `update_task(task_id, status=None, priority=None, ...)` → task
- `complete_task(task_id)` → task

### Search + scoring + segments

- `search(q, kind=None, limit=20)` → list
- `score_contact(contact_id)` → {scores}
- `get_scores(contact_id)` → {scores}
- `top_contacts_by_score(score_type="opportunity", limit=20)` → list
- `create_dynamic_segment(name, slug, rules)` → segment
- `list_segments()` → list
- `list_segment_members(segment_id, limit=200)` → list
- `evaluate_segment(segment_id)` → {added, removed, total}

### Reports

- `list_reports_catalog()` → list
- `run_report(name, params=None)` → result

### Portals

- `issue_portal_token(contact_id, scope="client", label=None, expires_in_days=30)` → token + url
- `list_portal_tokens(contact_id)` → list

### Inbound

- `create_inbound_endpoint(slug, name, routing=None, signature_scheme="simple")` → endpoint (with shared_secret returned once)
- `list_inbound_endpoints()` → list
- `list_inbound_events(endpoint_id, limit=100)` → list

### Plug-ins + saved views

- `list_plugins()` → list
- `reload_plugins()` → {ok, count}
- `create_saved_view(entity, name, config, shared=False)` → view
- `list_saved_views(entity)` → list

---

## Idempotency

Pass `idempotency_key="..."` to any write tool. Repeated calls with
the same key + acting principal return the stored result.

## Example agent script

```python
from mcp_client import MCPClient   # pseudocode

crm = MCPClient.connect("crm")

# Find or create a contact, log a meeting, tag, score
existing = crm.find_contacts(q="maya@blueriver.media", limit=1)["items"]
if existing:
    contact = existing[0]
else:
    contact = crm.create_contact(name="Maya Sato",
                                  email="maya@blueriver.media",
                                  title="Marketing Director")["contact"]

crm.log_interaction(type="meeting",
                    contact_id=contact["id"],
                    title="Coffee chat",
                    body="Wants to discuss Q3 sponsorship.")

vip = crm.create_tag(name="vip", color="#c47a4a", scope="contact").get("tag", {})
if vip.get("id"):
    crm.tag_contact(contact_id=contact["id"], tag_id=vip["id"])

crm.score_contact(contact_id=contact["id"])
```

## Where to look in code

- `agent_surface/mcp_server.py` — tool registrations
- `backend/services/*.py` — actual logic each tool dispatches to

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
- [cli.md](cli.md)
- [mcp.md](mcp.md) **← you are here**
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
