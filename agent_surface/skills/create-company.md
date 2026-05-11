---
verb: create
noun: company
canonical_transport: rest
mcp_tool: create_company
cli: company create
rest: POST /api/companies
required_scope: write
related: ["create-contact", "find-contact"]
---

# Create a company

Creates a company record. Companies represent organizations contacts
work for. Soft-deleted via `deleted_at`.

## Required fields
- `name` (string)

## Optional fields
- `slug` (URL-safe; auto-generated from name if omitted)
- `website` (string, e.g., "https://acme.coffee")
- `domain` (string, lowercased — used for inbound contact resolution)
- `industry` (string)
- `size` (string)
- `location` (string)
- `description` (string)
- `custom_fields_json` (JSON string)

## Example (REST)

```bash
curl -sX POST $BASE/api/companies \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "name":      "Acme Roastery",
    "domain":    "acme.coffee",
    "industry":  "food & beverage",
    "location":  "Vancouver, BC"
  }'
```

## Example (CLI)

```bash
python -m agent_surface.cli company create \
  --name "Acme Roastery" --domain "acme.coffee" \
  --industry "food & beverage" --location "Vancouver, BC"
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | name missing |
| `COMPANY_SLUG_EXISTS` | slug already in use |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="company.created"`
- Webhook: `company.created` with `{"company": {...}}`
- Plug-in hook: `on_company_created(ctx, company, conn)`
