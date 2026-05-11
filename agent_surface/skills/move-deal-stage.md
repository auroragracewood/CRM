---
verb: move
noun: deal-stage
canonical_transport: rest
mcp_tool: update_deal
cli: deal update
rest: PUT /api/deals/{id}
required_scope: write
related: ["create-deal"]
---

# Move a deal between stages

Moves a deal to a different stage of its pipeline. The status flips
automatically when entering a won or lost stage; clears `closed_at`
when moving back to open.

## Required fields
- deal id (in URL / `--id` / `deal_id`)
- `stage_id` (integer; must belong to the deal's pipeline)

## Optional companion updates
You can pass any other deal fields in the same call:
- `status` (rarely needed; usually inferred from stage)
- `value_cents`, `probability`, `next_step`, `notes`, etc.

## Default behavior

- Moving to a stage with `is_won=1` auto-sets `status="won"`,
  `closed_at=now`.
- Moving to `is_lost=1` auto-sets `status="lost"`, `closed_at=now`.
- Moving back to an open stage clears `closed_at`, sets `status="open"`.

## Example (REST)

```bash
curl -sX PUT $BASE/api/deals/1 \
  -H "Authorization: Bearer $KEY" \
  -d '{"stage_id": 5}'
# may emit BOTH deal.stage_changed AND deal.won
```

## Example (CLI)

```bash
python -m agent_surface.cli deal update --id 1 --stage-id 5
```

## Common errors

| code | meaning |
|------|---------|
| `DEAL_NOT_FOUND` | id missing or soft-deleted |
| `PIPELINE_STAGE_NOT_FOUND` | stage doesn't belong to the deal's pipeline |
| `DEAL_STAGE_GATE` | service-level invariant blocked (e.g., proposal stage needs value) |

## Audit + webhooks

- Audit: `action="deal.stage_moved"`, plus `deal.won`/`deal.lost`/
  `deal.reopened` if applicable
- Webhooks: `deal.stage_changed`, plus `deal.won`/`deal.lost`/
  `deal.reopened` as applicable
- Plug-in hook: `on_deal_stage_changed(ctx, before, after, conn)`,
  plus `on_deal_won` / `on_deal_lost` as applicable
