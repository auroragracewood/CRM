"""Bulk import + export.

Imports run through the regular service layer, so every imported row gets the
same validation, audit log, and webhook firing as a UI / REST / CLI create.

Column mapping is flexible: callers can pass an explicit `mapping` dict that
maps CSV header names to canonical field names, or rely on heuristic detection
(case-insensitive partial match against known field aliases).

Exports stream CSV rows so a multi-thousand-row export doesn't load the whole
table into memory.
"""
import csv
import io
from typing import Iterable, Iterator, Optional, TextIO

from ..context import ServiceContext
from ..db import db
from .contacts import ServiceError
from . import contacts as contacts_service
from . import companies as companies_service


# ---------- imports ----------

_CONTACT_ALIASES = {
    "full_name": ["full_name", "fullname", "name", "full name"],
    "first_name": ["first_name", "first name", "firstname", "given_name"],
    "last_name":  ["last_name", "last name", "lastname", "surname"],
    "email":      ["email", "email address", "e-mail"],
    "phone":      ["phone", "phone_number", "telephone", "mobile", "cell"],
    "title":      ["title", "job_title", "position", "role"],
    "location":   ["location", "city", "address", "town"],
    "avatar_url": ["avatar_url", "avatar", "picture", "photo"],
    "timezone":   ["timezone", "time_zone", "tz"],
    "preferred_channel": ["preferred_channel", "channel"],
}
_COMPANY_ALIASES = {
    "name":     ["name", "company", "company_name", "organization"],
    "slug":     ["slug"],
    "website":  ["website", "url", "site"],
    "domain":   ["domain"],
    "industry": ["industry", "sector"],
    "size":     ["size", "company_size", "headcount"],
    "location": ["location", "city", "address"],
    "description": ["description", "about", "notes"],
}


def _detect_mapping(headers: list[str], aliases: dict[str, list[str]]) -> dict[str, str]:
    """Map source CSV header → canonical field. Case-insensitive."""
    lower = [h.strip().lower() for h in headers]
    mapping: dict[str, str] = {}
    for canonical, variants in aliases.items():
        for v in variants:
            if v.lower() in lower:
                src = headers[lower.index(v.lower())]
                mapping[src] = canonical
                break
    return mapping


def _stream_csv(text_io: TextIO) -> Iterator[dict]:
    reader = csv.DictReader(text_io)
    if not reader.fieldnames:
        return iter([])
    return reader


def import_contacts(
    ctx: ServiceContext,
    csv_text: str,
    *,
    mapping: Optional[dict[str, str]] = None,
    dry_run: bool = False,
) -> dict:
    """Import contacts from CSV text.

    `mapping`: optional source-header → canonical-field map. If omitted, heuristic.

    Returns {"created": N, "matched": N (by-email re-use), "errors": [...]}
    """
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    text_io = io.StringIO(csv_text)
    reader = csv.DictReader(text_io)
    headers = reader.fieldnames or []
    if not mapping:
        mapping = _detect_mapping(headers, _CONTACT_ALIASES)

    created = matched = 0
    errors: list[dict] = []
    for idx, raw in enumerate(reader, start=2):  # row 1 is the header
        payload = {}
        for src, canonical in mapping.items():
            v = (raw.get(src) or "").strip()
            if v:
                payload[canonical] = v
        if not payload:
            errors.append({"row": idx, "error": "no mappable fields"})
            continue
        # match-by-email re-use
        email = payload.get("email")
        if email:
            existing = contacts_service.find_by_email(ctx, email)
            if existing:
                matched += 1
                continue
        if dry_run:
            created += 1
            continue
        try:
            contacts_service.create(ctx, payload)
            created += 1
        except ServiceError as e:
            errors.append({"row": idx, "code": e.code, "error": e.message})
    return {
        "created": created, "matched": matched, "errors": errors,
        "mapping": mapping, "dry_run": dry_run,
    }


def import_companies(
    ctx: ServiceContext,
    csv_text: str,
    *,
    mapping: Optional[dict[str, str]] = None,
    dry_run: bool = False,
) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    text_io = io.StringIO(csv_text)
    reader = csv.DictReader(text_io)
    headers = reader.fieldnames or []
    if not mapping:
        mapping = _detect_mapping(headers, _COMPANY_ALIASES)

    created = 0
    errors: list[dict] = []
    for idx, raw in enumerate(reader, start=2):
        payload = {}
        for src, canonical in mapping.items():
            v = (raw.get(src) or "").strip()
            if v:
                payload[canonical] = v
        if not payload.get("name"):
            errors.append({"row": idx, "error": "name is required"})
            continue
        if dry_run:
            created += 1
            continue
        try:
            companies_service.create(ctx, payload)
            created += 1
        except ServiceError as e:
            errors.append({"row": idx, "code": e.code, "error": e.message})
    return {"created": created, "errors": errors, "mapping": mapping, "dry_run": dry_run}


# ---------- exports ----------

_CONTACT_EXPORT_COLS = [
    "id", "full_name", "first_name", "last_name", "email", "phone",
    "title", "location", "timezone", "preferred_channel",
    "company_id", "created_at", "updated_at", "deleted_at",
]
_COMPANY_EXPORT_COLS = [
    "id", "name", "slug", "website", "domain", "industry", "size",
    "location", "description", "created_at", "updated_at", "deleted_at",
]
_DEAL_EXPORT_COLS = [
    "id", "contact_id", "company_id", "pipeline_id", "stage_id", "title",
    "value_cents", "currency", "probability", "expected_close",
    "status", "next_step", "assigned_to", "created_at", "updated_at", "closed_at",
]
_TASK_EXPORT_COLS = [
    "id", "title", "description", "contact_id", "company_id", "deal_id",
    "assigned_to", "due_date", "priority", "status",
    "created_by", "created_at", "completed_at",
]
_INTERACTION_EXPORT_COLS = [
    "id", "contact_id", "company_id", "type", "channel", "title",
    "body", "source", "occurred_at", "created_at",
]


def _stream_rows(table: str, cols: list[str], where: str = "") -> Iterator[dict]:
    sql = f"SELECT {','.join(cols)} FROM {table}"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id"
    with db() as c:
        for row in c.execute(sql):
            yield {k: row[k] for k in cols}


def export_csv(ctx: ServiceContext, kind: str,
               *, include_deleted: bool = False) -> Iterator[str]:
    """Yield CSV rows (one string per line, newline-included) for streaming."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    if kind == "contacts":
        cols = _CONTACT_EXPORT_COLS
        where = "" if include_deleted else "deleted_at IS NULL"
        table = "contacts"
    elif kind == "companies":
        cols = _COMPANY_EXPORT_COLS
        where = "" if include_deleted else "deleted_at IS NULL"
        table = "companies"
    elif kind == "deals":
        cols = _DEAL_EXPORT_COLS; where = ""; table = "deals"
    elif kind == "tasks":
        cols = _TASK_EXPORT_COLS; where = ""; table = "tasks"
    elif kind == "interactions":
        cols = _INTERACTION_EXPORT_COLS; where = ""; table = "interactions"
    else:
        raise ServiceError("VALIDATION_ERROR",
                           f"unknown export kind {kind!r}; "
                           "expected one of contacts, companies, deals, tasks, interactions")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue()
    buf.seek(0); buf.truncate(0)

    for row in _stream_rows(table, cols, where):
        writer.writerow(row)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
