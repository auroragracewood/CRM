# Dev · Writing a plug-in

> Build a plug-in from blank file to running hook in 15 minutes.
> Worked example walks through file shape, hook implementation,
> testing, deployment, and the gotchas.

## Context

Plug-ins are the elective-behavior layer. Anything that's not part
of the core CRM contract but is part of YOUR install belongs here.
The default repo ships two examples (`auto_tag_from_interactions.py`
and `example_fit_score.py`); this guide shows you how to write your
own.

For the WHY, see [01-concepts/plugins](../01-concepts/plugins.md).
For the contract, see [03-reference/plugins](../03-reference/plugins.md).

## Result

A working plug-in:

- File in `agent_surface/plugins/<name>.py`.
- Registered after `plugin reload`.
- Fires on the chosen hook when the corresponding event happens.
- Errors caught and logged (the parent transaction is safe).

## Recipe — a "Slack notify on new high-value lead" plug-in

### Goal

When a contact is created via the public form AND has a recognized
high-value domain (e.g., a Fortune 500 list, or just a hand-curated
list), post to Slack.

### Step 1 — file structure

`agent_surface/plugins/slack_hv_lead.py`:

```python
"""Slack notification on new high-value lead.

Reads HIGH_VALUE_DOMAINS from plug-in config_json (settable via UI),
falls back to a hard-coded sample list.
"""
import json
import os

import requests

# These three constants are MANDATORY.
NAME = "slack_hv_lead"
VERSION = "0.1.0"
DESCRIPTION = ("Posts to Slack when a contact is created from the "
               "contact-us form and matches a configured domain list.")

# Optional: per-hook priority. Lower runs first.
HOOK_PRIORITIES = {
    "on_form_submitted": 200,    # after other "lead enrichment" plug-ins
}

# Optional: env var resolution. Don't hardcode the URL.
SLACK_URL = os.environ.get("SLACK_INCOMING_WEBHOOK_URL")
DEFAULT_DOMAINS = {"acme.coffee", "blueriver.media"}


def _config(conn) -> dict:
    """Read plug-in config from the plugins table."""
    row = conn.execute("SELECT config_json FROM plugins WHERE name=?",
                       (NAME,)).fetchone()
    if not row or not row["config_json"]:
        return {}
    try:
        return json.loads(row["config_json"])
    except Exception:
        return {}


def _is_high_value(email: str, conn) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    cfg = _config(conn)
    domains = set(cfg.get("high_value_domains") or DEFAULT_DOMAINS)
    return domain in domains


def on_form_submitted(ctx, form, submission, contact, conn):
    """Hook: fired right after `forms.submit` resolves a contact."""
    if form.get("slug") != "contact-us":
        return
    if not contact or not SLACK_URL:
        return

    email = contact.get("email") or ""
    if not _is_high_value(email, conn):
        return

    text = (f":sparkles: *High-value lead from contact form*\n"
            f"*Name*: {contact.get('full_name')}\n"
            f"*Email*: {email}\n"
            f"*Title*: {contact.get('title') or '—'}\n"
            f"*Source*: {form.get('slug')}\n"
            f"*Contact ID*: {contact['id']}")

    try:
        resp = requests.post(SLACK_URL, json={"text": text}, timeout=2)
        resp.raise_for_status()
    except Exception as e:
        # Re-raise so the dispatcher catches + logs to plugins.last_error.
        # Do NOT silently swallow — you'd lose observability.
        raise
```

### Step 2 — reload

```bash
python -m agent_surface.cli plugin reload
```

Output:

```
Reloaded 4 plug-ins. Registered: slack_hv_lead, auto_tag_from_interactions, ...
```

Verify:

```bash
python -m agent_surface.cli plugin list
# {"items": [{"name":"slack_hv_lead", "enabled":1, ...}, ...]}
```

### Step 3 — configure

UI: Plug-ins page → slack_hv_lead → Config → paste:

```json
{ "high_value_domains": [
    "acme.coffee", "blueriver.media", "hammerbuild.example",
    "fortune500-co-1.com", "fortune500-co-2.com"
]}
```

Or via REST:

```bash
curl -X PUT http://localhost:8000/api/plugins/3/config \
  -H "Authorization: Bearer $KEY" \
  -d '{"high_value_domains":[...]}'
```

The plug-in reads config on every dispatch — no restart needed.

### Step 4 — set env

```bash
export SLACK_INCOMING_WEBHOOK_URL="https://hooks.slack.com/..."
sudo systemctl restart crm
```

### Step 5 — test

Submit a form:

```bash
curl -sX POST http://localhost:8000/f/contact-us \
  -d '{"name":"Greg Johnson","email":"greg@hammerbuild.example",
       "interest":"signage","message":"Test."}'
```

You should see a Slack message. Then verify the audit chain:

```sql
SELECT action, object_type, request_id FROM audit_log
WHERE request_id = (SELECT request_id FROM audit_log
                    WHERE object_type='form_submission'
                    ORDER BY id DESC LIMIT 1)
ORDER BY ts;
```

You see the form, contact, interaction, scoring rows. The Slack
call doesn't produce its own audit row (the plug-in didn't call any
service to mutate); but if Slack returns non-2xx, you'll see
`action='plugin.error', object_id=<plugin_id>` in audit.

## Operations

### Disabling temporarily

```bash
python -m agent_surface.cli plugin disable --id 3
# (or via UI / REST / MCP)
```

The file stays; the hook stops firing.

### Reading recent errors

UI Plug-ins page shows `last_error`. For history:

```sql
SELECT ts, request_id, before_json
FROM audit_log
WHERE action='plugin.error' AND object_id=3
ORDER BY ts DESC LIMIT 20;
```

