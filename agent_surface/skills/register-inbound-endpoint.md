---
verb: register
noun: inbound-endpoint
canonical_transport: rest
mcp_tool: create_inbound_endpoint
cli: inbound create
rest: POST /api/inbound-endpoints
required_scope: admin
related: ["build-form"]
---

# Register an inbound endpoint

Creates a public POST receiver at `/in/{slug}`. External systems
(Stripe, n8n, Calendly, your own scripts) sign their requests with
the shared secret and POST events; the CRM verifies, routes, and
logs.

## Required fields
- `slug` (string; URL-safe, unique)
- `name` (string; human-readable)

## Optional fields
- `routing` (JSON; parse + tag rules — see below)
- `signature_scheme` — `simple` (default) | `stripe-v1`
- `active` (boolean; default true)

## Routing rule shape

```json
{
  "match_event_field":   "type",
  "match_event_value":   "customer.created",
  "extract": {
    "email":        "data.object.email",
    "full_name":    "data.object.name",
    "external_id":  "data.object.id"
  },
  "auto_create_contact": true,
  "match_by_email":      true,
  "tag_with":            ["source:stripe", "stripe_customer"],
  "interaction": {
    "type":  "system",
    "title": "Stripe customer created",
    "body":  "Stripe customer {{external_id}} ({{email}}) created."
  }
}
```

All routing fields are optional. With no rules, the receiver just
logs raw events and verifies signatures.

## Example (REST)

```bash
curl -sX POST $BASE/api/inbound-endpoints \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{
    "slug":   "stripe",
    "name":   "Stripe events",
    "routing": {...},
    "signature_scheme": "stripe-v1"
  }'
# response includes shared_secret ONCE — save it on the sender side
```

## Example (CLI)

```bash
python -m agent_surface.cli inbound create \
  --slug stripe --name "Stripe events" \
  --signature-scheme stripe-v1 \
  --routing-file routing.json
```

## Common errors

| code | meaning |
|------|---------|
| `INBOUND_SLUG_EXISTS` | slug already in use |
| `VALIDATION_ERROR` | routing malformed |
| `FORBIDDEN` | non-admin caller |

## Audit + webhooks

- Audit: `action="inbound_endpoint.created"`
- Webhook: no event for endpoint creation by default
- Per inbound event: `action="inbound.received"` audit + webhook
  with the parsed contact/interaction ids

## Signature verification

Senders include `X-Signature: sha256=<hex>` (for `simple` scheme).
The hex is `hmac.sha256(shared_secret, raw_body).hexdigest()`.

For `stripe-v1`, the header is Stripe's `Stripe-Signature: t=...,
v1=...` format.

## Receiving (sender side)

```bash
curl -sX POST $BASE/in/stripe \
  -H "X-Signature: sha256=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1)" \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

## Inspecting events

```bash
python -m agent_surface.cli inbound events --id <endpoint_id> --limit 50
```

Shows raw body, signature status, parse status, contact link.

## Rotating the secret

```bash
python -m agent_surface.cli inbound rotate-secret --id <endpoint_id>
```

Old secret stops working immediately. Update the sender atomically.
