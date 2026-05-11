---
verb: issue
noun: portal-token
canonical_transport: rest
mcp_tool: issue_portal_token
cli: portal issue
rest: POST /api/contacts/{id}/portal-tokens
required_scope: write
related: ["create-contact"]
---

# Issue a portal token

Creates a self-service URL for one contact. The URL is HTTPS,
contains a random opaque token, and lets the contact see a scope-
specific view of their own data without signing in.

## Required fields
- contact id (in URL)
- `scope` (string)

## Optional fields
- `label` (string; human-readable purpose)
- `expires_in_days` (integer; default depends on scope)

## Scopes

| scope | who sees what |
|-------|---------------|
| `client` | profile, open deals, non-private notes, "reply to us" form |
| `applicant` | profile, application status, withdraw button |
| `sponsor` | profile, partner deals, agreement docs |
| `member` | profile, membership status, renewal |

Extend by adding scope handlers in `services/portals.py:_RENDER_BY_SCOPE`.

## Example (REST)

```bash
curl -sX POST $BASE/api/contacts/5/portal-tokens \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "scope":           "client",
    "label":           "Acme proposal Q2",
    "expires_in_days": 60
  }'
# {"ok":true,"token":"<random>","url":"https://crm.example.com/portal/<random>"}
```

The raw token is returned ONCE — copy the URL for delivery to the
contact. The CRM stores it hashed.

## Example (CLI)

```bash
python -m agent_surface.cli portal issue \
  --contact-id 5 --scope client \
  --label "Acme proposal Q2" --expires-in-days 60
```

## Common errors

| code | meaning |
|------|---------|
| `CONTACT_NOT_FOUND` | contact_id missing |
| `VALIDATION_ERROR` | unknown scope |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="portal_token.issued"`
- Webhook: `portal_token.issued`
- First-use audit: `portal_token.used` (audit + webhook on FIRST hit)
- Plug-in hook: none by default; can add `on_portal_used` (planned)

## Revoking

```bash
curl -sX POST $BASE/api/portal-tokens/<id>/revoke \
  -H "Authorization: Bearer $KEY"
```

Revoke is irreversible. The token row stays (audit trail) but
`portals.lookup` raises `PORTAL_TOKEN_REVOKED` (HTTP 410) on hits.

## Sharing safely

- Always use HTTPS.
- Don't embed in public docs or social posts.
- One token per recipient — don't share one token across many people.
- Set expiry. 30-90 days is typical; never "no expiry."
