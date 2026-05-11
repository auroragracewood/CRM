---
name: find-contact
description: Search the CRM for contacts by name or email substring. Returns paginated list.
---

# Find contacts

Use this BEFORE creating a contact to avoid duplicates. Email uniqueness among active contacts is enforced at the DB level (partial unique index), so `create_contact` will fail with `CONTACT_EMAIL_EXISTS` if you skip this step and try to insert a known email.

## MCP

```
tool: find_contacts
arguments:
  q: "maya"
  limit: 50
  offset: 0
```

## REST

```
GET /api/contacts?q=maya&limit=50
Authorization: Bearer crm_<key>
```

## CLI

```
python -m agent_surface.cli contact list --q maya
```

## Result shape

```
{ "ok": true, "items": [...], "total": N, "limit": 50, "offset": 0 }
```

The search is `LIKE %q%` over `full_name` and `email` (both lowercased). FTS5 cross-table search lands in v0 Milestone 2.

## Tips

- Empty `q` returns the most recent contacts (id DESC).
- For exact email lookup, prefer `find_by_email` (same module — `contacts_service.find_by_email`) which uses the active-email index directly.
- Soft-deleted contacts are excluded by default. Pass `include_deleted=true` to see them.
