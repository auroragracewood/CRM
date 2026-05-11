"""FastAPI app entry. UI routes + API mount + background webhook dispatcher.

UI templates live in ../ui/*.html with `{{placeholder}}` markers, rendered via
str.replace(). Vanilla HTML+JS, no build step. styles.css served at /static/.
"""
import asyncio
import html
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from . import webhooks as webhooks_mod
from .api import router as api_router
from .context import ServiceContext
from .db import db, schema_initialized
from .services import (
    contacts as contacts_service,
    companies as companies_service,
    interactions as interactions_service,
    notes as notes_service,
    pipelines as pipelines_service,
    deals as deals_service,
    tasks as tasks_service,
    forms as forms_service,
    search as search_service,
    duplicates as duplicates_service,
    scoring as scoring_service,
    segments as segments_service,
    reports as reports_service,
    portals as portals_service,
    inbound as inbound_service,
    plugins as plugins_service,
    saved_views as saved_views_service,
)
from .services.contacts import ServiceError


ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
DOCS_DIR = ROOT / "docs"


app = FastAPI(title="CRM", version="0.1")
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


# ---------- helpers ----------

def _tpl(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def _h(s) -> str:
    return html.escape(str(s) if s is not None else "")


def _render(name: str, **kwargs) -> str:
    txt = _tpl(name)
    for k, v in kwargs.items():
        txt = txt.replace("{{" + k + "}}", v if isinstance(v, str) else str(v))
    return txt


def _require_session(request: Request) -> dict:
    if not schema_initialized():
        raise HTTPException(status_code=503, detail="CRM not initialized; run `python setup.py`")
    sid = request.cookies.get(auth_mod.SESSION_COOKIE_NAME)
    if not sid:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    with db() as conn:
        sess = auth_mod.lookup_session(conn, sid)
    if not sess:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return sess


def _ctx_from_session(sess: dict) -> ServiceContext:
    scope = ("admin" if sess["role"] == "admin"
             else "read" if sess["role"] == "readonly"
             else "write")
    return ServiceContext(
        user_id=sess["user_id"], role=sess["role"], scope=scope,
        surface="ui", request_id=str(uuid.uuid4()),
    )


def _csrf_check(request: Request, sess: dict, token: str) -> None:
    if not auth_mod.verify_csrf(sess["id"], token or ""):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _topnav(active: str, sess: dict, csrf: str) -> str:
    items = [("Dashboard", "/", "home"),
             ("Contacts", "/contacts", "contacts"),
             ("Companies", "/companies", "companies"),
             ("Pipelines", "/pipelines", "pipelines"),
             ("Tasks", "/tasks", "tasks"),
             ("Forms", "/forms", "forms"),
             ("Segments", "/segments", "segments"),
             ("Reports", "/reports", "reports"),
             ("Connectors", "/connectors", "connectors"),
             ("Plug-ins", "/plugins", "plugins"),
             ("Settings", "/settings", "settings")]
    links = "".join(
        f'<a href="{href}"{"class=active" if key == active else ""}>{label}</a>'.replace(
            "class=active", 'class="active"'
        )
        for label, href, key in items
    )
    return (
        '<header class="topbar">'
        '<span class="brand">CRM</span>'
        f'<nav>{links}</nav>'
        '<form method="get" action="/search" class="topsearch">'
        '<input type="search" name="q" placeholder="Search contacts, companies, notes…">'
        '</form>'
        '<form method="post" action="/logout" style="display:inline">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<span class="user">{_h(sess["email"])}<span class="role">{_h(sess["role"])}</span></span> '
        '<button class="btn secondary" style="margin-left:10px" type="submit">Sign out</button>'
        '</form>'
        '</header>'
    )


# ---------- login / logout ----------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    err_html = (
        f'<div class="err">{_h(error)}</div>' if error else ""
    )
    return HTMLResponse(_render("login.html", error=err_html))


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    e = email.strip().lower()
    with db() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role FROM users WHERE email = ?", (e,)
        ).fetchone()
    if not row or not auth_mod.verify_password(password, row["password_hash"]):
        return RedirectResponse("/login?error=Invalid+email+or+password", status_code=303)
    user_id = row["id"]
    import time
    with db() as conn:
        sid = auth_mod.create_session(conn, user_id)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (int(time.time()), user_id))
    secure = (
        os.environ.get("CRM_COOKIE_SECURE", "").lower() == "true"
        or os.environ.get("CRM_ENV") == "prod"
    )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        key=auth_mod.SESSION_COOKIE_NAME,
        value=sid,
        max_age=auth_mod.SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    return resp


@app.post("/logout")
async def logout(request: Request, csrf: str = Form("")):
    sid = request.cookies.get(auth_mod.SESSION_COOKIE_NAME)
    if sid:
        with db() as conn:
            sess = auth_mod.lookup_session(conn, sid)
            if sess and auth_mod.verify_csrf(sess["id"], csrf):
                auth_mod.invalidate_session(conn, sid)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth_mod.SESSION_COOKIE_NAME)
    return resp


