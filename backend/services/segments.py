"""Segments service: static (explicit contact lists) and dynamic (rule-evaluated).

Dynamic segments use a small JSON rule language:

  {
    "all": [
      {"field": "tag", "op": "has", "value": "vip"},
      {"field": "score.opportunity", "op": ">=", "value": 70},
      {"field": "last_interaction_days_ago", "op": "<=", "value": 30}
    ]
  }

Combinators: `all` (every child true), `any` (at least one true), `not` (negate).
Combinators may be nested. Leaves are `{field, op, value}` triples.

Supported fields:
  tag                          — value: string; ops: has, not_has
  tags                         — value: list[str]; ops: has_all, has_any, has_none
  score.<type>                 — type ∈ scoring.SCORE_TYPES; ops: =, !=, >, <, >=, <=
  last_interaction_days_ago    — number; ops: =, !=, >, <, >=, <=, is_null
  interactions_last_90         — number; ops: =, !=, >, <, >=, <=
  email_consent                — string; ops: =, !=  (values: granted | withdrawn | unknown)
  has_open_deal                — boolean; ops: =
  company_id                   — number; ops: =, !=, in
  location                     — string; ops: =, contains
  created_after / created_before — unix seconds; op-less, value is the timestamp

All operators do strict typed compare. Missing-data semantics: when a contact
has no value for a field, the leaf returns False (the contact is excluded).
"""
import json
import re
import time
from typing import Any, Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


# ---------- record assembly ----------

def _records_for_evaluation(conn, contact_ids: Optional[list[int]] = None) -> list[dict]:
    """Build one evaluable dict per active contact.

    Returns a list of dicts shaped like:
      {
        "id": ..., "full_name": ..., "email": ..., "company_id": ...,
        "location": ..., "created_at": ...,
        "tags": {"vip", "lead", ...},                  (lowercased)
        "scores": {"opportunity": 81, "intent": 85, ...},
        "consent": {"email": "granted", ...},
        "last_interaction_days_ago": 3,                (or None)
        "interactions_last_90": 5,
        "has_open_deal": True,
      }
    """
    now = int(time.time())

    where = "deleted_at IS NULL"
    params: list = []
    if contact_ids:
        qmarks = ",".join("?" * len(contact_ids))
        where += f" AND id IN ({qmarks})"
        params = list(contact_ids)

    rows = conn.execute(
        f"SELECT id, full_name, email, company_id, location, created_at "
        f"FROM contacts WHERE {where}",
        params,
    ).fetchall()
    base = {r["id"]: dict(r) for r in rows}
    if not base:
        return []

    ids_csv = ",".join(str(i) for i in base.keys())

    # Tags
    for r in conn.execute(
        f"SELECT ct.contact_id, t.name FROM contact_tags ct "
        f"JOIN tags t ON t.id = ct.tag_id WHERE ct.contact_id IN ({ids_csv})"
    ):
        base[r["contact_id"]].setdefault("tags", set()).add(r["name"].lower())

    # Scores
    for r in conn.execute(
        f"SELECT contact_id, score_type, score FROM contact_scores WHERE contact_id IN ({ids_csv})"
    ):
        base[r["contact_id"]].setdefault("scores", {})[r["score_type"]] = r["score"]

    # Consent
    for r in conn.execute(
        f"SELECT contact_id, channel, status FROM consent WHERE contact_id IN ({ids_csv})"
    ):
        base[r["contact_id"]].setdefault("consent", {})[r["channel"]] = r["status"]

    # Interaction signals
    for r in conn.execute(
        f"""SELECT contact_id,
                   MAX(occurred_at) AS last_at,
                   SUM(CASE WHEN occurred_at >= ? THEN 1 ELSE 0 END) AS cnt_90
              FROM interactions WHERE contact_id IN ({ids_csv})
             GROUP BY contact_id""",
        (now - 90 * 24 * 3600,),
    ):
        rec = base[r["contact_id"]]
        rec["interactions_last_90"] = r["cnt_90"] or 0
        if r["last_at"]:
            rec["last_interaction_days_ago"] = (now - r["last_at"]) // (24 * 3600)
        else:
            rec["last_interaction_days_ago"] = None

    # Open deals
    for r in conn.execute(
        f"SELECT contact_id, COUNT(*) c FROM deals "
        f"WHERE contact_id IN ({ids_csv}) AND status='open' GROUP BY contact_id"
    ):
        base[r["contact_id"]]["has_open_deal"] = r["c"] > 0

    # Defaults for fields no row populated
    for rec in base.values():
        rec.setdefault("tags", set())
        rec.setdefault("scores", {})
        rec.setdefault("consent", {})
        rec.setdefault("last_interaction_days_ago", None)
        rec.setdefault("interactions_last_90", 0)
        rec.setdefault("has_open_deal", False)

    return list(base.values())


