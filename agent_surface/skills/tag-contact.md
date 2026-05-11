---
name: tag-contact
description: Attach a tag to a contact (or company). Tags are global labels with optional scope.
---

# Tag a contact

Tags are reusable labels. Each tag has a name (unique), optional color, and an optional scope (`contact`, `company`, or `any`).

## Two steps

1. Make sure the tag exists (or create it).
2. Attach it to the contact (or company).

## MCP

```
tool: create_tag      # if it doesn't exist yet
arguments:
  name: "warm-lead"
  color: "#4a5fc1"
  scope: "contact"

tool: tag_contact
arguments:
  contact_id: 123
  tag_id: 7
```

## REST

```
POST /api/tags
Authorization: Bearer crm_<key>

{ "name": "warm-lead", "color": "#4a5fc1", "scope": "contact" }
```

```
POST /api/contacts/123/tags/7
```

## CLI

```
python -m agent_surface.cli tag create --name warm-lead --color "#4a5fc1" --scope contact
python -m agent_surface.cli tag attach --tag-id 7 --contact-id 123
```

## Notes

- Detach: `DELETE /api/contacts/{contact_id}/tags/{tag_id}` or `tag detach` (similar shape, attached as a future v1 helper).
- Tag names are unique across the install. Attempting to create an existing name returns `TAG_EXISTS` with the existing `tag_id` in `details`.
- Tags don't have a `scope` enforcement at the join level yet — that's a v1 lint. You can attach a `company`-scoped tag to a contact today; v1 will reject it.
