"""Users service: list users, change profile, change password, manage
the built-in 'role' column AND the per-user RBAC role assignments.

The built-in role (admin/user/readonly) lives on users.role and is what
ServiceContext.role reflects on a session. The newer multi-role RBAC
(via roles + user_roles) is additive — it lets you grant fine-grained
permissions on top of the built-in role.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, auth
from .contacts import ServiceError, _row_to_dict


VALID_ROLES = ("admin", "user", "readonly")


def create_user(ctx: ServiceContext, *,
                email: str, password: str,
                display_name: Optional[str] = None,
                role: str = "user") -> dict:
    """Admin-only: create a new user account. New user can sign in
    immediately with the supplied credentials. Email is normalized to
    lowercase; collision raises USER_EMAIL_EXISTS."""
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "creating users requires admin scope")
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        raise ServiceError("VALIDATION_ERROR", "invalid email")
    if not password or len(password) < 8:
        raise ServiceError("VALIDATION_ERROR", "password must be at least 8 characters")
    if role not in VALID_ROLES:
        raise ServiceError("VALIDATION_ERROR", f"role must be one of {VALID_ROLES}")
    now = int(time.time())
    with db() as conn:
        clash = conn.execute("SELECT id FROM users WHERE email=?", (e,)).fetchone()
        if clash:
            raise ServiceError("USER_EMAIL_EXISTS",
                               f"email {e!r} already in use",
                               {"user_id": clash[0]})
        ph = auth.hash_password(password)
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role, "
            "                   created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (e, ph, (display_name or "").strip() or None, role, now, now),
        )
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit.log(conn, ctx, action="user.created", object_type="user",
                  object_id=uid,
                  after={"email": e, "role": role,
                         "display_name": display_name})
    return {"id": uid, "email": e, "role": role,
            "display_name": display_name}


def list_(ctx: ServiceContext) -> list[dict]:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "listing users requires admin scope")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, email, display_name, role, created_at, last_login_at "
            "FROM users ORDER BY id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get(ctx: ServiceContext, user_id: int) -> dict:
    if not (ctx.is_admin() or ctx.user_id == user_id):
        raise ServiceError("FORBIDDEN", "can only view your own profile (unless admin)")
    with db() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, role, created_at, last_login_at "
            "FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise ServiceError("USER_NOT_FOUND", f"user {user_id} not found")
    return _row_to_dict(row)


def update_profile(ctx: ServiceContext, user_id: int, *,
                   email: Optional[str] = None,
                   display_name: Optional[str] = None) -> dict:
    if not (ctx.is_admin() or ctx.user_id == user_id):
        raise ServiceError("FORBIDDEN", "can only edit your own profile (unless admin)")
    fields = {}
    if email is not None:
        e = email.strip().lower()
        if "@" not in e:
            raise ServiceError("VALIDATION_ERROR", "invalid email")
        fields["email"] = e
    if display_name is not None:
        fields["display_name"] = display_name.strip() or None
    if not fields:
        raise ServiceError("VALIDATION_ERROR", "no fields to update")
    fields["updated_at"] = int(time.time())
    with db() as conn:
        before = conn.execute(
            "SELECT id, email, display_name, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not before:
            raise ServiceError("USER_NOT_FOUND", f"user {user_id} not found")
        before = dict(before)
        # Email uniqueness check
        if "email" in fields and fields["email"] != before["email"]:
            clash = conn.execute(
                "SELECT id FROM users WHERE email=? AND id != ?",
                (fields["email"], user_id),
            ).fetchone()
            if clash:
                raise ServiceError("USER_EMAIL_EXISTS",
                                   f"email {fields['email']!r} already in use",
                                   {"user_id": clash[0]})
        set_sql = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE users SET {set_sql} WHERE id=?",
                     list(fields.values()) + [user_id])
        after = dict(conn.execute(
            "SELECT id, email, display_name, role FROM users WHERE id=?", (user_id,)
        ).fetchone())
        audit.log(conn, ctx, action="user.updated", object_type="user",
                  object_id=user_id, before=before, after=after)
    return after


def change_password(ctx: ServiceContext, user_id: int, *,
                    current_password: Optional[str], new_password: str) -> dict:
    """Self-service password change. Admins can change others' passwords
    without supplying the current password."""
    if ctx.user_id != user_id and not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "only self or admin can change a password")
    if not new_password or len(new_password) < 8:
        raise ServiceError("VALIDATION_ERROR", "new password must be at least 8 characters")
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?",
                           (user_id,)).fetchone()
        if not row:
            raise ServiceError("USER_NOT_FOUND", f"user {user_id} not found")
        # Self-service path requires the current password
        if ctx.user_id == user_id and not ctx.is_admin():
            if not current_password or not auth.verify_password(current_password,
                                                                 row["password_hash"]):
                raise ServiceError("VALIDATION_ERROR", "current password is incorrect")
        new_hash = auth.hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
            (new_hash, int(time.time()), user_id),
        )
        audit.log(conn, ctx, action="user.password_changed", object_type="user",
                  object_id=user_id)
    return {"id": user_id, "ok": True}


def set_role(ctx: ServiceContext, user_id: int, role: str) -> dict:
    """Change the built-in role column. Admin only."""
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    if role not in VALID_ROLES:
        raise ServiceError("VALIDATION_ERROR", f"role must be one of {VALID_ROLES}")
    with db() as conn:
        row = conn.execute("SELECT id, role FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ServiceError("USER_NOT_FOUND", f"user {user_id} not found")
        if row["role"] == role:
            return {"id": user_id, "role": role}
        conn.execute(
            "UPDATE users SET role=?, updated_at=? WHERE id=?",
            (role, int(time.time()), user_id),
        )
        audit.log(conn, ctx, action="user.role_changed", object_type="user",
                  object_id=user_id,
                  before={"role": row["role"]}, after={"role": role})
    return {"id": user_id, "role": role}


def list_sessions(ctx: ServiceContext, user_id: int) -> list[dict]:
    if ctx.user_id != user_id and not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "only self or admin can list sessions")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, created_at, last_seen_at, expires_at FROM sessions "
            "WHERE user_id=? ORDER BY last_seen_at DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_session(ctx: ServiceContext, session_id: str) -> dict:
    """Self can revoke any of their own sessions; admin can revoke any."""
    with db() as conn:
        row = conn.execute("SELECT user_id FROM sessions WHERE id=?",
                           (session_id,)).fetchone()
        if not row:
            raise ServiceError("SESSION_NOT_FOUND", "session not found")
        if row["user_id"] != ctx.user_id and not ctx.is_admin():
            raise ServiceError("FORBIDDEN", "cannot revoke another user's session")
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        audit.log(conn, ctx, action="session.revoked", object_type="session",
                  object_id=None,
                  before={"session_id": session_id[:8] + "…",
                          "user_id": row["user_id"]})
    return {"id": session_id, "revoked": True}


# ---------- RBAC role assignment (the additive layer) ----------

def list_role_assignments(ctx: ServiceContext, user_id: int) -> list[dict]:
    if ctx.user_id != user_id and not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "only self or admin can list role assignments")
    with db() as conn:
        rows = conn.execute(
            "SELECT r.id, r.name, r.description, ur.granted_at, ur.granted_by "
            "FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id=? ORDER BY r.name",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def grant_role(ctx: ServiceContext, user_id: int, role_id: int) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    with db() as conn:
        if not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
            raise ServiceError("USER_NOT_FOUND", f"user {user_id} not found")
        if not conn.execute("SELECT id FROM roles WHERE id=?", (role_id,)).fetchone():
            raise ServiceError("ROLE_NOT_FOUND", f"role {role_id} not found")
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id, granted_at, granted_by) "
            "VALUES (?,?,?,?)",
            (user_id, role_id, int(time.time()), ctx.user_id),
        )
        audit.log(conn, ctx, action="user.role_granted", object_type="user",
                  object_id=user_id, after={"role_id": role_id})
    return {"user_id": user_id, "role_id": role_id, "granted": True}


def revoke_role(ctx: ServiceContext, user_id: int, role_id: int) -> dict:
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "admin only")
    with db() as conn:
        conn.execute("DELETE FROM user_roles WHERE user_id=? AND role_id=?",
                     (user_id, role_id))
        audit.log(conn, ctx, action="user.role_revoked", object_type="user",
                  object_id=user_id, before={"role_id": role_id})
    return {"user_id": user_id, "role_id": role_id, "revoked": True}
