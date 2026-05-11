# SCHEMATICS.md — How the pieces fit together

ASCII diagrams of the CRM's architecture. Reading this file should give
you the mental model for everything else in the wiki.

---

## 1. Process topology — what runs where

```
┌────────────────────────────────────────────────────────────────┐
│                       single host (Linux/Win/Mac)              │
│                                                                │
│   ┌─────────────────────┐    ┌─────────────────────┐           │
│   │ uvicorn (main.py)   │    │ python -m agent_    │           │
│   │  ─ FastAPI app       │    │   surface.cli       │           │
│   │  ─ UI routes         │    │  ─ in-process       │           │
│   │  ─ /api router       │    │    service calls    │           │
│   │  ─ /f/{slug} public  │    └─────────────────────┘           │
│   │  ─ /in/{slug} public │                                      │
│   │  ─ /portal/{token}   │    ┌─────────────────────┐           │
│   └─────────┬───────────┘    │ FastMCP server      │           │
│             │                 │ (stdio JSON-RPC)    │           │
│             ▼                 │  ─ launched by an   │           │
│      ┌────────────┐           │    MCP client       │           │
│      │  crm.db    │ ◄─────────┤                     │           │
│      │  (SQLite,  │           └─────────────────────┘           │
│      │   WAL)     │ ◄───── all surfaces talk to the same DB     │
│      └────────────┘                                             │
│             ▲                                                   │
│             │     ┌────────────────────┐                        │
│             └─────┤ webhook delivery   │ ── HTTP outbound ──▶   │
│                   │ worker (in-process │                        │
│                   │  background task)  │                        │
│                   └────────────────────┘                        │
└────────────────────────────────────────────────────────────────┘
```

There is exactly **one SQLite file** (`crm.db`). All surfaces touch the
same file. There is no message broker, no cache layer, no replica. The
CRM is designed to run on a single machine.

---

## 2. Layered architecture — how a request flows

```
   ┌───────────────────────────────────────────────────────────┐
   │                       TRANSPORTS                          │
   │                                                           │
   │  REST handlers    CLI commands    MCP tools    UI routes  │
   │  (backend/api.py) (agent_surface/  (agent_     (backend/   │
   │                    cli.py)         surface/     main.py)   │
   │                                    mcp_server) │           │
   │                       │   │   │   │                       │
   │            (each transport builds a ServiceContext)       │
   └────────────────────────┬──────────────────────────────────┘
                            │   passes ctx + payload
                            ▼
   ┌───────────────────────────────────────────────────────────┐
   │                    SERVICE LAYER                          │
   │                  (backend/services/*.py)                  │
   │                                                           │
   │  contacts  companies  interactions  notes  tags  consent  │
   │  pipelines deals      tasks        forms  search          │
   │  duplicates imports   scoring      segments  reports      │
   │  portals    inbound   plugins      saved_views auth_keys  │
   │                                                           │
   │  Each service function:                                   │
   │    1. validates payload                                   │
   │    2. opens a transaction                                 │
   │    3. writes data row(s)                                  │
   │    4. writes audit_log row                                │
   │    5. enqueues webhook outbox row                         │
   │    6. dispatches plug-in hooks                            │
   │    7. commits                                             │
   └───────────────────────────────────────────────────────────┘
                            │  one SQLite connection per call
                            ▼
   ┌───────────────────────────────────────────────────────────┐
   │                      STORAGE                              │
   │                    crm.db (SQLite)                        │
   │                                                           │
   │  31 application tables                                    │
   │  13 FTS5 virtual + shadow tables                          │
   │  PRAGMAs: foreign_keys=ON, journal_mode=WAL,              │
   │           busy_timeout=5000                               │
   └───────────────────────────────────────────────────────────┘
                            │
                            │  after commit
                            ▼
   ┌───────────────────────────────────────────────────────────┐
   │                  ASYNC SIDE-EFFECTS                       │
   │                                                           │
   │  webhook delivery worker (drains webhook_events)          │
   │  plug-in hooks (already ran in-tx; side-effects below     │
   │    may include outbound HTTP, but those fire AFTER the    │
   │    transaction commits if the plug-in is written          │
   │    correctly — see plugins concept doc)                   │
   └───────────────────────────────────────────────────────────┘
```

---

## 3. Service-layer pattern — one function, every surface

