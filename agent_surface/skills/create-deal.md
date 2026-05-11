---
verb: create
noun: deal
canonical_transport: rest
mcp_tool: create_deal
cli: deal create
rest: POST /api/deals
required_scope: write
related: ["move-deal-stage", "create-pipeline"]
---

# Create a deal

Creates an open opportunity in a pipeline stage. Tied to a contact
and/or a company.

## Required fields
- `title` (string)
- `pipeline_id` (integer)
- `stage_id` (integer)

## Optional fields
- `contact_id` (integer) — strongly recommended
- `company_id` (integer)
- `value_cents` (integer; e.g., 1800000 = $18,000)
- `currency` (lowercase ISO, e.g., "cad")
- `probability` (0..100)
- `expected_close` (unix seconds)
- `next_step` (string)
- `notes` (string)
- `assigned_to` (user id)

## Default behavior

- Stage with `is_won=1` auto-sets `status="won"`, `closed_at=now`.
- Stage with `is_lost=1` auto-sets `status="lost"`, `closed_at=now`.
- Otherwise `status="open"`.

## Example (REST)

```bash
curl -sX POST $BASE/api/deals \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: deal-acme-rebrand-2026-05-11" \
  -d '{
    "title":      "Acme cafe rebrand",
    "pipeline_id":1, "stage_id":2,
    "contact_id": 5, "company_id":3,
    "value_cents":1800000, "currency":"cad",
    "probability":40
  }'
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | required field missing/wrong type |
| `PIPELINE_NOT_FOUND` | pipeline_id doesn't exist |
| `PIPELINE_STAGE_NOT_FOUND` | stage doesn't belong to pipeline |
| `PIPELINE_ARCHIVED` | tried to create on an archived pipeline |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="deal.created"`
- Webhook: `deal.created` with `{"deal": {...}}`
- Plug-in hook: `on_deal_created(ctx, deal, conn)`
