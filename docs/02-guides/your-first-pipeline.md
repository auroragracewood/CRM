# Guide · Your first pipeline

> Create a sales pipeline, attach a deal to one of your contacts, and
> move it through stages. Watch how stage transitions cascade through
> audit, webhooks, plug-in hooks, and scoring.

## Context

Pipelines and deals are how the CRM tracks "active opportunities" —
work-in-progress that will end as won, lost, or nurture. They are
NOT a free-form Kanban board; they have opinionated invariants:

- A deal belongs to exactly one pipeline.
- A pipeline has ordered stages with `is_won` / `is_lost` flags.
- Moving a deal to a `is_won` stage auto-flips the deal's `status`
  to `won` and stamps `closed_at`.
- The same for `is_lost`.
- Reopening a deal (moving back to an open stage) clears `closed_at`.

This guide walks through creating a pipeline from a built-in
template, creating one deal on it, and moving the deal through the
flow.

## Understanding

Three relevant tables:

```
pipelines           pipeline_stages          deals
  id                  id                       id
  name                pipeline_id → pipelines  pipeline_id → pipelines
  type                name                     stage_id    → pipeline_stages
  description         position (order)         contact_id  → contacts (SET NULL)
  archived            is_won (bool)            company_id  → companies
                      is_lost (bool)           title
                                               value_cents, currency, probability
                                               status (open|won|lost|nurture)
                                               expected_close, next_step
                                               assigned_to → users
                                               closed_at
                                               notes
```

Three relevant services:

- `pipelines.create_from_template(ctx, name, template)` — spins up a
  pipeline with sensible stages.
- `deals.create(ctx, payload)` — creates a deal in stage[0] by default
  or a specified stage.
- `deals.move(ctx, deal_id, stage_id)` — moves a deal; handles the
  won/lost auto-status logic.

Built-in templates:

- `sales` — 6 stages: New → Qualified → Proposal → Negotiation → Won → Lost
- `client` — 4 stages: Lead → Active client → On hold → Churned
- `sponsor` — 4 stages: Outreach → Pitch → Confirmed → Declined

You can also build pipelines from scratch via `pipelines.create` +
`pipelines.add_stage`.

## Reason

**Why force won/lost flags on stages?**

Because "the deal closed" is a critical signal that downstream
consumers (reports, scoring, plug-ins) need to detect reliably.
Without the flag, every consumer would need to maintain a list of
"which stage names mean won?" — a recipe for drift. With the flag,
moving to a won stage is unambiguous everywhere.

**Why does stage transition auto-flip status?**

To prevent the inconsistent state "stage=Won but status=open." A
user moving a card UI-side never has to remember "also flip the
status field." The model enforces the invariant.

**Why templates and not just empty pipelines?**

Most teams want sales/client/sponsor flows that look 90% like every
other team's. Shipping the 90% as templates removes the "what should
my stages be?" decision from setup.

**Why is `status='nurture'` a thing?**

It's "open but parked." Useful for prospects who said "not now,
ping in Q3" — you want them out of the active pipeline view but not
marked lost. The pipeline page filters `status='open'` by default
and shows `nurture` deals on opt-in.

## Result

After this guide you'll have:

- A pipeline named "Q4 Sales" with the 6 standard sales stages.
- One deal on the pipeline tied to a real contact.
- Practice moving the deal between stages, including into and out
  of `won`.
- A clear picture of how stage transitions trigger audit, webhooks,
  and plug-in hooks.

## Use case — a Q4 sales pipeline with one deal

Assumptions: you have at least one contact (from
[first-contact](first-contact.md)). Suppose her id is `2`.

### 1. Create the pipeline from a template

UI: Pipelines → New pipeline → From template → Sales.

CLI:
```bash
python -m agent_surface.cli pipeline from-template \
  --name "Q4 Sales" --template sales
# {"ok":true,"pipeline":{"id":1,"stages":[
#   {"id":1,"name":"New","position":1},
#   {"id":2,"name":"Qualified","position":2},
#   {"id":3,"name":"Proposal","position":3},
#   {"id":4,"name":"Negotiation","position":4},
#   {"id":5,"name":"Won","position":5,"is_won":1},
#   {"id":6,"name":"Lost","position":6,"is_lost":1}
# ]}}
```

