"""CRM MCP server — exposes service-layer operations as MCP tools.

Designed for direct integration with Claude Code, OpenClaw, or any agent that
speaks the Model Context Protocol over stdio. All tools dispatch through
`backend.services.*` so the MCP path shares validation + audit + webhook
behavior with REST, CLI, and UI.

Run standalone:
    python -m agent_surface.mcp_server

Then point a client at it via stdio. The user is resolved via the same fallback
the CLI uses: --as-user-id env var, --as-email env var, or first admin.

Auth note: this is a LOCAL stdio server. There is no network auth. The agent
running the server inherits filesystem trust to the same crm.db file.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.context import ServiceContext  # noqa: E402
from backend.db import db  # noqa: E402
from backend.services import (  # noqa: E402
    contacts as contacts_service,
    companies as companies_service,
    interactions as interactions_service,
    notes as notes_service,
    tags as tags_service,
    consent as consent_service,
)
from backend.services.contacts import ServiceError  # noqa: E402


def _resolve_user() -> tuple[int, str]:
    """Resolve acting principal from CRM_AS_USER_ID / CRM_AS_EMAIL or first admin."""
    as_id = os.environ.get("CRM_AS_USER_ID")
    as_email = os.environ.get("CRM_AS_EMAIL")
    with db() as conn:
        if as_id:
            row = conn.execute("SELECT id, role FROM users WHERE id = ?", (int(as_id),)).fetchone()
        elif as_email:
            row = conn.execute("SELECT id, role FROM users WHERE email = ?", (as_email.lower(),)).fetchone()
        else:
            row = conn.execute(
                "SELECT id, role FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
            ).fetchone()
    if not row:
        raise RuntimeError(
            "No matching user found (and no admin to fall back to). "
            "Run `python setup.py` to create an admin."
        )
    return row["id"], row["role"]


def _ctx(role: str, user_id: int) -> ServiceContext:
    scope = "admin" if role == "admin" else ("read" if role == "readonly" else "write")
    return ServiceContext(user_id=user_id, role=role, scope=scope, surface="mcp")


def _err(e: ServiceError) -> dict:
    return {"ok": False, "error": {
        "code": e.code, "message": e.message, "details": e.details,
    }}


# ----- MCP tool registration -----

# We support two server implementations. Prefer FastMCP (`mcp` package).
# If unavailable, fall back to a minimal stdio JSON-RPC implementation so the
# server still works without an extra dependency. Either way the tool set is
# identical and routes through services.

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore

    mcp = FastMCP("crm")

    @mcp.tool()
    def create_contact(
        full_name: str = "",
        email: str = "",
        phone: str = "",
        title: str = "",
        location: str = "",
    ) -> dict:
        """Create a new contact in the CRM. Returns the created contact (or error)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {
            "full_name": full_name or None,
            "email": email or None,
            "phone": phone or None,
            "title": title or None,
            "location": location or None,
        }
        try:
            return {"ok": True, "contact": contacts_service.create(ctx, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def get_contact(contact_id: int) -> dict:
        """Fetch a contact by id."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "contact": contacts_service.get(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def find_contacts(q: str = "", limit: int = 50, offset: int = 0) -> dict:
        """Search contacts by name or email substring. Returns paginated list."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **contacts_service.list_(ctx, q=q or None, limit=limit, offset=offset)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def update_contact(
        contact_id: int,
        full_name: str = None,
        email: str = None,
        phone: str = None,
        title: str = None,
        location: str = None,
    ) -> dict:
        """Update a contact's fields. Pass only the fields you want to change."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v for k, v in {
            "full_name": full_name, "email": email, "phone": phone,
            "title": title, "location": location,
        }.items() if v is not None}
        if not payload:
            return {"ok": False, "error": {"code": "VALIDATION_ERROR", "message": "no fields to update", "details": {}}}
        try:
            return {"ok": True, "contact": contacts_service.update(ctx, contact_id, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def delete_contact(contact_id: int) -> dict:
        """Soft-delete a contact (recoverable; frees the email)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **contacts_service.delete(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_company(name: str, slug: str = "", website: str = "",
                       domain: str = "", industry: str = "", location: str = "") -> dict:
        """Create a new company. Returns the created company or an error."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v or None for k, v in {
            "name": name, "slug": slug, "website": website,
            "domain": domain, "industry": industry, "location": location,
        }.items()}
        try:
            return {"ok": True, "company": companies_service.create(ctx, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def get_company(company_id: int) -> dict:
        """Fetch a company by id."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "company": companies_service.get(ctx, company_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def find_companies(q: str = "", limit: int = 50, offset: int = 0) -> dict:
        """Search companies by name or domain substring."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **companies_service.list_(ctx, q=q or None, limit=limit, offset=offset)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def log_interaction(
        type: str,
        contact_id: int = None,
        company_id: int = None,
        title: str = "",
        body: str = "",
        channel: str = "",
        source: str = "",
    ) -> dict:
        """Log an interaction (timeline event). type must be one of:
        email, call, meeting, form_submission, page_view, note_system, system."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "interaction": interactions_service.log(ctx, {
                "type": type,
                "contact_id": contact_id,
                "company_id": company_id,
                "title": title or None,
                "body": body or None,
                "channel": channel or None,
                "source": source or None,
            })}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def get_timeline(contact_id: int = None, company_id: int = None,
                     limit: int = 50, offset: int = 0) -> dict:
        """Get the timeline (interactions) for a contact or company."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            if contact_id:
                items = interactions_service.list_for_contact(ctx, contact_id, limit=limit, offset=offset)
            elif company_id:
                items = interactions_service.list_for_company(ctx, company_id, limit=limit, offset=offset)
            else:
                return {"ok": False, "error": {"code": "VALIDATION_ERROR",
                                               "message": "contact_id or company_id required",
                                               "details": {}}}
            return {"ok": True, "items": items}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def add_note(body: str, contact_id: int = None, company_id: int = None,
                 visibility: str = "team") -> dict:
        """Add a note. visibility: public, team, private. Private requires admin to reveal."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "note": notes_service.create(
                ctx, contact_id=contact_id, company_id=company_id,
                body=body, visibility=visibility,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_notes(contact_id: int) -> dict:
        """List notes for a contact (private notes from others appear redacted)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": notes_service.list_for_contact(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_tag(name: str, color: str = "", scope: str = "any") -> dict:
        """Create a tag. scope: contact, company, any."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "tag": tags_service.create(ctx, name, color=color or None, scope=scope)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def tag_contact(contact_id: int, tag_id: int) -> dict:
        """Attach a tag to a contact."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **tags_service.attach(ctx, tag_id=tag_id, contact_id=contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def record_consent(contact_id: int, channel: str, status: str,
                       source: str = "", proof: str = "") -> dict:
        """Record consent. status: granted, withdrawn, unknown."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "consent": consent_service.record(
                ctx, contact_id, channel, status,
                source=source or None, proof=proof or None,
            )}
        except ServiceError as e:
            return _err(e)

    def main():
        mcp.run()

