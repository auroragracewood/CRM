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
