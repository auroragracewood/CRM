# Dev · Writing a skill

> Skills are the agent-facing documentation for a single action.
> Each skill file teaches an AI agent how to perform one verb on one
> noun: required fields, transports, example payload, common errors.

## Context

Agents need predictability. Reading `docs/03-reference/api.md` gives
them the surface but not the playbook — "to create a contact: use
this endpoint, with these fields, expect these errors." Skills fill
that gap.

A skill file is a markdown document with frontmatter. It's small
(usually <100 lines), action-scoped, and lives in
`agent_surface/skills/`. Agents browse the directory the same way
they browse the wiki — file names are predictable verbs.

The CRM ships with a starter set: `create-contact.md`,
`find-contact.md`, `log-interaction.md`, `add-note.md`,
`tag-contact.md`. This page is how you write more.

## Result

After this guide:

- You know the canonical skill file shape.
- You can write a skill for any new action in ~10 minutes.
- Agents picking up your CRM cold can discover and use it.

## Recipe — a "create-deal" skill

### 1. Naming

File name = `<verb>-<noun>.md`. Examples:

- `create-deal.md`
- `find-deal.md`
- `move-deal-stage.md`
- `issue-portal-token.md`
- `evaluate-segment.md`
- `record-consent.md`
- `recompute-score.md`

Hyphenated, lowercase, action-shaped. The pattern lets agents guess
filenames before listing the directory.

### 2. Frontmatter

```yaml
---
verb: create
noun: deal
canonical_transport: rest
mcp_tool: create_deal
cli: deal create
rest: POST /api/deals
required_scope: write
related: ["move-deal-stage", "find-deal"]
---
```

| field | purpose |
|-------|---------|
| `verb` | the action verb |
| `noun` | the entity acted on |
| `canonical_transport` | which transport an agent should prefer |
| `mcp_tool` | the MCP tool name |
| `cli` | the CLI command path |
| `rest` | the REST method + path |
| `required_scope` | `read` / `write` / `admin` |
| `related` | list of other skill files in this directory |

Optional fields:
- `audit_action` — the action string written to audit_log
- `webhook_events` — list of webhook events fired

### 3. Body

```markdown
# Create a deal

Creates a deal in a pipeline stage. A deal represents an open
opportunity tied to a contact and/or company.

## Required fields
- `title` (string)
- `pipeline_id` (integer)
- `stage_id` (integer)

## Optional fields
- `contact_id` (integer) — strongly recommended
- `company_id` (integer)
- `value_cents` (integer, e.g., 1800000 = $18,000)
- `currency` (lowercase ISO, e.g., "cad")
- `probability` (integer 0..100)
- `expected_close` (unix seconds)
- `next_step` (string)
- `notes` (string)
- `assigned_to` (user id)

## Default behavior

- If `stage_id` references a stage with `is_won=1`, the deal's
  `status` is auto-set to `won` and `closed_at` is stamped.
- Same for `is_lost`.
- Otherwise `status` defaults to `open`.

## Example (REST)

```bash
curl -sX POST $BASE/api/deals \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: deal-acme-rebrand-2026-05-11" \
  -d '{
    "title":      "Acme cafe rebrand",
    "pipeline_id":1,
    "stage_id":   2,
    "contact_id": 5,
    "company_id": 3,
    "value_cents":1800000,
    "currency":   "cad",
    "probability":40
  }'
```

## Example (CLI)

```bash
python -m agent_surface.cli deal create \
  --title "Acme cafe rebrand" \
  --pipeline-id 1 --stage-id 2 \
  --contact-id 5 --company-id 3 \
  --value-cents 1800000 --currency cad --probability 40
```

## Example (MCP)

```python
create_deal(
    title="Acme cafe rebrand",
    pipeline_id=1, stage_id=2,
    contact_id=5, company_id=3,
    value_cents=1_800_000, currency="cad", probability=40,
)
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | required field missing or wrong type |
| `PIPELINE_NOT_FOUND` | pipeline_id doesn't exist |
| `PIPELINE_STAGE_NOT_FOUND` | stage_id doesn't belong to the pipeline |
| `PIPELINE_ARCHIVED` | tried to create on an archived pipeline |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit row: `action="deal.created"`, `object_type="deal"`.
- Webhook event: `deal.created` with `{"deal": {...}}`.
- Plug-in hook: `on_deal_created(ctx, deal, conn)`.

