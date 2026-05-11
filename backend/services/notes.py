"""Notes service. Visibility-scoped (public / team / private).

Private notes are hidden by default even from admins. Admin reveal is an
explicit action that writes `note.private_revealed` to audit_log per fetch.
Private notes never appear in webhook payloads.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict


VISIBILITIES = ("public", "team", "private")


def create(
    ctx: ServiceContext, *,
    contact_id: Optional[int] = None,
    company_id: Optional[int] = None,
    body: str,
    visibility: str = "team",
) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if visibility not in VISIBILITIES:
        raise ServiceError("VALIDATION_ERROR",
                           f"visibility must be one of {VISIBILITIES}")
    if not (contact_id or company_id):
        raise ServiceError("VALIDATION_ERROR",
                           "note requires contact_id or company_id")
    if not body or not body.strip():
        raise ServiceError("VALIDATION_ERROR", "note body cannot be empty")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            """INSERT INTO notes (contact_id, company_id, body, visibility,
                                  created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (contact_id, company_id, body, visibility, ctx.user_id, now, now),
        )
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
        note = _row_to_dict(row)
        audit.log(conn, ctx, action="note.created", object_type="note",
                  object_id=nid,
                  after={"id": nid, "visibility": visibility,
                         "contact_id": contact_id, "company_id": company_id,
                         "created_by": ctx.user_id})
        redact = ["body"] if visibility == "private" else None
        webhooks.enqueue(conn, "note.created", dict(note), redact_keys=redact)
    return note


def _can_see_without_reveal(ctx: ServiceContext, note: dict) -> bool:
    if note["visibility"] == "public":
        return True
    if note["visibility"] == "team":
        return ctx.scope in ("write", "admin")
    return note["visibility"] == "private" and note.get("created_by") == ctx.user_id


def _strip_private(note: dict) -> dict:
    n = dict(note)
    n["body"] = None
    n["_private_redacted"] = True
    return n


def list_for_contact(ctx: ServiceContext, contact_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM notes WHERE contact_id = ?
                ORDER BY created_at DESC, id DESC""",
            (contact_id,),
        ).fetchall()
    out = []
    for r in rows:
        note = _row_to_dict(r)
        if _can_see_without_reveal(ctx, note):
            out.append(note)
        elif note["visibility"] == "private" and ctx.is_admin():
            out.append(_strip_private(note))
    return out


def reveal_private(ctx: ServiceContext, note_id: int) -> dict:
    """Explicit private-note reveal. Writes an audit row per call."""
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "private note reveal requires admin scope")
    with db() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            raise ServiceError("NOTE_NOT_FOUND", f"note {note_id} not found")
        note = _row_to_dict(row)
        if note["visibility"] != "private":
            return note
        audit.log(conn, ctx, action="note.private_revealed",
                  object_type="note", object_id=note_id,
                  before={"visibility": "private"})
    return note
