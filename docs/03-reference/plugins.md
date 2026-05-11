# Plug-in framework reference

> See [01-concepts/plugins](../01-concepts/plugins.md) for the why and
> the operating model. This page is the contract.

## Module shape

```python
# agent_surface/plugins/<name>.py

NAME = "your_plugin_name"        # required, unique
VERSION = "0.1.0"                # required
DESCRIPTION = "What I do"        # required

HOOK_PRIORITIES = {              # optional
    "on_contact_created": 100,
}

ENABLED_HOOKS = {                # optional; default = all matching
    "on_contact_created",
}

def on_contact_created(ctx, contact, conn):
    ...
```

`NAME` MUST be unique across all installed plug-ins. The loader
upserts on this column.

## Loader flow

1. Scan `agent_surface/plugins/*.py` (non-recursive).
2. Import each module.
3. UPSERT into `plugins` (NAME, VERSION, DESCRIPTION).
4. For each callable matching a name in `KNOWN_HOOKS`, register a
   row in `plugin_hooks`.
5. Cache module references for dispatch.

Reload trigger:
- Startup (server.py / setup.py)
- `POST /api/plugins/reload` (admin scope)
- `python -m agent_surface.cli plugin reload`
- `reload_plugins()` MCP tool

## KNOWN_HOOKS

```
on_contact_created       on_contact_updated      on_contact_deleted
on_contact_merged        on_company_created      on_company_updated
on_interaction_logged    on_note_created
on_consent_changed
on_deal_created          on_deal_stage_changed
on_deal_won              on_deal_lost
on_task_created          on_task_completed
on_form_submitted        on_inbound_received
compute_fit_score        ← return-value hook
```

Future hooks under consideration (not yet wired):

```
on_contact_assigned     on_deal_assigned
on_segment_evaluated    on_segment_member_added    on_segment_member_removed
on_portal_used          on_consent_granted         on_consent_withdrawn
on_install              on_upgrade
```

## Signatures

### Fire-and-forget hooks

```python
def on_<event>(ctx: ServiceContext, *event_args, conn) -> None:
    ...
```

Examples:

```python
def on_contact_created(ctx, contact, conn): ...
def on_contact_updated(ctx, before, after, conn): ...
def on_contact_deleted(ctx, before, conn): ...
def on_interaction_logged(ctx, interaction, conn): ...
def on_deal_stage_changed(ctx, before, after, conn): ...
def on_deal_won(ctx, deal, conn): ...
def on_form_submitted(ctx, form, submission, contact, conn): ...
def on_inbound_received(ctx, endpoint, event, conn): ...
```

Return value is ignored. Exceptions are caught by the dispatcher,
logged to `plugins.last_error`, audited as `plugin.error`.

### compute_fit_score (return-value hook)

```python
def compute_fit_score(ctx: ServiceContext, contact: dict, conn) -> dict:
    return {
        "score":    25,           # 0..100 component
        "weight":   1.0,           # relative weighting
        "evidence": [
            {"reason": "Industry matches ICP", "delta": +20},
            {"reason": "Senior title",         "delta": +5},
        ],
    }
```

The scoring service aggregates across all plug-ins as a weighted
average + concatenated evidence.

## ctx + conn

- `ctx`: the `ServiceContext` of the call that fired the hook. Pass it
  to any service function you call from inside the hook so writes
  appear correctly attributed in audit.
- `conn`: the SQLite connection of the parent transaction. Reads see
  uncommitted state. Writes commit/rollback with the parent. **Always**
  pass `conn=conn` to any service function you call from inside the
  hook.

## Plug-in calling other services

```python
from backend.services import tags, interactions

def on_form_submitted(ctx, form, submission, contact, conn):
    if contact:
        tags.attach(ctx, tag_id=_LEAD_TAG_ID, contact_id=contact["id"], conn=conn)
        interactions.log(ctx, {
            "type":  "system",
            "contact_id": contact["id"],
            "title": "Form-driven lead tagging",
            "body":  "Auto-tagged as lead via form submission.",
        }, conn=conn)
```

## Plug-in config

`plugins.config_json` stores per-plug-in configuration. Read it:

```python
def _config(conn, plugin_name):
    row = conn.execute(
        "SELECT config_json FROM plugins WHERE name = ?", (plugin_name,)
    ).fetchone()
    return json.loads(row["config_json"]) if row and row["config_json"] else {}
```

Update via UI / REST. Plug-in re-reads on each dispatch (no restart
needed).

## Error handling rules

- Plug-in raises → dispatcher catches, logs to `plugins.last_error`,
  writes `audit_log.action='plugin.error'`. Parent transaction
  continues.
- Plug-in returns normally → considered success.
- Plug-in calls another service that raises → that service's error
  propagates (also caught by the dispatcher). Other plug-ins still
  fire.

Do NOT use exceptions for control flow within a plug-in.

## Lifecycle

A plug-in is:

- **Registered** when its file exists and reload has run.
- **Enabled** when `plugins.enabled = 1` (default after registration).
- **Disabled** when `plugins.enabled = 0`. Hooks won't fire.
- **Errored** when `plugins.last_error` is non-empty. Still enabled
  unless you disable it.
- **Removed** when the file is deleted and reload has run AND you
  delete the registry row manually. Better: leave it disabled.

## Testing a plug-in

```python
# tests/test_my_plugin.py
import sqlite3
from backend.db import db, set_db_path
from backend.context import system_context
from backend.services import plugins as plugmod, contacts

def test_my_plugin_fires_on_contact_create(tmp_path):
    set_db_path(str(tmp_path / "test.db"))
    plugmod.reload_all()
    ctx = system_context()
    contacts.create(ctx, {"full_name": "Test", "email": "t@e.com"})
    # assert your plug-in side-effects here
```

## Where to look in code

- `backend/services/plugins.py` — loader + dispatcher + KNOWN_HOOKS
- `agent_surface/plugins/auto_tag_from_interactions.py` — example
- `agent_surface/plugins/example_fit_score.py` — compute_fit_score example
- `migrations/0006_v4.sql` — `plugins`, `plugin_hooks` schema

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
- [mcp.md](mcp.md)
- [plugins.md](plugins.md) **← you are here**
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