### Profiling

If the plug-in is slow (e.g., the LLM call takes 2s), the parent
service waits 2s. Solutions:

1. Move the slow work to a deferred-work pattern — write to a queue
   table inside the transaction; an async worker drains.
2. Set a tight `timeout` on the outbound call (`requests.post(...,
   timeout=2)`).
3. Add a circuit breaker decorator.

## Fine-tuning

### Hook priority

`HOOK_PRIORITIES = {"on_form_submitted": 200}` runs this plug-in
AFTER the default 100. Useful if your plug-in wants to read the
effects of earlier ones (e.g., the auto-tag plug-in's topic tags).

### Per-hook enable

`ENABLED_HOOKS = {"on_form_submitted"}` — register ONLY this hook
even if you define multiple. Useful for plug-ins under development.

### Idempotency

If the plug-in is called twice for the same logical event (e.g.,
because the parent service was retried with an idempotency key),
your plug-in's external effects may double-fire. Make external
effects keyed:

```python
def on_form_submitted(ctx, form, submission, contact, conn):
    delivery_key = f"{form['slug']}-{submission['id']}"
    if _seen_recently(delivery_key, conn):
        return
    # ... do the work
    _remember(delivery_key, conn)
```

Where `_seen_recently` checks a small dedup table you maintain
inside the plug-in's data scope.

### Plug-in writes that should be transactional

If your plug-in mutates the CRM (attaches tags, creates interactions),
do it through service functions, passing `conn=conn`:

```python
from backend.services import tags, interactions

def on_contact_created(ctx, contact, conn):
    if contact.get("source") == "form:contact-us":
        tags.attach(ctx, tag_id=_FORM_LEAD_TAG_ID,
                    contact_id=contact["id"], conn=conn)
        interactions.log(ctx, {
            "type": "system", "contact_id": contact["id"],
            "title": "Auto-tagged as form lead",
            "body":  "Plug-in slack_hv_lead also notified Slack.",
        }, conn=conn)
```

This keeps everything atomic: if any of these calls raise, the
parent rolls back.

### LLM plug-ins

For LLM calls (Claude, OpenAI, etc.):

```python
import os, anthropic
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def on_interaction_logged(ctx, interaction, conn):
    if (interaction.get("type") not in ("meeting","call","email")
            or not interaction.get("body")):
        return
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role":"user","content":
                f"Extract 3 topic tags (one or two words each) from:\n\n{interaction['body']}"
            }],
        )
        # ...parse and attach via tags service
    except Exception:
        raise
```

Don't catch and swallow — let the dispatcher log it. Use a haiku-
tier model for cost; cache aggressively (e.g., per `body` hash).

### Testing

```python
# tests/test_slack_hv_lead.py
import os, sqlite3
from backend.context import system_context
from backend.services import plugins as plugmod, forms, contacts

def test_high_value_triggers_slack(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("CRM_DB_PATH", db_path)
    monkeypatch.setenv("SLACK_INCOMING_WEBHOOK_URL", "")  # no real call
    # ... apply migrations
    # ... create the form
    # ... call forms.submit
    # ... assert plug-in didn't error
```

Test plug-ins by exercising the parent service that fires their hook,
not the hook directly.

## Maximizing potential

1. **Plug-in suites for verticals.** Bundle 10-20 plug-ins for a
   vertical (e.g., "Plug-in suite for art galleries"). Drop the
   folder into `agent_surface/plugins/`, reload, your CRM is now
   vertical-specific.

2. **Plug-in versioning.** Use `VERSION` semver. When upgrading,
   detect old version + run migration logic in an `on_upgrade` hook
   (planned addition to KNOWN_HOOKS).

3. **Composition.** Two plug-ins on the same hook with different
   priorities. The first computes a derived value; the second uses
   it. Each is independent.

4. **Plug-in marketplace.** Publish plug-ins as small git repos.
   `crm-cli plugin install https://github.com/...` clones into
   `agent_surface/plugins/<repo>/`.

5. **Per-team plug-ins.** Read `ctx.user_id` and adjust behavior.
   Sales-team plug-ins only fire when sales team is acting; ops-team
   plug-ins likewise.

6. **Self-monitoring.** A meta-plug-in that listens on every
   `plugin.error` audit row and notifies you when a plug-in starts
   failing. (Plug-ins watching plug-ins.)

## Anti-patterns

- **Catching all exceptions and silently returning.** You lose
  observability. Always let the dispatcher catch (or re-raise after
  logging your own context).
- **Long blocking I/O inside the transaction.** The parent service
  is holding the SQLite write lock. Either timeout aggressively
  (<= 2s) or defer.
- **Writing raw SQL from a plug-in.** Use service functions to
  mutate. Otherwise plug-in writes don't trigger their own audit /
  webhook / plug-in chains.
- **Mutable global state in the plug-in module.** Multiple workers
  (or multiple hook invocations) will race. Keep state in DB or in
  a process-local cache with proper locking.
- **Hardcoding secrets in the .py file.** Use env vars or
  `plugins.config_json` (and treat that as sensitive).
- **Plug-ins that depend on other plug-ins by side-effect.** If
  plug-in B reads tag X created by plug-in A, B will break when A
  is disabled. Document dependencies in the module docstring.

## Where to look in code

- `backend/services/plugins.py` — loader, dispatcher, KNOWN_HOOKS
- `agent_surface/plugins/auto_tag_from_interactions.py` — full example
- `agent_surface/plugins/example_fit_score.py` — return-value hook example

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
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](adding-an-entity.md)
- [writing-a-plugin.md](writing-a-plugin.md) **← you are here**
- [writing-a-skill.md](writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
