---
verb: record
noun: consent
canonical_transport: rest
mcp_tool: record_consent
cli: consent record
rest: POST /api/consent
required_scope: write
related: ["create-contact"]
---

# Record consent

Records a contact's consent state for a specific channel. Used for
GDPR/CASL-style compliance. The `consent` table has a UNIQUE
constraint on `(contact_id, channel)` — recording overwrites the
existing entry for that pair.

## Required fields
- `contact_id` (integer)
- `channel` (string; e.g., `email`, `phone`, `sms`, `mail`, `in_person`)
- `status` — one of `granted`, `withdrawn`, `unknown`

## Optional fields
- `source` (string; e.g., `manual`, `form:contact-us`, `import`,
  `portal_first_use`)
- `proof` (string; URL or note documenting the consent — may be
  masked in audit if marked sensitive)

## Example (REST)

```bash
curl -sX POST $BASE/api/consent \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "contact_id": 5,
    "channel":    "email",
    "status":     "granted",
    "source":     "manual"
  }'
```

## Example (CLI)

```bash
python -m agent_surface.cli consent record \
  --contact-id 5 --channel email --status granted --source manual
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | channel/status missing or invalid |
| `CONTACT_NOT_FOUND` | contact_id missing |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="consent.granted"` or `"consent.withdrawn"`
- Webhook: same name as audit action
- Plug-in hook: `on_consent_changed(ctx, before, after, conn)`

## When to use which `source`

| source | meaning |
|--------|---------|
| `manual` | Salesperson recorded after a conversation |
| `form:<slug>` | Contact submitted a form that included a consent checkbox |
| `import` | Imported from a CSV that included consent state |
| `portal_first_use` | Inferred from a portal hit |
| `inbound:<endpoint>` | Came from an inbound webhook (e.g., Stripe) |

Always set a `source`; it's load-bearing for compliance audits.
