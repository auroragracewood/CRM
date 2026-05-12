"""Pipelines + stages service."""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError


_PIPELINE_FIELDS = ("name", "type", "description", "archived")


def create_pipeline(ctx: ServiceContext, payload: dict, stages: Optional[list[dict]] = None) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    name = (payload.get("name") or "").strip()
    ptype = (payload.get("type") or "").strip()
    if not name or not ptype:
        raise ServiceError("VALIDATION_ERROR", "pipeline requires name and type")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO pipelines (name, type, description, archived, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (name, ptype, payload.get("description"), 0, now, now),
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if stages:
            for i, s in enumerate(stages):
                conn.execute(
                    "INSERT INTO pipeline_stages (pipeline_id, name, position, is_won, is_lost, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (pid, s["name"], s.get("position", i),
                     1 if s.get("is_won") else 0, 1 if s.get("is_lost") else 0, now),
                )
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pid,)).fetchone()
        result = dict(row)
        result["stages"] = [dict(r) for r in conn.execute(
            "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY position", (pid,)
        ).fetchall()]
        audit.log(conn, ctx, action="pipeline.created", object_type="pipeline",
                  object_id=pid, after=result)
        webhooks.enqueue(conn, "pipeline.created", {"pipeline": result})
    return result


def get_pipeline(ctx: ServiceContext, pipeline_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        if not row:
            raise ServiceError("PIPELINE_NOT_FOUND", f"pipeline {pipeline_id} not found")
        result = dict(row)
        result["stages"] = [dict(r) for r in conn.execute(
            "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY position", (pipeline_id,)
        ).fetchall()]
    return result


def list_pipelines(ctx: ServiceContext, *, include_archived: bool = False) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        sql = "SELECT * FROM pipelines"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY id ASC"
        rows = conn.execute(sql).fetchall()
        out = []
        for r in rows:
            p = dict(r)
            p["stages"] = [dict(s) for s in conn.execute(
                "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY position", (p["id"],)
            ).fetchall()]
            out.append(p)
    return out


def add_stage(ctx: ServiceContext, pipeline_id: int, name: str, *,
              position: Optional[int] = None,
              is_won: bool = False, is_lost: bool = False) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        if position is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1)+1 FROM pipeline_stages WHERE pipeline_id=?",
                (pipeline_id,),
            ).fetchone()
            position = row[0]
        conn.execute(
            "INSERT INTO pipeline_stages (pipeline_id, name, position, is_won, is_lost, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (pipeline_id, name.strip(), position, 1 if is_won else 0, 1 if is_lost else 0, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit.log(conn, ctx, action="stage.created", object_type="stage",
                  object_id=sid, after={"pipeline_id": pipeline_id, "name": name, "position": position})
    return {"id": sid, "pipeline_id": pipeline_id, "name": name, "position": position}


def archive_pipeline(ctx: ServiceContext, pipeline_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "UPDATE pipelines SET archived=1, updated_at=? WHERE id=?",
            (now, pipeline_id),
        )
        audit.log(conn, ctx, action="pipeline.archived", object_type="pipeline",
                  object_id=pipeline_id)
    return {"id": pipeline_id, "archived": True}


def unarchive_pipeline(ctx: ServiceContext, pipeline_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "UPDATE pipelines SET archived=0, updated_at=? WHERE id=?",
            (now, pipeline_id),
        )
        audit.log(conn, ctx, action="pipeline.unarchived", object_type="pipeline",
                  object_id=pipeline_id)
    return {"id": pipeline_id, "archived": False}


# Common pipeline templates (referenced by setup-style seeding or CLI).
TEMPLATES = {
    "sales": [
        ("New Lead", False, False),
        ("Qualified", False, False),
        ("Discovery", False, False),
        ("Proposal", False, False),
        ("Negotiation", False, False),
        ("Won", True, False),
        ("Lost", False, True),
        ("Nurture", False, False),
    ],
    "client": [
        ("Inquiry", False, False),
        ("Intake", False, False),
        ("Scope", False, False),
        ("Quote", False, False),
        ("Approved", False, False),
        ("In Production", False, False),
        ("Review", False, False),
        ("Delivered", True, False),
        ("Follow-up", False, False),
    ],
    "sponsor": [
        ("Identified", False, False),
        ("Researched", False, False),
        ("Contacted", False, False),
        ("Interested", False, False),
        ("Deck Sent", False, False),
        ("Call Booked", False, False),
        ("Proposal Sent", False, False),
        ("Negotiating", False, False),
        ("Confirmed", True, False),
        ("Lost", False, True),
    ],
}


def create_from_template(ctx: ServiceContext, name: str, template: str) -> dict:
    if template not in TEMPLATES:
        raise ServiceError("VALIDATION_ERROR",
                           f"template must be one of {sorted(TEMPLATES)}")
    stages = [{"name": n, "is_won": w, "is_lost": l} for n, w, l in TEMPLATES[template]]
    return create_pipeline(ctx, {"name": name, "type": template,
                                 "description": f"Created from {template} template"},
                           stages=stages)
