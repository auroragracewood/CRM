"""Roles service. CRUD on roles + their permissions.

Permissions are simple action strings ('contact.read', 'deal.write', etc.).
The schema doesn't enumerate them centrally — anything you grant works
as long as your service code checks for it.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


def list_(ctx: ServiceContext) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        roles = conn.execute(
            "SELECT id, name, description, built_in, created_at FROM roles ORDER BY id"
        ).fetchall()
        perms = conn.execute(
            "SELECT role_id, permission FROM role_permissions ORDER BY permission"
        ).fetchall()
    perms_by_role: dict[int, list[str]] = {}
    for p in perms:
        perms_by_role.setdefault(p["role_id"], []).append(p["permission"])
    out = []
    for r in roles:
        d = dict(r)
        d["permissions"] = perms_by_role.get(r["id"], [])
        out.append(d)
    return out


def get(ctx: ServiceContext, role_id: int) -> dict:
    for r in list_(ctx):
        if r["id"] == role_id:
            return r
    raise ServiceError("ROLE_NOT_FOUND", f"role {role_id} not found")


def create(ctx: ServiceContext, name: str, *,
           description: Optional[str] = None) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    nm = name.strip()
    if not nm:
        raise ServiceError("VALIDATION_ERROR", "role name cannot be empty")
    now = int(time.time())
    with db() as conn:
        existing = conn.execute("SELECT id FROM roles WHERE name=?", (nm,)).fetchone()
        if existing:
            raise ServiceError("ROLE_EXISTS", f"role {nm!r} already exists",
                               {"role_id": existing[0]})
        conn.execute(
            "INSERT INTO roles (name, description, built_in, created_at) VALUES (?,?,?,?)",
            (nm, description, 0, now),
        )
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit.log(conn, ctx, action="role.created", object_type="role",
                  object_id=rid, after={"name": nm, "description": description})
    return {"id": rid, "name": nm, "description": description, "permissions": []}


def update(ctx: ServiceContext, role_id: int, *,
           name: Optional[str] = None, description: Optional[str] = None) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    fields = {}
    if name is not None and name.strip():
        fields["name"] = name.strip()
    if description is not None:
        fields["description"] = description or None
    if not fields:
        raise ServiceError("VALIDATION_ERROR", "no fields to update")
    with db() as conn:
        before = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not before:
            raise ServiceError("ROLE_NOT_FOUND", f"role {role_id} not found")
        if before["built_in"]:
            raise ServiceError("VALIDATION_ERROR",
                               "built-in roles cannot be edited")
        set_sql = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE roles SET {set_sql} WHERE id=?",
                     list(fields.values()) + [role_id])
        after = dict(conn.execute("SELECT * FROM roles WHERE id=?",
                                  (role_id,)).fetchone())
        audit.log(conn, ctx, action="role.updated", object_type="role",
                  object_id=role_id, before=dict(before), after=after)
    return after


def delete(ctx: ServiceContext, role_id: int) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    with db() as conn:
        before = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not before:
            raise ServiceError("ROLE_NOT_FOUND", f"role {role_id} not found")
        if before["built_in"]:
            raise ServiceError("VALIDATION_ERROR",
                               "built-in roles cannot be deleted")
        n = conn.execute("DELETE FROM user_roles WHERE role_id=?",
                         (role_id,)).rowcount
        conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
        audit.log(conn, ctx, action="role.deleted", object_type="role",
                  object_id=role_id, before=dict(before),
                  after={"revoked_from_users": n})
    return {"id": role_id, "deleted": True, "revoked_from_users": n}


def grant_permission(ctx: ServiceContext, role_id: int, permission: str) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    perm = permission.strip()
    if not perm:
        raise ServiceError("VALIDATION_ERROR", "permission cannot be empty")
    with db() as conn:
        if not conn.execute("SELECT id FROM roles WHERE id=?", (role_id,)).fetchone():
            raise ServiceError("ROLE_NOT_FOUND", f"role {role_id} not found")
        conn.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?,?)",
            (role_id, perm),
        )
        audit.log(conn, ctx, action="role.permission_granted", object_type="role",
                  object_id=role_id, after={"permission": perm})
    return {"role_id": role_id, "permission": perm, "granted": True}


def revoke_permission(ctx: ServiceContext, role_id: int, permission: str) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    with db() as conn:
        conn.execute(
            "DELETE FROM role_permissions WHERE role_id=? AND permission=?",
            (role_id, permission),
        )
        audit.log(conn, ctx, action="role.permission_revoked", object_type="role",
                  object_id=role_id, before={"permission": permission})
    return {"role_id": role_id, "permission": permission, "revoked": True}