except ImportError:  # ----- fallback: minimal stdio JSON-RPC server -----

    # If `mcp` isn't installed, expose the same tools via a tiny JSON-RPC-over-stdio
    # protocol. Clients can call methods like contact.create, contact.get, etc.
    # by writing one JSON request per line and reading one JSON response per line.

    _TOOLS = {
        "create_contact": ("contacts_service.create", ("full_name", "email", "phone", "title", "location")),
        "get_contact":    ("contacts_service.get", ("contact_id",)),
        "find_contacts":  ("contacts_service.list_", ("q", "limit", "offset")),
        "update_contact": ("contacts_service.update", ("contact_id", "payload")),
        "delete_contact": ("contacts_service.delete", ("contact_id",)),
    }

    def _do(method: str, params: dict) -> dict:
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            # contacts
            if method == "create_contact":
                return {"ok": True, "contact": contacts_service.create(ctx, params)}
            if method == "get_contact":
                return {"ok": True, "contact": contacts_service.get(ctx, int(params["contact_id"]))}
            if method == "find_contacts":
                return {"ok": True, **contacts_service.list_(
                    ctx, q=params.get("q") or None,
                    limit=int(params.get("limit", 50)),
                    offset=int(params.get("offset", 0)),
                )}
            if method == "update_contact":
                cid = int(params.pop("contact_id"))
                return {"ok": True, "contact": contacts_service.update(ctx, cid, params)}
            if method == "delete_contact":
                return {"ok": True, **contacts_service.delete(ctx, int(params["contact_id"]))}
            # companies
            if method == "create_company":
                return {"ok": True, "company": companies_service.create(ctx, params)}
            if method == "get_company":
                return {"ok": True, "company": companies_service.get(ctx, int(params["company_id"]))}
            if method == "find_companies":
                return {"ok": True, **companies_service.list_(
                    ctx, q=params.get("q") or None,
                    limit=int(params.get("limit", 50)),
                    offset=int(params.get("offset", 0)),
                )}
            # interactions
            if method == "log_interaction":
                return {"ok": True, "interaction": interactions_service.log(ctx, params)}
            if method == "get_timeline":
                if params.get("contact_id"):
                    items = interactions_service.list_for_contact(
                        ctx, int(params["contact_id"]),
                        limit=int(params.get("limit", 50)), offset=int(params.get("offset", 0)),
                    )
                else:
                    items = interactions_service.list_for_company(
                        ctx, int(params["company_id"]),
                        limit=int(params.get("limit", 50)), offset=int(params.get("offset", 0)),
                    )
                return {"ok": True, "items": items}
            # notes
            if method == "add_note":
                return {"ok": True, "note": notes_service.create(
                    ctx,
                    contact_id=params.get("contact_id"),
                    company_id=params.get("company_id"),
                    body=params.get("body", ""),
                    visibility=params.get("visibility", "team"),
                )}
            if method == "list_notes":
                return {"ok": True, "items": notes_service.list_for_contact(
                    ctx, int(params["contact_id"])
                )}
            # tags
            if method == "create_tag":
                return {"ok": True, "tag": tags_service.create(
                    ctx, params["name"], color=params.get("color"), scope=params.get("scope", "any")
                )}
            if method == "tag_contact":
                return {"ok": True, **tags_service.attach(
                    ctx, tag_id=int(params["tag_id"]), contact_id=int(params["contact_id"])
                )}
            # consent
            if method == "record_consent":
                return {"ok": True, "consent": consent_service.record(
                    ctx, int(params["contact_id"]), params["channel"], params["status"],
                    source=params.get("source"), proof=params.get("proof"),
                )}
            return {"ok": False, "error": {"code": "UNKNOWN_METHOD", "message": method, "details": {}}}
        except ServiceError as e:
            return _err(e)

    def main():
        print(
            "WARNING: `mcp` package not installed. Running stdio JSON-RPC fallback. "
            "Install with `pip install mcp` for full FastMCP compatibility.",
            file=sys.stderr,
        )
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                method = req.get("method", "")
                params = req.get("params", {}) or {}
                result = _do(method, params)
                resp = {"id": req.get("id"), "result": result}
            except Exception as ex:
                resp = {"id": req.get("id") if "req" in locals() else None,
                        "error": {"code": "EXCEPTION", "message": str(ex)}}
            print(json.dumps(resp, default=str), flush=True)


if __name__ == "__main__":
    main()
