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
    pipelines as pipelines_service,
    deals as deals_service,
    tasks as tasks_service,
    scoring as scoring_service,
    segments as segments_service,
    reports as reports_service,
    portals as portals_service,
    inbound as inbound_service,
    plugins as plugins_service,
    saved_views as saved_views_service,
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

    # ---------- v1: pipelines, deals, tasks ----------

    @mcp.tool()
    def create_pipeline_from_template(name: str, template: str) -> dict:
        """Create a pipeline + its stages from a built-in template.
        templates: sales, client, sponsor."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "pipeline": pipelines_service.create_from_template(ctx, name, template)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_pipelines(include_archived: bool = False) -> dict:
        """List pipelines (with stages embedded)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": pipelines_service.list_pipelines(ctx, include_archived=include_archived)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def get_pipeline(pipeline_id: int) -> dict:
        """Fetch a pipeline with its stages."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "pipeline": pipelines_service.get_pipeline(ctx, pipeline_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_deal(title: str, pipeline_id: int, stage_id: int,
                    contact_id: int = None, company_id: int = None,
                    value_cents: int = None, currency: str = "",
                    probability: int = None, expected_close: int = None) -> dict:
        """Create a deal on a pipeline + stage."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v for k, v in {
            "title": title, "pipeline_id": pipeline_id, "stage_id": stage_id,
            "contact_id": contact_id, "company_id": company_id,
            "value_cents": value_cents, "currency": currency or None,
            "probability": probability, "expected_close": expected_close,
        }.items() if v is not None and v != ""}
        try:
            return {"ok": True, "deal": deals_service.create(ctx, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def update_deal(deal_id: int, stage_id: int = None, status: str = "",
                    value_cents: int = None, probability: int = None,
                    next_step: str = "") -> dict:
        """Update a deal's stage, status, value, probability, or next step."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v for k, v in {
            "stage_id": stage_id, "status": status or None,
            "value_cents": value_cents, "probability": probability,
            "next_step": next_step or None,
        }.items() if v is not None and v != ""}
        if not payload:
            return {"ok": False, "error": {"code": "VALIDATION_ERROR",
                                           "message": "no fields to update", "details": {}}}
        try:
            return {"ok": True, "deal": deals_service.update(ctx, deal_id, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_deals(pipeline_id: int = None, stage_id: int = None,
                   status: str = "", assigned_to: int = None,
                   contact_id: int = None, company_id: int = None,
                   limit: int = 100, offset: int = 0) -> dict:
        """List deals with optional filters."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **deals_service.list_(
                ctx, pipeline_id=pipeline_id, stage_id=stage_id,
                status=status or None, assigned_to=assigned_to,
                contact_id=contact_id, company_id=company_id,
                limit=limit, offset=offset,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_task(title: str,
                    contact_id: int = None, company_id: int = None, deal_id: int = None,
                    assigned_to: int = None, due_date: int = None,
                    priority: str = "normal", description: str = "") -> dict:
        """Create a task. priority: low, normal, high, urgent. due_date is unix seconds."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v for k, v in {
            "title": title, "description": description or None,
            "contact_id": contact_id, "company_id": company_id, "deal_id": deal_id,
            "assigned_to": assigned_to, "due_date": due_date,
            "priority": priority,
        }.items() if v is not None and v != ""}
        try:
            return {"ok": True, "task": tasks_service.create(ctx, payload)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_tasks(status: str = "", assigned_to: int = None,
                   contact_id: int = None, company_id: int = None, deal_id: int = None,
                   overdue: bool = False, due_before: int = None,
                   limit: int = 100, offset: int = 0) -> dict:
        """List tasks. Useful filters: status, assigned_to, overdue=True, due_before=<unix sec>."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **tasks_service.list_(
                ctx, status=status or None, assigned_to=assigned_to,
                contact_id=contact_id, company_id=company_id, deal_id=deal_id,
                overdue=overdue, due_before=due_before,
                limit=limit, offset=offset,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def complete_task(task_id: int) -> dict:
        """Mark a task as done (sets status='done' and completed_at)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "task": tasks_service.complete(ctx, task_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def score_contact(contact_id: int) -> dict:
        """Recompute all five scores (relationship_strength, intent, fit, risk,
        opportunity) for a contact. Persists results + evidence."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **scoring_service.compute_for_contact(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def get_scores(contact_id: int) -> dict:
        """Fetch persisted scores + evidence for a contact (no recompute)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **scoring_service.get_scores(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_dynamic_segment(name: str, slug: str, rules: dict) -> dict:
        """Create a dynamic segment. rules is a JSON tree like:
        {"all": [{"field": "score.opportunity", "op": ">=", "value": 70}]}"""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "segment": segments_service.create_dynamic(
                ctx, name=name, slug=slug, rules=rules)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_segments() -> dict:
        """List all segments (with member counts)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": segments_service.list_(ctx)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_segment_members(segment_id: int, limit: int = 200) -> dict:
        """List the contacts belonging to a segment."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **segments_service.list_members(ctx, segment_id, limit=limit)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def evaluate_segment(segment_id: int) -> dict:
        """Re-evaluate a dynamic segment's membership against current data."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **segments_service.evaluate(ctx, segment_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_reports_catalog() -> dict:
        """List available reports (name + one-line description)."""
        return {"ok": True, "items": reports_service.list_reports()}

    @mcp.tool()
    def issue_portal_token(contact_id: int, scope: str = "client",
                            label: str = "", expires_in_days: int = None) -> dict:
        """Issue a self-service portal token for a contact. scope: client,
        applicant, sponsor, member. Returns the raw token (show once)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "portal_token": portals_service.issue(
                ctx, contact_id, scope=scope, label=label or None,
                expires_in_days=expires_in_days,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_portal_tokens(contact_id: int) -> dict:
        """List portal tokens for a contact (token bodies redacted to prefix)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": portals_service.list_for_contact(ctx, contact_id)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def create_inbound_endpoint(slug: str, name: str, routing: dict = None,
                                 description: str = "", generate_secret: bool = True) -> dict:
        """Register an inbound endpoint at /in/{slug}. routing dict describes
        how to parse POSTed payloads into contacts + interactions."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "endpoint": inbound_service.create_endpoint(
                ctx, slug=slug, name=name, description=description or None,
                routing=routing or {}, generate_secret=generate_secret,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_inbound_endpoints() -> dict:
        """List inbound endpoints."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": inbound_service.list_endpoints(ctx)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_plugins() -> dict:
        """List installed plug-ins + their hooks + enabled state."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": plugins_service.list_(ctx)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def reload_plugins() -> dict:
        """Re-scan agent_surface/plugins/ and reload all modules. Admin only."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        if not ctx.is_admin():
            return _err(ServiceError("FORBIDDEN", "reload requires admin"))
        return {"ok": True, **plugins_service.reload_all()}

    @mcp.tool()
    def create_saved_view(entity: str, name: str, config: dict,
                           slug: str = "", shared: bool = False) -> dict:
        """Save a list-page filter + sort + columns combo. entity: contact,
        company, deal, task, interaction."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "view": saved_views_service.create(
                ctx, entity=entity, name=name, config=config,
                slug=slug or None, shared=shared,
            )}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_saved_views(entity: str) -> dict:
        """List saved views the caller can see for an entity (own + shared)."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, "items": saved_views_service.list_for_entity(ctx, entity)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def list_inbound_events(endpoint_id: int, limit: int = 100) -> dict:
        """List recent events received on an inbound endpoint."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **inbound_service.list_events(ctx, endpoint_id, limit=limit)}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def run_report(name: str, params: dict = None) -> dict:
        """Run a named report. params is a dict of kwargs. Available reports:
        dormant_high_value, top_intent_now, pipeline_velocity, conversion_funnel,
        deal_pipeline_summary, lead_sources, tag_distribution, overdue_tasks,
        recent_form_submissions."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            return {"ok": True, **reports_service.run(ctx, name, **(params or {}))}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def top_contacts_by_score(score_type: str = "opportunity",
                              min_score: int = None, limit: int = 20) -> dict:
        """List contacts ranked by a score type. score_type: opportunity, intent,
        relationship_strength, risk, fit."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        try:
            items = scoring_service.list_top(ctx, score_type, limit=limit, min_score=min_score)
            return {"ok": True, "items": items, "score_type": score_type}
        except ServiceError as e:
            return _err(e)

    @mcp.tool()
    def update_task(task_id: int, status: str = "", priority: str = "",
                    due_date: int = None, title: str = "", description: str = "",
                    assigned_to: int = None) -> dict:
        """Update a task. Pass only the fields you want to change."""
        user_id, role = _resolve_user()
        ctx = _ctx(role, user_id)
        payload = {k: v for k, v in {
            "status": status or None, "priority": priority or None,
            "due_date": due_date, "title": title or None,
            "description": description or None, "assigned_to": assigned_to,
        }.items() if v is not None and v != ""}
        if not payload:
            return {"ok": False, "error": {"code": "VALIDATION_ERROR",
                                           "message": "no fields to update", "details": {}}}
        try:
            return {"ok": True, "task": tasks_service.update(ctx, task_id, payload)}
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
            # pipelines
            if method == "create_pipeline_from_template":
                return {"ok": True, "pipeline": pipelines_service.create_from_template(
                    ctx, params["name"], params["template"]
                )}
            if method == "list_pipelines":
                return {"ok": True, "items": pipelines_service.list_pipelines(
                    ctx, include_archived=bool(params.get("include_archived", False))
                )}
            if method == "get_pipeline":
                return {"ok": True, "pipeline": pipelines_service.get_pipeline(
                    ctx, int(params["pipeline_id"])
                )}
            # deals
            if method == "create_deal":
                return {"ok": True, "deal": deals_service.create(ctx, params)}
            if method == "update_deal":
                did = int(params.pop("deal_id"))
                return {"ok": True, "deal": deals_service.update(ctx, did, params)}
            if method == "list_deals":
                return {"ok": True, **deals_service.list_(
                    ctx,
                    pipeline_id=params.get("pipeline_id"),
                    stage_id=params.get("stage_id"),
                    status=params.get("status") or None,
                    assigned_to=params.get("assigned_to"),
                    contact_id=params.get("contact_id"),
                    company_id=params.get("company_id"),
                    limit=int(params.get("limit", 100)),
                    offset=int(params.get("offset", 0)),
                )}
            # tasks
            if method == "create_task":
                return {"ok": True, "task": tasks_service.create(ctx, params)}
            if method == "list_tasks":
                return {"ok": True, **tasks_service.list_(
                    ctx,
                    status=params.get("status") or None,
                    assigned_to=params.get("assigned_to"),
                    contact_id=params.get("contact_id"),
                    company_id=params.get("company_id"),
                    deal_id=params.get("deal_id"),
                    overdue=bool(params.get("overdue", False)),
                    due_before=params.get("due_before"),
                    limit=int(params.get("limit", 100)),
                    offset=int(params.get("offset", 0)),
                )}
            if method == "complete_task":
                return {"ok": True, "task": tasks_service.complete(ctx, int(params["task_id"]))}
            if method == "update_task":
                tid = int(params.pop("task_id"))
                return {"ok": True, "task": tasks_service.update(ctx, tid, params)}
            # scoring
            if method == "score_contact":
                return {"ok": True, **scoring_service.compute_for_contact(ctx, int(params["contact_id"]))}
            if method == "get_scores":
                return {"ok": True, **scoring_service.get_scores(ctx, int(params["contact_id"]))}
            if method == "top_contacts_by_score":
                items = scoring_service.list_top(
                    ctx, params.get("score_type", "opportunity"),
                    limit=int(params.get("limit", 20)),
                    min_score=params.get("min_score"),
                )
                return {"ok": True, "items": items, "score_type": params.get("score_type", "opportunity")}
            # segments
            if method == "create_dynamic_segment":
                return {"ok": True, "segment": segments_service.create_dynamic(
                    ctx, name=params["name"], slug=params["slug"],
                    rules=params.get("rules") or {})}
            if method == "list_segments":
                return {"ok": True, "items": segments_service.list_(ctx)}
            if method == "list_segment_members":
                return {"ok": True, **segments_service.list_members(
                    ctx, int(params["segment_id"]),
                    limit=int(params.get("limit", 200)))}
            if method == "evaluate_segment":
                return {"ok": True, **segments_service.evaluate(ctx, int(params["segment_id"]))}
            # reports
            if method == "list_reports_catalog":
                return {"ok": True, "items": reports_service.list_reports()}
            if method == "run_report":
                return {"ok": True, **reports_service.run(
                    ctx, params["name"], **(params.get("params") or {}),
                )}
            # portals
            if method == "issue_portal_token":
                return {"ok": True, "portal_token": portals_service.issue(
                    ctx, int(params["contact_id"]),
                    scope=params.get("scope", "client"),
                    label=params.get("label"),
                    expires_in_days=params.get("expires_in_days"),
                )}
            if method == "list_portal_tokens":
                return {"ok": True, "items": portals_service.list_for_contact(
                    ctx, int(params["contact_id"]))}
            # inbound
            if method == "create_inbound_endpoint":
                return {"ok": True, "endpoint": inbound_service.create_endpoint(
                    ctx, slug=params["slug"], name=params["name"],
                    description=params.get("description"),
                    routing=params.get("routing") or {},
                    generate_secret=bool(params.get("generate_secret", True)),
                )}
            if method == "list_inbound_endpoints":
                return {"ok": True, "items": inbound_service.list_endpoints(ctx)}
            if method == "list_inbound_events":
                return {"ok": True, **inbound_service.list_events(
                    ctx, int(params["endpoint_id"]),
                    limit=int(params.get("limit", 100)),
                )}
            # plugins
            if method == "list_plugins":
                return {"ok": True, "items": plugins_service.list_(ctx)}
            if method == "reload_plugins":
                if not ctx.is_admin():
                    return {"ok": False, "error": {
                        "code": "FORBIDDEN", "message": "reload requires admin", "details": {},
                    }}
                return {"ok": True, **plugins_service.reload_all()}
            # saved views
            if method == "create_saved_view":
                return {"ok": True, "view": saved_views_service.create(
                    ctx, entity=params["entity"], name=params["name"],
                    config=params.get("config") or {},
                    slug=params.get("slug"), shared=bool(params.get("shared", False)),
                )}
            if method == "list_saved_views":
                return {"ok": True, "items": saved_views_service.list_for_entity(
                    ctx, params["entity"])}
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
