# Plug-ins

Drop-in Python modules that extend the CRM at runtime. Loaded by
`backend/services/plugins.py` from this directory on startup and via the
admin **Plug-ins** page (Settings → Plug-ins → Reload).

## Contract

Each plug-in is a single `.py` file with at minimum:

```python
NAME = "my-plugin"            # required, unique
VERSION = "0.1.0"             # optional
DESCRIPTION = "What it does." # optional
```

And one or more hook functions. The framework discovers them by name from
the `KNOWN_HOOKS` registry in `backend/services/plugins.py`:

| Hook | Signature | Fired when |
| --- | --- | --- |
| `on_contact_created` | `(ctx, contact: dict, conn)` | A contact row is inserted |
| `on_contact_updated` | `(ctx, before, after, conn)` | A contact row changes |
| `on_contact_deleted` | `(ctx, contact: dict, conn)` | Contact is soft-deleted |
| `on_company_created` | `(ctx, company, conn)` | A company is inserted |
| `on_interaction_logged` | `(ctx, interaction, conn)` | Any interaction row appended |
| `on_note_created` | `(ctx, note, conn)` | A note is created |
| `on_deal_created` | `(ctx, deal, conn)` | A deal is created |
| `on_deal_stage_changed` | `(ctx, deal, from_stage, to_stage, conn)` | Deal moves stage |
| `on_task_completed` | `(ctx, task, conn)` | Task status becomes `done` |
| `on_form_submitted` | `(ctx, submission, conn)` | Public form post received |
| `on_inbound_received` | `(ctx, event, conn)` | Inbound webhook received |
| `compute_fit_score` | `(ctx, contact_id) -> (score, evidence) \| None` | Scoring asks for ICP fit |

A plug-in can implement any subset.

## Behavior contract

- Hooks run **synchronously** inside the request that triggered them. Keep
  them fast (no network calls, no long sleeps).
- Hooks share the calling transaction's `conn`. Writes commit with the host
  mutation; raising an exception rolls everything back.
- The host catches plug-in exceptions and writes the traceback to
  `plugins.last_error`. A broken plug-in cannot crash the server.
- For long-running work, queue a `webhook_events` row from the hook and let
  the outbox dispatcher deliver — the outbox already handles retry + failure.

## Registry

`backend/services/plugins.reload_all()` re-scans this directory, loads every
`*.py` file, and upserts a `plugins` row per discovered module. Hooks declared
by the module become live immediately if `enabled=1`.

Enable/disable via the admin UI or `cli.py plugin enable --id N` /
`disable --id N`. Disabled plug-ins remain installed but no hooks fire.

## Files in this directory

- `README.md` — this file.
- `example_fit_score.py` — a working example. Shows the `NAME` + hook
  pattern; demonstrates both a return-value hook (`compute_fit_score`) and
  a side-effect hook (`on_contact_created`).

## What NOT to put here

Plug-ins are **in-process Python**. They have full access to the SQLite
database and the service layer. Don't put third-party untrusted code here.
For cross-system integration, prefer the inbound endpoints (`/in/{slug}`)
or webhook delivery — those have HMAC signatures + audit + retry.
