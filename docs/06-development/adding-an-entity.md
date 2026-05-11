# Dev · Adding a new entity

> The 10-step checklist for adding a new noun to the CRM — "projects",
> "subscriptions", "incidents", whatever your install needs. Service
> layer first; transports follow automatically.

## Context

The most common extension is "we need to track X here too." Without
discipline this turns into a half-modeled table, a couple of REST
routes that grew like weeds, and no audit/webhook/plug-in support.

This checklist makes "adding X" mechanical: schema → service →
transports → docs. Follow it once and the new entity has all the
same guarantees as `contacts` or `deals` — for free.

## Understanding

The full anatomy of a CRM entity in this codebase:

```
migrations/000N_add_<entity>.sql      ─ schema + triggers + indexes
backend/services/<entity>.py          ─ service-layer module
backend/services/__init__.py          ─ (no change; lazy imports OK)
backend/api.py                        ─ REST router additions
agent_surface/cli.py                  ─ CLI argparse additions
agent_surface/mcp_server.py           ─ MCP tool registrations
agent_surface/skills/<verb>-<entity>.md  ─ agent skill files
backend/main.py                       ─ UI routes (if user-visible)
ui/<entity>.html                      ─ UI templates (if user-visible)
docs/03-reference/data-model.md       ─ entry under the appropriate version
docs/03-reference/api.md              ─ endpoint table
docs/03-reference/cli.md              ─ command group table
docs/03-reference/mcp.md              ─ MCP tool list
docs/03-reference/webhooks.md         ─ events emitted
docs/03-reference/errors.md           ─ new error codes
docs/01-concepts/<entity>.md          ─ (if conceptually significant)
```

You don't always need all of these. The minimum viable entity is:
schema + service + REST + CLI + audit/webhook integration + docs.

## Result

After the checklist, your new entity:

- Has a migration applied across all environments via
  `python -m backend.migrations`.
- Has a single service module that handles validation, audit,
  webhook, plug-in dispatch.
- Is callable from REST, CLI, and MCP with identical behavior.
- Appears in the audit log when mutated.
- Emits webhook events for create/update/delete.
- Has documentation in the wiki.

## Recipe — the 10-step checklist

Suppose you're adding `projects` (each project belongs to a company,
has a status, due date, and team members).

### 1. Write the migration

`migrations/0009_add_projects.sql`:

```sql
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'active',
        -- 'active' | 'paused' | 'completed' | 'cancelled'
    due_date      INTEGER,
    description   TEXT,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    deleted_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_projects_company  ON projects(company_id);
CREATE INDEX IF NOT EXISTS idx_projects_status   ON projects(status)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_projects_due_date ON projects(due_date)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS project_members (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member',  -- 'lead' | 'member'
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

-- (Optional) FTS triggers if projects should appear in global search
CREATE TRIGGER IF NOT EXISTS projects_ai AFTER INSERT ON projects
BEGIN
    INSERT INTO search_index(kind, ref, title, body)
    VALUES ('project', NEW.id, NEW.name, COALESCE(NEW.description, ''));
END;
CREATE TRIGGER IF NOT EXISTS projects_au AFTER UPDATE OF name, description ON projects
BEGIN
    DELETE FROM search_index WHERE kind='project' AND ref=NEW.id;
    INSERT INTO search_index(kind, ref, title, body)
    VALUES ('project', NEW.id, NEW.name, COALESCE(NEW.description, ''));
END;
CREATE TRIGGER IF NOT EXISTS projects_ad AFTER DELETE ON projects
BEGIN
    DELETE FROM search_index WHERE kind='project' AND ref=OLD.id;
END;
```

Apply: `python -m backend.migrations`.

### 2. Write the service module

`backend/services/projects.py`:

