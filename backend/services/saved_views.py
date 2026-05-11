"""Saved views: per-user (or shared) stored filter + sort + columns combos
for the entity list pages.

A view's config_json is opaque to the service — list pages know how to
interpret it (which columns to show, what to filter by, sort order). The
service just CRUDs the rows and enforces ownership: only the owner (or an
admin) can edit/delete a non-shared view; shared views are readable by all.
"""
import json
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


VALID_ENTITIES = ("contact", "company", "deal", "task", "interaction")


def create(
    ctx: ServiceContext, *,
    entity: str, name: str,
    config: dict, slug: Optional[str] = None,
    shared: bool = False,
) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if entity not in VALID_ENTITIES:
        raise ServiceError("VALIDATION_ERROR", f"entity must be one of {VALID_ENTITIES}")
    if not name or not name.strip():
        raise ServiceError("VALIDATION_ERROR", "name required")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            """INSERT INTO saved_views
                 (user_id, entity, name, slug, config_json, shared,
                  created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ctx.user_id, entity, name, slug,
             json.dumps(config or {}), 1 if shared else 0, now, now),
        )
        vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM saved_views WHERE id=?", (vid,)).fetchone()
        audit.log(conn, ctx, action="saved_view.created", object_type="saved_view",
                  object_id=vid, after={"entity": entity, "name": name, "shared": shared})
    return dict(row)


def list_for_entity(ctx: ServiceContext, entity: str) -> list[dict]:
    """List saved views visible to the caller for an entity. Returns:
    user's own views (regardless of shared flag) + everyone-else's shared views."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    if entity not in VALID_ENTITIES:
        raise ServiceError("VALIDATION_ERROR", f"entity must be one of {VALID_ENTITIES}")
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM saved_views
                WHERE entity=? AND (user_id=? OR shared=1)
                ORDER BY shared ASC, name ASC""",
            (entity, ctx.user_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get(ctx: ServiceContext, view_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM saved_views WHERE id=?", (view_id,)).fetchone()
    if not row:
        raise ServiceError("SAVED_VIEW_NOT_FOUND", f"saved_view {view_id} not found")
    d = dict(row)
    if d["user_id"] != ctx.user_id and not d["shared"] and not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "saved view is private to its owner")
    return d


def update(ctx: ServiceContext, view_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        before = conn.execute("SELECT * FROM saved_views WHERE id=?", (view_id,)).fetchone()
        if not before:
            raise ServiceError("SAVED_VIEW_NOT_FOUND", f"saved_view {view_id} not found")
        if before["user_id"] != ctx.user_id and not ctx.is_admin():
            raise ServiceError("FORBIDDEN", "only owner or admin can edit a saved view")

        cleaned = {}
        if "name" in payload:
            cleaned["name"] = payload["name"]
        if "shared" in payload:
            cleaned["shared"] = 1 if payload["shared"] else 0
        if "config" in payload:
            cleaned["config_json"] = json.dumps(payload["config"] or {})
        if not cleaned:
            raise ServiceError("VALIDATION_ERROR", "no updatable fields")
        cleaned["updated_at"] = int(time.time())
        set_sql = ", ".join(f"{k}=?" for k in cleaned)
        conn.execute(f"UPDATE saved_views SET {set_sql} WHERE id=?",
                     list(cleaned.values()) + [view_id])
        after = conn.execute("SELECT * FROM saved_views WHERE id=?", (view_id,)).fetchone()
        audit.log(conn, ctx, action="saved_view.updated", object_type="saved_view",
                  object_id=view_id, before=dict(before), after=dict(after))
    return dict(after)


def delete(ctx: ServiceContext, view_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        row = conn.execute("SELECT user_id, name FROM saved_views WHERE id=?", (view_id,)).fetchone()
        if not row:
            raise ServiceError("SAVED_VIEW_NOT_FOUND", f"saved_view {view_id} not found")
        if row["user_id"] != ctx.user_id and not ctx.is_admin():
            raise ServiceError("FORBIDDEN", "only owner or admin can delete a saved view")
        conn.execute("DELETE FROM saved_views WHERE id=?", (view_id,))
        audit.log(conn, ctx, action="saved_view.deleted", object_type="saved_view",
                  object_id=view_id, before={"name": row["name"]})
    return {"id": view_id, "deleted": True}
