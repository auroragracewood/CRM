"""Tags service. Labels for contacts and companies."""
import sqlite3
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


VALID_SCOPES = ("contact", "company", "any")


def create(ctx: ServiceContext, name: str, *,
           color: Optional[str] = None, scope: str = "any") -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if not name or not name.strip():
        raise ServiceError("VALIDATION_ERROR", "tag name cannot be empty")
    if scope not in VALID_SCOPES:
        raise ServiceError("VALIDATION_ERROR", f"scope must be one of {VALID_SCOPES}")
    nm = name.strip()
    now = int(time.time())
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO tags (name, color, scope, created_at) VALUES (?,?,?,?)",
                (nm, color, scope, now),
            )
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT * FROM tags WHERE name = ?", (nm,)).fetchone()
            if row:
                raise ServiceError("TAG_EXISTS", f"tag {nm!r} already exists",
                                   {"tag_id": row["id"]})
            raise
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM tags WHERE id = ?", (tid,)).fetchone()
        tag = dict(row)
        audit.log(conn, ctx, action="tag.created", object_type="tag",
                  object_id=tid, after=tag)
    return tag


def attach(ctx: ServiceContext, *,
           tag_id: int,
           contact_id: Optional[int] = None,
           company_id: Optional[int] = None) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if bool(contact_id) == bool(company_id):
        raise ServiceError("VALIDATION_ERROR",
                           "attach: exactly one of contact_id or company_id required")
    now = int(time.time())
    with db() as conn:
        if contact_id:
            conn.execute(
                """INSERT OR IGNORE INTO contact_tags
                     (contact_id, tag_id, added_at, added_by)
                   VALUES (?,?,?,?)""",
                (contact_id, tag_id, now, ctx.user_id),
            )
            audit.log(conn, ctx, action="tag.attached",
                      object_type="contact", object_id=contact_id,
                      after={"tag_id": tag_id})
            return {"ok": True, "contact_id": contact_id, "tag_id": tag_id}
        else:
            conn.execute(
                """INSERT OR IGNORE INTO company_tags
                     (company_id, tag_id, added_at, added_by)
                   VALUES (?,?,?,?)""",
                (company_id, tag_id, now, ctx.user_id),
            )
            audit.log(conn, ctx, action="tag.attached",
                      object_type="company", object_id=company_id,
                      after={"tag_id": tag_id})
            return {"ok": True, "company_id": company_id, "tag_id": tag_id}


def detach(ctx: ServiceContext, *,
           tag_id: int,
           contact_id: Optional[int] = None,
           company_id: Optional[int] = None) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        if contact_id:
            conn.execute(
                "DELETE FROM contact_tags WHERE contact_id = ? AND tag_id = ?",
                (contact_id, tag_id),
            )
            audit.log(conn, ctx, action="tag.detached",
                      object_type="contact", object_id=contact_id,
                      before={"tag_id": tag_id})
        elif company_id:
            conn.execute(
                "DELETE FROM company_tags WHERE company_id = ? AND tag_id = ?",
                (company_id, tag_id),
            )
            audit.log(conn, ctx, action="tag.detached",
                      object_type="company", object_id=company_id,
                      before={"tag_id": tag_id})
    return {"ok": True}


def list_all(ctx: ServiceContext) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def list_for_contact(ctx: ServiceContext, contact_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT t.* FROM tags t
                 JOIN contact_tags ct ON ct.tag_id = t.id
                WHERE ct.contact_id = ? ORDER BY t.name""",
            (contact_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_for_company(ctx: ServiceContext, company_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT t.* FROM tags t
                 JOIN company_tags ct ON ct.tag_id = t.id
                WHERE ct.company_id = ? ORDER BY t.name""",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]
