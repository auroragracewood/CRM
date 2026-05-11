# Concept · Plug-ins

> User-installed Python modules that hook into service-layer events.
> Where AI behavior, integrations, and product-specific automation
> live — outside the core, without forking it.

## Context

Every CRM eventually accumulates customer-specific behavior:

- "When a lead from this form comes in, tag it with that label and
  notify Slack."
- "When a deal moves to 'Won', create three onboarding tasks for the
  AM team."
- "When a contact's title looks senior, bump their fit score by 10."
- "When an interaction is logged, run an LLM over the body and pull
  out topic tags."

Putting any of these in core services is a trap: they're customer-
specific, they make the core untestable, they couple the CRM to your
favorite LLM provider, and they make upgrades painful.

Putting them in a side-script that polls the database is worse: it's
flaky, has a race window, can't be audited, and tells external
agents nothing about what the system actually does.

Plug-ins are the right answer: drop a Python file in
`agent_surface/plugins/`, the system picks it up, registers its hooks,
and calls them in the same transaction as the events that trigger
them. Auditable, atomic, surgical. Lean on them for everything that
isn't "things every install needs."

## Understanding

A plug-in is a Python module exposing three constants and zero or more
hook functions:

```python
# agent_surface/plugins/auto_tag_from_interactions.py
NAME = "auto_tag_from_interactions"
VERSION = "0.2.0"
DESCRIPTION = "Extract topic tags from interaction body text"

def on_interaction_logged(ctx, interaction, conn):
    text = (interaction.get("title") or "") + " " + (interaction.get("body") or "")
    topics = _extract_topics(text)             # heuristic or LLM
    for topic in topics:
        tag = tags.create_or_get(ctx, name=f"topic:{topic}",
                                 color="#ff66aa", scope="contact",
                                 conn=conn)
        tags.attach(ctx, tag_id=tag["id"],
                    contact_id=interaction["contact_id"], conn=conn)
```

On startup (and on `POST /api/plugins/reload`), the loader:

1. Scans `agent_surface/plugins/*.py` non-recursively.
2. Imports each module.
3. Reads `NAME`, `VERSION`, `DESCRIPTION` and UPSERTs into the
   `plugins` table.
4. Discovers callables whose name matches a member of `KNOWN_HOOKS`
   (see below) and registers them in `plugin_hooks(plugin_id,
   hook_name, priority)`.
5. Caches module references for runtime dispatch.

When a service function reaches its `plugins.dispatch(hook_name, ctx,
..., conn=conn)` step, the dispatcher:

1. Looks up `plugin_hooks WHERE hook_name = ? AND plugin.enabled = 1`
   ordered by `priority ASC` (lower = earlier).
2. For each, calls the function and catches every exception.
3. If a hook raises, the error is written to `plugins.last_error` AND
   an `audit_log` row `action='plugin.error'` is inserted. The
   parent transaction continues — a misbehaving plug-in cannot abort
   the parent service call.

### Known hooks

Defined in `backend/services/plugins.py`:

```
on_contact_created       on_contact_updated      on_contact_deleted
on_contact_merged        on_company_created      on_company_updated
on_interaction_logged    on_note_created
on_consent_changed
on_deal_created          on_deal_stage_changed
on_deal_won              on_deal_lost
on_task_created          on_task_completed
on_form_submitted        on_inbound_received
compute_fit_score        ← only return-value hook
```

`compute_fit_score(ctx, contact, conn) -> dict` is the one hook whose
return value matters. Each plug-in contributes a score component +
evidence; scoring aggregates them. See [scoring](scoring.md).

## Reason

**Why this architecture and not alternatives?**

1. **Plug-ins beat forking.** A fork drifts. With plug-ins, every
   install runs the SAME core. Customer-specific behavior lives in
   that customer's plug-in directory. Core upgrades don't conflict
   with customer behavior.

2. **Plug-ins beat external scripts.** External scripts polling
   `audit_log` race; they fire after commit and may miss state
   transitions. Plug-ins run synchronously in the same transaction
   as the event, sharing the SQLite connection — they see exactly
   the same state the service sees.

