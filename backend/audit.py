"""Mutation logger.

Every service-layer mutation calls `audit.log(conn, ctx, ...)` inside the same
transaction as the data change. The acting principal comes from ServiceContext;
caller-supplied user_id / api_key_id is never trusted.

`before` and `after` are JSON-serialized; pass plain dicts/lists/scalars. Use
None on create (no before) or on hard delete (no after).
"""
import json
import time
from typing import Any, Optional

from .context import ServiceContext


def log(
    conn,
    ctx: ServiceContext,
    *,
    action: str,
    object_type: str,
    object_id: Optional[int],
    before: Optional[Any] = None,
    after: Optional[Any] = None,
) -> int:
    now = int(time.time())
    conn.execute(
        """INSERT INTO audit_log
             (ts, user_id, api_key_id, surface, action,
              object_type, object_id, before_json, after_json, request_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            now,
            ctx.user_id,
            ctx.api_key_id,
            ctx.surface,
            action,
            object_type,
            object_id,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
            ctx.request_id,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
