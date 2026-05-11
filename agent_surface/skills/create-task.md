---
verb: create
noun: task
canonical_transport: rest
mcp_tool: create_task
cli: task create
rest: POST /api/tasks
required_scope: write
related: ["complete-task"]
---

# Create a task

Creates a task. Tasks are typically attached to a contact, company,
or deal — at least one parent is recommended for context.

## Required fields
- `title` (string)

## Optional fields
- `description` (string)
- `contact_id` (integer)
- `company_id` (integer)
- `deal_id` (integer)
- `assigned_to` (user id)
- `due_date` (unix seconds)
- `priority` — `low` | `normal` | `high` | `urgent` (default `normal`)
- `status` — `open` | `in_progress` | `done` | `cancelled` (default `open`)

## Example (REST)

```bash
curl -sX POST $BASE/api/tasks \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: task-followup-greg-2026-05-11" \
  -d '{
    "title":      "Send Hammer formal proposal",
    "contact_id": 3,
    "deal_id":    2,
    "priority":   "high",
    "due_date":   1715200000
  }'
```

## Example (CLI)

```bash
python -m agent_surface.cli task create \
  --title "Send Hammer formal proposal" \
  --contact-id 3 --deal-id 2 \
  --priority high --due-date 1715200000
```

## Common errors

| code | meaning |
|------|---------|
| `VALIDATION_ERROR` | title missing or invalid priority/status |
| `CONTACT_NOT_FOUND` / `COMPANY_NOT_FOUND` / `DEAL_NOT_FOUND` | referenced parent doesn't exist |
| `USER_NOT_FOUND` | assigned_to id doesn't exist |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="task.created"`
- Webhook: `task.created`
- Plug-in hook: `on_task_created(ctx, task, conn)`
