"""API key lifecycle. Thin wrapper around backend.auth + audit + permissions."""
import time
from typing import Optional

from .. import auth as auth_mod, audit
from ..context import ServiceContext
from ..db import db
from .contacts import ServiceError


VALID_SCOPES = ("read", "write", "admin")


def create(ctx: ServiceContext, user_id: int, name: str, scope: str = "write") -> dict:
    if not ctx.is_admin() and ctx.user_id != user_id:
        raise ServiceError("FORBIDDEN", "can only create keys for self unless admin")
    if scope not in VALID_SCOPES:
        raise ServiceError("VALIDATION_ERROR", f"scope must be one of {VALID_SCOPES}")
    if not name or not name.strip():
        raise ServiceError("VALIDATION_ERROR", "name required")
    raw, prefix, key_hash = auth_mod.generate_api_key()
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, name.strip()[:80], prefix, key_hash, scope, now),
        )
        kid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit.log(conn, ctx, action="api_key.created",
                  object_type="api_key", object_id=kid,
                  after={"name": name, "scope": scope, "key_prefix": prefix, "user_id": user_id})
    return {"id": kid, "raw_key": raw, "key_prefix": prefix,
            "scope": scope, "user_id": user_id}


def revoke(ctx: ServiceContext, key_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT user_id, revoked_at FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise ServiceError("API_KEY_NOT_FOUND", f"api_key {key_id} not found")
        if not ctx.is_admin() and ctx.user_id != row["user_id"]:
            raise ServiceError("FORBIDDEN", "can only revoke own keys unless admin")
        if row["revoked_at"]:
            return {"id": key_id, "revoked": True, "already": True}
        auth_mod.revoke_api_key(conn, key_id)
        audit.log(conn, ctx, action="api_key.revoked",
                  object_type="api_key", object_id=key_id)
    return {"id": key_id, "revoked": True}


def list_for_user(ctx: ServiceContext, user_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    if not ctx.is_admin() and ctx.user_id != user_id:
        raise ServiceError("FORBIDDEN", "can only list own keys unless admin")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, key_prefix, scope, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
