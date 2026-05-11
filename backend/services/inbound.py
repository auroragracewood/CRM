"""Inbound webhook receiver service.

Each `inbound_endpoint` is a slug-named URL listener at /in/{slug}. External
systems (payment processors, marketing tools, custom integrations) POST events
to it. The CRM:

  1. Logs the raw payload (always) into `inbound_events`
  2. Verifies the HMAC signature if a shared_secret is configured
  3. Parses the payload per the endpoint's routing rules
  4. Matches-or-creates a contact by email path
  5. Applies routing tags
  6. Logs an interaction on the contact's timeline
  7. Updates the inbound_events row with the resolved contact + interaction ids

Routing rules (routing_json shape):
  {
    "type":           "system",                       # interaction.type
    "email_path":     "data.email",                   # dot path into payload
    "name_path":      "data.name",                    # optional
    "title_template": "Stripe event: {data.event}",   # {dot.path} substitutions
    "tags":           ["webhook", "stripe"],          # always-attached
    "create_contact": true                            # default true
  }
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any, Optional

from ..context import ServiceContext, system_context
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError
from . import plugins as _plugins  # type: ignore


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


def _get_path(obj: Any, path: str) -> Any:
    """Walk a dot-path through nested dicts/lists. Returns None on miss."""
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _interpolate(template: str, payload: dict) -> str:
    """Replace {dot.path.refs} in `template` with values from payload."""
    def repl(m):
        v = _get_path(payload, m.group(1))
        return str(v) if v is not None else ""
    return re.sub(r"\{([^}]+)\}", repl, template or "")


# ---------- endpoint management (admin) ----------

def create_endpoint(
    ctx: ServiceContext, *,
    slug: str,
    name: str,
    description: Optional[str] = None,
    routing: Optional[dict] = None,
    generate_secret: bool = True,
) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if not _SLUG_RE.match(slug or ""):
        raise ServiceError("VALIDATION_ERROR",
                           "slug must be lowercase alphanumeric (- or _), 2-64 chars")
    if not name or not name.strip():
        raise ServiceError("VALIDATION_ERROR", "name required")
    now = int(time.time())
    shared_secret = secrets.token_urlsafe(24) if generate_secret else None
    with db() as conn:
        if conn.execute("SELECT id FROM inbound_endpoints WHERE slug=?", (slug,)).fetchone():
            raise ServiceError("INBOUND_SLUG_EXISTS",
                               f"slug {slug!r} already in use")
        conn.execute(
            """INSERT INTO inbound_endpoints
                 (slug, name, description, shared_secret, active,
                  routing_json, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                slug, name, description, shared_secret, 1,
                json.dumps(routing or {}), ctx.user_id, now, now,
            ),
        )
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM inbound_endpoints WHERE id=?", (eid,)).fetchone()
        audit.log(conn, ctx, action="inbound_endpoint.created",
                  object_type="inbound_endpoint", object_id=eid,
                  after={"slug": slug, "name": name})
    return dict(row)


def list_endpoints(ctx: ServiceContext) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM inbound_endpoints ORDER BY id DESC",
        ).fetchall()
    return [dict(r) for r in rows]


def get_endpoint(ctx: ServiceContext, endpoint_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM inbound_endpoints WHERE id=?", (endpoint_id,),
        ).fetchone()
    if not row:
        raise ServiceError("INBOUND_ENDPOINT_NOT_FOUND",
                           f"inbound endpoint {endpoint_id} not found")
    return dict(row)


def list_events(ctx: ServiceContext, endpoint_id: int, *,
                limit: int = 100, offset: int = 0) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 500))
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM inbound_events WHERE endpoint_id=?", (endpoint_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM inbound_events WHERE endpoint_id=? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (endpoint_id, limit, offset),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


