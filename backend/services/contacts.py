"""Contacts service — the single source of truth for contact mutations.

REST, CLI, MCP, and UI all dispatch through these functions. Validation, audit
writes, and webhook outbox enqueue all happen inside the service-layer
transaction so they share commit atomicity with the data change.

Error codes (raised via ServiceError):
  CONTACT_NOT_FOUND       contact_id doesn't exist or is soft-deleted
  CONTACT_EMAIL_EXISTS    another active contact has this email
  VALIDATION_ERROR        payload failed shape/format checks
  FORBIDDEN               ctx.scope insufficient
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks


_FIELDS = (
    "full_name", "first_name", "last_name", "email", "phone", "avatar_url",
    "company_id", "title", "location", "timezone", "preferred_channel",
    "custom_fields_json",
)


class ServiceError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    e = email.strip().lower()
    return e or None


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


def _validate_create(payload: dict) -> dict:
    cleaned = {k: payload.get(k) for k in _FIELDS}
    cleaned["email"] = _normalize_email(cleaned.get("email"))

    if not (cleaned.get("full_name") or cleaned.get("first_name")
            or cleaned.get("last_name") or cleaned.get("email")):
        raise ServiceError(
            "VALIDATION_ERROR",
            "Contact requires at least one of: full_name, first_name, last_name, email",
        )
    if cleaned["email"] and "@" not in cleaned["email"]:
        raise ServiceError(
            "VALIDATION_ERROR", "email is not a valid address",
            {"field": "email", "value": cleaned["email"]},
        )
    return cleaned


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate_create(payload)
    now = int(time.time())

    with db() as conn:
        if cleaned["email"]:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE email = ? AND deleted_at IS NULL",
                (cleaned["email"],),
            ).fetchone()
            if existing:
                raise ServiceError(
                    "CONTACT_EMAIL_EXISTS",
                    f"Another active contact already has email {cleaned['email']!r}",
                    {"contact_id": existing[0]},
                )

        cols = list(cleaned.keys()) + ["created_at", "updated_at"]
        vals = list(cleaned.values()) + [now, now]
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO contacts ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        contact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        contact = _row_to_dict(row)

        audit.log(conn, ctx,
                  action="contact.created",
                  object_type="contact",
                  object_id=contact_id,
                  after=contact)
        webhooks.enqueue(conn, "contact.created", {"contact": contact})

    return contact


def get(ctx: ServiceContext, contact_id: int, *, include_deleted: bool = False) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        sql = "SELECT * FROM contacts WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = conn.execute(sql, (contact_id,)).fetchone()
    if not row:
        raise ServiceError("CONTACT_NOT_FOUND", f"contact {contact_id} not found")
    return _row_to_dict(row)


def find_by_email(ctx: ServiceContext, email: str) -> Optional[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    e = _normalize_email(email)
    if not e:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE email = ? AND deleted_at IS NULL", (e,),
        ).fetchone()
    return _row_to_dict(row)


def list_(
    ctx: ServiceContext,
    *,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    company_id: Optional[int] = None,
    include_deleted: bool = False,
) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where = []
    params: list = []
    if not include_deleted:
        where.append("deleted_at IS NULL")
    if q:
        where.append("(LOWER(full_name) LIKE ? OR LOWER(email) LIKE ?)")
        like = f"%{q.lower()}%"
        params += [like, like]
    if company_id is not None:
        where.append("company_id = ?")
        params.append(int(company_id))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM contacts{where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM contacts{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def update(ctx: ServiceContext, contact_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = {k: payload[k] for k in payload if k in _FIELDS}
    if "email" in cleaned:
        cleaned["email"] = _normalize_email(cleaned["email"])
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")
    now = int(time.time())

    with db() as conn:
        before_row = conn.execute(
            "SELECT * FROM contacts WHERE id = ? AND deleted_at IS NULL", (contact_id,),
        ).fetchone()
        if not before_row:
            raise ServiceError("CONTACT_NOT_FOUND", f"contact {contact_id} not found")
        before = _row_to_dict(before_row)

        if "email" in cleaned and cleaned["email"]:
            clash = conn.execute(
                "SELECT id FROM contacts WHERE email = ? AND deleted_at IS NULL AND id != ?",
                (cleaned["email"], contact_id),
            ).fetchone()
            if clash:
                raise ServiceError(
                    "CONTACT_EMAIL_EXISTS",
                    f"Another active contact already has email {cleaned['email']!r}",
                    {"contact_id": clash[0]},
                )

        set_sql = ", ".join(f"{k} = ?" for k in cleaned) + ", updated_at = ?"
        params = list(cleaned.values()) + [now, contact_id]
        conn.execute(f"UPDATE contacts SET {set_sql} WHERE id = ?", params)

        after_row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        after = _row_to_dict(after_row)

        audit.log(conn, ctx,
                  action="contact.updated", object_type="contact",
                  object_id=contact_id, before=before, after=after)
        webhooks.enqueue(conn, "contact.updated", {"contact": after, "before": before})

    return after


def delete(ctx: ServiceContext, contact_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ? AND deleted_at IS NULL", (contact_id,),
        ).fetchone()
        if not row:
            raise ServiceError("CONTACT_NOT_FOUND", f"contact {contact_id} not found")
        before = _row_to_dict(row)
        conn.execute(
            "UPDATE contacts SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, contact_id),
        )
        audit.log(conn, ctx,
                  action="contact.deleted", object_type="contact",
                  object_id=contact_id, before=before)
        webhooks.enqueue(conn, "contact.deleted", {"contact_id": contact_id})
    return {"id": contact_id, "deleted_at": now}
