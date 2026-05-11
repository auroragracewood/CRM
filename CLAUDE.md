# CLAUDE.md — guide for AI agents working ON this codebase

If you are coding *on* the CRM repo (not using it via MCP), this is your orientation.

## The product, in one line

A self-hosted, single-company customer relations management application whose every action is callable through UI, REST, MCP, CLI, skills, webhooks, and cron. No LLM inside; the agent is external.

## Architecture rule that matters more than any other

**Surfaces are transports, not features.** Every action lives in `backend/services/*.py`. REST, CLI, MCP, and UI all dispatch through services. If you find yourself writing the same validation in `api.py` and `cli.py`, you have broken the rule — move it into the service.

## Build and test

```bash
python -m pip install -r requirements.txt
python setup.py --non-interactive --admin-email a@b.c --admin-password test1234
python -m tests.test_milestone1
python server.py    # browse http://127.0.0.1:8765/
```

The Milestone 1 acceptance script verifies the architecture: same contact created through service, REST (with bearer key), CLI, and MCP fallback, all writing audit_log rows tagged with their surface, plus webhook outbox enqueue.

## Stack and patterns

- **FastAPI + SQLite + vanilla HTML/JS templates.** No Docker, no Postgres, no React, no build step.
- **HTML rendered by `backend/main.py`** via `.replace("{{placeholder}}", value)` against files in `ui/`.
- **ServiceContext is the first arg of every service call.** Built once at the transport boundary (REST in `api.py:build_context`, UI in `main.py:_ctx_from_session`, CLI in `cli.py:_ctx`, MCP in `mcp_server.py:_ctx`). Services never reach into HTTP/CLI/MCP request objects.

## Database

- 15 tables at v0 install (defined in `schema.sql`). v1 adds 6 more via migrations (not yet implemented; the migration runner is in `backend/db.py`).
- Every connection opens with `PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;`.
- Soft-delete on `contacts` and `companies` only. Others hard-delete.
- Partial unique index on `contacts.email` for active rows so soft-deleted contacts free up their email.
- `interactions.type` is a string enum, not a separate table. Adding values = schema-doc update + service check, not a migration.

## Audit and webhooks

- **Every service-layer mutation** writes a row to `audit_log` and (when relevant) enqueues a `webhook_events` row INSIDE the same transaction.
- Webhook delivery happens AFTER commit, via the background dispatcher in `backend/main.py:_dispatcher_loop`. Failures never roll back the original mutation.
- Headers required on every webhook delivery: `X-CRM-Event`, `X-CRM-Timestamp`, `X-CRM-Signature` (HMAC-SHA256), `X-CRM-Delivery-ID`.

## Adding a new entity (e.g., "products")

Follow the contacts pattern in order. Don't skip steps.

1. Add table(s) to `schema.sql` + write a migration if the install is already in the wild.
2. Add `backend/services/products.py` with `create / get / list_ / update / delete`, each taking `ctx` first, writing audit, enqueuing webhooks.
3. Add REST endpoints in `backend/api.py` (thin — dispatch to service).
4. Add CLI commands in `agent_surface/cli.py`.
5. Add MCP tools in `agent_surface/mcp_server.py` (both FastMCP path and fallback `_do`).
6. Add a skill markdown file in `agent_surface/skills/`.
7. Add UI pages in `ui/` + routes in `backend/main.py`.
8. Extend the acceptance test if it's a load-bearing flow.
9. Document in `docs/api.md`, `docs/cli.md`, `docs/mcp.md` (those land as docs work catches up).

## Mandatory non-goals (will be reverted)

- Multi-tenant / parent-entity / subsidiary logic in core
- LLM / provider-specific imports (no `openai`, no `anthropic` in core)
- Docker, build steps, frontend frameworks requiring a build
- Business logic inside transports (REST/CLI/MCP/UI route functions)
- Creative/agent behavior in CRM code ("summarize this contact," "suggest a follow-up" etc.)

## Where the spec lives

`Blueprint.md` and `prompt.md` in the repo root describe the architectural intent. Read them when in doubt about scope.