```python
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from . import plugins as _plugins
from .contacts import ServiceError   # one canonical ServiceError class


_FIELDS = ("name", "slug", "company_id", "status",
           "due_date", "description")


def _validate(payload: dict, *, partial: bool=False) -> dict:
    cleaned = {k: payload.get(k) for k in _FIELDS if k in payload}
    if not partial:
        if not cleaned.get("name"):
            raise ServiceError("VALIDATION_ERROR", "name is required")
        if not cleaned.get("slug"):
            raise ServiceError("VALIDATION_ERROR", "slug is required")
    if "status" in cleaned and cleaned["status"] not in (
            "active", "paused", "completed", "cancelled"):
        raise ServiceError("VALIDATION_ERROR",
                           f"status must be one of active|paused|completed|cancelled")
    return cleaned


def _row(r): return dict(r) if r else None


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate(payload)
    cleaned["status"] = cleaned.get("status") or "active"
    now = int(time.time())

    with db() as conn:
        exists = conn.execute(
            "SELECT id FROM projects WHERE slug=? AND deleted_at IS NULL",
            (cleaned["slug"],),
        ).fetchone()
        if exists:
            raise ServiceError("PROJECT_SLUG_EXISTS",
                               f"Slug already in use: {cleaned['slug']!r}",
                               {"project_id": exists[0]})

        cols = list(cleaned.keys()) + ["created_at", "updated_at"]
        vals = list(cleaned.values()) + [now, now]
        conn.execute(
            f"INSERT INTO projects ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", vals)
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = _row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())

        audit.log(conn, ctx, action="project.created",
                  object_type="project", object_id=pid, after=row)
        webhooks.enqueue(conn, "project.created", {"project": row})
        _plugins.dispatch("on_project_created", ctx, row, conn)

    return row


def get(ctx: ServiceContext, project_id: int, *, include_deleted: bool=False) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        sql = "SELECT * FROM projects WHERE id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = conn.execute(sql, (project_id,)).fetchone()
    if not row:
        raise ServiceError("PROJECT_NOT_FOUND", f"project {project_id} not found")
    return _row(row)


def list_(ctx: ServiceContext, *, status: Optional[str]=None,
          company_id: Optional[int]=None, limit: int=50, offset: int=0) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    where = ["deleted_at IS NULL"]
    params = []
    if status:     where.append("status=?");     params.append(status)
    if company_id: where.append("company_id=?"); params.append(int(company_id))
    where_sql = " WHERE " + " AND ".join(where)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM projects{where_sql}",
                             params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM projects{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
    return {"items": [_row(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


def update(ctx: ServiceContext, project_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate(payload, partial=True)
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")
    now = int(time.time())

    with db() as conn:
        before = _row(conn.execute(
            "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
            (project_id,)).fetchone())
        if not before:
            raise ServiceError("PROJECT_NOT_FOUND", f"project {project_id} not found")

        set_sql = ", ".join(f"{k}=?" for k in cleaned) + ", updated_at=?"
        params = list(cleaned.values()) + [now, project_id]
        conn.execute(f"UPDATE projects SET {set_sql} WHERE id=?", params)

        after = _row(conn.execute("SELECT * FROM projects WHERE id=?",
                                  (project_id,)).fetchone())
        audit.log(conn, ctx, action="project.updated",
                  object_type="project", object_id=project_id,
                  before=before, after=after)
        webhooks.enqueue(conn, "project.updated",
                         {"project": after, "before": before})
        _plugins.dispatch("on_project_updated", ctx, before, after, conn)

    return after


def delete(ctx: ServiceContext, project_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        before = _row(conn.execute(
            "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
            (project_id,)).fetchone())
        if not before:
            raise ServiceError("PROJECT_NOT_FOUND", f"project {project_id} not found")
        conn.execute("UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?",
                     (now, now, project_id))
        audit.log(conn, ctx, action="project.deleted",
                  object_type="project", object_id=project_id, before=before)
        webhooks.enqueue(conn, "project.deleted", {"project_id": project_id})
        _plugins.dispatch("on_project_deleted", ctx, before, conn)
    return {"id": project_id, "deleted_at": now}
```

### 3. Add hook names to KNOWN_HOOKS

`backend/services/plugins.py`:

```python
KNOWN_HOOKS = [
    ...,
    "on_project_created", "on_project_updated", "on_project_deleted",
]
```

### 4. Add REST routes

`backend/api.py`:

```python
from .services import projects as projects_service

@router.post("/projects")
async def api_project_create(request: Request):
    ctx = build_context(request)
    try:
        body = await request.json()
        out = projects_service.create(ctx, body)
        return {"ok": True, "project": out}
    except ServiceError as e:
        return _error(e, ctx.request_id)

@router.get("/projects")
async def api_project_list(request: Request, status: str = None,
                           company_id: int = None,
                           limit: int = 50, offset: int = 0):
    ctx = build_context(request)
    try:
        return {"ok": True, **projects_service.list_(
            ctx, status=status, company_id=company_id,
            limit=limit, offset=offset)}
    except ServiceError as e:
        return _error(e, ctx.request_id)

# ... GET/{id}, PUT/{id}, DELETE/{id} similarly
```

