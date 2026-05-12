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


# ---------- one-shot flash for issued portal tokens (security) ----------
#
# Raw portal tokens MUST NOT travel in URLs. Putting them in the
# `?portal_token=` query param leaks credentials into browser history,
# access logs, screenshots, and referer headers. Instead, the POST handler
# stashes the raw token here, the GET handler pops it once, and the URL
# the user sees is just `/contacts/{id}`. TTL is short — if the user
# doesn't load the contact page within 120 seconds they need to re-issue
# (preferable to a long-lived leak surface).
#
# Single-process design: this dict lives in the uvicorn worker. The
# deploy guide already pins workers=1; multi-worker would need an
# external store (sqlite + ttl, or a flash-via-cookie pattern).

import time as _time

_PORTAL_TOKEN_FLASH: dict[tuple[int, int], tuple[str, float]] = {}
_PORTAL_TOKEN_FLASH_TTL = 120  # seconds


def _flash_portal_token(user_id: int, contact_id: int, raw_token: str) -> None:
    _PORTAL_TOKEN_FLASH[(user_id, contact_id)] = (
        raw_token, _time.time() + _PORTAL_TOKEN_FLASH_TTL,
    )


def _pop_portal_token(user_id: int, contact_id: int) -> str:
    """Return the freshly-issued token once, then drop it. Empty string if none."""
    key = (user_id, contact_id)
    entry = _PORTAL_TOKEN_FLASH.pop(key, None)
    if not entry:
        return ""
    raw, exp = entry
    if _time.time() > exp:
        return ""
    return raw


# ---------- helpers ----------

def _tpl(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def _h(s) -> str:
    return html.escape(str(s) if s is not None else "")


def _render(template_name: str, **kwargs) -> str:
    txt = _tpl(template_name)
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
             ("Audit", "/audit", "audit"),
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
        '<input type="search" name="q" placeholder="Search contacts, companies, notes…" autocomplete="off">'
        '</form>'
        '<script src="/static/topnav.js" defer></script>'
        '<script src="/static/modal.js" defer></script>'
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
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])

    with db() as conn:
        contact_count = conn.execute("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL").fetchone()[0]
        company_count = conn.execute("SELECT COUNT(*) FROM companies WHERE deleted_at IS NULL").fetchone()[0]
        open_deals = conn.execute("SELECT COUNT(*) FROM deals WHERE status='open'").fetchone()[0]
        open_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('open','in_progress')").fetchone()[0]

    def _widget(title, report_name, columns, fmt_row, params=None,
                empty_msg="No matches."):
        """Render a single dashboard card by running a report function."""
        try:
            r = reports_service.run(ctx, report_name, **(params or {}))
        except ServiceError:
            return f'<div class="card"><h2>{title}</h2><div class="empty">unavailable</div></div>'
        rows = r["rows"]
        if not rows:
            body = f'<div class="empty" style="padding:14px">{empty_msg}</div>'
        else:
            head = "".join(f"<th>{_h(c)}</th>" for c in columns)
            body = '<table><thead><tr>' + head + '</tr></thead><tbody>' + "".join(
                fmt_row(row) for row in rows[:8]
            ) + '</tbody></table>'
            if len(rows) > 8:
                body += (f'<p class="muted" style="font-size:11px;text-align:right;'
                         f'margin-top:6px">+ {len(rows) - 8} more in '
                         f'<a href="/reports?run={report_name}">full report</a></p>')
        return f'<div class="card"><h2>{title}</h2>{body}</div>'

    def _name(r):
        return _h(r.get("full_name") or r.get("email") or f"#{r['id']}")

    intent_widget = _widget(
        "Top intent right now", "top_intent_now",
        ["Contact", "Intent"],
        lambda r: f'<tr><td><a href="/contacts/{r["id"]}">{_name(r)}</a></td>'
                  f'<td class="mono">{r["intent"]}</td></tr>',
        params={"limit": 10},
        empty_msg="No contacts have intent scores yet. Recompute scores on a contact to populate this.",
    )

    dormant_widget = _widget(
        "Dormant high-value", "dormant_high_value",
        ["Contact", "Opp.", "Silent (days)"],
        lambda r: f'<tr><td><a href="/contacts/{r["id"]}">{_name(r)}</a></td>'
                  f'<td class="mono">{r["opportunity"]}</td>'
                  f'<td class="mono">{r["days_since_last_interaction"]}</td></tr>',
        params={"opportunity_min": 60, "days_silent": 30, "limit": 10},
        empty_msg="Nothing dormant. Bar is opportunity ≥ 60 and ≥ 30 days silent.",
    )

    tasks_widget = _widget(
        "Overdue tasks", "overdue_tasks",
        ["Title", "Priority", "Assignee"],
        lambda r: f'<tr><td>{_h(r["title"])}</td>'
                  f'<td><span class="task-prio prio-{_h(r["priority"])}">{_h(r["priority"])}</span></td>'
                  f'<td class="mono faint">{_h(r["assigned_email"])}</td></tr>',
        empty_msg="Nothing overdue. Nice.",
    )

    forms_widget = _widget(
        "Recent form submissions", "recent_form_submissions",
        ["When", "Form", "Contact"],
        lambda r: f'<tr><td class="mono faint">{_h(r["created_at"])}</td>'
                  f'<td>{_h(r["form_name"])}</td>'
                  f'<td>'
                  + (f'<a href="/contacts/{r["contact_id"]}">'
                     f'{_h(r["contact_name"] or r["contact_email"] or f"#{r[chr(39)+chr(99)+chr(111)+chr(110)+chr(116)+chr(97)+chr(99)+chr(116)+chr(95)+chr(105)+chr(100)+chr(39)]}")}</a>'
                     if r.get("contact_id") else '<span class="faint">—</span>')
                  + '</td></tr>',
        params={"days": 14},
        empty_msg="No form submissions in the last 14 days.",
    )

    deals_widget = _widget(
        "Open deal pipeline", "deal_pipeline_summary",
        ["Pipeline", "Open", "Value", "Avg prob"],
        lambda r: f'<tr><td>{_h(r["pipeline"])}</td>'
                  f'<td class="mono">{r["open_deals"]}</td>'
                  f'<td class="mono">${(r["total_value_cents"] or 0)/100:,.0f}</td>'
                  f'<td class="mono">{r["avg_probability"] or "—"}%</td></tr>',
        empty_msg="No open deals. Create a pipeline to start tracking.",
    )

    leads_widget = _widget(
        "Lead sources (30d)", "lead_sources",
        ["Source", "Contacts"],
        lambda r: f'<tr><td class="mono">{_h(r["source"])}</td>'
                  f'<td class="mono">{r["contacts"]}</td></tr>',
        params={"days": 30},
        empty_msg="No new lead activity in 30 days.",
    )

    return HTMLResponse(_render(
        "dashboard.html",
        topnav=_topnav("home", sess, csrf),
        contacts=str(contact_count),
        companies=str(company_count),
        open_deals=str(open_deals),
        open_tasks=str(open_tasks),
        intent_widget=intent_widget,
        dormant_widget=dormant_widget,
        tasks_widget=tasks_widget,
        forms_widget=forms_widget,
        deals_widget=deals_widget,
        leads_widget=leads_widget,
    ))


