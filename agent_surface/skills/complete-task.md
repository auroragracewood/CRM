---
verb: complete
noun: task
canonical_transport: rest
mcp_tool: complete_task
cli: task complete
rest: POST /api/tasks/{id}/complete
required_scope: write
related: ["create-task"]
---

# Complete a task

Marks a task as done. Stamps `completed_at = now`. Re-opening (via
`update_task` with status=open) clears `completed_at`.

## Required fields
- task id

## Example (REST)

```bash
curl -sX POST $BASE/api/tasks/12/complete \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: complete-task-12-2026-05-11"
```

## Example (CLI)

```bash
python -m agent_surface.cli task complete --id 12
```

## Common errors

| code | meaning |
|------|---------|
| `TASK_NOT_FOUND` | id missing |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="task.completed"`, before/after show status change
- Webhook: `task.completed`
- Plug-in hook: `on_task_completed(ctx, task, conn)`
