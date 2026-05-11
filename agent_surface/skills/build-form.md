---
verb: build
noun: form
canonical_transport: rest
mcp_tool: create_form
cli: form create
rest: POST /api/forms
required_scope: write
related: ["register-inbound-endpoint"]
---

# Build a public form

Creates a public form at `/f/{slug}`. Submissions auto-create or
update contacts and log interactions per the routing rules.

## Required fields
- `slug` (string; URL-safe)
- `name` (string)
- `schema` (object; field definitions)

## Optional fields
- `routing` (object; what to do with submissions)
- `redirect_url` (string; where to send the submitter after submit)
- `active` (boolean; default true)

## Schema shape

```json
{
  "fields": [
    {"key":"name",     "type":"text",     "label":"Name", "required": true},
    {"key":"email",    "type":"email",    "label":"Email","required": true},
    {"key":"interest", "type":"select",   "label":"Interest",
                       "options":["signage","sculpture","consulting"]},
    {"key":"message",  "type":"textarea", "label":"Message"}
  ]
}
```

Supported field types: `text`, `email`, `phone`, `textarea`,
`select`, `multiselect`, `checkbox`, `hidden`.

## Routing shape

```json
{
  "tags": ["lead", "form:contact-us"],
  "interest_tag_prefix": "interest:",
  "auto_create_contact": true,
  "match_by_email":      true,
  "interaction": {
    "type":  "form_submission",
    "title": "Contact form submission",
    "body":  "{{message}}"
  }
}
```

## Example (REST)

```bash
curl -sX POST $BASE/api/forms \
  -H "Authorization: Bearer $KEY" \
  -d @form-spec.json
# response includes the public URL: $BASE/f/contact-us
```

## Common errors

| code | meaning |
|------|---------|
| `FORM_SLUG_EXISTS` | slug already in use |
| `VALIDATION_ERROR` | schema/routing malformed |
| `FORBIDDEN` | scope insufficient |

## After creation

- Visit `/f/<slug>` in a browser — auto-rendered form.
- Or embed the HTML on your own page (`POST` to the same URL).

## Audit + webhooks

- Audit: `action="form.created"`
- Webhook: no event for form creation
- Per submission: `action="form.submitted"` + plug-in hook
  `on_form_submitted(ctx, form, submission, contact, conn)`

## Disabling

```bash
curl -sX PUT $BASE/api/forms/<id> \
  -H "Authorization: Bearer $KEY" \
  -d '{"active": false}'
```

Disabled forms return 410 Gone on POST.
