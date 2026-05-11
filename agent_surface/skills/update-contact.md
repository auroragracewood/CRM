---
verb: update
noun: contact
canonical_transport: rest
mcp_tool: update_contact
cli: contact update
rest: PUT /api/contacts/{id}
required_scope: write
related: ["create-contact", "find-contact"]
---

# Update a contact

Partial update — only fields in the payload are changed.

## Required fields
- contact id (in URL / `--id`)

## Updatable fields

All fields accepted by `create-contact`, plus the v4.1 richer set:
- Identity: `full_name`, `first_name`, `last_name`, `email`, `phone`,
  `title`, `pronouns`, `birthday`, `language`, `location`, `timezone`
- Social: `linkedin_url`, `twitter_url`, `instagram_url`, `website_url`
- About: `about` (free-form), `interests_json` (JSON array)
- Source: `source`, `referrer`
- Preferences: `preferred_channel`, `best_contact_window`,
  `do_not_contact` (0 or 1)
- Org: `company_id`
- `custom_fields_json`

## Example (REST)

```bash
curl -sX PUT $BASE/api/contacts/5 \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "title": "Sr. Marketing Director",
    "best_contact_window": "weekday mornings PT"
  }'
```

## Example (CLI)

```bash
python -m agent_surface.cli contact update --id 5 \
  --title "Sr. Marketing Director"
```

## Email-change rules

If you update `email` to one that's in use by another active contact,
returns `CONTACT_EMAIL_EXISTS` (409). Resolve via duplicates merge or
soft-delete the other contact first.

## Common errors

| code | meaning |
|------|---------|
| `CONTACT_NOT_FOUND` | id missing/soft-deleted |
| `CONTACT_EMAIL_EXISTS` | another active contact uses the new email |
| `VALIDATION_ERROR` | bad field value (e.g., bad email format) |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="contact.updated"`, with full before/after JSON
- Webhook: `contact.updated` with `{"contact":..., "before":...}`
- Plug-in hook: `on_contact_updated(ctx, before, after, conn)`