Add `PROJECT_NOT_FOUND` (404) and `PROJECT_SLUG_EXISTS` (409) to the
`_STATUS` mapping at top of file.

### 5. Add CLI commands

`agent_surface/cli.py`:

```python
def cmd_project_create(args):
    user_id, role = _resolve_user(args)
    ctx = _ctx(args, role, user_id)
    payload = {k: getattr(args, k) for k in
               ("name","slug","company_id","status","due_date","description")
               if getattr(args, k, None) is not None}
    try:
        _print({"ok": True, "project":
                projects_service.create(ctx, payload)})
    except ServiceError as e:
        _print(_err(e)); sys.exit(2)

# similarly: cmd_project_get, cmd_project_list, cmd_project_update,
# cmd_project_delete
```

In `build_parser`:

```python
project = sub.add_parser("project", help="Project commands")
psub = project.add_subparsers(dest="action", required=True)

pc = psub.add_parser("create")
pc.add_argument("--name", required=True)
pc.add_argument("--slug", required=True)
pc.add_argument("--company-id", dest="company_id", type=int)
pc.add_argument("--status", choices=["active","paused","completed","cancelled"])
pc.add_argument("--due-date", dest="due_date", type=int)
pc.add_argument("--description")
pc.set_defaults(func=cmd_project_create)

pg = psub.add_parser("get"); pg.add_argument("--id", type=int, required=True)
pg.set_defaults(func=cmd_project_get)

# list / update / delete similarly
```

### 6. Add MCP tools

`agent_surface/mcp_server.py`:

```python
@mcp.tool()
def create_project(name: str, slug: str, company_id: int = None,
                   status: str = "active", due_date: int = None,
                   description: str = None) -> dict:
    user_id, role = _resolve_user()
    ctx = _ctx(role, user_id)
    try:
        return {"ok": True, "project": projects_service.create(ctx, {
            "name": name, "slug": slug, "company_id": company_id,
            "status": status, "due_date": due_date, "description": description,
        })}
    except ServiceError as e:
        return _err(e)
```

Repeat for get/list/update/delete.

### 7. Add UI (if user-visible)

`ui/projects.html` and a `/projects` route in `backend/main.py`,
following the pattern of `ui/contacts.html` + route. The form posts
to `/projects/new`, which calls `projects_service.create`.

### 8. Add skill files for agents

`agent_surface/skills/create-project.md`:

```markdown
---
verb: create
noun: project
canonical_transport: rest
mcp_tool: create_project
cli: project create
rest: POST /api/projects
required_scope: write
---

# Create a project

Creates a new project, optionally tied to a company.

## Required fields
- `name`
- `slug` (URL-safe, lowercase, unique)

## Optional fields
- `company_id`
- `status` (default `active`)
- `due_date` (unix seconds)
- `description`

## Example (REST)

```json
POST /api/projects
{ "name": "Q4 brand refresh", "slug": "q4-brand-refresh",
  "company_id": 3, "status": "active",
  "due_date": 1735689600, "description": "..." }
```

## Errors

- `VALIDATION_ERROR` — missing required field
- `PROJECT_SLUG_EXISTS` — slug already in use
- `FORBIDDEN` — caller lacks write scope
```

Repeat for `find-project`, `update-project`, `complete-project`,
`assign-project-member`, etc.

### 9. Update reference docs

- `docs/03-reference/data-model.md` — add a section under the
  appropriate version (or a new one) for `projects`.
- `docs/03-reference/api.md` — add the new endpoints.
- `docs/03-reference/cli.md` — add the new command group.
- `docs/03-reference/mcp.md` — list the new MCP tools.
- `docs/03-reference/webhooks.md` — list `project.*` events.
- `docs/03-reference/errors.md` — list new error codes.

### 10. Run the smoke test

