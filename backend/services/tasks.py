"""Tasks service. Things-to-do attached to a contact, company, deal, or any
combination of those. Has owner + due_date + priority + status workflow.

Status transitions are tracked in audit_log; the service also auto-sets
`completed_at` when a task transitions into `done`. Moving back out of `done`
clears it again, so a re-opened task doesn't carry a stale completion stamp.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict


_FIELDS = (
    "contact_id", "company_id", "deal_id", "assigned_to",
    "title", "description", "due_date", "priority", "status",
)
VALID_PRIORITIES = ("low", "normal", "high", "urgent")
VALID_STATUSES = ("open", "in_progress", "done", "cancelled")


def _validate(payload: dict, *, is_create: bool) -> dict:
    cleaned = {k: payload.get(k) for k in _FIELDS}
    if is_create:
        if not cleaned.get("title") or not cleaned["title"].strip():
            raise ServiceError("VALIDATION_ERROR", "task requires a title")
    if cleaned.get("priority") and cleaned["priority"] not in VALID_PRIORITIES:
        raise ServiceError("VALIDATION_ERROR",
                           f"priority must be one of {VALID_PRIORITIES}")
    if cleaned.get("status") and cleaned["status"] not in VALID_STATUSES:
        raise ServiceError("VALIDATION_ERROR",
                           f"status must be one of {VALID_STATUSES}")
    if cleaned.get("due_date") is not None:
        try:
            cleaned["due_date"] = int(cleaned["due_date"])
        except (TypeError, ValueError):
            raise ServiceError("VALIDATION_ERROR",
                               "due_date must be unix seconds (integer)")
    return cleaned


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate(payload, is_create=True)
    cleaned["priority"] = cleaned.get("priority") or "normal"
    cleaned["status"] = cleaned.get("status") or "open"
    now = int(time.time())
    with db() as conn:
        # Optional sanity checks on referenced rows so we fail with a useful
        # error instead of an opaque IntegrityError. Skip when the column is None.
        if cleaned.get("contact_id"):
            r = conn.execute(
                "SELECT id FROM contacts WHERE id=? AND deleted_at IS NULL",
                (cleaned["contact_id"],),
            ).fetchone()
            if not r:
                raise ServiceError("CONTACT_NOT_FOUND",
                                   f"contact {cleaned['contact_id']} not found")
        if cleaned.get("company_id"):
            r = conn.execute(
                "SELECT id FROM companies WHERE id=? AND deleted_at IS NULL",
                (cleaned["company_id"],),
            ).fetchone()
            if not r:
                raise ServiceError("COMPANY_NOT_FOUND",
                                   f"company {cleaned['company_id']} not found")
        if cleaned.get("deal_id"):
            r = conn.execute("SELECT id FROM deals WHERE id=?", (cleaned["deal_id"],)).fetchone()
            if not r:
                raise ServiceError("DEAL_NOT_FOUND",
                                   f"deal {cleaned['deal_id']} not found")
        if cleaned.get("assigned_to"):
            r = conn.execute("SELECT id FROM users WHERE id=?", (cleaned["assigned_to"],)).fetchone()
            if not r:
                raise ServiceError("USER_NOT_FOUND",
                                   f"user {cleaned['assigned_to']} not found")

        cols = list(cleaned.keys()) + ["created_by", "created_at", "updated_at"]
        vals = list(cleaned.values()) + [ctx.user_id, now, now]
        ph = ",".join("?" * len(cols))
        conn.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({ph})", vals)
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        task = _row_to_dict(row)
        audit.log(conn, ctx, action="task.created", object_type="task",
                  object_id=tid, after=task)
        webhooks.enqueue(conn, "task.created", {"task": task})
    return task


def get(ctx: ServiceContext, task_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ServiceError("TASK_NOT_FOUND", f"task {task_id} not found")
    return _row_to_dict(row)


def list_(
    ctx: ServiceContext, *,
    status: Optional[str] = None,
    assigned_to: Optional[int] = None,
    contact_id: Optional[int] = None,
    company_id: Optional[int] = None,
    deal_id: Optional[int] = None,
    due_before: Optional[int] = None,
    overdue: bool = False,
    limit: int = 100, offset: int = 0,
) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    where, params = [], []
    if status:
        if status not in VALID_STATUSES:
            raise ServiceError("VALIDATION_ERROR",
                               f"status must be one of {VALID_STATUSES}")
        where.append("status = ?"); params.append(status)
    if assigned_to is not None:
        where.append("assigned_to = ?"); params.append(int(assigned_to))
    if contact_id is not None:
        where.append("contact_id = ?"); params.append(int(contact_id))
    if company_id is not None:
        where.append("company_id = ?"); params.append(int(company_id))
    if deal_id is not None:
        where.append("deal_id = ?"); params.append(int(deal_id))
    if due_before is not None:
        where.append("due_date IS NOT NULL AND due_date <= ?")
        params.append(int(due_before))
    if overdue:
        where.append("status IN ('open','in_progress')")
        where.append("due_date IS NOT NULL AND due_date < ?")
        params.append(int(time.time()))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # Sort: overdue first by due, then by priority weight, then newest.
    order_sql = (
        " ORDER BY "
        " CASE WHEN status IN ('done','cancelled') THEN 1 ELSE 0 END ASC, "
        " CASE WHEN due_date IS NULL THEN 1 ELSE 0 END ASC, "
        " due_date ASC, "
        " CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
        "               WHEN 'normal' THEN 2 ELSE 3 END ASC, "
        " id DESC"
    )
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM tasks{where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM tasks{where_sql}{order_sql} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


def update(ctx: ServiceContext, task_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate({k: v for k, v in payload.items() if k in _FIELDS}, is_create=False)
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")
    now = int(time.time())
    with db() as conn:
        before_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not before_row:
            raise ServiceError("TASK_NOT_FOUND", f"task {task_id} not found")
        before = _row_to_dict(before_row)

        set_clauses = [f"{k} = ?" for k in cleaned]
        params = list(cleaned.values())
        # completed_at lifecycle: set when moving INTO done, clear when moving OUT of done
        if "status" in cleaned and cleaned["status"] != before["status"]:
            if cleaned["status"] == "done":
                set_clauses.append("completed_at = ?")
                params.append(now)
            elif before["status"] == "done":
                set_clauses.append("completed_at = NULL")
        set_clauses.append("updated_at = ?")
        params.append(now)
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id=?", params)

        after_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        after = _row_to_dict(after_row)
        audit.log(conn, ctx, action="task.updated", object_type="task",
                  object_id=task_id, before=before, after=after)
        if "status" in cleaned and cleaned["status"] != before["status"]:
            webhooks.enqueue(conn, "task.status_changed",
                             {"task": after, "from": before["status"], "to": after["status"]})
        webhooks.enqueue(conn, "task.updated", {"task": after})
    return after


def complete(ctx: ServiceContext, task_id: int) -> dict:
    return update(ctx, task_id, {"status": "done"})


def delete(ctx: ServiceContext, task_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ServiceError("TASK_NOT_FOUND", f"task {task_id} not found")
        before = _row_to_dict(row)
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        audit.log(conn, ctx, action="task.deleted", object_type="task",
                  object_id=task_id, before=before)
        webhooks.enqueue(conn, "task.deleted", {"task_id": task_id})
    return {"id": task_id, "deleted": True}
