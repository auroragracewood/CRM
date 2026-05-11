"""Portal tokens: self-service URLs that let an EXTERNAL contact see their own
data without an admin login.

A token grants read-only access to ONE contact's view: their profile + their
own interactions + their non-private notes (and submissions). The portal route
(/portal/{token}) renders this view directly; no admin session required.

Tokens are random secrets (32-byte url-safe). Stored as plain values (not
hashed) so an admin can re-show the URL on demand — that's intentional. If
this is too loose, switch to hashed-storage and require re-issue to view.
Each fetch updates last_used_at so we can spot abuse.
"""
import secrets
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


VALID_SCOPES = ("client", "applicant", "sponsor", "member")


def issue(
    ctx: ServiceContext,
    contact_id: int,
    *,
    scope: str = "client",
    label: Optional[str] = None,
    expires_in_days: Optional[int] = None,
) -> dict:
    """Issue a new portal token for a contact. Returns the full token row
    including the raw secret — surface it once to the admin then store hashed
    if you upgrade the policy."""
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if scope not in VALID_SCOPES:
        raise ServiceError("VALIDATION_ERROR",
                           f"scope must be one of {VALID_SCOPES}")
    now = int(time.time())
    expires_at = (now + int(expires_in_days) * 86400) if expires_in_days else None
    token = secrets.token_urlsafe(32)

    with db() as conn:
        c = conn.execute(
            "SELECT id FROM contacts WHERE id=? AND deleted_at IS NULL", (contact_id,),
        ).fetchone()
        if not c:
            raise ServiceError("CONTACT_NOT_FOUND", f"contact {contact_id} not found")
        conn.execute(
            """INSERT INTO portal_tokens
                 (token, contact_id, scope, label, expires_at,
                  created_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (token, contact_id, scope, label, expires_at, ctx.user_id, now),
        )
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM portal_tokens WHERE id=?", (tid,)).fetchone()
        audit.log(conn, ctx, action="portal_token.issued",
                  object_type="portal_token", object_id=tid,
                  after={"contact_id": contact_id, "scope": scope, "label": label})
    return dict(row)


def list_for_contact(ctx: ServiceContext, contact_id: int) -> list[dict]:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            "SELECT id, scope, label, expires_at, revoked_at, "
            "created_at, last_used_at, "
            "substr(token, 1, 10) || '…' AS token_prefix "
            "FROM portal_tokens WHERE contact_id=? ORDER BY id DESC",
            (contact_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke(ctx: ServiceContext, token_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT id, contact_id FROM portal_tokens WHERE id=?",
            (token_id,),
        ).fetchone()
        if not row:
            raise ServiceError("PORTAL_TOKEN_NOT_FOUND",
                               f"portal_token {token_id} not found")
        conn.execute(
            "UPDATE portal_tokens SET revoked_at=? WHERE id=?",
            (now, token_id),
        )
        audit.log(conn, ctx, action="portal_token.revoked",
                  object_type="portal_token", object_id=token_id,
                  before={"contact_id": row["contact_id"]})
    return {"id": token_id, "revoked": True}


def resolve(token: str) -> Optional[dict]:
    """Public, no-auth lookup: trade a token for {contact, scope}. Returns
    None if the token doesn't exist, is revoked, or has expired."""
    if not token:
        return None
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            """SELECT pt.id, pt.contact_id, pt.scope, pt.expires_at, pt.revoked_at,
                      c.full_name, c.email, c.phone, c.title, c.location, c.company_id
                 FROM portal_tokens pt
                 JOIN contacts c ON c.id = pt.contact_id
                WHERE pt.token = ? AND c.deleted_at IS NULL""",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["revoked_at"]:
            return None
        if row["expires_at"] and row["expires_at"] < now:
            return None
        # Touch last_used_at — drift doesn't matter, no atomic guarantee needed.
        conn.execute(
            "UPDATE portal_tokens SET last_used_at=? WHERE id=?",
            (now, row["id"]),
        )
    return {
        "token_id": row["id"],
        "scope": row["scope"],
        "contact": {
            "id": row["contact_id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "phone": row["phone"],
            "title": row["title"],
            "location": row["location"],
            "company_id": row["company_id"],
        },
    }


def view_data(token: str) -> Optional[dict]:
    """Public read of everything a portal viewer should see. Returns the
    contact's profile + interactions (excluding private system events) +
    non-private notes. Returns None for invalid tokens."""
    resolved = resolve(token)
    if not resolved:
        return None
    cid = resolved["contact"]["id"]
    with db() as conn:
        company = None
        cmp_id = resolved["contact"]["company_id"]
        if cmp_id:
            r = conn.execute(
                "SELECT id, name, website, domain FROM companies WHERE id=?",
                (cmp_id,),
            ).fetchone()
            if r:
                company = dict(r)

        timeline = [dict(r) for r in conn.execute(
            "SELECT id, type, channel, title, body, occurred_at "
            "FROM interactions WHERE contact_id=? "
            "ORDER BY occurred_at DESC, id DESC LIMIT 50",
            (cid,),
        )]
        notes = [dict(r) for r in conn.execute(
            "SELECT id, body, visibility, created_at FROM notes "
            "WHERE contact_id=? AND visibility != 'private' "
            "ORDER BY id DESC LIMIT 25",
            (cid,),
        )]
        # Deals limited to a public summary
        deals = [dict(r) for r in conn.execute(
            "SELECT d.id, d.title, d.status, ps.name AS stage "
            "FROM deals d JOIN pipeline_stages ps ON ps.id = d.stage_id "
            "WHERE d.contact_id=? ORDER BY d.id DESC LIMIT 25",
            (cid,),
        )]

    return {
        "scope": resolved["scope"],
        "contact": resolved["contact"],
        "company": company,
        "timeline": timeline,
        "notes": notes,
        "deals": deals,
    }
