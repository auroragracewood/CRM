"""Forms service.

Admin operations (create/update/list/delete/archive) require a ServiceContext
with write scope. The PUBLIC submission path (`submit`) takes no caller-supplied
context — it constructs a `system` ServiceContext internally and lets anonymous
visitors post to `/f/{slug}`.

A form has:
  - `slug`        URL-friendly identifier; the public endpoint is /f/{slug}
  - `schema_json` field definitions: [{key, label, type, required, options?}]
  - `routing_json` instructions to apply when a submission comes in:
        - tags:               always-attached tag names
        - interest_tag_prefix optional prefix; if a 'select' field has key 'interest'
                              and value 'branding', a tag 'interest:branding' is
                              attached (created if missing).
        - auto_create_contact (default true): build a contact from the submission
        - match_by_email      (default true): re-use existing active contact when
                              the submitted email matches.

Field types supported by the validator:
  text | email | tel | textarea | select | checkbox | number | url
"""
import json
import re
import time
from typing import Optional

from ..context import ServiceContext, system_context
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict


_FORM_FIELDS = ("slug", "name", "description", "schema_json",
                "routing_json", "redirect_url", "active")
VALID_FIELD_TYPES = {"text", "email", "tel", "textarea", "select",
                     "checkbox", "number", "url"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{1,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _serialize_json(field, payload, *, default="{}"):
    v = payload.get(field)
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def _validate_schema(schema: dict) -> dict:
    """Light validation of the form schema. Returns the validated dict."""
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        raise ServiceError("VALIDATION_ERROR",
                           "form schema must be an object with a `fields` array")
    seen_keys = set()
    for i, f in enumerate(schema["fields"]):
        if not isinstance(f, dict):
            raise ServiceError("VALIDATION_ERROR", f"field {i} is not an object")
        key = f.get("key")
        ftype = f.get("type")
        if not key or not isinstance(key, str):
            raise ServiceError("VALIDATION_ERROR", f"field {i} requires a `key`")
        if key in seen_keys:
            raise ServiceError("VALIDATION_ERROR", f"field key {key!r} appears twice")
        seen_keys.add(key)
        if ftype not in VALID_FIELD_TYPES:
            raise ServiceError("VALIDATION_ERROR",
                               f"field {key!r} has invalid type {ftype!r}; "
                               f"must be one of {sorted(VALID_FIELD_TYPES)}")
        if ftype == "select" and not isinstance(f.get("options"), list):
            raise ServiceError("VALIDATION_ERROR",
                               f"select field {key!r} requires `options` array")
    return schema


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    slug = (payload.get("slug") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    if not slug or not _SLUG_RE.match(slug):
        raise ServiceError("VALIDATION_ERROR",
                           "slug must be lowercase alphanumeric (with - or _), 2-64 chars")
    if not name:
        raise ServiceError("VALIDATION_ERROR", "form requires a name")

    schema_raw = payload.get("schema") or payload.get("schema_json")
    if isinstance(schema_raw, str):
        try:
            schema_obj = json.loads(schema_raw)
        except json.JSONDecodeError as e:
            raise ServiceError("VALIDATION_ERROR", f"schema_json is not valid JSON: {e}")
    elif isinstance(schema_raw, dict):
        schema_obj = schema_raw
    else:
        raise ServiceError("VALIDATION_ERROR",
                           "form requires `schema` (object) or `schema_json` (string)")
    schema_obj = _validate_schema(schema_obj)

    routing_raw = payload.get("routing") or payload.get("routing_json") or {}
    if isinstance(routing_raw, str):
        try:
            routing_obj = json.loads(routing_raw)
        except json.JSONDecodeError as e:
            raise ServiceError("VALIDATION_ERROR", f"routing_json is not valid JSON: {e}")
    else:
        routing_obj = routing_raw

    now = int(time.time())
    with db() as conn:
        existing = conn.execute("SELECT id FROM forms WHERE slug = ?", (slug,)).fetchone()
        if existing:
            raise ServiceError("FORM_SLUG_EXISTS",
                               f"form with slug {slug!r} already exists",
                               {"form_id": existing[0]})
        conn.execute(
            """INSERT INTO forms
                 (slug, name, description, schema_json, routing_json, redirect_url,
                  active, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                slug, name, payload.get("description"),
                json.dumps(schema_obj), json.dumps(routing_obj),
                payload.get("redirect_url"),
                1 if payload.get("active", True) else 0,
                ctx.user_id, now, now,
            ),
        )
        fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM forms WHERE id=?", (fid,)).fetchone()
        form = _row_to_dict(row)
        audit.log(conn, ctx, action="form.created", object_type="form",
                  object_id=fid, after={"slug": slug, "name": name})
        webhooks.enqueue(conn, "form.created", {"form": {"id": fid, "slug": slug, "name": name}})
    return form


def get(ctx: ServiceContext, form_id: int) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        row = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
    if not row:
        raise ServiceError("FORM_NOT_FOUND", f"form {form_id} not found")
    return _row_to_dict(row)


def get_by_slug_public(slug: str) -> Optional[dict]:
    """Public read — used by the form-render endpoint. No ctx required.
    Returns None if not found or inactive (so 404 leaks no info)."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM forms WHERE slug=? AND active=1", (slug,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_(ctx: ServiceContext, *, include_inactive: bool = False) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        if include_inactive:
            rows = conn.execute("SELECT * FROM forms ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM forms WHERE active=1 ORDER BY id DESC",
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update(ctx: ServiceContext, form_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    cleaned = {}
    for k in ("name", "description", "redirect_url"):
        if k in payload:
            cleaned[k] = payload[k]
    if "active" in payload:
        cleaned["active"] = 1 if payload["active"] else 0
    if "schema" in payload or "schema_json" in payload:
        s = payload.get("schema") or payload.get("schema_json")
        if isinstance(s, str):
            try: s = json.loads(s)
            except json.JSONDecodeError as e:
                raise ServiceError("VALIDATION_ERROR", f"schema_json is not valid JSON: {e}")
        cleaned["schema_json"] = json.dumps(_validate_schema(s))
    if "routing" in payload or "routing_json" in payload:
        r = payload.get("routing") or payload.get("routing_json")
        if isinstance(r, str):
            try: r = json.loads(r)
            except json.JSONDecodeError as e:
                raise ServiceError("VALIDATION_ERROR", f"routing_json is not valid JSON: {e}")
        cleaned["routing_json"] = json.dumps(r or {})
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")

    with db() as conn:
        before_row = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
        if not before_row:
            raise ServiceError("FORM_NOT_FOUND", f"form {form_id} not found")
        before = _row_to_dict(before_row)
        set_sql = ", ".join(f"{k}=?" for k in cleaned) + ", updated_at=?"
        params = list(cleaned.values()) + [now, form_id]
        conn.execute(f"UPDATE forms SET {set_sql} WHERE id=?", params)
        after_row = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
        after = _row_to_dict(after_row)
        audit.log(conn, ctx, action="form.updated", object_type="form",
                  object_id=form_id, before=before, after=after)
    return after


def list_submissions(ctx: ServiceContext, form_id: int, *,
                     limit: int = 100, offset: int = 0) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 500))
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM form_submissions WHERE form_id=?", (form_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM form_submissions WHERE form_id=? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (form_id, limit, offset),
        ).fetchall()
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


# ---------- public submission path ----------

def _validate_submission(schema: dict, payload: dict) -> dict:
    """Light validation of an incoming submission against the form schema."""
    out = {}
    fields = schema.get("fields", [])
    for f in fields:
        k = f["key"]
        v = payload.get(k)
        if (v is None or v == "") and f.get("required"):
            raise ServiceError("VALIDATION_ERROR",
                               f"field {k!r} is required",
                               {"field": k})
        if v is None or v == "":
            continue
        ftype = f["type"]
        if ftype == "email":
            if not _EMAIL_RE.match(str(v)):
                raise ServiceError("VALIDATION_ERROR",
                                   f"field {k!r}: not a valid email",
                                   {"field": k})
        elif ftype == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ServiceError("VALIDATION_ERROR",
                                   f"field {k!r}: must be a number",
                                   {"field": k})
        elif ftype == "checkbox":
            v = bool(v) and v not in ("false", "0", 0, False)
        elif ftype == "select":
            opts = f.get("options", [])
            if v not in opts:
                raise ServiceError("VALIDATION_ERROR",
                                   f"field {k!r}: {v!r} not in allowed options",
                                   {"field": k, "options": opts})
        out[k] = v
    # Drop any unknown keys silently (so receivers can't be tricked into trusting extras).
    return out


def _apply_routing(conn, contact_id: int, validated: dict, routing: dict,
                   ctx: ServiceContext) -> list[str]:
    """Attach tags per routing rules. Returns the list of tag names attached.
    Creates missing tags as needed."""
    applied: list[str] = []
    if not routing:
        return applied
    tag_names = list(routing.get("tags", []) or [])

    prefix = routing.get("interest_tag_prefix")
    if prefix and "interest" in validated:
        tag_names.append(f"{prefix}{validated['interest']}")

    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            tag_id = row[0]
        else:
            conn.execute(
                "INSERT INTO tags (name, color, scope, created_at) VALUES (?,?,?,?)",
                (name, None, "any", int(time.time())),
            )
            tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT OR IGNORE INTO contact_tags
                 (contact_id, tag_id, added_at, added_by)
               VALUES (?,?,?,?)""",
            (contact_id, tag_id, int(time.time()), ctx.user_id),
        )
        applied.append(name)
    return applied


def submit_public(slug: str, payload: dict, *,
                  ip: Optional[str] = None,
                  user_agent: Optional[str] = None) -> dict:
    """Handle a public form submission. Creates a system ServiceContext."""
    ctx = system_context()
    ctx.surface = "rest"  # actually behind a public REST endpoint; record that fact

    form = get_by_slug_public(slug)
    if not form:
        raise ServiceError("FORM_NOT_FOUND",
                           f"form {slug!r} not found or inactive")

    schema = json.loads(form["schema_json"])
    routing = json.loads(form["routing_json"] or "{}")
    validated = _validate_submission(schema, payload)

    now = int(time.time())
    with db() as conn:
        # 1) match-or-create contact (by email when available)
        contact_id = None
        email = (validated.get("email") or "").strip().lower() or None
        if routing.get("auto_create_contact", True):
            if email and routing.get("match_by_email", True):
                existing = conn.execute(
                    "SELECT id FROM contacts WHERE email = ? AND deleted_at IS NULL",
                    (email,),
                ).fetchone()
                if existing:
                    contact_id = existing[0]
            if not contact_id:
                # Try to assemble a usable name from common field keys.
                full_name = (validated.get("name") or validated.get("full_name")
                             or validated.get("Name") or "").strip() or None
                if full_name or email:
                    conn.execute(
                        "INSERT INTO contacts (full_name, email, created_at, updated_at) "
                        "VALUES (?,?,?,?)",
                        (full_name, email, now, now),
                    )
                    contact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    audit.log(conn, ctx, action="contact.created",
                              object_type="contact", object_id=contact_id,
                              after={"full_name": full_name, "email": email,
                                     "source": f"form:{form['slug']}"})
                    webhooks.enqueue(conn, "contact.created",
                                     {"contact": {"id": contact_id,
                                                  "full_name": full_name,
                                                  "email": email,
                                                  "source": f"form:{form['slug']}"}})

        # 2) apply routing tags
        applied_tags = []
        if contact_id:
            applied_tags = _apply_routing(conn, contact_id, validated, routing, ctx)

        # 3) record the submission
        conn.execute(
            """INSERT INTO form_submissions
                 (form_id, payload_json, contact_id, ip, user_agent, source, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (form["id"], json.dumps(validated), contact_id, ip, user_agent,
             "public", now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 4) log a 'form_submission' interaction on the timeline
        if contact_id:
            conn.execute(
                """INSERT INTO interactions
                     (contact_id, type, channel, title, body, metadata_json,
                      source, occurred_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    contact_id, "form_submission", "web",
                    f"Form: {form['name']}",
                    None,
                    json.dumps({"form_id": form["id"], "form_slug": form["slug"],
                                "submission_id": sid, "tags": applied_tags,
                                "payload": validated}),
                    f"form:{form['slug']}", now, now,
                ),
            )

        # 5) audit + webhook
        audit.log(conn, ctx, action="form.submitted", object_type="form_submission",
                  object_id=sid, after={
                      "form_id": form["id"], "slug": form["slug"],
                      "contact_id": contact_id, "tags": applied_tags,
                  })
        webhooks.enqueue(conn, "form.submitted", {
            "submission_id": sid,
            "form_id": form["id"],
            "form_slug": form["slug"],
            "contact_id": contact_id,
            "tags": applied_tags,
            "payload": validated,
        })

    return {
        "submission_id": sid,
        "contact_id": contact_id,
        "tags": applied_tags,
        "redirect_url": form.get("redirect_url"),
    }