## When NOT to use this skill

- For an EXISTING deal whose pipeline_id you just want to change:
  use `move-deal-stage` instead.
- For a one-off opportunity that's not really pipeline-tracked: use
  a tag (`tag-contact`) + interaction (`log-interaction`).
```

That's it. Save as `agent_surface/skills/create-deal.md`.

## Operations

### Discovery

An agent lists `agent_surface/skills/` and grep for the verb-noun
combination it needs. There's no central index file — the filenames
are the index.

### Updating

Whenever the corresponding service function changes (new field,
new error, changed default), update the skill in the same commit.
Stale skills mislead agents into wrong API calls.

### Linting

A small CI check: parse each skill's frontmatter, verify the
`mcp_tool`, `cli`, `rest` references exist in the code. Catches
drift early.

## Fine-tuning

### Composite skills

Some agent workflows need multiple service calls. You can write a
"recipe-style" skill that walks through the chain:

```markdown
---
verb: onboard
noun: client
canonical_transport: rest
composite: true
calls: ["create-company", "create-contact", "log-interaction",
        "create-deal", "create-task", "issue-portal-token"]
---

# Onboard a client (composite)

...
```

For predictable end-to-end flows, this lets agents execute one
narrative instead of reasoning about the chain.

### Skill vs concept doc

A skill is one action. A concept doc explains a SYSTEM. Don't write
a "tagging" skill that's really about tags as a concept — that's
already in `01-concepts/`. Write `attach-tag-to-contact`,
`detach-tag-from-contact`, `create-tag`.

### LLM-readability

Avoid heavy markdown structure. Plain headers, lists, fenced code.
The skill file is going to be embedded in a prompt; weird formatting
confuses some models.

### Idempotency hints

Every write skill should mention idempotency-key usage. Many agent
bugs trace to "I tried to do X twice, got an EXISTS error, and got
confused." A clear "use Idempotency-Key" note prevents this.

## Maximizing potential

1. **Skill-driven agent training.** New agents read all skills as
   a system prompt section. Their action space is bounded by the
   skill catalog. They behave predictably.

2. **Skill stubs from service signatures.** A small codegen tool
   reads each service function's docstring + signature and emits a
   skill markdown stub. Maintain by hand from there, but the
   scaffolding is automatic.

3. **Per-install skill overrides.** A `skills-local/` directory the
   agents check first, then fall back to `skills/`. Customer-
   specific actions don't pollute the open-source repo.

4. **Skill discoverability.** A `GET /api/skills` endpoint that
   returns the catalog (filenames + frontmatter). Agents that prefer
   API to filesystem can call it.

5. **Skill-aware AGENTS.md.** Cross-link the top of `AGENTS.md` to
   the skills directory: "before you act, find the skill for your
   intended action." Reduces hallucinated API calls.

6. **Recipe skills with branching logic.** "If the contact exists,
   call X. Otherwise call Y." Agents follow the branch. Encodes
   playbooks declaratively.

## Anti-patterns

- **Stale skill files.** If the service function changes, skill
  must change. Treat skill drift like audit drift — a bug.
- **Skills that drift from the API.** A skill saying "field X is
  required" when the API treats X as optional is worse than no
  skill. Agents will fail validation needlessly.
- **Skills with no examples.** The example is the most useful part.
  No example = useless skill.
- **Repeating concept docs in skills.** Skills are action-scoped.
  Link to concepts for theory; don't duplicate.
- **Markdown table soup.** Long tables in skills confuse some LLMs.
  Use bullet lists for fields, tables only for error codes.

## Where to look in code / repo

- `agent_surface/skills/` — the directory
- `agent_surface/skills/create-contact.md` — reference template
- `agent_surface/skills/find-contact.md` — for read actions
- `agent_surface/skills/log-interaction.md` — for activity actions

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
- [writing-a-plugin.md](writing-a-plugin.md)
- [writing-a-skill.md](writing-a-skill.md) **← you are here**

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