```bash
KEY="..."
BASE="http://localhost:8000"

# Create
curl -sX POST $BASE/api/projects -H "Authorization: Bearer $KEY" \
  -d '{"name":"Test Project","slug":"test-project"}'

# Read
curl -sH "Authorization: Bearer $KEY" $BASE/api/projects

# Update
curl -sX PUT $BASE/api/projects/1 -H "Authorization: Bearer $KEY" \
  -d '{"status":"completed"}'

# Delete
curl -sX DELETE $BASE/api/projects/1 -H "Authorization: Bearer $KEY"

# Confirm audit chain
sqlite3 crm.db "SELECT action, surface FROM audit_log \
                WHERE object_type='project' ORDER BY ts;"
```

If every step succeeds and the audit log shows the 4 expected actions
under `surface=rest`, the entity is wired up correctly.

## Operations

### Per-environment rollout

1. Code change merges to main.
2. Deploy to staging — migration runs automatically; smoke test the
   new endpoint.
3. Deploy to production — same.

If migrations run unexpectedly long, see
[migrations](../05-operations/migrations.md) for guidance.

### Backward compatibility

For NEW entities, no compat concern. For ADDING fields to an existing
entity:

- New fields should be NULLABLE unless they have a sensible default
  (use `NOT NULL DEFAULT ...`).
- Service validation should accept missing values gracefully.
- Audit `before_json` of old records lacks the new field — handle
  that in any code that reads before/after diffs.

## Fine-tuning

### Adding to search

The triggers in step 1 already add this. Verify:

```bash
python -m agent_surface.cli search --q "Test Project"
# should return the project
```

### Adding plug-in hooks

Hook names in step 3. Plug-ins can immediately react:

```python
# agent_surface/plugins/project_notify.py
def on_project_created(ctx, project, conn):
    # notify Slack, etc.
    ...
```

### Adding scoring relevance

If projects should affect contact scores (e.g., a contact tied to a
"completed" project gets a `relationship_strength` bump), add a rule
to `scoring.py`. Triggered automatically when the project changes
state.

### Adding reports

A new function in `services/reports.py`:

```python
def projects_by_status(ctx):
    # ...
    return {"rows": [...], "total": ...}

CATALOG["projects_by_status"] = {
    "name": "Projects by status",
    "fn":   projects_by_status,
    "params": [],
}
```

Now visible in the Reports page + via `report run --name
projects_by_status`.

## Maximizing potential

1. **Generate the boilerplate.** Most of the 10 steps follow a template
   per entity. A small CLI command `crm-cli new-entity projects` could
   scaffold the migration, service, and route stubs. Saves an hour
   per entity.

2. **Cross-entity relationships.** A project links contacts (via
   memberships) and a company. Build dynamic-segment predicates so
   you can ask "contacts on at-risk projects." The entity becomes
   load-bearing in the rest of the system.

3. **Entity-specific dashboards.** Add a project dashboard widget
   that surfaces "projects with status=paused for >30 days." Drives
   action.

4. **Entity-as-attention-vector.** Plug-ins listening to
   `on_project_updated` can trigger task creation, send portal
   tokens to project members, or sync to external project-management
   tools. The entity becomes a hub.

5. **API contract testing.** Write a small contract test per new
   entity: create → read → list → update → delete via REST → audit
   log has the expected 4 rows. CI-checks regression prevention.

## Anti-patterns

- **Skipping the service module.** Putting "just one CREATE TABLE +
  one route" in `main.py` directly. Now you have an entity with no
  audit, no webhooks, no plug-ins, and a route that other transports
  can't reach. Always service-layer first.
- **Hard-coupling to an existing entity.** Don't store a project's
  data inside `contacts.custom_fields_json`. Make a real entity if
  it deserves one.
- **Snake_case vs camelCase drift.** This CRM uses snake_case for
  column names and JSON keys. Follow it; transports otherwise
  diverge.
- **Forgetting indexes.** A new `status` column without an index
  becomes a full-table scan when the entity grows. Add the index
  in the same migration.
- **Skipping docs.** "I'll write docs later" never happens. Write
  the skill markdown + reference table entry in the same PR.

## Where to look in code

- `backend/services/contacts.py` — the cleanest service template
- `backend/services/companies.py` — close second template
- `backend/api.py` — REST router pattern
- `agent_surface/cli.py` — CLI argparse pattern
- `agent_surface/mcp_server.py` — MCP tool pattern

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
- [adding-an-entity.md](adding-an-entity.md) **← you are here**
- [writing-a-plugin.md](writing-a-plugin.md)
- [writing-a-skill.md](writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
