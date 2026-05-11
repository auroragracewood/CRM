# Concept · ServiceContext

> Identity in flight. The dataclass that travels with every service-
> layer call and answers "who is doing this, with what permission,
> from where, as part of which request."

## Context

A CRM is many things, but at the core it's an authorization machine.
Every read, every write, every export, every webhook is gated by "who
asked for this and what are they allowed to do?"

A naive implementation passes `user_id` around as an integer. That
works for a single transport, but breaks the moment you need:

- An audit log that records WHICH SURFACE the user acted from (UI vs
  API vs CLI).
- A scope distinction between an admin user acting through a `read`-
  scope API key (they're still admin, but THIS call can't write).
- A way to correlate dozens of writes that all came from one logical
  agent run.
- Plug-ins that need to run as "the user who triggered me" not "a
  generic system user."

`ServiceContext` is the single dataclass that carries enough metadata
for all of this.

## Understanding

```python
@dataclass
class ServiceContext:
    user_id: int            # WHO is acting (always set)
    role: str               # admin | user | readonly  (user's stored role)
    scope: str              # read | write | admin     (this call's permission)
    surface: str            # ui | rest | cli | mcp | cron | plugin | webhook | system
    api_key_id: int | None  # which key, if REST/MCP
    request_id: str         # UUID; correlates one logical request

    def can_read(self):  return self.scope in {"read","write","admin"}
    def can_write(self): return self.scope in {"write","admin"}
    def can_admin(self): return self.scope == "admin"
```

Every service function takes `ctx` as the FIRST argument. Every audit
row reads `ctx.user_id`, `ctx.api_key_id`, `ctx.surface`,
`ctx.request_id`. Every scope check calls `ctx.can_*()`.

The six fields encode three distinct dimensions:

1. **Identity** — `user_id`, `api_key_id` — who is acting and via
   which credential.
2. **Authorization** — `role`, `scope` — what they are vs what this
   specific call allows.
3. **Provenance** — `surface`, `request_id` — where the call came from
   and which logical request it belongs to.

## Reason

**Why this exact shape?**

- **`role` vs `scope` are deliberately separate.** A user with
  `role="admin"` might be calling via a `scope="read"` key. The user
  is still admin in the org; but THIS call is read-only. Mixing the
  two would either give read-only keys admin power (bad) or block
  admin actions on admin users using narrow keys (annoying).

- **`surface` is recorded, not inferred.** When you see in the audit
  log that a contact was deleted from `surface=cli`, you know an
  operator did it locally — different escalation path than `surface=
  rest` (probably an automation) or `surface=plugin` (probably a
  reaction to another event).

