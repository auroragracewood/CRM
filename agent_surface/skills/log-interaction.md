---
name: log-interaction
description: Record a timeline event (email, call, meeting, form submission, page view, system event) against a contact or company.
---

# Log an interaction

Interactions are the firehose: every meaningful event lands in one table so the timeline + audit + agent context all share one source.

## When to use

- Someone sent or received a message — log type `email` or `call`.
- A meeting happened — log type `meeting` with title + body.
- A form submission arrived — log type `form_submission` with the payload in `metadata_json`.
- A page on a tracked surface was viewed — log type `page_view` with URL in `metadata_json`.
- A system change happened (merged contacts, automated import) — log type `system`.

## Required arguments

- `type` — one of `email | call | meeting | form_submission | page_view | note_system | system`
- `contact_id` OR `company_id` (at least one)
- `occurred_at` — optional unix seconds; defaults to now

## MCP

```
tool: log_interaction
arguments:
  type: "call"
  contact_id: 123
  title: "Discovery call"
  body: "Walked through pricing. They want a quote by Friday."
  channel: "phone"
```

## REST

```
POST /api/interactions
Authorization: Bearer crm_<key>

{
  "type": "email",
  "contact_id": 123,
  "title": "Re: pricing question",
  "body": "Sent the spec sheet attached.",
  "channel": "outbound"
}
```

## CLI

```
python -m agent_surface.cli interaction log \
  --type call --contact-id 123 \
  --title "Discovery call" \
  --body "Walked through pricing. They want a quote by Friday."
```

## Tips

- Append-only: there is no `update_interaction` or `delete_interaction`. If something was wrong, log a corrective `system` event referencing it.
- `metadata_json` shape varies by type. Use a dict for structured data; the service serializes.
- Reading the timeline: `get_timeline(contact_id=...)` or `GET /api/contacts/{id}/timeline`.
