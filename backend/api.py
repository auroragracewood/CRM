"""REST API. Thin transport: builds ServiceContext from auth, calls services.

Auth precedence on every request:
  1. Bearer API key in Authorization header  (canonical for agents)
  2. Cookie session                          (used by the browser UI)

Errors map to the standard JSON shape:
  {"ok": false, "error": {"code","message","details","request_id"}}

ServiceError code → HTTP status:
  VALIDATION_ERROR       400
  FORBIDDEN              403
  CONTACT_NOT_FOUND      404
  CONTACT_EMAIL_EXISTS   409
  (default)              400
"""
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import auth as auth_mod
from .context import ServiceContext
from .db import db
from .services import (
    contacts as contacts_service,
    companies as companies_service,
    interactions as interactions_service,
    notes as notes_service,
    tags as tags_service,
    consent as consent_service,
    pipelines as pipelines_service,
    deals as deals_service,
    tasks as tasks_service,
    forms as forms_service,
    duplicates as duplicates_service,
    search as search_service,
    imports as imports_service,
    scoring as scoring_service,
    segments as segments_service,
    reports as reports_service,
    portals as portals_service,
    inbound as inbound_service,
)
from .services.contacts import ServiceError


router = APIRouter(prefix="/api")

_STATUS = {
    "VALIDATION_ERROR": 400,
    "FORBIDDEN": 403,
    "CONTACT_NOT_FOUND": 404,
    "CONTACT_EMAIL_EXISTS": 409,
    "COMPANY_NOT_FOUND": 404,
    "COMPANY_SLUG_EXISTS": 409,
    "NOTE_NOT_FOUND": 404,
    "TAG_EXISTS": 409,
    "API_KEY_NOT_FOUND": 404,
    "PIPELINE_NOT_FOUND": 404,
    "DEAL_NOT_FOUND": 404,
    "TASK_NOT_FOUND": 404,
    "USER_NOT_FOUND": 404,
    "FORM_NOT_FOUND": 404,
    "FORM_SLUG_EXISTS": 409,
    "SEGMENT_NOT_FOUND": 404,
    "SEGMENT_SLUG_EXISTS": 409,
    "REPORT_NOT_FOUND": 404,
    "PORTAL_TOKEN_NOT_FOUND": 404,
    "INBOUND_ENDPOINT_NOT_FOUND": 404,
    "INBOUND_SLUG_EXISTS": 409,
}


def build_context(request: Request, surface: str = "rest") -> ServiceContext:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw_key = auth_header[7:].strip()
        with db() as conn:
            info = auth_mod.lookup_api_key(conn, raw_key)
        if info:
            return ServiceContext(
                api_key_id=info["id"],
                user_id=info["user_id"],
                role="user",
                scope=info["scope"],
                surface=surface,
                request_id=request_id,
            )

    sid = request.cookies.get(auth_mod.SESSION_COOKIE_NAME)
    if sid:
        with db() as conn:
            sess = auth_mod.lookup_session(conn, sid)
        if sess:
            scope = ("admin" if sess["role"] == "admin"
                     else "read" if sess["role"] == "readonly"
                     else "write")
            return ServiceContext(
                user_id=sess["user_id"],
                role=sess["role"],
                scope=scope,
                surface=surface,
                request_id=request_id,
            )

    raise HTTPException(status_code=401, detail="authentication required")


def _error(e: ServiceError, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS.get(e.code, 400),
        content={"ok": False, "error": {
            "code": e.code,
            "message": e.message,
            "details": e.details,
            "request_id": request_id,
        }},
    )


# ----- /api/me (identity inspection — useful for acceptance test) -----

@router.get("/me")
async def api_me(request: Request):
    ctx = build_context(request)
    return {
        "ok": True,
        "user_id": ctx.user_id,
        "api_key_id": ctx.api_key_id,
        "role": ctx.role,
        "scope": ctx.scope,
        "surface": ctx.surface,
        "request_id": ctx.request_id,
    }


# ----- Contacts -----