- **`request_id` is the join key.** A single agent action ("import
  these 50 leads") produces ~150 audit rows. With one stable
  request_id you reconstruct the whole operation with one SELECT. The
  caller chooses the id, or the transport generates one and echoes it
  back in the response — either way it's stable across the call.

- **`api_key_id` is separately stored.** When you revoke a key, you
  want to find every write that key did — `WHERE api_key_id = ?`
  vs `WHERE user_id = ?` (which would include the user's UI sessions
  and CLI work). The two are tracked independently.

- **Helpers (`can_read`, etc.) are methods, not free functions.**
  Because every check is `ctx.can_write()` not `auth.can_write(ctx)`,
  forgetting them is harder. The pattern reads like English in service
  functions.

## Result

What you get from `ServiceContext`:

- A scope check at the top of every service function that is one line
  and impossible to forget without a code review noticing it.
- An audit log that is queryable across every dimension you care about
  — who, what, when, where, via which credential, as part of which
  request.
- Plug-in code that doesn't need to "know" anything about the calling
  user — it receives the same `ctx` and can call other services as
  that user, with inherited permissions.
- API keys you can revoke surgically without affecting cookie sessions
  or CLI work for the same user.
- A clean integration point for OpenTelemetry / structured logging:
  every log line can read `ctx.request_id` to thread one operation
  through the logs.

## Use case — what flows through one call

A REST POST to `/api/contacts` arrives:

```
1. middleware: pull Authorization header → look up api_keys row
2. middleware: build ServiceContext(
      user_id     = 7,
      api_key_id  = 12,
      role        = "user",
      scope       = "write",     ← from the key's scope
      surface     = "rest",
      request_id  = hdr("X-Request-Id") or uuid4(),
   )
3. handler: contacts.create(ctx, payload)
   ├── service: ctx.can_write() → True
   ├── service: writes contacts row
   ├── service: audit.log(conn, ctx, action="contact.created", ...)
   │       audit row gets ctx.user_id, api_key_id, surface, request_id
   ├── service: webhooks.enqueue(conn, "contact.created", {...})
   └── service: plugins.dispatch("on_contact_created", ctx, ...)
       └── plug-in: tags.attach(ctx, ...)
                    ↑ plug-in passes the SAME ctx so the tag attach
                      shows in audit as "done by user 7 via REST" too
4. handler: returns 201 with X-Request-Id echoed back
```

When the user reports "I created a contact and the tag didn't get
attached", you run:

```sql
SELECT * FROM audit_log WHERE request_id = 'echoed-value' ORDER BY ts;
```

— and see the full chain, including any plug-in errors.

## Operations

**Practical operational use of ServiceContext:**

- **Run the same code as a different user.** From a Python REPL on
  the server:
  ```python
  from backend.context import ServiceContext
  from backend.services import contacts
  ctx = ServiceContext(user_id=1, role="admin", scope="admin",
                       surface="system", request_id="repl-investigation")
  print(contacts.list_(ctx, q="acme"))
  ```
  The audit log marks these as `surface=system` so you can spot them.

- **Find every action an API key did before you revoke it.**
  ```sql
  SELECT action, object_type, object_id, ts
  FROM audit_log
  WHERE api_key_id = 12
  ORDER BY ts DESC LIMIT 100;
  ```

- **Distinguish agent traffic from human traffic.** API keys have
  scopes; cookie sessions don't (they derive scope from user role).
  In dashboards, count audit rows grouped by `surface` to see what
  share of activity is humans (`ui`) vs agents (`rest`/`mcp`/`cli`).

- **Sample 1% of all UI mutations** for a UX research log:
  ```sql
  SELECT request_id, action, ts FROM audit_log
  WHERE surface='ui' AND (id % 100) = 0;
  ```

- **Cron jobs run as `system_context()`** — `user_id=1` (the first
  admin), `surface="cron"`. If you want a separate audit identity
  for automation, create a dedicated user (e.g., `automation@local`)
  and pass its id to `system_context(user_id=N)`.

## Fine-tuning

**Knobs you can turn on `ServiceContext`:**

- **Extend the dataclass.** Add fields like `client_ip`, `user_agent`,
  `tenant_id` (if you fork to multi-tenant), `feature_flags`,
  `dry_run`. Service functions can read them via `ctx.<field>`. Old
  call sites continue to work if you set defaults on the new fields.

- **Custom scopes.** The `scope` field is a string — `read`, `write`,
  `admin` are conventions, not enums. Add `audit_admin`,
  `consent_admin`, `plugin_admin` for narrower power. Update
  `can_*` helpers and the `_STATUS` map in `api.py`.

- **Make `request_id` mandatory on the wire.** Currently the REST
  transport defaults to `uuid4()` when no `X-Request-Id` header is
  set. You can make absence a 400 error in CI/staging to force
  agents to thread their own ids.

- **Per-surface defaults.** Add `system_context(surface=…)` variants
  so cron, migrations, and seed scripts get clearly-labeled
  `surface` values.

- **Add `effective_role` for impersonation.** If you build admin
  impersonation later, you want `role="admin"` (the actual admin)
  and `effective_role="user"` (the impersonated identity). The
  audit log gets both fields.

## Maximizing potential

1. **Treat `request_id` as a first-class trace id.** Pass it to every
   plug-in that calls external APIs (Anthropic, OpenAI, Slack) so the
   external system's logs can be joined back. The auto-tag plug-in
   already does this — see how it passes `request_id` into the
   Claude API call as a header. Result: end-to-end debugging across
   the CRM + your LLM provider's logs.

2. **Build a "ctx middleware chain" for cross-cutting features.**
   Rate-limit by `api_key_id`. Inject feature flags by `user_id`.
   Reject writes during maintenance windows by `scope`. All as
   composable middleware that runs in transport-build-ctx, before
   the service call. The service never knows; the ctx tells it
   what's allowed.

3. **Per-scope quotas.** `read` is cheap, `write` is medium,
   `admin` is expensive (e.g., bulk recompute). Implement quotas
   keyed on `api_key_id` × `scope`. Service functions check
   `ctx.quota_remaining` and raise a `QUOTA_EXHAUSTED` ServiceError
   if not.

4. **Provenance-aware UI.** Show "last edited by Maya via API" or
   "deleted by you via CLI on 2026-05-09" by joining audit rows with
   users + api_keys + surface. The data is already there; you just
   show it.

5. **Replay request by request_id.** Build a tool that reads all
   audit rows for a request_id and re-emits them as service calls
   in a clean environment. Useful for porting data, training
   environments, or debugging "what would happen if this request ran
   today against current rules?".

## Anti-patterns

- **Trusting payload-supplied identity.** A REST body containing
  `{"user_id": 5}` is just data, not identity. Never use it to write
  `audit_log.user_id`. Always use `ctx.user_id`.

- **Building a fresh ctx inside a plug-in.** Plug-ins receive the
  caller's ctx and should pass it onward when calling other services.
  Building a fresh `ServiceContext(user_id=1, ...)` breaks
  attribution: the audit row says the plug-in user did it, not the
  real user.

- **Mutating ctx mid-call.** ServiceContext is a dataclass and
  technically mutable, but treat it as immutable. If you need a
  variant (e.g., for an admin-only sub-step), build a new ctx via
  `dataclasses.replace(ctx, scope="admin")`.

- **Embedding secrets in ctx.** Never put raw API keys, passwords,
  or session tokens in ctx. The id is enough — the credential
  itself only lives in the transport layer where auth was verified.

## Where to look in code

- `backend/context.py` — dataclass + `system_context()` helper
- `backend/api.py:79-113` — REST builds ctx from header / cookie
- `agent_surface/cli.py:54-79` — CLI resolves user + builds ctx
- `agent_surface/mcp_server.py:48-79` — MCP user resolution
- `backend/audit.py` — reads every ctx field when logging
- `backend/services/contacts.py:78` — example scope check

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
- [service-context.md](service-context.md) **← you are here**
- [audit-and-webhooks.md](audit-and-webhooks.md)
- [plugins.md](plugins.md)
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
