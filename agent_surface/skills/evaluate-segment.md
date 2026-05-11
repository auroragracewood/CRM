---
verb: evaluate
noun: segment
canonical_transport: rest
mcp_tool: evaluate_segment
cli: segment evaluate
rest: POST /api/segments/{id}/evaluate
required_scope: write
related: ["run-report"]
---

# Re-evaluate a dynamic segment

Re-runs the segment's rule tree against current data and rewrites
`segment_members`. Returns delta counts. Only meaningful for
`type="dynamic"` segments — static segments raise.

## Required fields
- segment id (or slug via `--slug`)

## Example (REST)

```bash
curl -sX POST $BASE/api/segments/3/evaluate \
  -H "Authorization: Bearer $KEY"
# {"ok":true,"added":18,"removed":2,"total":18}
```

## Example (CLI)

```bash
python -m agent_surface.cli segment evaluate --id 3
# or
python -m agent_surface.cli segment evaluate --slug fresh-leads-7d
```

## Common errors

| code | meaning |
|------|---------|
| `SEGMENT_NOT_FOUND` | id/slug doesn't exist |
| `VALIDATION_ERROR` | tried to evaluate a static segment |
| `SEGMENT_RULES_INVALID` | the segment's stored rules are malformed |

## Audit + webhooks

- Audit: `action="segment.evaluated"` with delta in `after_json`
- Webhook: `segment.evaluated`
- Plug-in hook: `on_segment_evaluated(ctx, segment, before, after, conn)`
  (planned)

## When to evaluate

- After bulk changes (CSV import, scoring recompute) that would
  shift many contacts' eligibility.
- On a schedule for segments that drive dashboards.
- On-demand when an agent / human needs fresh membership now.

Default cadence: nightly cron evaluates all dynamic segments.

## Reading members after evaluation

```bash
curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/segments/3/members?limit=200"
```

Returns full contact rows joined with their score data.