# ---------- rule evaluator ----------

def _get_field(rec: dict, field: str) -> Any:
    if field.startswith("score."):
        return rec.get("scores", {}).get(field.split(".", 1)[1])
    if field == "tag":
        return rec.get("tags")            # set used by `has` op
    if field == "tags":
        return rec.get("tags")
    if field == "email_consent":
        return rec.get("consent", {}).get("email")
    return rec.get(field)


def _cmp(op: str, lhs: Any, rhs: Any) -> bool:
    if op == "is_null":
        return lhs is None
    if lhs is None:
        return False
    try:
        if op == "=":   return lhs == rhs
        if op == "!=":  return lhs != rhs
        if op == ">":   return float(lhs) > float(rhs)
        if op == "<":   return float(lhs) < float(rhs)
        if op == ">=":  return float(lhs) >= float(rhs)
        if op == "<=":  return float(lhs) <= float(rhs)
        if op == "in":  return lhs in rhs
        if op == "has":
            v = (rhs or "").lower() if isinstance(rhs, str) else rhs
            return v in (lhs or set())
        if op == "not_has":
            v = (rhs or "").lower() if isinstance(rhs, str) else rhs
            return v not in (lhs or set())
        if op == "has_all":
            wanted = {str(x).lower() for x in (rhs or [])}
            return wanted.issubset(lhs or set())
        if op == "has_any":
            wanted = {str(x).lower() for x in (rhs or [])}
            return bool(wanted & (lhs or set()))
        if op == "has_none":
            wanted = {str(x).lower() for x in (rhs or [])}
            return not (wanted & (lhs or set()))
        if op == "contains":
            return str(rhs).lower() in str(lhs).lower()
    except (TypeError, ValueError):
        return False
    return False


def _eval_node(node: dict, rec: dict) -> bool:
    if not isinstance(node, dict):
        return False
    if "all" in node:
        return all(_eval_node(c, rec) for c in node["all"])
    if "any" in node:
        return any(_eval_node(c, rec) for c in node["any"])
    if "not" in node:
        return not _eval_node(node["not"], rec)
    field = node.get("field"); op = node.get("op"); value = node.get("value")
    if field is None or op is None:
        return False
    return _cmp(op, _get_field(rec, field), value)


# ---------- segment CRUD ----------

def _validate_slug(slug: str):
    if not _SLUG_RE.match(slug or ""):
        raise ServiceError("VALIDATION_ERROR",
                           "slug must be lowercase alphanumeric (with - or _), 2-64 chars")