@router.post("/contacts")
async def api_create_contact(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        contact = contacts_service.create(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(
        status_code=201,
        content={"ok": True, "contact": contact, "request_id": ctx.request_id},
    )


@router.get("/contacts")
async def api_list_contacts(request: Request, limit: int = 50, offset: int = 0, q: str = None):
    ctx = build_context(request)
    try:
        result = contacts_service.list_(ctx, limit=limit, offset=offset, q=q)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/contacts/{contact_id}")
async def api_get_contact(contact_id: int, request: Request):
    ctx = build_context(request)
    try:
        contact = contacts_service.get(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "contact": contact, "request_id": ctx.request_id}


@router.put("/contacts/{contact_id}")
async def api_update_contact(contact_id: int, request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        contact = contacts_service.update(ctx, contact_id, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "contact": contact, "request_id": ctx.request_id}


@router.delete("/contacts/{contact_id}")
async def api_delete_contact(contact_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = contacts_service.delete(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Companies -----

@router.post("/companies")
async def api_create_company(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        company = companies_service.create(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "company": company, "request_id": ctx.request_id})


@router.get("/companies")
async def api_list_companies(request: Request, limit: int = 50, offset: int = 0, q: str = None):
    ctx = build_context(request)
    try:
        result = companies_service.list_(ctx, limit=limit, offset=offset, q=q)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/companies/{company_id}")
async def api_get_company(company_id: int, request: Request):
    ctx = build_context(request)
    try:
        company = companies_service.get(ctx, company_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "company": company, "request_id": ctx.request_id}


@router.put("/companies/{company_id}")
async def api_update_company(company_id: int, request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        company = companies_service.update(ctx, company_id, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "company": company, "request_id": ctx.request_id}


@router.delete("/companies/{company_id}")
async def api_delete_company(company_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = companies_service.delete(ctx, company_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Interactions -----

@router.post("/interactions")
async def api_log_interaction(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        item = interactions_service.log(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "interaction": item, "request_id": ctx.request_id})


@router.get("/contacts/{contact_id}/timeline")
async def api_contact_timeline(contact_id: int, request: Request, limit: int = 50, offset: int = 0):
    ctx = build_context(request)
    try:
        items = interactions_service.list_for_contact(ctx, contact_id, limit=limit, offset=offset)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.get("/companies/{company_id}/timeline")
async def api_company_timeline(company_id: int, request: Request, limit: int = 50, offset: int = 0):
    ctx = build_context(request)
    try:
        items = interactions_service.list_for_company(ctx, company_id, limit=limit, offset=offset)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


# ----- Notes -----

@router.post("/notes")
async def api_create_note(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        note = notes_service.create(
            ctx,
            contact_id=payload.get("contact_id"),
            company_id=payload.get("company_id"),
            body=payload.get("body", ""),
            visibility=payload.get("visibility", "team"),
        )
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "note": note, "request_id": ctx.request_id})


@router.get("/contacts/{contact_id}/notes")
async def api_contact_notes(contact_id: int, request: Request):
    ctx = build_context(request)
    try:
        items = notes_service.list_for_contact(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.post("/notes/{note_id}/reveal")
async def api_reveal_note(note_id: int, request: Request):
    ctx = build_context(request)
    try:
        note = notes_service.reveal_private(ctx, note_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "note": note, "request_id": ctx.request_id}


# ----- Tags -----

@router.post("/tags")
async def api_create_tag(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        tag = tags_service.create(ctx, payload.get("name", ""),
                                  color=payload.get("color"),
                                  scope=payload.get("scope", "any"))
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "tag": tag, "request_id": ctx.request_id})


@router.get("/tags")
async def api_list_tags(request: Request):
    ctx = build_context(request)
    try:
        items = tags_service.list_all(ctx)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.post("/contacts/{contact_id}/tags/{tag_id}")
async def api_attach_contact_tag(contact_id: int, tag_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = tags_service.attach(ctx, tag_id=tag_id, contact_id=contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.delete("/contacts/{contact_id}/tags/{tag_id}")
async def api_detach_contact_tag(contact_id: int, tag_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = tags_service.detach(ctx, tag_id=tag_id, contact_id=contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Consent -----

@router.post("/consent")
async def api_record_consent(request: Request):
    ctx = build_context(request)
    p = await request.json()
    try:
        result = consent_service.record(
            ctx,
            int(p["contact_id"]),
            p["channel"],
            p["status"],
            source=p.get("source"),
            proof=p.get("proof"),
        )
    except (KeyError, ValueError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": str(e),
            "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "consent": result, "request_id": ctx.request_id}


@router.get("/contacts/{contact_id}/consent")
async def api_list_consent(contact_id: int, request: Request):
    ctx = build_context(request)
    try:
        items = consent_service.list_for_contact(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


# ----- Pipelines + stages (v1) -----

@router.post("/pipelines")
async def api_create_pipeline(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    stages = payload.pop("stages", None)
    try:
        p = pipelines_service.create_pipeline(ctx, payload, stages=stages)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "pipeline": p, "request_id": ctx.request_id})


@router.post("/pipelines/from-template")
async def api_pipeline_from_template(request: Request):
    ctx = build_context(request)
    p = await request.json()
    try:
        pl = pipelines_service.create_from_template(ctx, p["name"], p["template"])
    except (KeyError, ValueError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": str(e), "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "pipeline": pl, "request_id": ctx.request_id})


@router.get("/pipelines")
async def api_list_pipelines(request: Request, include_archived: bool = False):
    ctx = build_context(request)
    try:
        items = pipelines_service.list_pipelines(ctx, include_archived=include_archived)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.get("/pipelines/{pipeline_id}")
async def api_get_pipeline(pipeline_id: int, request: Request):
    ctx = build_context(request)
    try:
        p = pipelines_service.get_pipeline(ctx, pipeline_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "pipeline": p, "request_id": ctx.request_id}


@router.post("/pipelines/{pipeline_id}/stages")
async def api_add_stage(pipeline_id: int, request: Request):
    ctx = build_context(request)
    p = await request.json()
    try:
        s = pipelines_service.add_stage(
            ctx, pipeline_id, p["name"],
            position=p.get("position"),
            is_won=bool(p.get("is_won")), is_lost=bool(p.get("is_lost")),
        )
    except (KeyError,) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": f"missing field: {e}", "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "stage": s, "request_id": ctx.request_id})


@router.post("/pipelines/{pipeline_id}/archive")
async def api_archive_pipeline(pipeline_id: int, request: Request):
    ctx = build_context(request)
    try:
        out = pipelines_service.archive_pipeline(ctx, pipeline_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


# ----- Deals (v1) -----

@router.post("/deals")
async def api_create_deal(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        d = deals_service.create(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "deal": d, "request_id": ctx.request_id})


@router.get("/deals")
async def api_list_deals(
    request: Request,
    pipeline_id: int = None, stage_id: int = None,
    status: str = None, assigned_to: int = None,
    contact_id: int = None, company_id: int = None,
    limit: int = 100, offset: int = 0,
):
    ctx = build_context(request)
    try:
        result = deals_service.list_(
            ctx, pipeline_id=pipeline_id, stage_id=stage_id,
            status=status, assigned_to=assigned_to,
            contact_id=contact_id, company_id=company_id,
            limit=limit, offset=offset,
        )
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/deals/{deal_id}")
async def api_get_deal(deal_id: int, request: Request):
    ctx = build_context(request)
    try:
        d = deals_service.get(ctx, deal_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "deal": d, "request_id": ctx.request_id}


@router.put("/deals/{deal_id}")
async def api_update_deal(deal_id: int, request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        d = deals_service.update(ctx, deal_id, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "deal": d, "request_id": ctx.request_id}


@router.delete("/deals/{deal_id}")
async def api_delete_deal(deal_id: int, request: Request):
    ctx = build_context(request)
    try:
        out = deals_service.delete(ctx, deal_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


# ----- Tasks (v1) -----

@router.post("/tasks")
async def api_create_task(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        t = tasks_service.create(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "task": t, "request_id": ctx.request_id})


@router.get("/tasks")
async def api_list_tasks(
    request: Request,
    status: str = None, assigned_to: int = None,
    contact_id: int = None, company_id: int = None, deal_id: int = None,
    overdue: bool = False, due_before: int = None,
    limit: int = 100, offset: int = 0,
):
    ctx = build_context(request)
    try:
        result = tasks_service.list_(
            ctx, status=status, assigned_to=assigned_to,
            contact_id=contact_id, company_id=company_id, deal_id=deal_id,
            overdue=overdue, due_before=due_before,
            limit=limit, offset=offset,
        )
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/tasks/{task_id}")
async def api_get_task(task_id: int, request: Request):
    ctx = build_context(request)
    try:
        t = tasks_service.get(ctx, task_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "task": t, "request_id": ctx.request_id}


@router.put("/tasks/{task_id}")
async def api_update_task(task_id: int, request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        t = tasks_service.update(ctx, task_id, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "task": t, "request_id": ctx.request_id}


@router.post("/tasks/{task_id}/complete")
async def api_complete_task(task_id: int, request: Request):
    ctx = build_context(request)
    try:
        t = tasks_service.complete(ctx, task_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "task": t, "request_id": ctx.request_id}


@router.delete("/tasks/{task_id}")
async def api_delete_task(task_id: int, request: Request):
    ctx = build_context(request)
    try:
        out = tasks_service.delete(ctx, task_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


# ----- Forms (v1) — admin endpoints. Public submission lives in main.py at /f/{slug} -----

@router.post("/forms")
async def api_create_form(request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        form = forms_service.create(ctx, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "form": form, "request_id": ctx.request_id})


@router.get("/forms")
async def api_list_forms(request: Request, include_inactive: bool = False):
    ctx = build_context(request)
    try:
        items = forms_service.list_(ctx, include_inactive=include_inactive)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.get("/forms/{form_id}")
async def api_get_form(form_id: int, request: Request):
    ctx = build_context(request)
    try:
        form = forms_service.get(ctx, form_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "form": form, "request_id": ctx.request_id}


@router.put("/forms/{form_id}")
async def api_update_form(form_id: int, request: Request):
    ctx = build_context(request)
    payload = await request.json()
    try:
        form = forms_service.update(ctx, form_id, payload)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "form": form, "request_id": ctx.request_id}


@router.get("/forms/{form_id}/submissions")
async def api_form_submissions(form_id: int, request: Request,
                                limit: int = 100, offset: int = 0):
    ctx = build_context(request)
    try:
        result = forms_service.list_submissions(ctx, form_id, limit=limit, offset=offset)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Global search (v1, FTS5-backed) -----

@router.get("/search")
async def api_search(request: Request, q: str = "", kinds: str = "", limit: int = 50):
    ctx = build_context(request)
    kinds_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    try:
        result = search_service.search(ctx, q, kinds=kinds_list, limit=limit)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Duplicates (v1) -----

@router.get("/duplicates")
async def api_duplicates_scan(request: Request, strategies: str = "",
                               max_groups: int = 200):
    ctx = build_context(request)
    s_list = [s.strip() for s in strategies.split(",") if s.strip()] if strategies else None
    try:
        result = duplicates_service.find(ctx, strategies=s_list, max_groups=max_groups)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Export (streaming CSV) -----

@router.get("/export/{kind}.csv")
def api_export_csv(kind: str, request: Request, include_deleted: bool = False):
    from fastapi.responses import StreamingResponse
    ctx = build_context(request)
    try:
        stream = imports_service.export_csv(ctx, kind, include_deleted=include_deleted)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return StreamingResponse(
        stream,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )


# ----- Scoring (v2) -----

@router.post("/contacts/{contact_id}/score")
async def api_score_contact(contact_id: int, request: Request):
    """Recompute all five scores for a contact and persist them."""
    ctx = build_context(request)
    try:
        result = scoring_service.compute_for_contact(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/contacts/{contact_id}/scores")
async def api_get_scores(contact_id: int, request: Request):
    """Return persisted scores + evidence for a contact."""
    ctx = build_context(request)
    try:
        result = scoring_service.get_scores(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.post("/scoring/recompute-all")
async def api_recompute_all(request: Request, limit: int = None):
    """Admin-only: batch-recompute scores for every active contact."""
    ctx = build_context(request)
    try:
        result = scoring_service.compute_for_all(ctx, limit=limit)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.get("/scoring/top")
async def api_top_scores(request: Request, type: str = "opportunity",
                         min: int = None, limit: int = 20):
    """List contacts ranked by a score type. Useful for `dormant high value`,
    `top intent right now`, etc."""
    ctx = build_context(request)
    try:
        items = scoring_service.list_top(ctx, type, limit=limit, min_score=min)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "score_type": type, "request_id": ctx.request_id}


# ----- Segments (v2) -----

@router.post("/segments")
async def api_create_segment(request: Request):
    """Body: {type: 'static'|'dynamic', name, slug, ...}
       - static  → also pass `contact_ids: [...]`
       - dynamic → also pass `rules: {...}`
    """
    ctx = build_context(request)
    p = await request.json()
    try:
        seg_type = p.get("type")
        if seg_type == "static":
            seg = segments_service.create_static(
                ctx, name=p["name"], slug=p["slug"],
                contact_ids=[int(x) for x in p.get("contact_ids", [])],
            )
        elif seg_type == "dynamic":
            seg = segments_service.create_dynamic(
                ctx, name=p["name"], slug=p["slug"], rules=p.get("rules") or {},
            )
        else:
            raise ServiceError("VALIDATION_ERROR",
                               "type must be 'static' or 'dynamic'")
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": str(e),
            "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "segment": seg,
                                                  "request_id": ctx.request_id})


@router.get("/segments")
async def api_list_segments(request: Request):
    ctx = build_context(request)
    try:
        items = segments_service.list_(ctx)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.get("/segments/{segment_id}")
async def api_get_segment(segment_id: int, request: Request):
    ctx = build_context(request)
    try:
        seg = segments_service.get(ctx, segment_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "segment": seg, "request_id": ctx.request_id}


@router.get("/segments/{segment_id}/members")
async def api_segment_members(segment_id: int, request: Request,
                              limit: int = 200, offset: int = 0):
    ctx = build_context(request)
    try:
        result = segments_service.list_members(ctx, segment_id, limit=limit, offset=offset)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.post("/segments/{segment_id}/evaluate")
async def api_segment_evaluate(segment_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = segments_service.evaluate(ctx, segment_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.delete("/segments/{segment_id}")
async def api_delete_segment(segment_id: int, request: Request):
    ctx = build_context(request)
    try:
        result = segments_service.delete(ctx, segment_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


# ----- Reports (v2) -----

@router.get("/reports")
async def api_list_reports(request: Request):
    """List available reports (their name + one-line description)."""
    ctx = build_context(request)
    return {"ok": True, "items": reports_service.list_reports(), "request_id": ctx.request_id}


@router.get("/reports/{name}")
async def api_run_report(name: str, request: Request):
    """Run a report by name. Any query-string params are forwarded as kwargs.
    Integer-looking values are coerced to int."""
    ctx = build_context(request)
    # Map query params, coercing ints when sensible
    raw = dict(request.query_params)
    kw = {}
    for k, v in raw.items():
        try:
            kw[k] = int(v)
        except (TypeError, ValueError):
            kw[k] = v
    try:
        out = reports_service.run(ctx, name, **kw)
    except TypeError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": f"bad parameter: {e}",
            "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


# ----- Portals (v3) -----

@router.post("/contacts/{contact_id}/portal-tokens")
async def api_issue_portal_token(contact_id: int, request: Request):
    """Issue a portal token for a contact. Body: {scope?, label?, expires_in_days?}"""
    ctx = build_context(request)
    p = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            p = await request.json()
        except Exception:
            p = {}
    try:
        token = portals_service.issue(
            ctx, contact_id,
            scope=p.get("scope", "client"),
            label=p.get("label"),
            expires_in_days=p.get("expires_in_days"),
        )
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "portal_token": token, "request_id": ctx.request_id})


@router.get("/contacts/{contact_id}/portal-tokens")
async def api_list_portal_tokens(contact_id: int, request: Request):
    ctx = build_context(request)
    try:
        items = portals_service.list_for_contact(ctx, contact_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.post("/portal-tokens/{token_id}/revoke")
async def api_revoke_portal_token(token_id: int, request: Request):
    ctx = build_context(request)
    try:
        out = portals_service.revoke(ctx, token_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


# ----- Inbound endpoints (v3) -----

@router.post("/inbound-endpoints")
async def api_create_inbound_endpoint(request: Request):
    """Body: {slug, name, description?, routing?, generate_secret?: true}"""
    ctx = build_context(request)
    p = await request.json()
    try:
        ep = inbound_service.create_endpoint(
            ctx, slug=p["slug"], name=p["name"],
            description=p.get("description"),
            routing=p.get("routing") or {},
            generate_secret=bool(p.get("generate_secret", True)),
        )
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": str(e),
            "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return JSONResponse(status_code=201, content={"ok": True, "endpoint": ep,
                                                  "request_id": ctx.request_id})


@router.get("/inbound-endpoints")
async def api_list_inbound_endpoints(request: Request):
    ctx = build_context(request)
    try:
        items = inbound_service.list_endpoints(ctx)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "items": items, "request_id": ctx.request_id}


@router.get("/inbound-endpoints/{endpoint_id}")
async def api_get_inbound_endpoint(endpoint_id: int, request: Request):
    ctx = build_context(request)
    try:
        ep = inbound_service.get_endpoint(ctx, endpoint_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, "endpoint": ep, "request_id": ctx.request_id}


@router.get("/inbound-endpoints/{endpoint_id}/events")
async def api_list_inbound_events(endpoint_id: int, request: Request,
                                   limit: int = 100, offset: int = 0):
    ctx = build_context(request)
    try:
        result = inbound_service.list_events(ctx, endpoint_id, limit=limit, offset=offset)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}


@router.delete("/inbound-endpoints/{endpoint_id}")
async def api_delete_inbound_endpoint(endpoint_id: int, request: Request):
    ctx = build_context(request)
    try:
        out = inbound_service.delete_endpoint(ctx, endpoint_id)
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **out, "request_id": ctx.request_id}


@router.get("/reports/{name}.csv")
def api_run_report_csv(name: str, request: Request):
    """Same report, streamed as CSV."""
    from fastapi.responses import StreamingResponse
    import csv as _csv
    import io as _io
    ctx = build_context(request)
    raw = dict(request.query_params)
    kw = {}
    for k, v in raw.items():
        try:
            kw[k] = int(v)
        except (TypeError, ValueError):
            kw[k] = v
    try:
        out = reports_service.run(ctx, name, **kw)
    except ServiceError as e:
        return _error(e, ctx.request_id)

    def stream():
        buf = _io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=out["columns"], extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for row in out["rows"]:
            writer.writerow(row)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    return StreamingResponse(
        stream(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@router.post("/duplicates/merge")
async def api_duplicates_merge(request: Request):
    ctx = build_context(request)
    p = await request.json()
    try:
        result = duplicates_service.merge(
            ctx, keep_id=int(p["keep_id"]),
            merge_ids=[int(x) for x in p["merge_ids"]],
        )
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": {
            "code": "VALIDATION_ERROR", "message": str(e),
            "details": {}, "request_id": ctx.request_id,
        }})
    except ServiceError as e:
        return _error(e, ctx.request_id)
    return {"ok": True, **result, "request_id": ctx.request_id}
