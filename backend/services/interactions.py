"""Interactions service. Append-only timeline event firehose.

interactions.type is a string enum:
  email | call | meeting | form_submission | page_view | note_system | system

Every meaningful action lands here. The metadata_json shape varies per type
(see docs/interactions.md when written).
"""
import json
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict


VALID_TYPES = {"email", "call", "meeting", "form_submission",
               "page_view", "note_system", "system"}


def log(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if payload.get("type") not in VALID_TYPES:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"interaction.type must be one of {sorted(VALID_TYPES)}",
            {"got": payload.get("type")},
        )
    if not (payload.get("contact_id") or payload.get("company_id")):
        raise ServiceError("VALIDATION_ERROR",
                           "interaction requires contact_id or company_id")
    now = int(time.time())
    occurred_at = int(payload.get("occurred_at") or now)

    with db() as conn:
        if payload.get("contact_id"):
            r = conn.execute(
                "SELECT id FROM contacts WHERE id = ? AND deleted_at IS NULL",
                (payload["contact_id"],),
            ).fetchone()
            if not r:
                raise ServiceError("CONTACT_NOT_FOUND",
                                   f"contact {payload['contact_id']} not found")
        if payload.get("company_id"):
            r = conn.execute(
                "SELECT id FROM companies WHERE id = ? AND deleted_at IS NULL",
                (payload["company_id"],),
            ).fetchone()
            if not r:
                raise ServiceError("COMPANY_NOT_FOUND",
                                   f"company {payload['company_id']} not found")

        meta = payload.get("metadata_json")
        if isinstance(meta, (dict, list)):
            meta = json.dumps(meta, default=str)

        conn.execute(
            """INSERT INTO interactions
                 (contact_id, company_id, type, channel, title, body,
                  metadata_json, source, occurred_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                payload.get("contact_id"), payload.get("company_id"),
                payload["type"], payload.get("channel"),
                payload.get("title"), payload.get("body"),
                meta, payload.get("source") or ctx.surface,
                occurred_at, now,
            ),
        )
        iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM interactions WHERE id = ?", (iid,)).fetchone()
        item = _row_to_dict(row)
        audit.log(conn, ctx, action="interaction.logged",
                  object_type="interaction", object_id=iid, after=item)
        webhooks.enqueue(conn, "interaction.logged", {"interaction": item})
    return item


def list_for_contact(ctx: ServiceContext, contact_id: int, *,
                     limit: int = 50, offset: int = 0) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 200))
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM interactions WHERE contact_id = ?
                ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?""",
            (contact_id, limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_for_company(ctx: ServiceContext, company_id: int, *,
                     limit: int = 50, offset: int = 0) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 200))
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM interactions WHERE company_id = ?
                ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?""",
            (company_id, limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
