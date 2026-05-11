---
verb: merge
noun: duplicates
canonical_transport: rest
mcp_tool: (use REST)
cli: duplicates merge
rest: POST /api/duplicates/merge
required_scope: admin
related: ["create-contact"]
---

# Merge duplicate contacts

Merges contact `from_id` into `to_id`. The loser is soft-deleted;
its timeline, tags, notes, deals, tasks, and consent records get
re-pointed to the winner.

## Required fields
- `from_id` (integer) — the contact to merge IN (will be soft-deleted)
- `to_id` (integer) — the contact to keep

## Optional fields
- `field_preferences` (object) — when fields conflict, which to keep:
  ```json
  {"email": "to", "phone": "from", "company_id": "to"}
  ```
  Default: keep `to` for all non-empty fields; fill missing `to`
  fields from `from`.

## Example (REST)

```bash
curl -sX POST $BASE/api/duplicates/merge \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Idempotency-Key: merge-maya-2-into-5-2026-05-11" \
  -d '{"from_id": 2, "to_id": 5}'
```

## Example (CLI)

```bash
python -m agent_surface.cli duplicates merge --from-id 2 --to-id 5
```

## What gets re-pointed

- `interactions.contact_id` from→to
- `notes.contact_id` from→to
- `contact_tags.contact_id` from→to (deduped if tag already on `to`)
- `consent.contact_id` from→to (deduped on `(contact_id, channel)`;
  later-status wins)
- `deals.contact_id` from→to
- `tasks.contact_id` from→to
- `portal_tokens.contact_id` from→to (active tokens transferred)

## What does NOT happen

- The `from` contact is NOT hard-deleted — `deleted_at` is set; it
  remains queryable with `include_deleted=true`.
- The `from` contact's email is NOT freed for reuse (the partial
  unique index allows reuse on deleted rows; just be aware).

## Common errors

| code | meaning |
|------|---------|
| `DUPLICATES_MERGE_INVALID` | from_id == to_id, or either is deleted |
| `CONTACT_NOT_FOUND` | either id missing |
| `FORBIDDEN` | non-admin caller |

## Audit + webhooks

- Audit: one `contact.merged_into` row on `from` (records the target),
  and a follow-up `contact.deleted` row.
- Webhook: `contact.merged` with `{"into": {...}, "from": {...}}`.
- Plug-in hook: `on_contact_merged(ctx, into, from_, conn)`.

## Finding duplicates first

```bash
curl -sH "Authorization: Bearer $KEY" $BASE/api/duplicates
# returns list of likely pairs with confidence + reasons
```

The duplicates service detects:
- Exact email match (after normalization)
- Normalized name + same company match
- Similar name match (Levenshtein distance) — flagged with lower
  confidence

Review pairs first; merge after confirmation.