```
                 ┌────────────────────────────────┐
                 │ contacts.create(ctx, payload)  │
                 │  → returns {"id":…, ...}       │
                 │  → raises ServiceError(code)   │
                 └───────────────┬────────────────┘
                                 │
       ┌───────────┬─────────────┼────────────┬───────────┐
       │           │             │            │           │
       ▼           ▼             ▼            ▼           ▼
   REST handler   CLI cmd    MCP tool    UI POST     plug-in
   /api/contacts  contact    create_     /contacts/   that calls
   POST           create     contact     new          contacts.
                                                      create()

   All five do EXACTLY the same thing. They differ only in:
     ─ how they read the request (JSON body / argparse / kwargs / form)
     ─ how they format the response (JSON / stdout / dict / HTML)
     ─ how they build the ServiceContext (Authorization header /
       --as-user / local user / cookie)
```

---

## 4. ServiceContext — identity in flight

```
   ┌─────────────────────── ServiceContext ───────────────────────┐
   │                                                              │
   │  user_id     int  — the acting human (always set)            │
   │  api_key_id  int? — set when REST/MCP uses a bearer key      │
   │  role        str  — admin | user | readonly                  │
   │  scope       str  — read | write | admin                     │
   │  surface     str  — ui | rest | cli | mcp | cron | plugin |  │
   │                     webhook | system                         │
   │  request_id  str  — UUID; correlates one logical request     │
   │                     across many service calls + audit rows   │
   │                                                              │
   │  helpers:                                                    │
   │    .can_read()  -> scope in {read,write,admin}               │
   │    .can_write() -> scope in {write,admin}                    │
   │    .can_admin() -> scope == admin                            │
   └──────────────────────────────────────────────────────────────┘
```

Every service function takes `ctx` as the first argument. Every audit
row, webhook payload metadata, and plug-in hook receives it. That's how
"who did this, on which surface, with what request id" travels
end-to-end.

---

## 5. Transactional side-effects — what happens on every mutation

```
    BEGIN TRANSACTION
      │
      ├─ INSERT/UPDATE/DELETE on the main table
      │
      ├─ INSERT into audit_log (
      │     ts, user_id, api_key_id, surface, action,
      │     object_type, object_id, before_json, after_json,
      │     request_id
      │   )
      │
      ├─ INSERT into webhook_events for each subscription matching
      │   this event (event_name LIKE * OR exact match)
      │
      ├─ plug-ins dispatched on this hook run NOW, inside the tx
      │   (they receive the same conn — can read/write more rows)
      │
    COMMIT
      │
      ▼
    AFTER COMMIT:
      ─ webhook delivery worker wakes up, picks pending rows,
        sends HMAC-signed HTTP POSTs, retries with backoff,
        records response_status + response_body
      ─ plug-ins that did outbound work synchronously have already
        returned (their HTTP calls happened inside the tx — discouraged)
```

---

## 6. Plug-in dispatch — extensibility without changing core

```
   service function call (e.g., interactions.log)
         │
         │ writes interaction row + audit + webhook
         │
         ▼
   plugins.dispatch("on_interaction_logged", ctx, interaction, conn)
         │
         ├──► auto_tag_from_interactions.on_interaction_logged(...)
         │       └─ extracts topics from title+body
         │       └─ attaches topic:<word> tags via tags service
         │
         ├──► your_own_plugin.on_interaction_logged(...)
         │
         └──► … any other plug-in registered for this hook
```

Plug-in exceptions are caught, logged into `plugins.last_error`, and do
NOT abort the parent transaction. (A plug-in that misbehaves can
disable itself by raising — the core service still completes.)

`compute_fit_score` is the one hook with a return value: plug-ins
return a number+evidence dict, the scoring service aggregates them.

---

## 7. Webhook outbox + delivery

```
    service mutation
         │
         ▼
    webhook_events INSERT (status='pending', delivery_id=uuid())
         │
         ▼
    delivery worker picks pending rows in age order
         │
         ├─ POST {webhook.url}
         │  headers:
         │    X-CRM-Event: contact.created
         │    X-CRM-Delivery-ID: <uuid>
         │    X-CRM-Signature: sha256=<hex>   ← HMAC of body w/ secret
         │
         ▼
    response 2xx          response 4xx/5xx
         │                       │
         ▼                       ▼
    status='delivered'    attempts++
                          if attempts < 8:
                            status='retrying'
                            next_attempt_at = now + 2^attempts * 30s
                          else:
                            status='failed'
```

