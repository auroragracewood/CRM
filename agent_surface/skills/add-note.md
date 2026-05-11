---
name: add-note
description: Attach a visibility-scoped human note to a contact or company. Private notes require explicit admin reveal.
---

# Add a note

Notes are separate from interactions because notes have **visibility scope**: public, team, private.

| visibility | who can read without explicit action |
| --- | --- |
| `public`  | anyone with read access on the record |
| `team`    | admins + author (the default) |
| `private` | the author only |

Private notes never appear in webhook payloads. An admin reading a private note must click "Reveal," which writes a `note.private_revealed` row to `audit_log`.

## MCP

```
tool: add_note
arguments:
  contact_id: 123
  body: "Prefers short emails. Followed up Friday."
  visibility: "team"
```

## REST

```
POST /api/notes
Authorization: Bearer crm_<key>

{
  "contact_id": 123,
  "body": "Prefers short emails. Followed up Friday.",
  "visibility": "team"
}
```

## CLI

```
python -m agent_surface.cli note create \
  --contact-id 123 \
  --visibility team \
  --body "Prefers short emails. Followed up Friday."
```

## Listing

```
GET /api/contacts/{id}/notes
```

Private notes that the requester isn't the author of (and isn't admin-revealing) come back with `body: null` and `_private_redacted: true`.

## Revealing a private note (admin only)

```
POST /api/notes/{note_id}/reveal     # writes note.private_revealed to audit_log
```

There is no silent admin-sees-everything override. Reveal is an explicit, audited action.
