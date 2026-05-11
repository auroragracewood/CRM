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
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
def contact_detail(contact_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        contact = contacts_service.get(ctx, contact_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    timeline = interactions_service.list_for_contact(ctx, contact_id, limit=50)
    notes_list = notes_service.list_for_contact(ctx, contact_id)
    csrf = auth_mod.csrf_token_for(sess["id"])

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
    ))


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
    if os.environ.get("CRM_DISABLE_DISPATCHER") != "1":
        _dispatcher_task = asyncio.create_task(_dispatcher_loop())


@app.on_event("shutdown")
async def _shutdown():
    global _dispatcher_task
    if _dispatcher_task:
        _dispatcher_task.cancel()