Receivers should dedupe on `X-CRM-Delivery-ID`. The CRM may retry the
same delivery up to 8 times.

---

## 8. Inbound — `POST /in/{slug}`

```
    external system POSTs JSON →  POST /in/{slug}
                                       │
                                       │ raw body + X-Signature header
                                       ▼
    inbound_events INSERT raw         (always, before parsing)
                                       │
                                       ▼
    verify HMAC signature  ──fail──► return 401
                                       │ ok
                                       ▼
    apply endpoint.routing_json
      ─ extract email → contacts.find_by_email or create
      ─ extract tags → tags.attach
      ─ extract title/body → interactions.log
                                       │
                                       ▼
    inbound_events UPDATE status='contact_linked',
                          contact_id, interaction_id
                                       │
                                       ▼
                                  return 200
```

---

## 9. Search (FTS5) — kept in sync by triggers

```
   contacts ─┐
   companies ┼─ INSERT/UPDATE/DELETE
   interactions │      │
   notes ─────┘      triggers
                      │
                      ▼
              search_index (FTS5 virtual)
                kind | ref | title | body
                tokenizer: porter unicode61
                EXCLUDES notes where visibility='private'
                      │
                      ▼
              services.search.run(q) → ranked results
```

Triggers are the contract: if you add a new entity that should appear
in search, you add triggers in the migration that creates it.

---

## 10. Migrations — schema evolution

```
   on startup (server.py or setup.py):
       │
       ▼
   migrations.run_all(conn)
       │
       ├─ read schema_versions to find last applied version
       ├─ for each file in migrations/*.sql in order:
       │    if version > last applied:
       │       execute SQL
       │       INSERT into schema_versions
       │
       └─ COMMIT after each migration; halt on any failure
```

Migrations are append-only files: `migrations/0002_...sql`,
`migrations/0003_...sql`, etc. The number is the version. Never edit a
migration that has been deployed — write a new one.

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](README.md) — human entry point
- [AGENTS.md](AGENTS.md) — AI agent operating contract
- [CLAUDE.md](CLAUDE.md) — Claude Code project conventions
- [SCHEMATICS.md](SCHEMATICS.md) **← you are here**
- [Blueprint.md](Blueprint.md) — product spec
- [prompt.md](prompt.md) — build-from-scratch prompt

**Wiki root** (`docs/`)
- [README.md](docs/README.md) — wiki index
- [00-start-here.md](docs/00-start-here.md) — 10-minute orientation

**01 — Concepts** (`docs/01-concepts/`) — why each piece exists
- [service-layer.md](docs/01-concepts/service-layer.md)
- [service-context.md](docs/01-concepts/service-context.md)
- [audit-and-webhooks.md](docs/01-concepts/audit-and-webhooks.md)
- [plugins.md](docs/01-concepts/plugins.md)
- [scoring.md](docs/01-concepts/scoring.md)
- [segments.md](docs/01-concepts/segments.md)
- [portals.md](docs/01-concepts/portals.md)
- [inbound.md](docs/01-concepts/inbound.md)
- [search.md](docs/01-concepts/search.md)

**02 — Guides** (`docs/02-guides/`) — step-by-step how-tos
- [install.md](docs/02-guides/install.md)
- [first-contact.md](docs/02-guides/first-contact.md)
- [your-first-pipeline.md](docs/02-guides/your-first-pipeline.md)
- [import-export.md](docs/02-guides/import-export.md)
- [deploying.md](docs/02-guides/deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](docs/03-reference/data-model.md)
- [api.md](docs/03-reference/api.md)
- [cli.md](docs/03-reference/cli.md)
- [mcp.md](docs/03-reference/mcp.md)
- [plugins.md](docs/03-reference/plugins.md)
- [webhooks.md](docs/03-reference/webhooks.md)
- [errors.md](docs/03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](docs/04-recipes/lead-intake.md)
- [dormant-revival.md](docs/04-recipes/dormant-revival.md)
- [agent-workflows.md](docs/04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](docs/05-operations/backup-restore.md)
- [migrations.md](docs/05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](docs/06-development/adding-an-entity.md)
- [writing-a-plugin.md](docs/06-development/writing-a-plugin.md)
- [writing-a-skill.md](docs/06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](docs/07-troubleshooting/error-codes.md)
