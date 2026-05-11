---
verb: create
noun: pipeline
canonical_transport: rest
mcp_tool: create_pipeline_from_template
cli: pipeline from-template
rest: POST /api/pipelines/from-template
required_scope: write
related: ["create-deal"]
---

# Create a pipeline

Spins up a pipeline with stages. Use a built-in template for the
common shapes; build custom by calling `POST /api/pipelines`
followed by `POST /api/pipelines/{id}/stages` per stage.

## Built-in templates

| template | stages |
|----------|--------|
| `sales`   | New → Qualified → Proposal → Negotiation → **Won** → **Lost** |
| `client`  | Lead → Active client → On hold → **Churned** |
| `sponsor` | Outreach → Pitch → **Confirmed** → **Declined** |

Bolded stages have `is_won=1` or `is_lost=1`.

## Required fields (from-template)
- `name` (string)
- `template` (one of: `sales`, `client`, `sponsor`)

## Example (REST)

```bash
curl -sX POST $BASE/api/pipelines/from-template \
  -H "Authorization: Bearer $KEY" \
  -d '{"name":"Q4 Sales","template":"sales"}'
```

## Example (CLI)

```bash
python -m agent_surface.cli pipeline from-template \
  --name "Q4 Sales" --template sales
```

## Custom pipeline

```bash
curl -sX POST $BASE/api/pipelines \
  -H "Authorization: Bearer $KEY" \
  -d '{"name":"Inbound Leads","type":"sales","description":"..."}'
# returns pipeline id

curl -sX POST $BASE/api/pipelines/<id>/stages \
  -H "Authorization: Bearer $KEY" \
  -d '{"name":"New","position":1}'
# repeat for each stage
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | name/template missing or unknown template |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="pipeline.created"` + one `pipeline.stage_added`
  per stage
- Webhook: `pipeline.created`
- No plug-in hook for pipeline creation by default