def delete_endpoint(ctx: ServiceContext, endpoint_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    with db() as conn:
        row = conn.execute(
            "SELECT slug FROM inbound_endpoints WHERE id=?", (endpoint_id,),
        ).fetchone()
        if not row:
            raise ServiceError("INBOUND_ENDPOINT_NOT_FOUND",
                               f"inbound endpoint {endpoint_id} not found")
        conn.execute("DELETE FROM inbound_endpoints WHERE id=?", (endpoint_id,))
        audit.log(conn, ctx, action="inbound_endpoint.deleted",
                  object_type="inbound_endpoint", object_id=endpoint_id,
                  before={"slug": row["slug"]})
    return {"id": endpoint_id, "deleted": True}


# ---------- receive (public path) ----------

def receive(
    slug: str,
    raw_body: bytes,
    *,
    headers: Optional[dict] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Accept an inbound POST. Always stores the raw payload, then attempts
    to parse + match-or-create a contact. Returns {event_id, status}."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    now = int(time.time())

    with db() as conn:
        ep_row = conn.execute(
            "SELECT * FROM inbound_endpoints WHERE slug=? AND active=1", (slug,),
        ).fetchone()
        if not ep_row:
            raise ServiceError("INBOUND_ENDPOINT_NOT_FOUND",
                               f"inbound endpoint {slug!r} not found or inactive")
        ep = dict(ep_row)

        # Optional HMAC verify
        sig_valid = False
        if ep["shared_secret"]:
            received_sig = headers.get("x-crm-inbound-signature") or ""
            expected = hmac.new(
                ep["shared_secret"].encode(), raw_body, hashlib.sha256,
            ).hexdigest()
            sig_valid = hmac.compare_digest(received_sig, expected)
        else:
            sig_valid = True   # no secret = no verification required

        # Always store the raw event first so we have provenance even on parse failure
        conn.execute(
            """INSERT INTO inbound_events
                 (endpoint_id, raw_payload, ip, user_agent, signature_valid,
                  status, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (ep["id"], raw_body.decode("utf-8", errors="replace"),
             ip, user_agent, 1 if sig_valid else 0, "received", now),
        )
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "UPDATE inbound_endpoints SET last_received_at=? WHERE id=?",
            (now, ep["id"]),
        )

        if not sig_valid:
            conn.execute(
                "UPDATE inbound_events SET status='error', error=? WHERE id=?",
                ("invalid HMAC signature", eid),
            )
            return {"event_id": eid, "status": "error",
                    "error": "invalid HMAC signature"}

        # Parse
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            conn.execute(
                "UPDATE inbound_events SET status='error', error=? WHERE id=?",
                (f"invalid JSON: {e}", eid),
            )
            return {"event_id": eid, "status": "error", "error": f"invalid JSON: {e}"}

        routing = json.loads(ep["routing_json"] or "{}")
        email = _get_path(payload, routing.get("email_path") or "")
        name = _get_path(payload, routing.get("name_path") or "")
        title = _interpolate(
            routing.get("title_template") or "Inbound event ({slug})".replace("{slug}", slug),
            payload,
        )
        itype = routing.get("type") or "system"
        if itype not in {"email","call","meeting","form_submission",
                         "page_view","note_system","system"}:
            itype = "system"

        # Match-or-create contact
        contact_id = None
        create_contact = routing.get("create_contact", True)
        if email:
            email = str(email).strip().lower()
            existing = conn.execute(
                "SELECT id FROM contacts WHERE email=? AND deleted_at IS NULL",
                (email,),
            ).fetchone()
            if existing:
                contact_id = existing[0]
            elif create_contact:
                conn.execute(
                    "INSERT INTO contacts (full_name, email, created_at, updated_at) "
                    "VALUES (?,?,?,?)",
                    (name, email, now, now),
                )
                contact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Tag the contact per routing (always-attached tags + auto-creation)
        if contact_id and routing.get("tags"):
            for tag_name in routing["tags"]:
                tag_name = str(tag_name).strip()
                if not tag_name: continue
                row_t = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
                if row_t:
                    tag_id = row_t[0]
                else:
                    conn.execute(
                        "INSERT INTO tags (name, scope, created_at) VALUES (?,?,?)",
                        (tag_name, "any", now),
                    )
                    tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """INSERT OR IGNORE INTO contact_tags
                         (contact_id, tag_id, added_at, added_by)
                       VALUES (?,?,?,?)""",
                    (contact_id, tag_id, now, None),
                )

        # Log the interaction on the timeline
        interaction_id = None
        if contact_id:
            conn.execute(
                """INSERT INTO interactions
                     (contact_id, type, channel, title, body,
                      metadata_json, source, occurred_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    contact_id, itype, "inbound",
                    title or f"Inbound event ({slug})",
                    None,
                    json.dumps({"endpoint_slug": slug, "event_id": eid,
                                "payload": payload}),
                    f"inbound:{slug}", now, now,
                ),
            )
            interaction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        status = "contact_linked" if contact_id else "parsed"
        conn.execute(
            "UPDATE inbound_events SET contact_id=?, interaction_id=?, status=? WHERE id=?",
            (contact_id, interaction_id, status, eid),
        )

        # Audit (system surface) + outbound webhook fan-out
        ctx = system_context()
        ctx.surface = "webhook"
        audit.log(conn, ctx, action="inbound.received",
                  object_type="inbound_event", object_id=eid,
                  after={"endpoint_slug": slug, "contact_id": contact_id,
                         "interaction_id": interaction_id})
        event_summary = {
            "event_id": eid, "endpoint_slug": slug,
            "contact_id": contact_id, "interaction_id": interaction_id,
        }
        webhooks.enqueue(conn, "inbound.received", event_summary)
        _plugins.dispatch("on_inbound_received", ctx, event_summary, conn)

    return {
        "event_id": eid, "status": status,
        "contact_id": contact_id, "interaction_id": interaction_id,
        "signature_valid": sig_valid,
    }