# ---------- dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    sess = _require_session(request)
    with db() as conn:
        contact_count = conn.execute("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL").fetchone()[0]
        company_count = conn.execute("SELECT COUNT(*) FROM companies WHERE deleted_at IS NULL").fetchone()[0]
        interaction_count = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        recent_audit = conn.execute(
            "SELECT ts, surface, action, object_type, object_id FROM audit_log "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
    csrf = auth_mod.csrf_token_for(sess["id"])

    audit_rows = "".join(
        f'<tr><td class="mono">{_h(a["ts"])}</td>'
        f'<td>{_h(a["surface"])}</td>'
        f'<td>{_h(a["action"])}</td>'
        f'<td>{_h(a["object_type"])}/{_h(a["object_id"])}</td></tr>'
        for a in recent_audit
    ) or '<tr><td colspan="4" class="empty">No activity yet.</td></tr>'

    return HTMLResponse(_render(
        "dashboard.html",
        topnav=_topnav("home", sess, csrf),
        contacts=str(contact_count),
        companies=str(company_count),
        interactions=str(interaction_count),
        audits=str(audit_count),
        audit_rows=audit_rows,
    ))


# ---------- contacts ----------

@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request, q: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    result = contacts_service.list_(ctx, limit=200, offset=0, q=q or None)
    rows = []
    for c in result["items"]:
        label = c.get("full_name") or c.get("email") or f"#{c['id']}"
        rows.append(
            f'<tr><td><a href="/contacts/{c["id"]}">{_h(label)}</a></td>'
            f'<td>{_h(c.get("email") or "")}</td>'
            f'<td>{_h(c.get("phone") or "")}</td>'
            f'<td>{_h(c.get("title") or "")}</td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="4" class="empty">No contacts yet. Add one below.</td></tr>'
    csrf = auth_mod.csrf_token_for(sess["id"])
    return HTMLResponse(_render(
        "contacts.html",
        topnav=_topnav("contacts", sess, csrf),
        rows=rows_html,
        total=str(result["total"]),
        q=_h(q),
        csrf=csrf,
    ))


@app.post("/contacts/new")
async def contacts_create_form(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    title: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    payload = {
        "full_name": full_name.strip() or None,
        "email": email.strip() or None,
        "phone": phone.strip() or None,
        "title": title.strip() or None,
    }
    try:
        contact = contacts_service.create(ctx, payload)
    except ServiceError as e:
        return RedirectResponse(f"/contacts?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact['id']}", status_code=303)


@app.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(contact_id: int, request: Request, portal_token: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        contact = contacts_service.get(ctx, contact_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    timeline = interactions_service.list_for_contact(ctx, contact_id, limit=50)
    notes_list = notes_service.list_for_contact(ctx, contact_id)
    scores_data = scoring_service.get_scores(ctx, contact_id)
    portal_tokens_list = portals_service.list_for_contact(ctx, contact_id)
    csrf = auth_mod.csrf_token_for(sess["id"])

    # Build scores HTML (compact bar visualization with evidence on hover).
    score_order = [
        ("relationship_strength", "Relationship", "--moss"),
        ("intent",                "Intent",       "--blueberry"),
        ("fit",                   "Fit",          "--grey-5"),
        ("risk",                  "Risk",         "--copper"),
        ("opportunity",           "Opportunity",  "--blueberry-dark"),
    ]
    score_rows = []
    for stype, label, color in score_order:
        s = scores_data["scores"].get(stype)
        if s:
            evidence_html = "".join(
                f'<li style="font-size:11px;color:var(--fg-muted)">'
                f'  <span style="color:{("var(--moss-dark)" if e["delta"]>0 else "var(--copper-dark)" if e["delta"]<0 else "var(--fg-muted)")};font-weight:700">'
                f'    {e["delta"]:+d}'
                f'  </span> {_h(e["reason"])}'
                f'</li>'
                for e in s["evidence"]
            )
            score_rows.append(
                f'<div class="score-row">'
                f'  <div class="score-label">{label}</div>'
                f'  <div class="score-bar"><div class="score-fill" '
                f'       style="width:{s["score"]}%;background:var({color})"></div>'
                f'    <span class="score-num">{s["score"]}</span></div>'
                f'  <details class="score-evidence">'
                f'    <summary class="muted" style="font-size:11px;cursor:pointer">why?</summary>'
                f'    <ul style="margin:6px 0 0 14px;padding:0">{evidence_html}</ul>'
                f'  </details>'
                f'</div>'
            )
        else:
            score_rows.append(
                f'<div class="score-row">'
                f'  <div class="score-label">{label}</div>'
                f'  <div class="score-bar"><span class="muted" style="font-size:11px">not yet computed</span></div>'
                f'  <div></div>'
                f'</div>'
            )
    scores_html = (
        '<form method="post" action="/contacts/' + str(contact_id) + '/score" style="margin-bottom:10px">'
        + f'<input type="hidden" name="csrf" value="{csrf}">'
        + '<button class="btn secondary" style="font-size:10px;padding:5px 10px" type="submit">Recompute now</button>'
        + (f' <span class="faint" style="font-size:11px;margin-left:8px">'
           f'last computed: {_h(next(iter(scores_data["scores"].values()), {}).get("computed_at", "never"))}'
           f'</span>' if scores_data["scores"] else '')
        + '</form>'
        + "".join(score_rows)
    )

    timeline_rows = "".join(
        f'<tr><td class="mono faint">{_h(i.get("occurred_at"))}</td>'
        f'<td><strong>{_h(i.get("type"))}</strong></td>'
        f'<td>{_h(i.get("title") or "")}</td>'
        f'<td class="muted">{_h(i.get("body") or "")[:120]}</td></tr>'
        for i in timeline
    ) or '<tr><td colspan="4" class="empty">No interactions yet.</td></tr>'

    notes_html = "".join(
        f'<div class="card" style="margin:0 0 8px 0; padding:10px 12px">'
        f'<div class="row-flex" style="margin-bottom:4px">'
        f'<span class="label-uppercase" style="color: {"var(--copper)" if n.get("visibility")=="private" else "var(--fg-muted)"}">{_h(n.get("visibility"))}</span>'
        f'<span class="spacer"></span>'
        f'<span class="muted mono" style="font-size:11px">{_h(n.get("created_at"))}</span>'
        f'</div>'
        f'<div>{("<em class=&#34;faint&#34;>private — body redacted</em>" if n.get("_private_redacted") else _h(n.get("body") or ""))}</div>'
        f'</div>'
        for n in notes_list
    ) or '<div class="empty" style="padding:14px">No notes yet.</div>'

    # Portal tokens block — shows existing tokens + a "issue new" form. If
    # ?portal_token=<raw> just arrived (after issuance), highlight the new URL.
    base_url = os.environ.get("CRM_BASE_URL", "").rstrip("/") or ""
    token_rows = []
    for t in portal_tokens_list:
        status = "revoked" if t.get("revoked_at") else "active"
        token_rows.append(
            f'<tr><td>{_h(t.get("label") or "—")}</td>'
            f'<td class="mono faint">{_h(t.get("token_prefix"))}</td>'
            f'<td>{_h(t["scope"])}</td>'
            f'<td>{_h(status)}</td>'
            f'<td class="mono faint">{_h(t.get("last_used_at") or "—")}</td>'
            f'<td>'
            + (
                f'<form method="post" action="/portal-tokens/{t["id"]}/revoke" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<input type="hidden" name="contact_id" value="{contact["id"]}">'
                f'<button class="btn secondary" style="padding:3px 9px;font-size:10px">Revoke</button></form>'
                if status == "active" else ""
            )
            + '</td></tr>'
        )
    tokens_table = "\n".join(token_rows) or '<tr><td colspan="6" class="empty">No portal tokens yet.</td></tr>'

    new_token_block = ""
    if portal_token:
        portal_url = f'{base_url}/portal/{portal_token}' if base_url else f'/portal/{portal_token}'
        new_token_block = (
            f'<div class="flash success">'
            f'<strong>New portal link (copy now — token only shown once):</strong>'
            f'<pre>{_h(portal_url)}</pre></div>'
        )

    portal_block = (
        new_token_block
        + '<form class="inline-form" method="post" action="/contacts/' + str(contact["id"]) + '/portal-tokens" '
          'style="grid-template-columns: 1fr 130px 130px auto">'
        + f'<input type="hidden" name="csrf" value="{csrf}">'
        + '<div><label>Label (optional)</label><input name="label" placeholder="e.g. v1 onboarding"></div>'
        + '<div><label>Scope</label><select name="scope">'
          '<option value="client">client</option>'
          '<option value="applicant">applicant</option>'
          '<option value="sponsor">sponsor</option>'
          '<option value="member">member</option>'
          '</select></div>'
        + '<div><label>Expires in (days)</label><input name="expires_in_days" type="number" min="1" placeholder="empty = never"></div>'
        + '<button class="btn" type="submit">Issue portal link</button>'
        + '</form>'
        + '<table><thead><tr><th>Label</th><th>Token</th><th>Scope</th><th>Status</th><th>Last used</th><th></th></tr></thead>'
        + f'<tbody>{tokens_table}</tbody></table>'
    )

    return HTMLResponse(_render(
        "contact.html",
        topnav=_topnav("contacts", sess, csrf),
        id=str(contact["id"]),
        full_name=_h(contact.get("full_name") or ""),
        email=_h(contact.get("email") or ""),
        phone=_h(contact.get("phone") or ""),
        title=_h(contact.get("title") or ""),
        location=_h(contact.get("location") or ""),
        created_at=_h(contact.get("created_at")),
        updated_at=_h(contact.get("updated_at")),
        csrf=csrf,
        timeline_rows=timeline_rows,
        notes_html=notes_html,
        scores_html=scores_html,
        portal_block=portal_block,
    ))


@app.post("/contacts/{contact_id}/score")
async def contact_recompute_score_form(contact_id: int, request: Request,
                                        csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        scoring_service.compute_for_contact(ctx, contact_id)
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@app.post("/contacts/{contact_id}/interactions/new")
async def contact_log_interaction(
    contact_id: int, request: Request,
    type: str = Form(...), title: str = Form(""), body: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        interactions_service.log(ctx, {
            "type": type, "contact_id": contact_id,
            "title": title.strip() or None, "body": body.strip() or None,
            "source": "ui",
        })
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@app.post("/contacts/{contact_id}/notes/new")
async def contact_add_note(
    contact_id: int, request: Request,
    body: str = Form(...), visibility: str = Form("team"),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        notes_service.create(ctx, contact_id=contact_id, body=body, visibility=visibility)
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


# ---------- companies ----------

@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, q: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    result = companies_service.list_(ctx, limit=200, offset=0, q=q or None)
    rows = []
    for c in result["items"]:
        rows.append(
            f'<tr><td><a href="/companies/{c["id"]}">{_h(c.get("name") or f"#{c[chr(39)+chr(105)+chr(100)+chr(39)]}")}</a></td>'
            f'<td>{_h(c.get("domain") or "")}</td>'
            f'<td>{_h(c.get("industry") or "")}</td>'
            f'<td>{_h(c.get("location") or "")}</td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="4" class="empty">No companies yet. Add one below.</td></tr>'
    csrf = auth_mod.csrf_token_for(sess["id"])
    return HTMLResponse(_render(
        "companies.html",
        topnav=_topnav("companies", sess, csrf),
        rows=rows_html,
        total=str(result["total"]),
        q=_h(q),
        csrf=csrf,
    ))


@app.post("/companies/new")
async def companies_create_form(
    request: Request,
    name: str = Form(""), domain: str = Form(""),
    industry: str = Form(""), location: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        company = companies_service.create(ctx, {
            "name": name.strip() or None,
            "domain": domain.strip() or None,
            "industry": industry.strip() or None,
            "location": location.strip() or None,
        })
    except ServiceError as e:
        return RedirectResponse(f"/companies?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/companies/{company['id']}", status_code=303)


@app.get("/companies/{company_id}", response_class=HTMLResponse)
def company_detail(company_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        company = companies_service.get(ctx, company_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    timeline = interactions_service.list_for_company(ctx, company_id, limit=50)
    csrf = auth_mod.csrf_token_for(sess["id"])

    timeline_rows = "".join(
        f'<tr><td class="mono faint">{_h(i.get("occurred_at"))}</td>'
        f'<td><strong>{_h(i.get("type"))}</strong></td>'
        f'<td>{_h(i.get("title") or "")}</td>'
        f'<td class="muted">{_h(i.get("body") or "")[:120]}</td></tr>'
        for i in timeline
    ) or '<tr><td colspan="4" class="empty">No interactions yet.</td></tr>'

    return HTMLResponse(_render(
        "company.html",
        topnav=_topnav("companies", sess, csrf),
        id=str(company["id"]),
        name=_h(company.get("name") or ""),
        domain=_h(company.get("domain") or ""),
        website=_h(company.get("website") or ""),
        industry=_h(company.get("industry") or ""),
        location=_h(company.get("location") or ""),
        description=_h(company.get("description") or ""),
        timeline_rows=timeline_rows,
        csrf=csrf,
    ))


@app.post("/companies/{company_id}/edit")
async def company_edit_form(
    company_id: int, request: Request,
    name: str = Form(""), domain: str = Form(""), website: str = Form(""),
    industry: str = Form(""), location: str = Form(""), description: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        companies_service.update(ctx, company_id, {
            "name": name.strip() or None,
            "domain": domain.strip() or None,
            "website": website.strip() or None,
            "industry": industry.strip() or None,
            "location": location.strip() or None,
            "description": description.strip() or None,
        })
    except ServiceError as e:
        return RedirectResponse(f"/companies/{company_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/companies/{company_id}", status_code=303)


@app.post("/companies/{company_id}/delete")
async def company_delete_form(company_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        companies_service.delete(ctx, company_id)
    except ServiceError:
        pass
    return RedirectResponse("/companies", status_code=303)


# ---------- pipelines + deals (kanban) ----------

def _deal_card_html(deal: dict, csrf: str, stages: list[dict]) -> str:
    """Render one deal card inside a kanban column."""
    value_str = ""
    if deal.get("value_cents") is not None:
        currency = (deal.get("currency") or "").upper()
        value_str = f"<div class='deal-value'>{deal['value_cents']/100:,.0f} {currency}</div>"
    prob = (f"<span class='faint mono'>{deal['probability']}%</span>"
            if deal.get("probability") is not None else "")
    # Build stage options for the inline move dropdown
    stage_options = "".join(
        f'<option value="{s["id"]}"{ " selected" if s["id"] == deal["stage_id"] else ""}>{_h(s["name"])}</option>'
        for s in stages
    )
    return (
        f'<div class="deal-card" data-deal-id="{deal["id"]}">'
        f'  <div class="deal-title">{_h(deal["title"])}</div>'
        f'  {value_str}'
        f'  <div class="deal-meta">'
        f'    <span class="deal-status status-{_h(deal["status"])}">{_h(deal["status"])}</span> '
        f'    {prob}'
        f'  </div>'
        f'  <form method="post" action="/deals/{deal["id"]}/move" class="deal-move">'
        f'    <input type="hidden" name="csrf" value="{csrf}">'
        f'    <select name="stage_id" onchange="this.form.submit()">{stage_options}</select>'
        f'  </form>'
        f'</div>'
    )


@app.get("/pipelines", response_class=HTMLResponse)
def pipelines_page(request: Request, pipeline_id: int = 0):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    pipelines_list = pipelines_service.list_pipelines(ctx)
    csrf = auth_mod.csrf_token_for(sess["id"])

    # Pipeline selector strip
    if pipelines_list:
        if not pipeline_id:
            pipeline_id = pipelines_list[0]["id"]
        selector = "".join(
            f'<a href="/pipelines?pipeline_id={p["id"]}"'
            f'   class="pipeline-chip{" active" if p["id"] == pipeline_id else ""}">'
            f'{_h(p["name"])}'
            f'   <span class="muted">· {_h(p["type"])}</span>'
            f'</a>'
            for p in pipelines_list
        )
    else:
        selector = '<span class="faint">No pipelines yet. Create one below.</span>'

    # Kanban for selected pipeline
    kanban_html = ""
    active = next((p for p in pipelines_list if p["id"] == pipeline_id), None)
    if active:
        deals_data = deals_service.list_(ctx, pipeline_id=active["id"], limit=500)
        deals_by_stage: dict[int, list[dict]] = {}
        for d in deals_data["items"]:
            deals_by_stage.setdefault(d["stage_id"], []).append(d)

        columns = []
        for stage in active["stages"]:
            stage_deals = deals_by_stage.get(stage["id"], [])
            sum_value = sum((d.get("value_cents") or 0) for d in stage_deals)
            badge = ""
            if stage.get("is_won"):
                badge = '<span class="stage-flag won">won</span>'
            elif stage.get("is_lost"):
                badge = '<span class="stage-flag lost">lost</span>'
            cards = "".join(_deal_card_html(d, csrf, active["stages"]) for d in stage_deals)
            if not cards:
                cards = '<div class="kanban-empty">no deals</div>'

            columns.append(
                f'<div class="kanban-col">'
                f'  <div class="kanban-col-head">'
                f'    <span class="kanban-col-name">{_h(stage["name"])}</span> '
                f'    {badge}'
                f'    <span class="kanban-col-count">{len(stage_deals)}</span>'
                f'    {("<div class=&#34;kanban-col-sum mono faint&#34;>$" + f"{sum_value/100:,.0f}" + "</div>") if sum_value else ""}'
                f'  </div>'
                f'  <div class="kanban-col-body">{cards}</div>'
                f'  <form method="post" action="/deals/new" class="kanban-newdeal">'
                f'    <input type="hidden" name="csrf" value="{csrf}">'
                f'    <input type="hidden" name="pipeline_id" value="{active["id"]}">'
                f'    <input type="hidden" name="stage_id" value="{stage["id"]}">'
                f'    <input type="text" name="title" placeholder="+ new deal here" required>'
                f'    <button class="btn secondary" type="submit">Add</button>'
                f'  </form>'
                f'</div>'
            )
        kanban_html = (
            f'<div class="kanban">{"".join(columns)}</div>'
        )

    return HTMLResponse(_render(
        "pipelines.html",
        topnav=_topnav("pipelines", sess, csrf),
        selector=selector,
        kanban=kanban_html,
        csrf=csrf,
    ))


@app.post("/pipelines/new")
async def pipelines_create_form(request: Request, name: str = Form(...),
                                template: str = Form("sales"), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        p = pipelines_service.create_from_template(ctx, name, template)
    except ServiceError as e:
        return RedirectResponse(f"/pipelines?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/pipelines?pipeline_id={p['id']}", status_code=303)


@app.post("/deals/new")
async def deal_create_form(
    request: Request,
    title: str = Form(...),
    pipeline_id: int = Form(...), stage_id: int = Form(...),
    contact_id: int = Form(None), company_id: int = Form(None),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        deals_service.create(ctx, {
            "title": title, "pipeline_id": pipeline_id, "stage_id": stage_id,
            "contact_id": contact_id, "company_id": company_id,
        })
    except ServiceError as e:
        return RedirectResponse(f"/pipelines?pipeline_id={pipeline_id}&error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/pipelines?pipeline_id={pipeline_id}", status_code=303)


@app.post("/deals/{deal_id}/move")
async def deal_move_form(deal_id: int, request: Request,
                         stage_id: int = Form(...), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        d = deals_service.update(ctx, deal_id, {"stage_id": stage_id})
    except ServiceError as e:
        return RedirectResponse(f"/pipelines?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/pipelines?pipeline_id={d['pipeline_id']}", status_code=303)


# ---------- tasks ----------

def _task_row_html(t: dict, csrf: str) -> str:
    overdue_cls = ""
    if t.get("due_date") and t["status"] in ("open", "in_progress") and t["due_date"] < int(__import__("time").time()):
        overdue_cls = " overdue"
    due_str = ""
    if t.get("due_date"):
        import time as _t
        due_str = _t.strftime("%Y-%m-%d", _t.localtime(t["due_date"]))
    done_cls = " task-done" if t["status"] == "done" else ""

    return (
        f'<tr class="task-row{overdue_cls}{done_cls}">'
        f'  <td style="width:30px">'
        f'    {("<form method=&#34;post&#34; action=&#34;/tasks/" + str(t["id"]) + "/complete&#34; style=&#34;display:inline&#34;>"          "<input type=&#34;hidden&#34; name=&#34;csrf&#34; value=&#34;" + csrf + "&#34;>"          "<button class=&#34;btn secondary&#34; style=&#34;padding:2px 8px&#34; title=&#34;Mark done&#34;>✓</button>"          "</form>") if t["status"] != "done" else "<span class=&#34;faint&#34;>done</span>"}'
        f'  </td>'
        f'  <td><strong>{_h(t["title"])}</strong>'
        + (f'<div class="muted" style="font-size:11.5px">{_h(t["description"])[:140]}</div>' if t.get("description") else "")
        + f'</td>'
        f'  <td><span class="task-prio prio-{_h(t["priority"])}">{_h(t["priority"])}</span></td>'
        f'  <td class="mono">{_h(due_str)}</td>'
        f'  <td>{_h(t["status"])}</td>'
        f'  <td><form method="post" action="/tasks/{t["id"]}/delete" style="display:inline" onsubmit="return confirm(&#39;Delete this task?&#39;)">'
        f'    <input type="hidden" name="csrf" value="{csrf}">'
        f'    <button class="btn danger" style="padding:2px 8px">×</button>'
        f'  </form></td>'
        f'</tr>'
    )


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, view: str = "open"):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    if view == "overdue":
        result = tasks_service.list_(ctx, overdue=True, limit=500)
    elif view == "mine":
        result = tasks_service.list_(ctx, assigned_to=sess["user_id"], status="open", limit=500)
    elif view == "done":
        result = tasks_service.list_(ctx, status="done", limit=500)
    elif view == "all":
        result = tasks_service.list_(ctx, limit=500)
    else:
        view = "open"
        result = tasks_service.list_(ctx, status="open", limit=500)

    csrf = auth_mod.csrf_token_for(sess["id"])
    rows = "".join(_task_row_html(t, csrf) for t in result["items"])
    if not rows:
        rows = '<tr><td colspan="6" class="empty">No tasks match this view.</td></tr>'

    def _tab(label, key):
        cls = "active" if key == view else ""
        return f'<a href="/tasks?view={key}" class="task-tab {cls}">{label}</a>'

    tabs = "".join([
        _tab("Open", "open"),
        _tab("My open", "mine"),
        _tab("Overdue", "overdue"),
        _tab("Done", "done"),
        _tab("All", "all"),
    ])

    return HTMLResponse(_render(
        "tasks.html",
        topnav=_topnav("tasks", sess, csrf),
        tabs=tabs,
        rows=rows,
        total=str(result["total"]),
        csrf=csrf,
    ))


@app.post("/tasks/new")
async def task_create_form(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("normal"),
    due_date: str = Form(""),
    contact_id: int = Form(None),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    payload = {
        "title": title.strip(),
        "description": description.strip() or None,
        "priority": priority,
        "contact_id": contact_id,
    }
    # Allow YYYY-MM-DD due_date entry; convert to unix.
    if due_date.strip():
        try:
            import time as _t
            ts = int(_t.mktime(_t.strptime(due_date.strip(), "%Y-%m-%d")))
            payload["due_date"] = ts
        except ValueError:
            return RedirectResponse(f"/tasks?error=Invalid+date+format+(use+YYYY-MM-DD)", status_code=303)
    try:
        tasks_service.create(ctx, payload)
    except ServiceError as e:
        return RedirectResponse(f"/tasks?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/complete")
async def task_complete_form(task_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        tasks_service.complete(ctx, task_id)
    except ServiceError:
        pass
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/delete")
async def task_delete_form(task_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        tasks_service.delete(ctx, task_id)
    except ServiceError:
        pass
    return RedirectResponse("/tasks", status_code=303)


# ---------- forms (admin UI) ----------

@app.get("/forms", response_class=HTMLResponse)
def forms_page(request: Request, created: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    forms_list = forms_service.list_(ctx, include_inactive=True)
    csrf = auth_mod.csrf_token_for(sess["id"])
    base_url = os.environ.get("CRM_BASE_URL", "").rstrip("/") or ""

    rows = []
    for f in forms_list:
        public_url = f'{base_url}/f/{f["slug"]}' if base_url else f'/f/{f["slug"]}'
        status_pill = (
            '<span class="task-prio prio-normal">active</span>' if f.get("active")
            else '<span class="task-prio prio-low">inactive</span>'
        )
        rows.append(
            f'<tr><td><a href="/forms/{f["id"]}">{_h(f["name"])}</a></td>'
            f'<td><a class="mono" href="{public_url}" target="_blank">/f/{_h(f["slug"])}</a></td>'
            f'<td>{status_pill}</td>'
            f'<td class="mono faint">{_h(f.get("created_at"))}</td>'
            f'</tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="4" class="empty">No forms yet. Create one below.</td></tr>'

    created_block = (
        f'<div class="flash success">Form created. Public URL: '
        f'<code><a href="/f/{_h(created)}" target="_blank">/f/{_h(created)}</a></code></div>'
        if created else ""
    )

    return HTMLResponse(_render(
        "forms.html",
        topnav=_topnav("forms", sess, csrf),
        rows=rows_html,
        csrf=csrf,
        created_block=created_block,
    ))


@app.post("/forms/new")
async def form_create_simple(
    request: Request,
    slug: str = Form(...), name: str = Form(...),
    description: str = Form(""),
    csrf: str = Form(""),
):
    """Create a simple form preset: name + email + interest + message.
    Power users can post the full schema via /api/forms; this is the one-click
    'I just need a contact form' affordance from the UI."""
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    schema = {"fields": [
        {"key": "name", "type": "text", "label": "Your name", "required": True},
        {"key": "email", "type": "email", "label": "Email", "required": True},
        {"key": "interest", "type": "select", "label": "What's this about?",
         "options": ["sales", "support", "partnership", "other"]},
        {"key": "message", "type": "textarea", "label": "Message"},
    ]}
    routing = {
        "tags": ["lead", f"form:{slug}"],
        "interest_tag_prefix": "interest:",
        "auto_create_contact": True,
        "match_by_email": True,
    }
    try:
        form = forms_service.create(ctx, {
            "slug": slug, "name": name, "description": description.strip() or None,
            "schema": schema, "routing": routing, "active": True,
        })
    except ServiceError as e:
        return RedirectResponse(f"/forms?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/forms?created={form['slug']}", status_code=303)


@app.get("/forms/{form_id}", response_class=HTMLResponse)
def form_detail(form_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        form = forms_service.get(ctx, form_id)
    except ServiceError as e:
        raise HTTPException(404, e.message)
    subs = forms_service.list_submissions(ctx, form_id, limit=100)
    csrf = auth_mod.csrf_token_for(sess["id"])

    base_url = os.environ.get("CRM_BASE_URL", "").rstrip("/") or ""
    public_url = f'{base_url}/f/{form["slug"]}' if base_url else f'/f/{form["slug"]}'

    sub_rows = []
    import json as _json
    for s in subs["items"]:
        try:
            payload = _json.loads(s.get("payload_json") or "{}")
        except Exception:
            payload = {}
        payload_pretty = "; ".join(f"{k}={v!r}" for k, v in payload.items())
        sub_rows.append(
            f'<tr><td class="mono faint">{_h(s.get("created_at"))}</td>'
            f'<td>'
            + (f'<a href="/contacts/{s["contact_id"]}">#{s["contact_id"]}</a>' if s.get("contact_id") else '<span class="faint">none</span>')
            + f'</td>'
            f'<td class="mono" style="font-size:11.5px">{_h(payload_pretty)[:240]}</td>'
            f'</tr>'
        )
    sub_rows_html = "\n".join(sub_rows) or '<tr><td colspan="3" class="empty">No submissions yet.</td></tr>'

    return HTMLResponse(_render(
        "form.html",
        topnav=_topnav("forms", sess, csrf),
        id=str(form["id"]),
        name=_h(form["name"]),
        slug=_h(form["slug"]),
        public_url=public_url,
        schema_json=_h(form.get("schema_json") or ""),
        routing_json=_h(form.get("routing_json") or ""),
        sub_rows=sub_rows_html,
        total=str(subs["total"]),
    ))


# ---------- forms (public submission) ----------

def _render_public_form(form: dict) -> str:
    """Render the public form HTML from its schema. Vanilla; no JS required."""
    import json as _json
    schema = _json.loads(form["schema_json"] or "{}")
    rows = []
    for f in schema.get("fields", []):
        label = _h(f.get("label") or f["key"])
        required = "required" if f.get("required") else ""
        ftype = f["type"]
        key = _h(f["key"])
        if ftype == "textarea":
            ctrl = f'<textarea name="{key}" {required}></textarea>'
        elif ftype == "select":
            opts = "".join(f'<option value="{_h(o)}">{_h(o)}</option>' for o in f.get("options", []))
            ctrl = f'<select name="{key}" {required}><option value="">— choose —</option>{opts}</select>'
        elif ftype == "checkbox":
            ctrl = f'<input type="checkbox" name="{key}" value="1">'
        else:
            html_type = {"email": "email", "tel": "tel", "url": "url", "number": "number"}.get(ftype, "text")
            ctrl = f'<input type="{html_type}" name="{key}" {required}>'
        rows.append(f'<div class="row"><label>{label}</label>{ctrl}</div>')
    fields_html = "\n".join(rows)

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>' + _h(form["name"]) + '</title>'
        '<link rel="stylesheet" href="/static/styles.css">'
        '</head><body><div class="login-shell">'
        '<div class="login-card" style="max-width: 480px">'
        '<div class="brand">' + _h(form["name"]) + '</div>'
        + (f'<p class="muted" style="font-size:12px">{_h(form.get("description") or "")}</p>' if form.get("description") else '')
        + f'<form method="post" action="/f/{form["slug"]}">'
        + fields_html
        + '<div class="row"><label></label><button class="btn" type="submit">Submit</button></div>'
        '</form></div></div></body></html>'
    )


@app.get("/f/{slug}", response_class=HTMLResponse)
def public_form_render(slug: str):
    form = forms_service.get_by_slug_public(slug)
    if not form:
        return HTMLResponse(
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:32px">'
            '<h1>Form not found</h1></body></html>',
            status_code=404,
        )
    return HTMLResponse(_render_public_form(form))


# ---------- global search + duplicates UI ----------

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", kind: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])

    result = {"items": [], "buckets": {}, "total": 0, "query": q}
    if q.strip():
        kinds_list = [kind] if kind else None
        result = search_service.search(ctx, q, kinds=kinds_list, limit=100)

    bucket_html_parts = []
    bucket_order = [("contact", "Contacts"), ("company", "Companies"),
                    ("interaction", "Interactions"), ("note", "Notes")]
    for key, label in bucket_order:
        hits = result["buckets"].get(key, []) if isinstance(result.get("buckets"), dict) else []
        if not hits:
            continue
        rows = "".join(
            f'<tr><td><a href="{h["url"]}">{h["label"]}</a></td>'
            f'<td class="muted" style="font-size:11.5px">{h.get("title") or ""}</td>'
            f'<td class="muted" style="font-size:11.5px">{h.get("body") or ""}</td></tr>'
            for h in hits
        )
        bucket_html_parts.append(
            f'<div class="card"><h2>{label} <span class="muted">— {len(hits)}</span></h2>'
            f'<table><tbody>{rows}</tbody></table></div>'
        )
    buckets_html = "\n".join(bucket_html_parts) or (
        '<div class="empty" style="padding:30px">'
        + (f'No matches for <code>{_h(q)}</code>.' if q.strip() else 'Type a query above to search.')
        + '</div>'
    )

    return HTMLResponse(_render(
        "search.html",
        topnav=_topnav("", sess, csrf),
        q=_h(q),
        total=str(result["total"]),
        buckets=buckets_html,
    ))


# ---------- duplicates UI ----------

@app.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])

    scan = duplicates_service.find(ctx, max_groups=100)

    group_html_parts = []
    for g in scan["groups"]:
        rows = "".join(
            f'<tr><td><input type="checkbox" name="ids" value="{c["id"]}"></td>'
            f'<td><a href="/contacts/{c["id"]}">{_h(c.get("full_name") or c.get("email") or f"#{c[chr(39)+chr(105)+chr(100)+chr(39)]}")}</a>{" <span class=&#34;faint&#34;>(deleted)</span>" if c.get("deleted_at") else ""}</td>'
            f'<td class="mono">{_h(c.get("email") or "")}</td>'
            f'<td class="mono">{_h(c.get("phone") or "")}</td></tr>'
            for c in g["contacts"]
        )
        group_html_parts.append(
            f'<div class="card">'
            f'  <h2>{_h(g["strategy"])}  <span class="muted">— {_h(str(g["key"]))[:80]}</span></h2>'
            f'  <form method="post" action="/duplicates/merge">'
            f'    <input type="hidden" name="csrf" value="{csrf}">'
            f'    <table><thead><tr><th></th><th>Contact</th><th>Email</th><th>Phone</th></tr></thead>'
            f'    <tbody>{rows}</tbody></table>'
            f'    <p class="muted" style="font-size:11.5px; margin-top:10px">'
            f'      Pick the one to <strong>keep</strong>, check the others to merge into it. '
            f'      Merging re-parents all interactions, notes, tags, deals, and tasks; '
            f'      merged contacts are soft-deleted and their emails freed.'
            f'    </p>'
            f'    <div class="actions"><button class="btn" type="submit">Merge selected</button></div>'
            f'  </form>'
            f'</div>'
        )
    groups_html = "\n".join(group_html_parts) or (
        '<div class="empty" style="padding:30px">No duplicate groups detected.</div>'
    )

    return HTMLResponse(_render(
        "duplicates.html",
        topnav=_topnav("", sess, csrf),
        total=str(scan["total_groups"]),
        groups=groups_html,
    ))


@app.post("/duplicates/merge")
async def duplicates_merge_form(
    request: Request,
    ids: list[int] = Form([]),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    if len(ids) < 2:
        return RedirectResponse("/duplicates?error=Select+2+or+more+contacts+to+merge", status_code=303)
    # convention: first picked is "keep", rest are merged in
    keep_id = ids[0]
    merge_ids = ids[1:]
    try:
        duplicates_service.merge(ctx, keep_id=keep_id, merge_ids=merge_ids)
    except ServiceError as e:
        return RedirectResponse(f"/duplicates?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/duplicates", status_code=303)


@app.post("/f/{slug}")
async def public_form_submit(slug: str, request: Request):
    """Public submission. Accepts form-encoded or JSON. No auth, no CSRF.

    The CRM is single-tenant and the form's schema-driven validator drops any
    unknown keys, so an attacker can't inject arbitrary columns. Privacy gate
    is at the form level: setting `active=0` disables a slug entirely.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = await request.json()
    else:
        form_data = await request.form()
        payload = {k: v for k, v in form_data.items()}

    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        result = forms_service.submit_public(slug, payload, ip=ip, user_agent=ua)
    except ServiceError as e:
        if content_type.startswith("application/json"):
            return JSONResponse(status_code=400, content={"ok": False, "error": {
                "code": e.code, "message": e.message, "details": e.details,
            }})
        return HTMLResponse(
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:32px">'
            f'<h1>{_h(e.message)}</h1>'
            f'<p><a href="/f/{slug}">Back to form</a></p></body></html>',
            status_code=400,
        )

    if content_type.startswith("application/json"):
        return {"ok": True, **result}
    # form-encoded submitters get a thank-you (or redirect if configured)
    if result.get("redirect_url"):
        return RedirectResponse(result["redirect_url"], status_code=303)
    return HTMLResponse(
        '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:32px;'
        'background:#1a1a1a;color:#e6e6e6">'
        '<div style="max-width:480px;margin:60px auto;background:#fff;color:#1a1a1a;'
        'border:1px solid #cccccc;padding:32px;text-align:center">'
        f'<h1 style="margin:0 0 12px;font-size:20px">Thanks — we got it.</h1>'
        f'<p style="color:#666666">We\'ll be in touch.</p></div></body></html>'
    )




@app.post("/contacts/{contact_id}/edit")
async def contact_edit_form(
    contact_id: int,
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    title: str = Form(""),
    location: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    payload = {
        "full_name": full_name.strip() or None,
        "email": email.strip() or None,
        "phone": phone.strip() or None,
        "title": title.strip() or None,
        "location": location.strip() or None,
    }
    try:
        contacts_service.update(ctx, contact_id, payload)
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@app.post("/contacts/{contact_id}/delete")
async def contact_delete_form(contact_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        contacts_service.delete(ctx, contact_id)
    except ServiceError:
        pass
    return RedirectResponse("/contacts", status_code=303)


# ---------- settings: API keys ----------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, created_key: str = ""):
    sess = _require_session(request)
    csrf = auth_mod.csrf_token_for(sess["id"])
    with db() as conn:
        keys = conn.execute(
            "SELECT id, name, key_prefix, scope, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE user_id = ? ORDER BY id DESC",
            (sess["user_id"],),
        ).fetchall()
        webhooks_rows = conn.execute(
            "SELECT id, url, events_json, active FROM webhooks ORDER BY id DESC"
        ).fetchall()

    key_rows = "".join(
        f'<tr><td>{_h(k["name"])}</td>'
        f'<td class="mono">{_h(k["key_prefix"])}…</td>'
        f'<td>{_h(k["scope"])}</td>'
        f'<td>{_h("revoked" if k["revoked_at"] else "active")}</td>'
        f'<td>'
        + (
            f'<form method="post" action="/settings/keys/{k["id"]}/revoke" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button class="btn secondary" type="submit">Revoke</button></form>'
            if not k["revoked_at"] else ""
        )
        + '</td></tr>'
        for k in keys
    ) or '<tr><td colspan="5" class="empty">No API keys yet.</td></tr>'

    wh_rows = "".join(
        f'<tr><td class="mono">{_h(w["url"])}</td>'
        f'<td>{_h(w["events_json"])}</td>'
        f'<td>{_h("active" if w["active"] else "paused")}</td>'
        '</tr>'
        for w in webhooks_rows
    ) or '<tr><td colspan="3" class="empty">No webhooks subscribed.</td></tr>'

    new_key_block = (
        f'<div class="flash success">New API key (copy it now — it will not be shown again):'
        f'<pre>{_h(created_key)}</pre></div>'
        if created_key else ""
    )

    return HTMLResponse(_render(
        "settings.html",
        topnav=_topnav("settings", sess, csrf),
        key_rows=key_rows,
        wh_rows=wh_rows,
        new_key_block=new_key_block,
        csrf=csrf,
        email=_h(sess["email"]),
        role=_h(sess["role"]),
    ))


@app.post("/settings/keys/new")
async def create_api_key(
    request: Request,
    name: str = Form(...),
    scope: str = Form("write"),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if scope not in ("read", "write", "admin"):
        raise HTTPException(400, "invalid scope")
    raw, prefix, key_hash = auth_mod.generate_api_key()
    import time
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "INSERT INTO api_keys (user_id, name, key_prefix, key_hash, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sess["user_id"], name.strip()[:80], prefix, key_hash, scope, now),
        )
        kid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ctx = _ctx_from_session(sess)
        from . import audit
        audit.log(conn, ctx,
                  action="api_key.created", object_type="api_key", object_id=kid,
                  after={"name": name, "scope": scope, "key_prefix": prefix})
    return RedirectResponse(f"/settings?created_key={raw}", status_code=303)


@app.post("/settings/keys/{key_id}/revoke")
async def revoke_api_key(key_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    with db() as conn:
        row = conn.execute("SELECT user_id FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        if not row or row["user_id"] != sess["user_id"]:
            raise HTTPException(404, "key not found")
        auth_mod.revoke_api_key(conn, key_id)
        ctx = _ctx_from_session(sess)
        from . import audit
        audit.log(conn, ctx,
                  action="api_key.revoked", object_type="api_key", object_id=key_id)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/webhooks/new")
async def create_webhook(
    request: Request,
    url: str = Form(...),
    events: str = Form("contact.created"),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    import json, secrets, time
    secret = secrets.token_urlsafe(24)
    now = int(time.time())
    events_list = [e.strip() for e in events.split(",") if e.strip()]
    with db() as conn:
        conn.execute(
            "INSERT INTO webhooks (url, events_json, secret, active, created_at, updated_at, created_by) "
            "VALUES (?,?,?,?,?,?,?)",
            (url.strip(), json.dumps(events_list), secret, 1, now, now, sess["user_id"]),
        )
        wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ctx = _ctx_from_session(sess)
        from . import audit
        audit.log(conn, ctx,
                  action="webhook.created", object_type="webhook", object_id=wid,
                  after={"url": url, "events": events_list})
    return RedirectResponse("/settings", status_code=303)


# ---------- background webhook dispatcher ----------

_dispatcher_task: Optional[asyncio.Task] = None


# ---------- segments UI ----------

@app.get("/segments", response_class=HTMLResponse)
def segments_page(request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    segs = segments_service.list_(ctx)

    rows = []
    for s in segs:
        type_pill = (
            '<span class="task-prio prio-normal">dynamic</span>' if s["type"] == "dynamic"
            else '<span class="task-prio prio-low">static</span>'
        )
        rows.append(
            f'<tr>'
            f'  <td><a href="/segments/{s["id"]}">{_h(s["name"])}</a>'
            f'    <div class="muted mono" style="font-size:11px">{_h(s["slug"])}</div></td>'
            f'  <td>{type_pill}</td>'
            f'  <td class="mono">{s["member_count"]}</td>'
            f'  <td class="mono faint">{_h(s.get("last_evaluated_at") or "—")}</td>'
            f'  <td>'
            + (
                f'<form method="post" action="/segments/{s["id"]}/evaluate" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn secondary" style="padding:3px 9px;font-size:10px">Evaluate</button></form>'
                if s["type"] == "dynamic" else ""
            )
            + f'</td>'
            f'</tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="5" class="empty">No segments yet. Create one below.</td></tr>'

    return HTMLResponse(_render(
        "segments.html",
        topnav=_topnav("segments", sess, csrf),
        rows=rows_html,
        csrf=csrf,
    ))


@app.post("/segments/new-dynamic")
async def segment_create_dynamic_form(
    request: Request,
    name: str = Form(...), slug: str = Form(...),
    rules: str = Form(""), csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    import json as _json
    try:
        rules_obj = _json.loads(rules or "{}")
    except _json.JSONDecodeError as e:
        return RedirectResponse(f"/segments?error=Invalid+JSON+rules:+{_h(str(e))}", status_code=303)
    try:
        seg = segments_service.create_dynamic(ctx, name=name, slug=slug, rules=rules_obj)
    except ServiceError as e:
        return RedirectResponse(f"/segments?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/segments/{seg['id']}", status_code=303)


@app.post("/segments/{segment_id}/evaluate")
async def segment_evaluate_form(segment_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        segments_service.evaluate(ctx, segment_id)
    except ServiceError as e:
        return RedirectResponse(f"/segments?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/segments", status_code=303)


@app.get("/segments/{segment_id}", response_class=HTMLResponse)
def segment_detail(segment_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        seg = segments_service.get(ctx, segment_id)
    except ServiceError as e:
        raise HTTPException(404, e.message)
    members = segments_service.list_members(ctx, segment_id, limit=500)
    csrf = auth_mod.csrf_token_for(sess["id"])

    rows = "".join(
        f'<tr><td><a href="/contacts/{m["id"]}">{_h(m.get("full_name") or m.get("email") or f"#{m[chr(39)+chr(105)+chr(100)+chr(39)]}")}</a></td>'
        f'<td class="mono">{_h(m.get("email") or "")}</td>'
        f'<td class="mono faint">{_h(m.get("added_at"))}</td></tr>'
        for m in members["items"]
    ) or '<tr><td colspan="3" class="empty">No members in this segment.</td></tr>'

    return HTMLResponse(_render(
        "segment.html",
        topnav=_topnav("segments", sess, csrf),
        id=str(seg["id"]),
        name=_h(seg["name"]),
        slug=_h(seg["slug"]),
        type=_h(seg["type"]),
        member_count=str(seg["member_count"]),
        rules_json=_h(seg.get("rules_json") or "— (static segment)"),
        rows=rows,
        csrf=csrf,
    ))


@app.post("/segments/{segment_id}/delete")
async def segment_delete_form(segment_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        segments_service.delete(ctx, segment_id)
    except ServiceError:
        pass
    return RedirectResponse("/segments", status_code=303)


# ---------- reports UI ----------

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, run: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    catalog = reports_service.list_reports()

    cards = "".join(
        f'<div class="card report-card">'
        f'  <h2>{_h(r["name"])}</h2>'
        f'  <p class="muted" style="font-size:12px; margin-top:0">{_h(r["description"])}</p>'
        f'  <div class="actions">'
        f'    <a class="btn" href="/reports?run={r["name"]}">Run</a>'
        f'    <a class="btn secondary" href="/api/reports/{r["name"]}.csv">CSV</a>'
        f'  </div>'
        f'</div>'
        for r in catalog
    )

    result_html = ""
    if run:
        try:
            result = reports_service.run(ctx, run)
        except ServiceError as e:
            result_html = f'<div class="flash error">{_h(e.message)}</div>'
        else:
            cols = result["columns"]
            head = "".join(f"<th>{_h(c)}</th>" for c in cols)
            body = "".join(
                "<tr>" + "".join(f"<td class='mono'>{_h(row.get(c) if isinstance(row, dict) else getattr(row, c, ''))}</td>" for c in cols) + "</tr>"
                for row in result["rows"]
            ) or f'<tr><td colspan="{len(cols)}" class="empty">No rows.</td></tr>'
            totals_str = ""
            if result.get("totals"):
                totals_str = (
                    '<p class="muted" style="font-size:12px">'
                    + "; ".join(f"{k} = <strong>{v}</strong>" for k, v in result["totals"].items())
                    + "</p>"
                )
            result_html = (
                f'<div class="card">'
                f'  <h2>{_h(result["name"])}<span class="muted" style="font-weight:400;margin-left:10px">{_h(result["description"])}</span></h2>'
                f'  {totals_str}'
                f'  <table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
                f'  <p style="margin-top:10px"><a class="btn secondary" href="/api/reports/{run}.csv">Download CSV</a></p>'
                f'</div>'
            )

    return HTMLResponse(_render(
        "reports.html",
        topnav=_topnav("reports", sess, csrf),
        cards=cards,
        result=result_html,
    ))


# ---------- portals (PUBLIC, no admin auth) ----------

@app.get("/portal/{token}", response_class=HTMLResponse)
def portal_view(token: str):
    """A contact's self-service view. No admin session required. Validates the
    token, renders profile + timeline + non-private notes + deal summary."""
    data = portals_service.view_data(token)
    if not data:
        return HTMLResponse(
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:32px">'
            '<h1>This link is no longer valid.</h1>'
            '<p>The token may have expired or been revoked. Please contact us.</p>'
            '</body></html>',
            status_code=404,
        )

    contact = data["contact"]
    company = data.get("company")
    timeline = data["timeline"]
    notes = data["notes"]
    deals = data["deals"]

    def _fmt_ts(ts):
        if not ts: return ""
        import time as _t
        return _t.strftime("%Y-%m-%d", _t.localtime(int(ts)))

    timeline_rows = "".join(
        f'<tr><td class="mono faint">{_h(_fmt_ts(i.get("occurred_at")))}</td>'
        f'<td><strong>{_h(i.get("type"))}</strong></td>'
        f'<td>{_h(i.get("title") or "")}</td></tr>'
        for i in timeline
    ) or '<tr><td colspan="3" class="empty">No activity yet.</td></tr>'

    notes_html = "".join(
        f'<div class="card" style="padding:10px 12px;margin:0 0 6px 0">'
        f'<div class="muted" style="font-size:11px">{_h(_fmt_ts(n.get("created_at")))}</div>'
        f'<div style="margin-top:4px">{_h(n.get("body") or "")}</div>'
        f'</div>'
        for n in notes
    ) or '<div class="empty" style="padding:12px">No notes shared.</div>'

    deals_rows = "".join(
        f'<tr><td><strong>{_h(d["title"])}</strong></td>'
        f'<td>{_h(d.get("stage") or "")}</td>'
        f'<td><span class="deal-status status-{_h(d["status"])}">{_h(d["status"])}</span></td>'
        f'</tr>'
        for d in deals
    ) or '<tr><td colspan="3" class="empty">No deals.</td></tr>'

    company_block = ""
    if company:
        company_block = (
            f'<div class="card"><h2>Company</h2>'
            f'<div class="kv"><div class="k">Name</div><div class="v">{_h(company["name"])}</div>'
            + (f'<div class="k">Domain</div><div class="v">{_h(company.get("domain") or "")}</div>' if company.get("domain") else '')
            + '</div></div>'
        )

    return HTMLResponse(
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{_h(contact.get("full_name") or "Your portal")} · CRM</title>'
        '<link rel="stylesheet" href="/static/styles.css"></head><body>'
        '<header class="topbar"><span class="brand">PORTAL</span>'
        f'<nav><a class="active">{_h(contact.get("full_name") or "Welcome")}</a></nav>'
        f'<span class="user">scope: <span class="role">{_h(data["scope"])}</span></span>'
        '</header>'
        '<main class="content">'
        f'<div class="pagebar"><h1>Hi {_h(contact.get("full_name") or "")}</h1>'
        '<span class="meta">your self-service view</span></div>'
        '<div class="two-col">'
        '<div>'
        '<div class="card"><h2>Your profile</h2>'
        f'<div class="kv">'
        f'<div class="k">Email</div><div class="v">{_h(contact.get("email") or "")}</div>'
        f'<div class="k">Phone</div><div class="v">{_h(contact.get("phone") or "")}</div>'
        f'<div class="k">Title</div><div class="v">{_h(contact.get("title") or "")}</div>'
        f'<div class="k">Location</div><div class="v">{_h(contact.get("location") or "")}</div>'
        '</div></div>'
        f'{company_block}'
        '<div class="card"><h2>Activity</h2><table>'
        '<thead><tr><th>When</th><th>Type</th><th>Title</th></tr></thead>'
        f'<tbody>{timeline_rows}</tbody></table></div>'
        '</div>'
        '<div>'
        '<div class="card"><h2>Notes</h2>'
        f'{notes_html}</div>'
        '<div class="card"><h2>Deals</h2><table>'
        '<thead><tr><th>Title</th><th>Stage</th><th>Status</th></tr></thead>'
        f'<tbody>{deals_rows}</tbody></table></div>'
        '</div></div></main></body></html>'
    )


# ---------- inbound webhook (PUBLIC) ----------

@app.post("/in/{slug}")
async def public_inbound(slug: str, request: Request):
    """Public webhook receiver. Body is raw JSON; if the endpoint has a
    shared_secret, X-CRM-Inbound-Signature is HMAC-SHA256(secret, body)."""
    raw = await request.body()
    headers = {k.decode().lower() if isinstance(k, bytes) else k.lower():
               (v.decode() if isinstance(v, bytes) else v) for k, v in request.headers.items()}
    ip = request.client.host if request.client else None
    ua = headers.get("user-agent")
    try:
        result = inbound_service.receive(slug, raw, headers=headers, ip=ip, user_agent=ua)
    except ServiceError as e:
        return JSONResponse(status_code=404 if e.code == "INBOUND_ENDPOINT_NOT_FOUND" else 400,
                            content={"ok": False, "error": {
                                "code": e.code, "message": e.message, "details": e.details,
                            }})
    return {"ok": True, **result}


# ---------- connectors admin (UI) ----------

@app.get("/connectors", response_class=HTMLResponse)
def connectors_page(request: Request, created: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    eps = inbound_service.list_endpoints(ctx)
    base_url = os.environ.get("CRM_BASE_URL", "").rstrip("/") or ""

    rows = []
    for e in eps:
        public_url = f'{base_url}/in/{e["slug"]}' if base_url else f'/in/{e["slug"]}'
        secret_pill = (
            '<span class="task-prio prio-normal">signed</span>' if e["shared_secret"]
            else '<span class="task-prio prio-low">unsigned</span>'
        )
        rows.append(
            f'<tr>'
            f'  <td><a href="/connectors/{e["id"]}">{_h(e["name"])}</a>'
            f'    <div class="muted mono" style="font-size:11px">{_h(e["slug"])}</div></td>'
            f'  <td class="mono"><a href="{public_url}" target="_blank">{public_url}</a></td>'
            f'  <td>{secret_pill}</td>'
            f'  <td class="mono faint">{_h(e.get("last_received_at") or "—")}</td>'
            f'</tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="4" class="empty">No inbound endpoints yet.</td></tr>'

    created_block = (
        f'<div class="flash success">Endpoint <code>{_h(created)}</code> created. '
        f'See its detail page for the shared secret.</div>' if created else ""
    )

    return HTMLResponse(_render(
        "connectors.html",
        topnav=_topnav("connectors", sess, csrf),
        rows=rows_html,
        csrf=csrf,
        created_block=created_block,
    ))


@app.post("/connectors/new")
async def connectors_new(
    request: Request,
    slug: str = Form(...), name: str = Form(...),
    description: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    # Ship a sensible default routing — agents/users can edit the JSON later.
    default_routing = {
        "type": "system",
        "email_path": "email",
        "name_path": "name",
        "title_template": f"Inbound from {name}",
        "tags": [f"inbound:{slug}"],
        "create_contact": True,
    }
    try:
        inbound_service.create_endpoint(
            ctx, slug=slug, name=name,
            description=description.strip() or None,
            routing=default_routing, generate_secret=True,
        )
    except ServiceError as e:
        return RedirectResponse(f"/connectors?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/connectors?created={slug}", status_code=303)


@app.get("/connectors/{endpoint_id}", response_class=HTMLResponse)
def connector_detail(endpoint_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    try:
        ep = inbound_service.get_endpoint(ctx, endpoint_id)
    except ServiceError as e:
        raise HTTPException(404, e.message)
    events = inbound_service.list_events(ctx, endpoint_id, limit=50)
    base_url = os.environ.get("CRM_BASE_URL", "").rstrip("/") or ""
    public_url = f'{base_url}/in/{ep["slug"]}' if base_url else f'/in/{ep["slug"]}'

    ev_rows = "".join(
        f'<tr><td class="mono faint">{_h(e.get("created_at"))}</td>'
        f'<td>{_h(e["status"])}</td>'
        f'<td>{(("<a href=&#34;/contacts/" + str(e["contact_id"]) + "&#34;>#" + str(e["contact_id"]) + "</a>") if e.get("contact_id") else "—")}</td>'
        f'<td class="mono" style="font-size:11px">{_h((e.get("raw_payload") or "")[:120])}</td>'
        f'<td>{_h(e.get("error") or "")}</td>'
        f'</tr>'
        for e in events["items"]
    ) or '<tr><td colspan="5" class="empty">No events received yet.</td></tr>'

    return HTMLResponse(_render(
        "connector.html",
        topnav=_topnav("connectors", sess, csrf),
        id=str(ep["id"]),
        slug=_h(ep["slug"]),
        name=_h(ep["name"]),
        description=_h(ep.get("description") or ""),
        public_url=public_url,
        shared_secret=_h(ep.get("shared_secret") or ""),
        routing_json=_h(ep.get("routing_json") or ""),
        ev_rows=ev_rows,
        csrf=csrf,
    ))


@app.post("/connectors/{endpoint_id}/delete")
async def connector_delete_form(endpoint_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        inbound_service.delete_endpoint(ctx, endpoint_id)
    except ServiceError:
        pass
    return RedirectResponse("/connectors", status_code=303)


# ---------- portal-token issuance from contact detail (UI affordance) ----------

@app.post("/contacts/{contact_id}/portal-tokens")
async def contact_issue_portal_token(
    contact_id: int, request: Request,
    scope: str = Form("client"),
    expires_in_days: str = Form(""),
    label: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    eid = int(expires_in_days) if expires_in_days.strip() else None
    try:
        tok = portals_service.issue(ctx, contact_id, scope=scope,
                                    label=label.strip() or None,
                                    expires_in_days=eid)
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}?portal_token={tok['token']}", status_code=303)


@app.post("/portal-tokens/{token_id}/revoke")
async def portal_token_revoke_form(token_id: int, request: Request,
                                   contact_id: int = Form(...), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        portals_service.revoke(ctx, token_id)
    except ServiceError:
        pass
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


# ---------- plug-ins admin UI ----------

@app.get("/plugins", response_class=HTMLResponse)
def plugins_page(request: Request, reloaded: str = ""):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    pls = plugins_service.list_(ctx)

    rows = []
    for p in pls:
        status_pill = (
            '<span class="task-prio prio-normal">enabled</span>' if p.get("enabled")
            else '<span class="task-prio prio-low">disabled</span>'
        )
        loaded_pill = (
            '<span class="task-prio prio-low" style="background:var(--moss)">loaded</span>'
            if p.get("loaded") else
            '<span class="task-prio prio-high">not loaded</span>'
        )
        hook_list = ", ".join(p.get("hooks") or []) or "<span class='faint'>no hooks</span>"
        toggle_label = "Disable" if p["enabled"] else "Enable"
        toggle_action = f'/plugins/{p["id"]}/{("disable" if p["enabled"] else "enable")}'
        last_err = ""
        if p.get("last_error"):
            last_err = (
                f'<details style="margin-top:6px"><summary class="muted" style="font-size:11px;cursor:pointer">last_error</summary>'
                f'<pre style="font-size:10.5px">{_h(p["last_error"][:1500])}</pre></details>'
            )
        rows.append(
            f'<tr><td><strong>{_h(p["name"])}</strong>'
            f'  <div class="muted" style="font-size:11px">v{_h(p.get("version") or "?")} · {_h(p.get("description") or "")}</div></td>'
            f'<td>{status_pill}<br>{loaded_pill}</td>'
            f'<td class="mono" style="font-size:11px">{hook_list}{last_err}</td>'
            f'<td><form method="post" action="{toggle_action}" style="display:inline">'
            f'  <input type="hidden" name="csrf" value="{csrf}">'
            f'  <button class="btn secondary" style="padding:4px 10px;font-size:10px">{toggle_label}</button>'
            f'</form></td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="4" class="empty">No plug-ins installed. Drop a .py file into agent_surface/plugins/ then click Reload.</td></tr>'

    reload_block = (
        f'<div class="flash success">Reload complete. See traceback (if any) in the table.</div>'
        if reloaded else ""
    )

    return HTMLResponse(_render(
        "plugins.html",
        topnav=_topnav("plugins", sess, csrf),
        rows=rows_html,
        csrf=csrf,
        reload_block=reload_block,
    ))


@app.post("/plugins/reload")
async def plugins_reload_form(request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    try:
        plugins_service.reload_all()
    except Exception:
        pass
    return RedirectResponse("/plugins?reloaded=1", status_code=303)


@app.post("/plugins/{plugin_id}/enable")
async def plugin_enable_form(plugin_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        plugins_service.enable(ctx, plugin_id)
    except ServiceError as e:
        return RedirectResponse(f"/plugins?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/plugins", status_code=303)


@app.post("/plugins/{plugin_id}/disable")
async def plugin_disable_form(plugin_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        plugins_service.disable(ctx, plugin_id)
    except ServiceError as e:
        return RedirectResponse(f"/plugins?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/plugins", status_code=303)


async def _dispatcher_loop():
    """Drains the webhook_events outbox every 2 seconds."""
    while True:
        try:
            with db() as conn:
                webhooks_mod.dispatch_once(conn)
        except Exception:
            pass
        await asyncio.sleep(2)


@app.on_event("startup")
async def _startup():
    global _dispatcher_task
    # Load plug-ins at startup so hooks fire from the first request.
    try:
        plugins_service.reload_all()
    except Exception:
        pass
    if os.environ.get("CRM_DISABLE_DISPATCHER") != "1":
        _dispatcher_task = asyncio.create_task(_dispatcher_loop())


@app.on_event("shutdown")
async def _shutdown():
    global _dispatcher_task
    if _dispatcher_task:
        _dispatcher_task.cancel()