REST:
```bash
curl -sX POST http://localhost:8000/api/pipelines/from-template \
  -H "Authorization: Bearer $KEY" \
  -d '{"name":"Q4 Sales","template":"sales"}'
```

Note the pipeline's id and stage ids.

### 2. Create a deal

UI: Pipelines → Q4 Sales → New deal. Fill in title, contact, value.

CLI:
```bash
python -m agent_surface.cli deal create \
  --title "Acme cafe rebrand" \
  --pipeline-id 1 --stage-id 1 \
  --contact-id 2 \
  --value-cents 1800000 --currency cad --probability 30
# {"ok":true,"deal":{"id":1,"status":"open","closed_at":null,...}}
```

REST:
```bash
curl -sX POST http://localhost:8000/api/deals \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "title":"Acme cafe rebrand",
    "pipeline_id":1, "stage_id":1,
    "contact_id":2,
    "value_cents":1800000, "currency":"cad",
    "probability":30
  }'
```

### 3. Move the deal through stages

UI: Pipelines → Q4 Sales → drag the deal card from New to Qualified
to Proposal.

CLI:
```bash
python -m agent_surface.cli deal update --id 1 --stage-id 2
python -m agent_surface.cli deal update --id 1 --stage-id 3
```

REST: same via `PUT /api/deals/1` with `{"stage_id": 3}`.

Each move:
- Updates `deals.stage_id`.
- Writes one audit row `action='deal.stage_moved'`.
- Enqueues a `deal.stage_changed` webhook event.
- Dispatches the `on_deal_stage_changed(ctx, deal_before,
  deal_after, conn)` plug-in hook.
- Calls `scoring.maybe_recompute` for the deal's contact (the
  `opportunity` score reads stage position).

### 4. Move the deal to Won

```bash
python -m agent_surface.cli deal update --id 1 --stage-id 5
```

Behind the scenes:
- `deals.update` notices stage 5 has `is_won=1`.
- Flips `status='won'` and stamps `closed_at = now`.
- Audit row contains the full before/after JSON (you can see the
  status flip).
- Webhook event `deal.won` is enqueued (separate from
  `deal.stage_changed`).
- Plug-in hook `on_deal_won(ctx, deal, conn)` dispatches.

### 5. (Optional) Reopen by moving back

```bash
python -m agent_surface.cli deal update --id 1 --stage-id 4
```

- Status flips back to `open`.
- `closed_at` becomes `NULL`.
- Audit row records the un-close.
- Webhook event `deal.reopened` is enqueued.

### 6. Inspect

UI: Pipelines page shows a board view with cards per stage.

CLI:
```bash
python -m agent_surface.cli deal list --pipeline-id 1
python -m agent_surface.cli deal get --id 1
python -m agent_surface.cli report run --name pipeline_overview
```

## Operations

### Adding a stage to an existing pipeline

You CAN add stages to an existing pipeline mid-flight:

```bash
python -m agent_surface.cli pipeline add-stage \
  --pipeline-id 1 --name "Internal review" --position 4
```

Existing deals retain their stage. The new stage appears at position
4; existing stages at position >= 4 get bumped.

DO NOT renumber positions manually — let the service handle it.

### Renaming stages

```bash
# via REST
curl -sX PUT http://localhost:8000/api/pipelines/1/stages/2 \
  -H "Authorization: Bearer $KEY" -d '{"name":"Qualified lead"}'
```

Audit row preserves the old name in `before_json`.

### Archiving a pipeline

```bash
python -m agent_surface.cli pipeline archive --id 1
```