# ---------- contacts ----------

def _show_deleted_toggle(show_del: bool, base: str, q: str) -> str:
    """Render the 'Show deleted' / 'Hide deleted' link for list pages."""
    from urllib.parse import urlencode
    if show_del:
        href = base + (("?" + urlencode({"q": q})) if q else "")
        return f'<a href="{href}" class="btn secondary" style="padding:3px 10px;font-size:11px">Hide deleted</a>'
    qs = {"show_deleted": "1"}
    if q: qs["q"] = q
    href = base + "?" + urlencode(qs)
    return f'<a href="{href}" class="btn secondary" style="padding:3px 10px;font-size:11px">Show deleted</a>'


def _saved_views_block(ctx, entity: str, base_path: str, csrf: str,
                       current_params: dict) -> str:
    """Render the 'Save current view' + 'Load view' pair for a list page."""
    from .services import saved_views as _sv
    import json as _json
    from urllib.parse import urlencode
    views = _sv.list_for_entity(ctx, entity)
    options = ['<option value="">— Load a saved view —</option>']
    for v in views:
        try:
            cfg = _json.loads(v.get("config_json") or "{}")
        except Exception:
            cfg = {}
        qs_load = {k: str(cfg[k]) for k in ("q", "show_deleted") if cfg.get(k) not in (None, "", 0)}
        qs_load["loaded_view"] = str(v["id"])
        href = base_path + "?" + urlencode(qs_load)
        owner = "(mine)" if v.get("user_id") == ctx.user_id else "(shared)"
        shared_pill = " [shared]" if v.get("shared") else ""
        options.append(
            f'<option value="{href}">{_h(v["name"])} {owner}{shared_pill}</option>'
        )
    save_qs = {k: v for k, v in current_params.items() if v not in (None, "", 0)}
    save_qs_json = _json.dumps(save_qs)
    return (
        '<div class="saved-views-bar">'
        f'  <select onchange="if(this.value){{window.location.href=this.value}}">{"".join(options)}</select>'
        f'  <form method="post" action="/saved-views" style="display:inline">'
        f'    <input type="hidden" name="csrf" value="{csrf}">'
        f'    <input type="hidden" name="entity" value="{entity}">'
        f'    <input type="hidden" name="config_json" value="{_h(save_qs_json)}">'
        f'    <input type="hidden" name="redirect_to" value="{_h(base_path)}">'
        f'    <input name="name" placeholder="Save current view as…" style="font-size:12px;padding:4px 8px">'
        f'    <label style="font-size:11px"><input type="checkbox" name="shared" value="1"> shared</label>'
        f'    <button class="btn secondary" type="submit" style="padding:3px 10px;font-size:11px">Save</button>'
        f'  </form>'
        f'  <a href="/saved-views?entity={entity}" class="muted" style="font-size:11px;margin-left:8px">manage views ›</a>'
        '</div>'
    )


