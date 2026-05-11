"""Consent service. Per-contact, per-channel records.

v0 records consent; v1 will enforce it (block outbound sends when consent
missing). This service is the table-writing API regardless of enforcement.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


VALID_STATUS = ("granted", "withdrawn", "unknown")


def record(
    ctx: ServiceContext,
    contact_id: int,
    channel: str,
    status: str,
    *,
    source: Optional[str] = None,
    proof: Optional[str] = None,
) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if status not in VALID_STATUS:
        raise ServiceError("VALIDATION_ERROR", f"status must be one of {VALID_STATUS}")
    if not channel:
        raise ServiceError("VALIDATION_ERROR", "channel required")
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT id, granted_at, withdrawn_at FROM consent "
            "WHERE contact_id = ? AND channel = ?",
            (contact_id, channel),
        ).fetchone()
        if row:
            granted_at = now if status == "granted" else row["granted_at"]
            withdrawn_at = now if status == "withdrawn" else row["withdrawn_at"]
            conn.execute(
                """UPDATE consent
                     SET status = ?, source = ?, proof = ?,
                         granted_at = ?, withdrawn_at = ?, updated_at = ?
                   WHERE id = ?""",
                (status, source, proof, granted_at, withdrawn_at, now, row["id"]),
            )
            cid = row["id"]
        else:
            conn.execute(
                """INSERT INTO consent
                     (contact_id, channel, status, source, proof,
                      granted_at, withdrawn_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    contact_id, channel, status, source, proof,
                    now if status == "granted" else None,
                    now if status == "withdrawn" else None,
                    now, now,
                ),
            )
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        result = dict(conn.execute("SELECT * FROM consent WHERE id = ?", (cid,)).fetchone())
        audit.log(conn, ctx, action="consent.recorded",
                  object_type="consent", object_id=cid, after=result)
    return result


def list_for_contact(ctx: ServiceContext, contact_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM consent WHERE contact_id = ? ORDER BY channel",
            (contact_id,),
        ).fetchall()
    return [dict(r) for r in rows]