The pipeline is hidden from default lists. Open deals stay open
(they're not auto-lost). Archive is reversible.

### Deals without contacts

You can create a deal with no `contact_id` if you're tracking a
company-level opportunity. Pass `--company-id` only. Most reports
(intent, opportunity) require a contact; deal-only metrics still
work.

### Re-assigning a deal

```bash
python -m agent_surface.cli deal update --id 1 --assigned-to 3
```

Audit row records the assignment change. Plug-in
`on_deal_assigned(ctx, deal_before, deal_after, conn)` fires if
implemented (planned hook).

## Fine-tuning

### Custom pipeline types

Templates are dicts in `services/pipelines.py:TEMPLATES`. Add yours:

```python
TEMPLATES["fundraising"] = [
    {"name": "Identified", "position": 1},
    {"name": "Cultivated", "position": 2},
    {"name": "Asked",      "position": 3},
    {"name": "Pledged",    "position": 4, "is_won": True},
    {"name": "Declined",   "position": 5, "is_lost": True},
]
```

Now `pipeline from-template --template fundraising` works.

### Stage probabilities

The `probability` field on deals is independent of stage by default.
You can opt into auto-population: when moving to a stage, set the
deal's probability to a stage-specific value. Add to
`pipeline_stages` a `default_probability` column and update
`deals.move` to pick it up unless the caller overrides.

### Pipeline-specific report widgets

Reports can take a `pipeline_id` param:

```bash
python -m agent_surface.cli report run \
  --name pipeline_overview --params '{"pipeline_id":1}'
```

Returns aggregates only for that pipeline. Pin the result as a
dashboard widget for the team that owns the pipeline.

### Multi-pipeline deals

Not supported. A deal belongs to one pipeline. If you need to track
the same opportunity in multiple flows (e.g., sales + onboarding),
create two related deals and link them by tag (`"deal-pair:42"`) or
add a `parent_deal_id` column.

### Auto-task creation on stage transitions

A common plug-in pattern: when a deal hits "Proposal", auto-create
a task "Send proposal draft" with a 2-day due date.

```python
def on_deal_stage_changed(ctx, before, after, conn):
    target_stage = "Proposal"
    if _stage_name(after, conn) == target_stage:
        tasks.create(ctx, {
            "title":      "Send proposal draft",
            "deal_id":    after["id"],
            "contact_id": after.get("contact_id"),
            "priority":   "high",
            "due_date":   int(time.time()) + 2*86400,
        }, conn=conn)
```

## Maximizing potential

1. **Won-rate analytics.** Group `deal.won` audit rows by month
   and pipeline. Compute `won_count / (won_count + lost_count)`.
   The CRM's pipeline data IS your sales analytics.

2. **Velocity reports.** For each won deal, look up `stage_moved`
   audit rows and compute time-in-stage. Build a "where do deals
   stall?" report. The data is already in the audit log.

3. **Pipeline conversion forecasting.** Multiply value × probability
   for every open deal grouped by stage. The pipeline overview
   report does this; surface it more prominently.

4. **Stage-driven email cadences.** A plug-in
   `on_deal_stage_changed` that, when moving to "Proposal", drafts
   a templated email (or via LLM); when moving to "Negotiation",
   schedules a check-in task; when moving to "Won", sends an
   onboarding portal token. Pipelines become workflow automation.

5. **Stage gates.** Some teams want "you can't move to Proposal
   without a contact_id AND a value." Implement as a service-level
   validation in `deals.move`:

   ```python
   if target_stage_name == "Proposal":
       if not deal.get("contact_id") or not deal.get("value_cents"):
           raise ServiceError("DEAL_STAGE_GATE",
                              "Proposal stage requires contact + value")
   ```

6. **Pipeline as a contract.** For client-onboarding pipelines, link
   each stage to a contract milestone. Stage transitions trigger
   external systems (billing, doc generation, portal access changes).
   The CRM is the canonical state machine.

## Anti-patterns

- **Manually setting `status='won'` on a deal in an open stage.**
  Don't. Either move to a won stage (correct) or fix the stage's
  `is_won` flag.
- **Deleting stages with deals in them.** Deals would be orphaned.
  The service refuses; rename or migrate first.
- **Using deals for things that aren't opportunities.** A "deal"
  for tracking a customer's support ticket is a misuse — that's
  what tasks (or a separate `tickets` entity) are for.
- **Renaming pipelines mid-quarter.** Reports group by pipeline
  name in some contexts. Rename creates a discontinuity. Add a
  new pipeline, archive the old one.
- **Stages without won/lost in any pipeline.** Every pipeline
  needs at least one terminal stage (won OR lost) — otherwise
  deals can never close.

## Where to look in code

- `backend/services/pipelines.py` — pipelines + stages
- `backend/services/deals.py` — deals + status auto-flip logic
- `backend/services/reports.py:pipeline_overview` — report fn
- `migrations/0002_v1.sql` — pipelines/stages/deals schema
- `ui/pipelines.html` — board view template

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
- [install.md](install.md)
- [first-contact.md](first-contact.md)
- [your-first-pipeline.md](your-first-pipeline.md) **← you are here**
- [import-export.md](import-export.md)
- [deploying.md](deploying.md)

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
