---
verb: delete
noun: contact
canonical_transport: rest
mcp_tool: delete_contact
cli: contact delete
rest: DELETE /api/contacts/{id}
required_scope: write
related: ["create-contact", "merge-duplicates"]
---

# Delete a contact

Soft-delete. Sets `deleted_at = now`. The row remains in the database
but is hidden from default queries; its email becomes available for
re-use by a new contact.

## Required fields
- contact id

## Example (REST)

```bash
curl -sX DELETE $BASE/api/contacts/5 \
  -H "Authorization: Bearer $KEY"
# {"ok":true,"id":5,"deleted_at":1715200000}
```

## Example (CLI)

```bash
python -m agent_surface.cli contact delete --id 5
```

## What changes

- `contacts.deleted_at` set to current unix time.
- Email frees up (partial unique index excludes `WHERE deleted_at IS
  NOT NULL`).
- Contact is hidden from list endpoints unless `include_deleted=true`.
- Webhook `contact.deleted` fires.
- Plug-in hook `on_contact_deleted(ctx, before, conn)` fires.

## What stays

- The row itself (audit trail preserved).
- All foreign-key references (interactions still point at it; deals
  too — they get `contact_id` set to NULL by SET NULL FK only on hard
  delete, which the service never does).
- Tags remain attached.

## Re-activation

There is no `undelete` endpoint by design — soft-delete is final from
the operator's POV. If you need to recover, manually UPDATE
`contacts SET deleted_at = NULL WHERE id = ?`. Audit log records the
deletion; the recovery should be a manual decision.

## Common errors

| code | meaning |
|------|---------|
| `CONTACT_NOT_FOUND` | id missing or already deleted |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="contact.deleted"` with before_json populated
- Webhook: `contact.deleted` with `{"contact_id": N}`
- Plug-in hook: `on_contact_deleted(ctx, before, conn)`

## When to merge instead of delete

If the contact is a duplicate of another active contact, use
[merge-duplicates](merge-duplicates.md). Merging preserves their
history under the surviving record.
