---
name: create-contact
description: Create a new contact in the CRM. Use when the user mentions a person who should be tracked (signup, lead, inquiry, met-at-event) and the name or email isn't already in the system.
---

# Create a contact

Adds a new person record to the CRM. Returns the created contact (with assigned id) on success, or a structured error on failure.

## When to use

- The user mentions a new person (name, email, etc.) that should be saved.
- A form submission, signup, or inquiry happens and the lead needs a record.
- Bulk-importing contacts from a list (call once per person; use `Idempotency-Key` if available to avoid dupes on retry).

## Don't use when

- You only need to look someone up — use `find-contact` instead.
- You want to add a note or event about an existing person — use `add-note` or `log-interaction`.

## How to invoke

### Via MCP (preferred when running through a harness like Claude Code)

```
tool: create_contact
arguments:
  full_name: "Maya Sato"
  email: "maya@example.com"        # optional, lowercased+trimmed automatically
  phone: "+1 604 555 0188"         # optional
  title: "Marketing Director"      # optional
  location: "Vancouver, BC"        # optional
```

### Via REST

```
POST /api/contacts
Authorization: Bearer crm_<your-api-key>
Content-Type: application/json

{
  "full_name": "Maya Sato",
  "email": "maya@example.com",
  "phone": "+1 604 555 0188",
  "title": "Marketing Director"
}
```

Returns `201 Created`:
```
{ "ok": true, "contact": { "id": 123, "full_name": "Maya Sato", ... }, "request_id": "..." }
```

### Via CLI (local-only)

```
python -m agent_surface.cli contact create \
  --name "Maya Sato" \
  --email "maya@example.com" \
  --phone "+1 604 555 0188" \
  --title "Marketing Director"
```

## Required fields

At least ONE of `full_name`, `first_name`, `last_name`, or `email` must be provided. A contact with none of these would have no way to identify the person.

## Error codes

| code | meaning | what to do |
| --- | --- | --- |
| `VALIDATION_ERROR` | payload shape or format problem (e.g. email lacks `@`) | fix the payload and retry |
| `CONTACT_EMAIL_EXISTS` | another active contact already has this email | use `find-contact` to locate the existing one; consider `update-contact` instead |
| `FORBIDDEN` | your API key's scope is `read`-only | use a key with `write` or `admin` scope |

## What happens internally

1. The service normalizes the email (lowercase, trim) and validates required fields.
2. Inserts the row into `contacts`.
3. Writes an `audit_log` row recording the creation and the acting principal.
4. Enqueues a `contact.created` webhook event in the outbox (delivered after commit).
5. Commits the transaction.

All four steps share one database transaction. A webhook delivery failure later will NOT roll back the contact creation.
