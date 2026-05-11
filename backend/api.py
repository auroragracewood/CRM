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
