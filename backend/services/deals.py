"""Deals service. Lives on a pipeline + stage; soft-status (open/won/lost/nurture)."""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict
from . import plugins as _plugins  # type: ignore


_FIELDS = (
    "contact_id", "company_id", "pipeline_id", "stage_id", "title",
    "value_cents", "currency", "probability", "expected_close",
    "status", "next_step", "notes", "assigned_to",
)
_VALID_STATUS = ("open", "won", "lost", "nurture")


def _validate(payload: dict, *, is_create: bool) -> dict:
    cleaned = {k: payload.get(k) for k in _FIELDS}
    if is_create:
        if not cleaned.get("title"):
            raise ServiceError("VALIDATION_ERROR", "deal requires title")
        if not cleaned.get("pipeline_id"):
            raise ServiceError("VALIDATION_ERROR", "deal requires pipeline_id")
        if not cleaned.get("stage_id"):
            raise ServiceError("VALIDATION_ERROR", "deal requires stage_id")
    if cleaned.get("status") and cleaned["status"] not in _VALID_STATUS:
        raise ServiceError("VALIDATION_ERROR",
                           f"status must be one of {_VALID_STATUS}")
    if cleaned.get("probability") is not None:
        p = int(cleaned["probability"])
        if not 0 <= p <= 100:
            raise ServiceError("VALIDATION_ERROR", "probability must be 0-100")
        cleaned["probability"] = p
    if cleaned.get("currency"):
        cleaned["currency"] = cleaned["currency"].strip().lower()
    return cleaned


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate(payload, is_create=True)
    now = int(time.time())
    with db() as conn:
        # verify stage belongs to pipeline
        stg = conn.execute(
            "SELECT pipeline_id, is_won, is_lost FROM pipeline_stages WHERE id=?",
            (cleaned["stage_id"],),
        ).fetchone()
        if not stg or stg["pipeline_id"] != cleaned["pipeline_id"]:
            raise ServiceError("VALIDATION_ERROR",
                               "stage_id does not belong to pipeline_id")
        # auto status from stage
        if not cleaned.get("status"):
            cleaned["status"] = "won" if stg["is_won"] else ("lost" if stg["is_lost"] else "open")

        cols = list(cleaned.keys()) + ["created_at", "updated_at"]
        vals = list(cleaned.values()) + [now, now]
        ph = ",".join("?" * len(cols))
        conn.execute(f"INSERT INTO deals ({','.join(cols)}) VALUES ({ph})", vals)
        did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
        deal = _row_to_dict(row)
        audit.log(conn, ctx, action="deal.created", object_type="deal",
                  object_id=did, after=deal)
        webhooks.enqueue(conn, "deal.created", {"deal": deal})
        _plugins.dispatch("on_deal_created", ctx, deal, conn)
    return deal


def get(ctx: ServiceContext, deal_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if not row:
        raise ServiceError("DEAL_NOT_FOUND", f"deal {deal_id} not found")
    return _row_to_dict(row)


def list_(ctx: ServiceContext, *,
          pipeline_id: Optional[int] = None,
          stage_id: Optional[int] = None,
          status: Optional[str] = None,
          assigned_to: Optional[int] = None,
          contact_id: Optional[int] = None,
          company_id: Optional[int] = None,
          limit: int = 100, offset: int = 0) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    where, params = [], []
    for col, val in (("pipeline_id", pipeline_id), ("stage_id", stage_id),
                     ("status", status), ("assigned_to", assigned_to),
                     ("contact_id", contact_id), ("company_id", company_id)):
        if val is not None:
            where.append(f"{col} = ?")
            params.append(val)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM deals{where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM deals{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


def update(ctx: ServiceContext, deal_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate({k: v for k, v in payload.items() if k in _FIELDS}, is_create=False)
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")
    now = int(time.time())
    with db() as conn:
        before_row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if not before_row:
            raise ServiceError("DEAL_NOT_FOUND", f"deal {deal_id} not found")
        before = _row_to_dict(before_row)

        # if moving stage, auto-update status from stage flags + closed_at
        if "stage_id" in cleaned and cleaned["stage_id"]:
            stg = conn.execute(
                "SELECT pipeline_id, is_won, is_lost FROM pipeline_stages WHERE id=?",
                (cleaned["stage_id"],),
            ).fetchone()
            if not stg:
                raise ServiceError("VALIDATION_ERROR", "stage_id not found")
            pid = cleaned.get("pipeline_id") or before["pipeline_id"]
            if stg["pipeline_id"] != pid:
                raise ServiceError("VALIDATION_ERROR",
                                   "stage_id does not belong to this deal's pipeline")
            if "status" not in cleaned:
                cleaned["status"] = "won" if stg["is_won"] else ("lost" if stg["is_lost"] else "open")

        set_clauses = [f"{k} = ?" for k in cleaned]
        set_clauses.append("updated_at = ?")
        params = list(cleaned.values()) + [now]
        if cleaned.get("status") in ("won", "lost") and not before["closed_at"]:
            set_clauses.append("closed_at = ?")
            params.append(now)
        params.append(deal_id)
        conn.execute(f"UPDATE deals SET {', '.join(set_clauses)} WHERE id=?", params)

        after_row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        after = _row_to_dict(after_row)

        if before["stage_id"] != after["stage_id"]:
            webhooks.enqueue(conn, "deal.stage_changed",
                             {"deal": after, "from_stage": before["stage_id"],
                              "to_stage": after["stage_id"]})
            _plugins.dispatch("on_deal_stage_changed", ctx, after,
                              before["stage_id"], after["stage_id"], conn)
        audit.log(conn, ctx, action="deal.updated", object_type="deal",
                  object_id=deal_id, before=before, after=after)
        webhooks.enqueue(conn, "deal.updated", {"deal": after})
        _plugins.dispatch("on_deal_updated", ctx, before, after, conn)
    return after


def delete(ctx: ServiceContext, deal_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if not row:
            raise ServiceError("DEAL_NOT_FOUND", f"deal {deal_id} not found")
        before = _row_to_dict(row)
        conn.execute("DELETE FROM deals WHERE id=?", (deal_id,))
        audit.log(conn, ctx, action="deal.deleted", object_type="deal",
                  object_id=deal_id, before=before)
        webhooks.enqueue(conn, "deal.deleted", {"deal_id": deal_id})
    return {"id": deal_id, "deleted": True}