3. **Plug-ins beat configuration tables.** "Configurable rules" in a
   table grow into a DSL no one can read. Plug-ins are Python — full
   expressive power, version-controlled, testable, debuggable.

4. **Plug-ins are how AI lives in the CRM without coupling.** Core
   services are LLM-free. If you want to call Claude, OpenAI, a
   self-hosted model, or no model at all, that's a plug-in choice,
   not a core choice. Three customers can each run a different LLM
   strategy on the same core build.

5. **Plug-ins are auditable.** The `plugins` and `plugin_hooks`
   tables make their existence and bindings inspectable. Every
   action they take through services produces audit rows tagged
   `surface='plugin'`. Caught exceptions are stored. Nothing is
   black-box.

6. **Plug-ins are toggleable.** A misbehaving plug-in can be
   disabled without removing the file:
   `python -m agent_surface.cli plugin disable --id 3`.
   Toggle from UI, REST, CLI, or MCP.

## Result

What plug-ins give you:

- **Surgical customization.** Add behavior without editing 1 line of
  core.
- **AI integration with a clean seam.** Every LLM call is one
  plug-in away from being swapped, disabled, or replaced.
- **Predictable execution model.** Hook → synchronous call →
  caught exceptions → audit trail. Same shape every time.
- **Inter-plug-in composition.** Two plug-ins can listen on the same
  hook; the priority field orders them. They can read each other's
  effects through `conn`.
- **A first-class extension API for your team or your users.**
  Anyone can read `KNOWN_HOOKS` and write a plug-in — no special
  build, no plug-in SDK.

## Use case 1 — auto-tag from interactions (shipped)

The plug-in `agent_surface/plugins/auto_tag_from_interactions.py`
implements `on_interaction_logged`. When a salesperson logs:

> Type: meeting · Title: "Lobby walk-through" · Body: "Greg wants a 2m
> copper signage piece and a feature wall. Budget Q2 install."

The plug-in:

1. Extracts the topic words `copper`, `signage`, `lobby`, `feature
   wall`, `q2-install` from the title+body using a keyword-frequency
   heuristic (or by calling Claude if `ANTHROPIC_API_KEY` is set in
   env).
2. For each topic, calls `tags.create` (idempotent — `TAG_EXISTS`
   handled) with `name="topic:<word>"`, `color="#ff66aa"`,
   `scope="contact"`.
3. Calls `tags.attach` to put each topic tag on the interaction's
   contact.
4. Logs a `system` interaction noting the auto-tagging so a human
   reading the timeline knows why those tags exist.

Result: contacts accumulate searchable topic tags over time without
anyone having to remember to tag them. The contact page's Tags chip
shows pink `topic:*` tags labeled "auto-extracted."

## Use case 2 — Slack notification on deal won (illustrative)

```python
# agent_surface/plugins/slack_deal_won.py
import os, requests
from backend.services import deals

NAME = "slack_deal_won"
VERSION = "0.1.0"
DESCRIPTION = "Posts to Slack when a deal moves to a won stage"

SLACK_URL = os.environ.get("SLACK_INCOMING_WEBHOOK_URL")

def on_deal_won(ctx, deal, conn):
    if not SLACK_URL:
        return
    text = f":tada: {deal['title']} won, ${deal['value_cents']/100:.0f}"
    try:
        requests.post(SLACK_URL, json={"text": text}, timeout=2)
    except Exception:
        # Don't fail the parent transaction over a Slack hiccup
        raise   # dispatcher will catch + log
```

Drop the file, `POST /api/plugins/reload`, and every won deal posts
to Slack. No core changes.

Operational note: the `requests.post` happens INSIDE the parent
transaction. If Slack is slow, the SQLite write lock is held the
whole time. For high-volume installs, move the Slack call to a
deferred-work pattern (write to a queue table, drain async).

## Use case 3 — `compute_fit_score` aggregation

The scoring service computes a contact's `fit` score by calling all
plug-ins that implement `compute_fit_score(ctx, contact, conn) ->
dict`. Each returns its component:

```python
# plugin: industry_match
def compute_fit_score(ctx, contact, conn):
    company = _get_company(contact, conn)
    in_icp = (company.get("industry") in {"construction", "architecture"})
    return {
        "score":   20 if in_icp else 0,
        "weight":  1.0,
        "evidence": [{"reason": "Industry matches ICP", "delta": +20}]
                    if in_icp else [],
    }

# plugin: seniority_signal
def compute_fit_score(ctx, contact, conn):
    title = (contact.get("title") or "").lower()
    senior = any(w in title for w in ("founder","owner","director","vp"))
    return {
        "score":   15 if senior else 0,
        "weight":  0.7,
        "evidence": [{"reason": "Senior title detected", "delta": +15}]
                    if senior else [],
    }
```

Scoring takes the weighted average across all plug-ins, persists
`contact_scores.score`, and stores the union of evidence arrays. The
contact page's "why?" expand-on-click reveals every contributing
reason.

## Operations

**Day-to-day operational mechanics:**

### Installing

Drop a `.py` file in `agent_surface/plugins/`. Trigger reload:

```bash
# CLI
python -m agent_surface.cli plugin reload

# REST
curl -X POST http://localhost:8000/api/plugins/reload \
     -H "Authorization: Bearer <admin-key>"

# UI: Plug-ins page → Reload button
```

The plug-in appears in the `plugins` table after reload.

### Enabling / disabling

```bash
python -m agent_surface.cli plugin disable --id 3
python -m agent_surface.cli plugin enable  --id 3
```

A disabled plug-in stays registered but its hooks don't fire.

### Inspecting hooks

```bash
sqlite3 crm.db "SELECT p.name, ph.hook_name, ph.priority
                FROM plugin_hooks ph JOIN plugins p ON p.id = ph.plugin_id
                WHERE p.enabled = 1 ORDER BY ph.hook_name, ph.priority;"
```

Shows which plug-in fires for what, in what order.

### Reading errors

```bash
sqlite3 crm.db "SELECT name, last_error FROM plugins
                WHERE last_error IS NOT NULL AND last_error != '';"
```

Or in the UI: Plug-ins page shows a red dot next to any plug-in with a
recent error.

For historical detail:

```sql
SELECT ts, request_id, before_json
FROM audit_log
WHERE action = 'plugin.error' AND object_id = <plugin_id>
ORDER BY ts DESC LIMIT 50;
```

### Config

Plug-ins can read their config from `plugins.config_json`:

```python
def on_contact_created(ctx, contact, conn):
    cfg = json.loads(_my_plugin_config(conn) or "{}")
    threshold = cfg.get("score_threshold", 70)
    ...
```

Updated via the UI or REST. Restart-free; the plug-in re-reads on
every dispatch.

### Removing a plug-in

Disable first. Then delete the .py file. Then reload. The `plugins`
row stays (for audit history), with `enabled=0`. To fully remove,
delete the row directly via SQL — but you lose the audit pointer.
Prefer leaving it disabled.

## Fine-tuning

### Priority

`plugin_hooks.priority` controls execution order — lower runs first.
Set it via the plug-in's optional `HOOK_PRIORITIES` dict:

```python
HOOK_PRIORITIES = {
    "on_contact_created": 10,      # earlier than default 100
    "on_interaction_logged": 200,  # later
}
```

Useful when one plug-in's output feeds another (e.g., topic
extraction → relevance scoring).

### Hook-by-hook enable/disable

Add to plug-in module:

```python
ENABLED_HOOKS = {"on_interaction_logged"}   # only this one
```

The loader registers ONLY listed hooks. Useful for plug-ins that
implement many hooks but you want only some.

### Per-context filtering

Inside the hook, filter on `ctx.surface` to act differently per
transport:

```python
def on_contact_created(ctx, contact, conn):
    if ctx.surface == "cli":
        return   # don't auto-tag during bulk imports
    ...
```

### Idempotency

Plug-ins may re-run if the parent service is retried with the same
idempotency key. Make hook actions idempotent — `tags.attach` already
is, `slack_post` should dedupe on `delivery_id`, etc.

### Synchronous vs deferred work

The default is synchronous (inside-transaction). For slow work, use
deferred:

```python
def on_deal_won(ctx, deal, conn):
    conn.execute(
        "INSERT INTO deferred_work (kind, payload, scheduled_for) "
        "VALUES (?, ?, ?)",
        ("slack_post", json.dumps({"deal_id": deal["id"]}),
         int(time.time())),
    )
    # commits with parent transaction; async worker handles delivery
```

You need a small worker that drains `deferred_work`. The plug-in's
synchronous part is trivially fast.

### LLM plug-ins

For LLM plug-ins, set:

```python
CIRCUIT_BREAKER = {
    "max_consecutive_errors": 5,
    "cool_off_seconds": 300,
}
```

Wrap the LLM call. After 5 errors, the plug-in self-disables for 5
minutes. (Implement this as a small decorator around your hook.)

## Maximizing potential

1. **Stack plug-ins for composite intelligence.** A
   `topic_extractor` plug-in attaches `topic:*` tags. A `routing`
   plug-in reads those tags and assigns to the right salesperson.
   A `nurture_cadence` plug-in reads the assignment and schedules
   the next outreach task. Each plug-in is small and replaceable.

2. **Use `compute_fit_score` for an LLM judge.** Write a plug-in
   that asks Claude "score this contact's fit 0-100 with reasons."
   Return the score + reasons as evidence. Combine with deterministic
   plug-ins (industry match, seniority) so the LLM influences but
   does not dominate.

3. **A/B test plug-ins.** Run two competing scoring plug-ins, both
   registered. Read both scores via `contact_scores.evidence_json`.
   Compare which predicts conversion better over a quarter, then
   disable the loser. The CRM doesn't care which one wins; both ran
   on real data.

4. **Build a marketplace.** Plug-ins are file-drop installable. If
   you build for a niche (e.g., CRMs for nonprofits, for boutique
   agencies, for solo consultants), package "starter packs" of
   plug-ins. Users install with one curl.

5. **Plug-ins as agent tools.** An MCP-driven agent can read
   `GET /api/plugins`, see what's enabled, and decide whether to
   trigger a hook explicitly via a related service call. Combined
   with skills, an agent learns the full vocabulary of what your
   CRM can do — including the customer-specific plug-in behavior.

6. **Plug-in version pinning + migrations.** Read `plugins.version`
   and write data migrations when upgrading a plug-in. The plug-in
   itself can implement `def on_install(ctx, conn):` and
   `def on_upgrade(ctx, conn, from_version, to_version):` hooks
   (not in `KNOWN_HOOKS` yet — easy to add).

7. **Plug-in observability dashboard.** Build a UI page that
   shows: which plug-ins fired today, how often, average duration,
   error rate. Use `audit_log` rows where `surface='plugin'`
   grouped by plug-in name. Find slow plug-ins. Optimize.

## Anti-patterns

- **Putting core logic in plug-ins.** "User must confirm consent
  before any outbound email" is a core invariant — that goes in the
  consent service, not in a plug-in. Plug-ins are for elective
  behavior; the core enforces invariants.
- **Bypassing services from inside a plug-in.** Always call other
  service functions to mutate, never raw SQL. Otherwise plug-in
  changes don't fire their downstream audit/webhook/plug-in chains.
- **Long blocking I/O in synchronous hooks.** Use the deferred-work
  pattern for slow calls.
- **Plug-ins that re-implement other plug-ins' logic.** Compose by
  reading state, not by duplicating behavior.
- **Hard-coding secrets in plug-in `.py` files.** Read from env or
  `plugins.config_json`. Audit-redact any secret-containing config.

## Where to look in code

- `backend/services/plugins.py` — loader, dispatcher, KNOWN_HOOKS
- `agent_surface/plugins/auto_tag_from_interactions.py` — full example
- `agent_surface/plugins/example_fit_score.py` — `compute_fit_score`
  example
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
- [service-layer.md](service-layer.md)
- [service-context.md](service-context.md)
- [audit-and-webhooks.md](audit-and-webhooks.md)
- [plugins.md](plugins.md) **← you are here**
- [scoring.md](scoring.md)
- [segments.md](segments.md)
- [portals.md](portals.md)
- [inbound.md](inbound.md)
- [search.md](search.md)

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
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