def _tag_options_html(ctx, scope_filter: str) -> str:
    """Render <option> list of all tags matching the scope (for bulk apply/remove)."""
    from .services import tags as _tags
    all_tags = _tags.list_all(ctx)
    opts = []
    for t in all_tags:
        if t.get("scope") in ("any", scope_filter):
            opts.append(f'<option value="{t["id"]}">{_h(t["name"])}</option>')
    return "\n".join(opts) or '<option value="">(no tags yet)</option>'


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request, q: str = "", show_deleted: int = 0):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    show_del = bool(show_deleted)
    result = contacts_service.list_(ctx, limit=200, offset=0, q=q or None,
                                    include_deleted=show_del)
    rows = []
    for c in result["items"]:
        label = c.get("full_name") or c.get("email") or f"#{c['id']}"
        is_deleted = bool(c.get("deleted_at"))
        if is_deleted:
            restore_btn = (
                f'<form method="post" action="/contacts/{c["id"]}/restore" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn secondary" style="padding:2px 8px">↩ restore</button></form>'
            )
            row_cls = ' class="row-deleted"'
            name_cell = f'<a href="/contacts/{c["id"]}">{_h(label)}</a> <span class="faint">(deleted)</span>'
        else:
            restore_btn = ""
            row_cls = ""
            name_cell = f'<a href="/contacts/{c["id"]}">{_h(label)}</a>'
        rows.append(
            f'<tr{row_cls}>'
            f'<td style="width:28px"><input type="checkbox" name="ids" value="{c["id"]}"></td>'
            f'<td>{name_cell}</td>'
            f'<td>{_h(c.get("email") or "")}</td>'
            f'<td>{_h(c.get("phone") or "")}</td>'
            f'<td>{_h(c.get("title") or "")} {restore_btn}</td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="5" class="empty">No contacts yet. Add one below.</td></tr>'
    return HTMLResponse(_render(
        "contacts.html",
        topnav=_topnav("contacts", sess, csrf),
        rows=rows_html,
        total=str(result["total"]),
        q=_h(q),
        show_deleted_toggle=_show_deleted_toggle(show_del, "/contacts", q),
        tag_options=_tag_options_html(ctx, "contact"),
        saved_views_bar=_saved_views_block(
            ctx, "contact", "/contacts", csrf,
            {"q": q, "show_deleted": ("1" if show_del else "")},
        ),
        csrf=csrf,
    ))


@app.post("/contacts/bulk")
async def contacts_bulk(request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    form = await request.form()
    raw_ids = form.getlist("ids")
    ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
    action = form.get("action", "").strip()
    tag_id = form.get("tag_id") or None
    try:
        tag_id_int = int(tag_id) if tag_id else None
    except ValueError:
        tag_id_int = None
    if not ids:
        return RedirectResponse("/contacts?error=No+rows+selected", status_code=303)
    try:
        result = contacts_service.bulk_apply(ctx, ids, action=action, tag_id=tag_id_int)
    except ServiceError as e:
        return RedirectResponse(f"/contacts?error={_h(e.message)}", status_code=303)
    msg = f"{len(result['ok'])} succeeded"
    if result["errors"]:
        msg += f", {len(result['errors'])} failed"
    from urllib.parse import urlencode
    return RedirectResponse("/contacts?" + urlencode({"info": msg}), status_code=303)


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
    # If we just issued a portal token for this contact, the raw token
    # is in the one-shot in-process flash (NOT the URL). Pop it once.
    portal_token = _pop_portal_token(sess["user_id"], contact_id)
    try:
        contact = contacts_service.get(ctx, contact_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    timeline = interactions_service.list_for_contact(ctx, contact_id, limit=50)
    notes_list = notes_service.list_for_contact(ctx, contact_id)
    scores_data = scoring_service.get_scores(ctx, contact_id)
    portal_tokens_list = portals_service.list_for_contact(ctx, contact_id)
    # v4.1 — show the contact's tags up front so users can SEE auto-tags appear
    from .services import tags as _tags_svc
    contact_tags = _tags_svc.list_for_contact(ctx, contact_id)
    csrf = auth_mod.csrf_token_for(sess["id"])

    tags_html = "".join(
        f'<span class="contact-tag{(" auto" if t["name"].startswith("topic:") else "")}">'
        f'{_h(t["name"])}</span>'
        for t in contact_tags
    ) or '<span class="faint" style="font-size:12px">No tags yet. They appear here when you (or a plug-in) add them.</span>'

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

    # Was a reveal just performed? Show the body of that one note unredacted.
    just_revealed = request.query_params.get("reveal", "")
    just_revealed_id = int(just_revealed) if just_revealed.isdigit() else 0

    def _note_card(n):
        is_redacted = bool(n.get("_private_redacted"))
        is_just_revealed = (n.get("id") == just_revealed_id)
        body_html = (
            f'<em class="faint">private — body redacted</em>'
            if is_redacted and not is_just_revealed
            else _h(n.get("body") or "")
        )
        reveal_btn = ""
        if is_redacted and not is_just_revealed and sess["role"] == "admin":
            reveal_btn = (
                f'<form method="post" action="/notes/{n["id"]}/reveal" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<input type="hidden" name="contact_id" value="{contact["id"]}">'
                f'<button class="btn secondary" style="padding:2px 10px;font-size:10.5px"'
                f'        onclick="return confirm(&#39;Reveal this private note? The reveal is audited.&#39;);">'
                f'  Reveal (audited)</button></form>'
            )
        revealed_pill = (
            ' <span class="task-prio prio-high" style="margin-left:6px">just revealed (audited)</span>'
            if is_just_revealed else ""
        )
        return (
            f'<div class="card" style="margin:0 0 8px 0; padding:10px 12px">'
            f'<div class="row-flex" style="margin-bottom:4px">'
            f'<span class="label-uppercase" style="color: {"var(--copper)" if n.get("visibility")=="private" else "var(--fg-muted)"}">{_h(n.get("visibility"))}</span>'
            f'{revealed_pill}'
            f'<span class="spacer"></span>'
            f'<span class="muted mono" style="font-size:11px">{_h(n.get("created_at"))}</span>'
            f'</div>'
            f'<div>{body_html}</div>'
            + (f'<div style="margin-top:6px">{reveal_btn}</div>' if reveal_btn else "")
            + f'</div>'
        )

    notes_html = "".join(_note_card(n) for n in notes_list) or \
                 '<div class="empty" style="padding:14px">No notes yet.</div>'

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

    pc = contact.get("preferred_channel") or ""
    dnc = 1 if contact.get("do_not_contact") else 0

    return HTMLResponse(_render(
        "contact.html",
        topnav=_topnav("contacts", sess, csrf),
        id=str(contact["id"]),
        full_name=_h(contact.get("full_name") or ""),
        email=_h(contact.get("email") or ""),
        phone=_h(contact.get("phone") or ""),
        title=_h(contact.get("title") or ""),
        location=_h(contact.get("location") or ""),
        timezone=_h(contact.get("timezone") or ""),
        pronouns=_h(contact.get("pronouns") or ""),
        birthday=_h(contact.get("birthday") or ""),
        language=_h(contact.get("language") or ""),
        website_url=_h(contact.get("website_url") or ""),
        linkedin_url=_h(contact.get("linkedin_url") or ""),
        twitter_url=_h(contact.get("twitter_url") or ""),
        instagram_url=_h(contact.get("instagram_url") or ""),
        about=_h(contact.get("about") or ""),
        interests_json=_h(contact.get("interests_json") or ""),
        source=_h(contact.get("source") or ""),
        referrer=_h(contact.get("referrer") or ""),
        best_contact_window=_h(contact.get("best_contact_window") or ""),
        pc_blank_sel=("selected" if pc == "" else ""),
        pc_email_sel=("selected" if pc == "email" else ""),
        pc_phone_sel=("selected" if pc == "phone" else ""),
        pc_sms_sel=("selected" if pc == "sms" else ""),
        pc_inperson_sel=("selected" if pc == "in_person" else ""),
        dnc_no_sel=("selected" if not dnc else ""),
        dnc_yes_sel=("selected" if dnc else ""),
        created_at=_h(contact.get("created_at")),
        updated_at=_h(contact.get("updated_at")),
        csrf=csrf,
        timeline_rows=timeline_rows,
        notes_html=notes_html,
        scores_html=scores_html,
        portal_block=portal_block,
        tags_html=tags_html,
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
def companies_page(request: Request, q: str = "", show_deleted: int = 0):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    show_del = bool(show_deleted)
    result = companies_service.list_(ctx, limit=200, offset=0, q=q or None,
                                     include_deleted=show_del)
    rows = []
    for c in result["items"]:
        label = c.get("name") or f"#{c['id']}"
        is_deleted = bool(c.get("deleted_at"))
        if is_deleted:
            restore_btn = (
                f'<form method="post" action="/companies/{c["id"]}/restore" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn secondary" style="padding:2px 8px">↩ restore</button></form>'
            )
            row_cls = ' class="row-deleted"'
            name_cell = f'<a href="/companies/{c["id"]}">{_h(label)}</a> <span class="faint">(deleted)</span>'
        else:
            restore_btn = ""
            row_cls = ""
            name_cell = f'<a href="/companies/{c["id"]}">{_h(label)}</a>'
        rows.append(
            f'<tr{row_cls}>'
            f'<td style="width:28px"><input type="checkbox" name="ids" value="{c["id"]}"></td>'
            f'<td>{name_cell}</td>'
            f'<td>{_h(c.get("domain") or "")}</td>'
            f'<td>{_h(c.get("industry") or "")}</td>'
            f'<td>{_h(c.get("location") or "")} {restore_btn}</td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="5" class="empty">No companies yet. Add one below.</td></tr>'
    return HTMLResponse(_render(
        "companies.html",
        topnav=_topnav("companies", sess, csrf),
        rows=rows_html,
        total=str(result["total"]),
        q=_h(q),
        show_deleted_toggle=_show_deleted_toggle(show_del, "/companies", q),
        tag_options=_tag_options_html(ctx, "company"),
        saved_views_bar=_saved_views_block(
            ctx, "company", "/companies", csrf,
            {"q": q, "show_deleted": ("1" if show_del else "")},
        ),
        csrf=csrf,
    ))


@app.post("/companies/bulk")
async def companies_bulk(request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    form = await request.form()
    raw_ids = form.getlist("ids")
    ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
    action = form.get("action", "").strip()
    tag_id = form.get("tag_id") or None
    try:
        tag_id_int = int(tag_id) if tag_id else None
    except ValueError:
        tag_id_int = None
    if not ids:
        return RedirectResponse("/companies?error=No+rows+selected", status_code=303)
    try:
        result = companies_service.bulk_apply(ctx, ids, action=action, tag_id=tag_id_int)
    except ServiceError as e:
        return RedirectResponse(f"/companies?error={_h(e.message)}", status_code=303)
    msg = f"{len(result['ok'])} succeeded"
    if result["errors"]:
        msg += f", {len(result['errors'])} failed"
    from urllib.parse import urlencode
    return RedirectResponse("/companies?" + urlencode({"info": msg}), status_code=303)


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
        f'  <div class="deal-title"><a href="/deals/{deal["id"]}">{_h(deal["title"])}</a></div>'
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
def pipelines_page(request: Request, pipeline_id: int = 0,
                   include_archived: int = 0):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    inc_arch = bool(include_archived)
    pipelines_list = pipelines_service.list_pipelines(ctx, include_archived=inc_arch)
    csrf = auth_mod.csrf_token_for(sess["id"])

    # Pipeline selector strip with per-pipeline archive/unarchive button
    if pipelines_list:
        if not pipeline_id:
            # Prefer first non-archived
            non_arch = [p for p in pipelines_list if not p.get("archived")]
            pipeline_id = (non_arch or pipelines_list)[0]["id"]
        chips = []
        for p in pipelines_list:
            arch_cls = " archived" if p.get("archived") else ""
            active_cls = " active" if p["id"] == pipeline_id else ""
            arch_btn_action = "unarchive" if p.get("archived") else "archive"
            arch_btn_label = "↩ unarchive" if p.get("archived") else "× archive"
            confirm_text = (f"Unarchive pipeline '{p['name']}'?"
                            if p.get("archived")
                            else f"Archive pipeline '{p['name']}'? Existing deals are unaffected.")
            chips.append(
                f'<span class="pipeline-chip{active_cls}{arch_cls}">'
                f'<a href="/pipelines?pipeline_id={p["id"]}{ "&include_archived=1" if inc_arch else "" }">{_h(p["name"])}'
                f'<span class="muted"> · {_h(p["type"])}</span></a>'
                f'<form method="post" action="/pipelines/{p["id"]}/{arch_btn_action}" style="display:inline" '
                f'      onsubmit="return confirm(\'{confirm_text}\');">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="pipeline-archive-btn" type="submit">{arch_btn_label}</button>'
                f'</form>'
                f'</span>'
            )
        toggle_link = (
            f'<a href="/pipelines?pipeline_id={pipeline_id}" class="btn secondary" '
            f'   style="padding:3px 10px;font-size:11px">Hide archived</a>'
            if inc_arch else
            f'<a href="/pipelines?pipeline_id={pipeline_id}&include_archived=1" class="btn secondary" '
            f'   style="padding:3px 10px;font-size:11px">Show archived</a>'
        )
        selector = "".join(chips) + f'<span style="margin-left:auto">{toggle_link}</span>'
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


@app.get("/deals/{deal_id}", response_class=HTMLResponse)
def deal_detail(deal_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        deal = deals_service.get(ctx, deal_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    pipeline = pipelines_service.get_pipeline(ctx, deal["pipeline_id"])
    csrf = auth_mod.csrf_token_for(sess["id"])

    # related entities
    contact_block = ""
    if deal.get("contact_id"):
        try:
            cc = contacts_service.get(ctx, deal["contact_id"])
            contact_block = (
                f'<a href="/contacts/{cc["id"]}">{_h(cc.get("full_name") or cc.get("email") or "(unnamed)")}</a>'
                + (f' <span class="muted">· {_h(cc.get("title") or "")}</span>' if cc.get("title") else "")
            )
        except ServiceError:
            contact_block = f'<span class="faint">contact #{deal["contact_id"]} (not found)</span>'
    else:
        contact_block = '<span class="faint">no contact linked</span>'

    company_block = ""
    if deal.get("company_id"):
        try:
            co = companies_service.get(ctx, deal["company_id"])
            company_block = f'<a href="/companies/{co["id"]}">{_h(co.get("name") or "(unnamed)")}</a>'
        except ServiceError:
            company_block = f'<span class="faint">company #{deal["company_id"]} (not found)</span>'
    else:
        company_block = '<span class="faint">no company linked</span>'

    # related tasks
    tasks_data = tasks_service.list_(ctx, deal_id=deal_id, limit=200)
    if tasks_data["items"]:
        task_rows = "".join(
            f'<tr><td><a href="/tasks/{t["id"]}">{_h(t["title"])}</a></td>'
            f'<td><span class="task-prio prio-{_h(t["priority"])}">{_h(t["priority"])}</span></td>'
            f'<td>{_h(t["status"])}</td></tr>'
            for t in tasks_data["items"]
        )
    else:
        task_rows = '<tr><td colspan="3" class="empty">No tasks on this deal yet.</td></tr>'

    # audit for this deal
    with db() as conn:
        ev_rows = conn.execute(
            "SELECT ts, action, surface, user_id FROM audit_log "
            "WHERE object_type='deal' AND object_id=? ORDER BY ts DESC LIMIT 50",
            (deal_id,),
        ).fetchall()
    import time as _t
    audit_rows = "".join(
        f'<tr><td class="mono faint">{_t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["ts"]))}</td>'
        f'<td class="mono">{_h(r["action"])}</td>'
        f'<td class="muted">{_h(r["surface"])}</td>'
        f'<td class="muted">user #{r["user_id"] or "—"}</td></tr>'
        for r in ev_rows
    ) or '<tr><td colspan="4" class="empty">No history.</td></tr>'

    # stage options for the move dropdown
    stage_options = "".join(
        f'<option value="{s["id"]}"{" selected" if s["id"] == deal["stage_id"] else ""}>{_h(s["name"])}'
        + (" (won)" if s.get("is_won") else "")
        + (" (lost)" if s.get("is_lost") else "")
        + '</option>'
        for s in pipeline["stages"]
    )
    # reopen control: shown only if deal is won/lost
    reopen_block = ""
    if deal["status"] in ("won", "lost"):
        # first non-terminal stage in this pipeline
        first_open = next(
            (s for s in pipeline["stages"]
             if not s.get("is_won") and not s.get("is_lost")),
            None,
        )
        if first_open:
            reopen_block = (
                '<form method="post" action="/deals/' + str(deal_id) + '/reopen" style="margin-top:8px">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<input type="hidden" name="stage_id" value="{first_open["id"]}">'
                f'<button class="btn secondary" type="submit">Reopen → {_h(first_open["name"])}</button>'
                '</form>'
            )

    value_value = (
        f"{deal['value_cents']/100:.2f}" if deal.get("value_cents") is not None else ""
    )
    expected_close_value = ""
    if deal.get("expected_close"):
        expected_close_value = _t.strftime("%Y-%m-%d", _t.localtime(deal["expected_close"]))

    return HTMLResponse(_render(
        "deal.html",
        topnav=_topnav("pipelines", sess, csrf),
        id=str(deal_id),
        title=_h(deal["title"] or "(untitled)"),
        status=_h(deal["status"]),
        pipeline_name=_h(pipeline["name"]),
        pipeline_id=str(deal["pipeline_id"]),
        stage_options=stage_options,
        reopen_block=reopen_block,
        value_cents=value_value,
        currency=_h(deal.get("currency") or ""),
        probability=str(deal.get("probability") or ""),
        expected_close=expected_close_value,
        next_step=_h(deal.get("next_step") or ""),
        notes=_h(deal.get("notes") or ""),
        contact_block=contact_block,
        company_block=company_block,
        task_rows=task_rows,
        audit_rows=audit_rows,
        created_at=str(deal.get("created_at") or ""),
        updated_at=str(deal.get("updated_at") or ""),
        csrf=csrf,
    ))


@app.post("/deals/{deal_id}/edit")
async def deal_edit_form(
    deal_id: int, request: Request,
    title: str = Form(""), value: str = Form(""), currency: str = Form(""),
    probability: str = Form(""), expected_close: str = Form(""),
    next_step: str = Form(""), notes: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    payload = {}
    if title.strip(): payload["title"] = title.strip()
    if value.strip():
        try:
            payload["value_cents"] = int(round(float(value) * 100))
        except ValueError:
            return RedirectResponse(f"/deals/{deal_id}?error=value+must+be+a+number", status_code=303)
    if currency.strip(): payload["currency"] = currency.strip().lower()
    if probability.strip():
        try:
            payload["probability"] = int(probability)
        except ValueError:
            return RedirectResponse(f"/deals/{deal_id}?error=probability+must+be+0-100", status_code=303)
    if expected_close.strip():
        try:
            import time as _t
            payload["expected_close"] = int(_t.mktime(_t.strptime(expected_close, "%Y-%m-%d")))
        except ValueError:
            return RedirectResponse(f"/deals/{deal_id}?error=expected_close+must+be+YYYY-MM-DD", status_code=303)
    if next_step.strip(): payload["next_step"] = next_step.strip()
    if notes.strip(): payload["notes"] = notes.strip()
    try:
        deals_service.update(ctx, deal_id, payload)
    except ServiceError as e:
        return RedirectResponse(f"/deals/{deal_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/deals/{deal_id}", status_code=303)


@app.post("/deals/{deal_id}/reopen")
async def deal_reopen_form(deal_id: int, request: Request,
                            stage_id: int = Form(...), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        deals_service.update(ctx, deal_id, {"stage_id": stage_id, "status": "open"})
    except ServiceError as e:
        return RedirectResponse(f"/deals/{deal_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/deals/{deal_id}", status_code=303)


@app.post("/deals/{deal_id}/delete")
async def deal_delete_form(deal_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        d = deals_service.get(ctx, deal_id)
        pid = d["pipeline_id"]
        deals_service.delete(ctx, deal_id)
    except ServiceError:
        return RedirectResponse("/pipelines", status_code=303)
    return RedirectResponse(f"/pipelines?pipeline_id={pid}", status_code=303)


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
        f'  <td><strong><a href="/tasks/{t["id"]}">{_h(t["title"])}</a></strong>'
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


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: int, request: Request):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    try:
        task = tasks_service.get(ctx, task_id)
    except ServiceError as e:
        raise HTTPException(status_code=404, detail=e.message)
    csrf = auth_mod.csrf_token_for(sess["id"])

    # related entity links
    def _link_to(svc, entity_id, kind, label_key):
        if not entity_id:
            return f'<span class="faint">no {kind} linked</span>'
        try:
            row = svc.get(ctx, entity_id)
            return f'<a href="/{kind}s/{row["id"]}">{_h(row.get(label_key) or row.get("name") or "(unnamed)")}</a>'
        except ServiceError:
            return f'<span class="faint">{kind} #{entity_id} (not found)</span>'

    contact_link = _link_to(contacts_service, task.get("contact_id"), "contact", "full_name")
    company_link = _link_to(companies_service, task.get("company_id"), "company", "name")
    deal_link = ""
    if task.get("deal_id"):
        try:
            dd = deals_service.get(ctx, task["deal_id"])
            deal_link = f'<a href="/deals/{dd["id"]}">{_h(dd["title"])}</a>'
        except ServiceError:
            deal_link = f'<span class="faint">deal #{task["deal_id"]} (not found)</span>'
    else:
        deal_link = '<span class="faint">no deal linked</span>'

    import time as _t
    due_value = ""
    if task.get("due_date"):
        due_value = _t.strftime("%Y-%m-%d", _t.localtime(task["due_date"]))

    # status + priority option selectors
    def _opts(values, current):
        return "".join(
            f'<option value="{v}"{ " selected" if v == current else "" }>{v}</option>'
            for v in values
        )
    priority_opts = _opts(("low", "normal", "high", "urgent"), task.get("priority") or "normal")
    status_opts = _opts(("open", "in_progress", "done", "cancelled"), task.get("status") or "open")

    # audit
    with db() as conn:
        ev_rows = conn.execute(
            "SELECT ts, action, surface, user_id FROM audit_log "
            "WHERE object_type='task' AND object_id=? ORDER BY ts DESC LIMIT 50",
            (task_id,),
        ).fetchall()
    audit_rows = "".join(
        f'<tr><td class="mono faint">{_t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["ts"]))}</td>'
        f'<td class="mono">{_h(r["action"])}</td>'
        f'<td class="muted">{_h(r["surface"])}</td>'
        f'<td class="muted">user #{r["user_id"] or "—"}</td></tr>'
        for r in ev_rows
    ) or '<tr><td colspan="4" class="empty">No history.</td></tr>'

    # quick complete/reopen control
    if task["status"] == "done":
        toggle_block = (
            '<form method="post" action="/tasks/' + str(task_id) + '/reopen" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button class="btn secondary" type="submit">↩ Reopen task</button>'
            '</form>'
        )
    else:
        toggle_block = (
            '<form method="post" action="/tasks/' + str(task_id) + '/complete" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<button class="btn" type="submit">✓ Mark done</button>'
            '</form>'
        )

    return HTMLResponse(_render(
        "task.html",
        topnav=_topnav("tasks", sess, csrf),
        id=str(task_id),
        title=_h(task["title"]),
        description=_h(task.get("description") or ""),
        status=_h(task["status"]),
        priority=_h(task["priority"]),
        priority_opts=priority_opts,
        status_opts=status_opts,
        due_date=due_value,
        contact_link=contact_link,
        company_link=company_link,
        deal_link=deal_link,
        toggle_block=toggle_block,
        audit_rows=audit_rows,
        created_at=str(task.get("created_at") or ""),
        updated_at=str(task.get("updated_at") or ""),
        completed_at=str(task.get("completed_at") or "") or "—",
        csrf=csrf,
    ))


@app.post("/tasks/{task_id}/edit")
async def task_edit_form(
    task_id: int, request: Request,
    title: str = Form(""), description: str = Form(""),
    priority: str = Form(""), status: str = Form(""),
    due_date: str = Form(""),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    payload = {}
    if title.strip(): payload["title"] = title.strip()
    if description.strip() != "": payload["description"] = description.strip()
    if priority.strip(): payload["priority"] = priority.strip()
    if status.strip(): payload["status"] = status.strip()
    if due_date.strip():
        try:
            import time as _t
            payload["due_date"] = int(_t.mktime(_t.strptime(due_date, "%Y-%m-%d")))
        except ValueError:
            return RedirectResponse(f"/tasks/{task_id}?error=due_date+must+be+YYYY-MM-DD", status_code=303)
    if not payload:
        return RedirectResponse(f"/tasks/{task_id}", status_code=303)
    try:
        tasks_service.update(ctx, task_id, payload)
    except ServiceError as e:
        return RedirectResponse(f"/tasks/{task_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/tasks/{task_id}/reopen")
async def task_reopen_form(task_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        tasks_service.update(ctx, task_id, {"status": "open"})
    except ServiceError as e:
        return RedirectResponse(f"/tasks/{task_id}?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/contacts/{contact_id}/restore")
async def contact_restore_form(contact_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        contacts_service.restore(ctx, contact_id)
    except ServiceError as e:
        return RedirectResponse(f"/contacts?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@app.post("/companies/{company_id}/restore")
async def company_restore_form(company_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        companies_service.restore(ctx, company_id)
    except ServiceError as e:
        return RedirectResponse(f"/companies?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/companies/{company_id}", status_code=303)


@app.post("/pipelines/{pipeline_id}/archive")
async def pipeline_archive_form(pipeline_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        pipelines_service.archive_pipeline(ctx, pipeline_id)
    except ServiceError as e:
        return RedirectResponse(f"/pipelines?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/pipelines", status_code=303)


@app.post("/pipelines/{pipeline_id}/unarchive")
async def pipeline_unarchive_form(pipeline_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        pipelines_service.unarchive_pipeline(ctx, pipeline_id)
    except ServiceError as e:
        return RedirectResponse(f"/pipelines?error={_h(e.message)}", status_code=303)
    return RedirectResponse(f"/pipelines?pipeline_id={pipeline_id}", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request,
               object_type: str = "", object_id: str = "",
               action: str = "", surface: str = "",
               user_id: str = "", request_id: str = "",
               limit: int = 100, offset: int = 0):
    sess = _require_session(request)
    if sess["role"] != "admin":
        raise HTTPException(status_code=403, detail="audit log is admin-only")
    csrf = auth_mod.csrf_token_for(sess["id"])

    where, params = [], []
    if object_type.strip(): where.append("object_type = ?"); params.append(object_type.strip())
    if object_id.strip():
        try: params.append(int(object_id)); where.append("object_id = ?")
        except ValueError: pass
    if action.strip(): where.append("action LIKE ?"); params.append(f"%{action.strip()}%")
    if surface.strip(): where.append("surface = ?"); params.append(surface.strip())
    if user_id.strip():
        try: params.append(int(user_id)); where.append("user_id = ?")
        except ValueError: pass
    if request_id.strip(): where.append("request_id = ?"); params.append(request_id.strip())

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    import time as _t
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log{where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, ts, user_id, api_key_id, surface, action, object_type, "
            f"       object_id, request_id FROM audit_log{where_sql} "
            f"ORDER BY ts DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    def _link_object(otype, oid):
        if oid is None: return f'<span class="faint">—</span>'
        plural = {"contact": "contacts", "company": "companies", "deal": "deals",
                  "task": "tasks", "form": "forms", "segment": "segments",
                  "note": "contacts"}.get(otype)
        if plural and plural != "contacts" or otype == "contact":
            return f'<a href="/{plural}/{oid}">{otype} #{oid}</a>'
        return f'{otype} #{oid}'

    body_rows = "".join(
        f'<tr><td class="mono faint">{_t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(r["ts"]))}</td>'
        f'<td class="mono">{_h(r["action"])}</td>'
        f'<td>{_link_object(r["object_type"], r["object_id"])}</td>'
        f'<td class="muted">{_h(r["surface"])}</td>'
        f'<td class="muted">user #{r["user_id"] or "—"}'
        + (f' / key #{r["api_key_id"]}' if r["api_key_id"] else "")
        + f'</td>'
        f'<td class="mono faint" style="font-size:10.5px">{_h(r["request_id"] or "")}</td></tr>'
        for r in rows
    ) or '<tr><td colspan="6" class="empty">No matching audit entries.</td></tr>'

    # Build prev/next links preserving the filter query
    from urllib.parse import urlencode
    qs_base = {k: v for k, v in {
        "object_type": object_type, "object_id": object_id, "action": action,
        "surface": surface, "user_id": user_id, "request_id": request_id,
        "limit": str(limit),
    }.items() if v}
    prev_off = max(0, offset - limit)
    next_off = offset + limit
    prev_link = (f'<a href="/audit?{urlencode({**qs_base, "offset": str(prev_off)})}">‹ prev</a>'
                 if offset > 0 else '<span class="faint">‹ prev</span>')
    next_link = (f'<a href="/audit?{urlencode({**qs_base, "offset": str(next_off)})}">next ›</a>'
                 if next_off < total else '<span class="faint">next ›</span>')

    return HTMLResponse(_render(
        "audit.html",
        topnav=_topnav("", sess, csrf),
        rows=body_rows,
        total=str(total),
        offset=str(offset),
        limit=str(limit),
        prev_link=prev_link,
        next_link=next_link,
        f_object_type=_h(object_type),
        f_object_id=_h(object_id),
        f_action=_h(action),
        f_surface=_h(surface),
        f_user_id=_h(user_id),
        f_request_id=_h(request_id),
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
async def contact_edit_form(contact_id: int, request: Request):
    """Accept any of the rich-contact field set. Empty strings clear the
    field; missing form keys leave the existing value alone."""
    sess = _require_session(request)
    form_data = await request.form()
    _csrf_check(request, sess, form_data.get("csrf", ""))
    ctx = _ctx_from_session(sess)

    # Build a payload from whichever sub-form posted. Only the fields the
    # form actually submitted appear in form_data — others are left as-is.
    allowed = {
        "full_name", "email", "phone", "title", "location", "timezone",
        "pronouns", "birthday", "language",
        "website_url", "linkedin_url", "twitter_url", "instagram_url",
        "about", "interests_json", "source", "referrer",
        "best_contact_window", "preferred_channel",
    }
    payload = {}
    for k in allowed:
        if k in form_data:
            v = (form_data.get(k) or "").strip()
            payload[k] = v or None
    if "do_not_contact" in form_data:
        payload["do_not_contact"] = 1 if form_data.get("do_not_contact") in ("1", "true", "on", "yes") else 0
    if not payload:
        return RedirectResponse(f"/contacts/{contact_id}", status_code=303)
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


# ---------- saved views ----------

@app.get("/saved-views", response_class=HTMLResponse)
def saved_views_page(request: Request, entity: str = "contact"):
    sess = _require_session(request)
    ctx = _ctx_from_session(sess)
    csrf = auth_mod.csrf_token_for(sess["id"])
    from .services import saved_views as _sv
    valid = ("contact", "company", "deal", "task", "interaction")
    if entity not in valid:
        entity = "contact"
    try:
        views = _sv.list_for_entity(ctx, entity)
    except ServiceError as e:
        raise HTTPException(400, e.message)
    import json as _json
    rows = []
    for v in views:
        try:
            cfg = _json.loads(v.get("config_json") or "{}")
        except Exception:
            cfg = {}
        is_mine = v.get("user_id") == sess["user_id"]
        actions = []
        if is_mine or sess["role"] == "admin":
            actions.append(
                f'<form method="post" action="/saved-views/{v["id"]}/toggle-shared" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn secondary" style="padding:2px 8px;font-size:11px">{"Unshare" if v.get("shared") else "Share"}</button>'
                f'</form>'
            )
            actions.append(
                f'<form method="post" action="/saved-views/{v["id"]}/delete" style="display:inline" '
                f'      onsubmit="return confirm(\'Delete saved view {_h(v["name"])}?\');">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                f'<button class="btn danger" style="padding:2px 8px;font-size:11px">Delete</button></form>'
            )
        rows.append(
            f'<tr><td>{_h(v["name"])}</td>'
            f'<td><span class="task-prio prio-{"normal" if v.get("shared") else "low"}">'
            f'{"shared" if v.get("shared") else "private"}</span></td>'
            f'<td class="muted">user #{v["user_id"]}{" (you)" if is_mine else ""}</td>'
            f'<td class="mono" style="font-size:11px">{_h(_json.dumps(cfg))}</td>'
            f'<td>{" ".join(actions)}</td></tr>'
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="5" class="empty">No saved views for this entity yet.</td></tr>'
    entity_tabs = "".join(
        f'<a href="/saved-views?entity={e}" class="task-tab{" active" if e == entity else ""}">'
        f'{e.title()}</a>'
        for e in valid
    )
    return HTMLResponse(_render(
        "saved_views.html",
        topnav=_topnav("", sess, csrf),
        entity_tabs=entity_tabs,
        entity=entity,
        rows=rows_html,
        csrf=csrf,
    ))


@app.post("/saved-views")
async def saved_view_create_form(
    request: Request,
    entity: str = Form(...),
    name: str = Form(...),
    config_json: str = Form("{}"),
    shared: str = Form(""),
    redirect_to: str = Form("/contacts"),
    csrf: str = Form(""),
):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    import json as _json
    try:
        cfg = _json.loads(config_json) if config_json else {}
    except _json.JSONDecodeError:
        cfg = {}
    from .services import saved_views as _sv
    try:
        _sv.create(ctx, entity=entity, name=name.strip(),
                   config=cfg, shared=bool(shared))
    except ServiceError as e:
        from urllib.parse import urlencode
        return RedirectResponse(redirect_to + "?" + urlencode({"error": e.message}),
                                status_code=303)
    from urllib.parse import urlencode
    return RedirectResponse(redirect_to + "?" + urlencode({"info": f"View {name!r} saved"}),
                            status_code=303)


@app.post("/saved-views/{view_id}/delete")
async def saved_view_delete_form(view_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    from .services import saved_views as _sv
    try:
        _sv.delete(ctx, view_id)
    except ServiceError as e:
        return RedirectResponse(f"/saved-views?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/saved-views?info=View+deleted", status_code=303)


@app.post("/saved-views/{view_id}/toggle-shared")
async def saved_view_toggle_shared_form(view_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    from .services import saved_views as _sv
    try:
        v = _sv.get(ctx, view_id)
        _sv.update(ctx, view_id, {"shared": not v.get("shared")})
    except ServiceError as e:
        return RedirectResponse(f"/saved-views?error={_h(e.message)}", status_code=303)
    return RedirectResponse("/saved-views?info=Updated", status_code=303)


# ---------- settings: webhook delivery log + retry + delete ----------

@app.get("/settings/webhooks/{webhook_id}", response_class=HTMLResponse)
def webhook_detail(webhook_id: int, request: Request):
    sess = _require_session(request)
    if sess["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    csrf = auth_mod.csrf_token_for(sess["id"])
    import time as _t
    with db() as conn:
        wh = conn.execute("SELECT * FROM webhooks WHERE id=?", (webhook_id,)).fetchone()
        if not wh:
            raise HTTPException(404, "webhook not found")
        wh = dict(wh)
        deliveries = conn.execute(
            "SELECT id, event_type, status, attempts, response_status, "
            "       next_attempt_at, created_at, delivery_id "
            "FROM webhook_events WHERE webhook_id=? ORDER BY id DESC LIMIT 100",
            (webhook_id,),
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM webhook_events "
            "WHERE webhook_id=? GROUP BY status",
            (webhook_id,),
        ).fetchall()

    counts_map = {r["status"]: r["n"] for r in counts}
    stat_pill = lambda label, key, color: (
        f'<span class="task-prio prio-{color}" style="margin-right:6px">'
        f'{counts_map.get(key, 0)} {label}</span>'
    )
    stats_html = (
        stat_pill("pending", "pending", "low")
        + stat_pill("retrying", "retrying", "high")
        + stat_pill("delivered", "delivered", "normal")
        + stat_pill("failed", "failed", "urgent")
    )

    delivery_rows = "".join(
        f'<tr><td class="mono faint">{_t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(d["created_at"]))}</td>'
        f'<td class="mono">{_h(d["event_type"])}</td>'
        f'<td>{_h(d["status"])}</td>'
        f'<td class="mono">{d["attempts"] or 0}</td>'
        f'<td class="mono faint">{_h(d["response_status"] or "—")}</td>'
        f'<td class="mono faint" style="font-size:10.5px">{_h(d["delivery_id"] or "")}</td>'
        f'<td>'
        + (
            f'<form method="post" action="/webhook-events/{d["id"]}/retry" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<button class="btn secondary" style="padding:2px 8px;font-size:11px">↻ retry</button></form>'
            if d["status"] in ("failed", "retrying") else ""
        )
        + '</td></tr>'
        for d in deliveries
    ) or '<tr><td colspan="7" class="empty">No deliveries logged yet.</td></tr>'

    return HTMLResponse(_render(
        "webhook.html",
        topnav=_topnav("settings", sess, csrf),
        id=str(webhook_id),
        url=_h(wh["url"]),
        events=_h(wh.get("events_json") or ""),
        status=("active" if wh.get("active") else "paused"),
        stats=stats_html,
        delivery_rows=delivery_rows,
        csrf=csrf,
    ))


@app.post("/settings/webhooks/{webhook_id}/delete")
async def webhook_delete_form(webhook_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if sess["role"] != "admin":
        raise HTTPException(403, "admin only")
    with db() as conn:
        conn.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))
    return RedirectResponse("/settings?info=Webhook+deleted", status_code=303)


@app.post("/settings/webhooks/{webhook_id}/toggle")
async def webhook_toggle_form(webhook_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if sess["role"] != "admin":
        raise HTTPException(403, "admin only")
    with db() as conn:
        conn.execute(
            "UPDATE webhooks SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
            (webhook_id,),
        )
    return RedirectResponse(f"/settings/webhooks/{webhook_id}", status_code=303)


@app.post("/webhook-events/{event_id}/retry")
async def webhook_event_retry(event_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if sess["role"] != "admin":
        raise HTTPException(403, "admin only")
    import time as _t
    with db() as conn:
        row = conn.execute("SELECT webhook_id FROM webhook_events WHERE id=?",
                           (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "delivery not found")
        conn.execute(
            "UPDATE webhook_events SET status='pending', attempts=0, "
            "  next_attempt_at=?, response_status=NULL, response_body=NULL "
            "WHERE id=?",
            (int(_t.time()), event_id),
        )
        wh_id = row["webhook_id"]
    return RedirectResponse(f"/settings/webhooks/{wh_id}?info=Delivery+re-queued",
                            status_code=303)


# ---------- plug-in detail / config editor ----------

@app.get("/plugins/{plugin_id}", response_class=HTMLResponse)
def plugin_detail(plugin_id: int, request: Request):
    sess = _require_session(request)
    csrf = auth_mod.csrf_token_for(sess["id"])
    import time as _t
    with db() as conn:
        p = conn.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        if not p:
            raise HTTPException(404, "plugin not found")
        p = dict(p)
        hooks = conn.execute(
            "SELECT hook_name, priority FROM plugin_hooks WHERE plugin_id=? "
            "ORDER BY hook_name", (plugin_id,)
        ).fetchall()
        recent_errors = conn.execute(
            "SELECT ts, before_json FROM audit_log "
            "WHERE action='plugin.error' AND object_id=? "
            "ORDER BY ts DESC LIMIT 20",
            (plugin_id,),
        ).fetchall()

    hook_rows = "".join(
        f'<tr><td class="mono">{_h(h["hook_name"])}</td>'
        f'<td class="mono">{h["priority"]}</td></tr>'
        for h in hooks
    ) or '<tr><td colspan="2" class="empty">No hooks registered.</td></tr>'

    err_rows = "".join(
        f'<tr><td class="mono faint">{_t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(r["ts"]))}</td>'
        f'<td class="mono" style="font-size:11px">{_h(r["before_json"] or "")[:300]}</td></tr>'
        for r in recent_errors
    ) or '<tr><td colspan="2" class="empty">No recent errors. 🎉</td></tr>'

    return HTMLResponse(_render(
        "plugin.html",
        topnav=_topnav("plugins", sess, csrf),
        id=str(plugin_id),
        name=_h(p["name"]),
        version=_h(p.get("version") or ""),
        description=_h(p.get("description") or ""),
        enabled=("yes" if p.get("enabled") else "no"),
        config_json=_h(p.get("config_json") or ""),
        last_error=_h(p.get("last_error") or ""),
        hook_rows=hook_rows,
        err_rows=err_rows,
        csrf=csrf,
    ))


@app.post("/plugins/{plugin_id}/config")
async def plugin_config_form(plugin_id: int, request: Request,
                              config_json: str = Form(""), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if sess["role"] != "admin":
        raise HTTPException(403, "admin only")
    import json as _json
    try:
        # Validate it's parseable JSON (or empty)
        if config_json.strip():
            _json.loads(config_json)
        with db() as conn:
            conn.execute("UPDATE plugins SET config_json=? WHERE id=?",
                         (config_json.strip() or None, plugin_id))
    except _json.JSONDecodeError as e:
        return RedirectResponse(
            f"/plugins/{plugin_id}?error=Invalid+JSON:+{_h(str(e))}",
            status_code=303,
        )
    return RedirectResponse(f"/plugins/{plugin_id}?info=Config+saved",
                            status_code=303)


@app.post("/plugins/{plugin_id}/clear-error")
async def plugin_clear_error_form(plugin_id: int, request: Request, csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    if sess["role"] != "admin":
        raise HTTPException(403, "admin only")
    with db() as conn:
        conn.execute("UPDATE plugins SET last_error=NULL WHERE id=?", (plugin_id,))
    return RedirectResponse(f"/plugins/{plugin_id}?info=Error+cleared", status_code=303)


# ---------- private note reveal (UI wrapper around notes.reveal_private) ----------

@app.post("/notes/{note_id}/reveal")
async def note_reveal_form(note_id: int, request: Request,
                            contact_id: int = Form(...), csrf: str = Form("")):
    sess = _require_session(request)
    _csrf_check(request, sess, csrf)
    ctx = _ctx_from_session(sess)
    try:
        notes_service.reveal_private(ctx, note_id)
    except ServiceError as e:
        return RedirectResponse(f"/contacts/{contact_id}?error={_h(e.message)}",
                                status_code=303)
    return RedirectResponse(f"/contacts/{contact_id}?reveal={note_id}",
                            status_code=303)


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
        f'<tr><td class="mono"><a href="/settings/webhooks/{w["id"]}">{_h(w["url"])}</a></td>'
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
    # Stash raw token in the one-shot flash; redirect with a CLEAN URL.
    # The contact page reads from the flash and shows the link once.
    _flash_portal_token(sess["user_id"], contact_id, tok["token"])
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


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
            f'<tr><td><strong><a href="/plugins/{p["id"]}">{_h(p["name"])}</a></strong>'
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