def create_static(ctx: ServiceContext, *, name: str, slug: str,
                  contact_ids: list[int]) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    _validate_slug(slug)
    if not name:
        raise ServiceError("VALIDATION_ERROR", "name required")
    now = int(time.time())
    with db() as conn:
        if conn.execute("SELECT id FROM segments WHERE slug=?", (slug,)).fetchone():
            raise ServiceError("SEGMENT_SLUG_EXISTS", f"segment slug {slug!r} taken")
        conn.execute(
            """INSERT INTO segments (name, slug, type, rules_json, member_count,
                                     created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, slug, "static", None, len(contact_ids), ctx.user_id, now, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for cid in contact_ids:
            conn.execute(
                "INSERT OR IGNORE INTO segment_members (segment_id, contact_id, added_at) "
                "VALUES (?,?,?)",
                (sid, cid, now),
            )
        audit.log(conn, ctx, action="segment.created", object_type="segment",
                  object_id=sid, after={"slug": slug, "type": "static",
                                        "members": len(contact_ids)})
    return get(ctx, sid)


def create_dynamic(ctx: ServiceContext, *, name: str, slug: str, rules: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    _validate_slug(slug)
    if not name:
        raise ServiceError("VALIDATION_ERROR", "name required")
    if not isinstance(rules, dict):
        raise ServiceError("VALIDATION_ERROR", "rules must be a JSON object")
    now = int(time.time())
    with db() as conn:
        if conn.execute("SELECT id FROM segments WHERE slug=?", (slug,)).fetchone():
            raise ServiceError("SEGMENT_SLUG_EXISTS", f"segment slug {slug!r} taken")
        conn.execute(
            """INSERT INTO segments (name, slug, type, rules_json, member_count,
                                     created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, slug, "dynamic", json.dumps(rules), 0, ctx.user_id, now, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit.log(conn, ctx, action="segment.created", object_type="segment",
                  object_id=sid, after={"slug": slug, "type": "dynamic", "rules": rules})
    evaluate(ctx, sid)
    return get(ctx, sid)


def get(ctx: ServiceContext, segment_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
    if not row:
        raise ServiceError("SEGMENT_NOT_FOUND", f"segment {segment_id} not found")
    return dict(row)


def list_(ctx: ServiceContext) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute("SELECT * FROM segments ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def list_members(ctx: ServiceContext, segment_id: int, *,
                 limit: int = 200, offset: int = 0) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 1000))
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM segment_members WHERE segment_id=?", (segment_id,),
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT c.id, c.full_name, c.email, c.company_id, sm.added_at
                 FROM segment_members sm
                 JOIN contacts c ON c.id = sm.contact_id
                WHERE sm.segment_id=? AND c.deleted_at IS NULL
                ORDER BY sm.added_at DESC, c.id DESC
                LIMIT ? OFFSET ?""",
            (segment_id, limit, offset),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


def evaluate(ctx: ServiceContext, segment_id: int) -> dict:
    """Re-evaluate a dynamic segment: refresh its segment_members table from rules.
    No-op (returns current count) for static segments.
    """
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    seg = get(ctx, segment_id)
    if seg["type"] != "dynamic":
        return {"segment_id": segment_id, "type": seg["type"],
                "member_count": seg["member_count"], "evaluated": False}

    rules = json.loads(seg["rules_json"] or "{}")
    now = int(time.time())
    with db() as conn:
        records = _records_for_evaluation(conn)
        matching = [rec["id"] for rec in records if _eval_node(rules, rec)]
        conn.execute("DELETE FROM segment_members WHERE segment_id=?", (segment_id,))
        for cid in matching:
            conn.execute(
                "INSERT INTO segment_members (segment_id, contact_id, added_at) VALUES (?,?,?)",
                (segment_id, cid, now),
            )
        conn.execute(
            "UPDATE segments SET member_count=?, last_evaluated_at=?, updated_at=? WHERE id=?",
            (len(matching), now, now, segment_id),
        )
        audit.log(conn, ctx, action="segment.evaluated", object_type="segment",
                  object_id=segment_id,
                  after={"matched": len(matching), "candidates": len(records)})
    return {"segment_id": segment_id, "type": "dynamic",
            "member_count": len(matching), "candidates": len(records),
            "evaluated": True}


def update(ctx: ServiceContext, segment_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    import json as _json, time as _t
    with db() as conn:
        row = conn.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone()
        if not row:
            raise ServiceError("SEGMENT_NOT_FOUND", f"segment {segment_id} not found")
        before = dict(row)
        updates = {}
        if "name" in payload and payload["name"]:
            updates["name"] = payload["name"].strip()
        if "rules" in payload and before["type"] == "dynamic":
            updates["rules_json"] = _json.dumps(payload["rules"] or {})
        if "description" in payload:
            updates["description"] = payload["description"] or None
        if not updates:
            raise ServiceError("VALIDATION_ERROR", "no fields to update")
        updates["updated_at"] = int(_t.time())
        set_sql = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE segments SET {set_sql} WHERE id=?",
                     list(updates.values()) + [segment_id])
        after = dict(conn.execute("SELECT * FROM segments WHERE id=?",
                                  (segment_id,)).fetchone())
        audit.log(conn, ctx, action="segment.updated", object_type="segment",
                  object_id=segment_id, before=before, after=after)
    return after


def delete(ctx: ServiceContext, segment_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        row = conn.execute("SELECT slug FROM segments WHERE id=?", (segment_id,)).fetchone()
        if not row:
            raise ServiceError("SEGMENT_NOT_FOUND", f"segment {segment_id} not found")
        conn.execute("DELETE FROM segments WHERE id=?", (segment_id,))
        audit.log(conn, ctx, action="segment.deleted", object_type="segment",
                  object_id=segment_id, before={"slug": row["slug"]})
    return {"id": segment_id, "deleted": True}
