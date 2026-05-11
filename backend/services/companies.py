"""Companies service. Mirrors contacts pattern."""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit, webhooks
from .contacts import ServiceError, _row_to_dict


_FIELDS = (
    "name", "slug", "website", "domain", "industry", "size", "location",
    "description", "custom_fields_json",
)


def _normalize_domain(domain: Optional[str]) -> Optional[str]:
    if domain is None:
        return None
    d = domain.strip().lower()
    return d or None


def _validate_create(payload: dict) -> dict:
    cleaned = {k: payload.get(k) for k in _FIELDS}
    cleaned["domain"] = _normalize_domain(cleaned.get("domain"))
    if not cleaned.get("name"):
        raise ServiceError("VALIDATION_ERROR", "Company requires a name")
    return cleaned


def create(ctx: ServiceContext, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = _validate_create(payload)
    now = int(time.time())
    with db() as conn:
        if cleaned.get("slug"):
            existing = conn.execute(
                "SELECT id FROM companies WHERE slug = ? AND deleted_at IS NULL",
                (cleaned["slug"],),
            ).fetchone()
            if existing:
                raise ServiceError(
                    "COMPANY_SLUG_EXISTS",
                    f"Company with slug {cleaned['slug']!r} already exists",
                    {"company_id": existing[0]},
                )
        cols = list(cleaned.keys()) + ["created_at", "updated_at"]
        vals = list(cleaned.values()) + [now, now]
        ph = ",".join("?" * len(cols))
        conn.execute(f"INSERT INTO companies ({','.join(cols)}) VALUES ({ph})", vals)
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
        company = _row_to_dict(row)
        audit.log(conn, ctx, action="company.created", object_type="company",
                  object_id=cid, after=company)
        webhooks.enqueue(conn, "company.created", {"company": company})
    return company


def get(ctx: ServiceContext, company_id: int, *, include_deleted: bool = False) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        sql = "SELECT * FROM companies WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = conn.execute(sql, (company_id,)).fetchone()
    if not row:
        raise ServiceError("COMPANY_NOT_FOUND", f"company {company_id} not found")
    return _row_to_dict(row)


def list_(
    ctx: ServiceContext, *,
    limit: int = 50, offset: int = 0, q: Optional[str] = None,
    include_deleted: bool = False,
) -> dict:
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where, params = [], []
    if not include_deleted:
        where.append("deleted_at IS NULL")
    if q:
        where.append("(LOWER(name) LIKE ? OR LOWER(domain) LIKE ?)")
        like = f"%{q.lower()}%"
        params += [like, like]
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM companies{where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM companies{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


def update(ctx: ServiceContext, company_id: int, payload: dict) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    cleaned = {k: payload[k] for k in payload if k in _FIELDS}
    if "domain" in cleaned:
        cleaned["domain"] = _normalize_domain(cleaned["domain"])
    if not cleaned:
        raise ServiceError("VALIDATION_ERROR", "no updatable fields in payload")
    now = int(time.time())
    with db() as conn:
        before_row = conn.execute(
            "SELECT * FROM companies WHERE id = ? AND deleted_at IS NULL", (company_id,),
        ).fetchone()
        if not before_row:
            raise ServiceError("COMPANY_NOT_FOUND", f"company {company_id} not found")
        before = _row_to_dict(before_row)
        set_sql = ", ".join(f"{k} = ?" for k in cleaned) + ", updated_at = ?"
        params = list(cleaned.values()) + [now, company_id]
        conn.execute(f"UPDATE companies SET {set_sql} WHERE id = ?", params)
        after_row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        after = _row_to_dict(after_row)
        audit.log(conn, ctx, action="company.updated", object_type="company",
                  object_id=company_id, before=before, after=after)
        webhooks.enqueue(conn, "company.updated", {"company": after, "before": before})
    return after


def delete(ctx: ServiceContext, company_id: int) -> dict:
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM companies WHERE id = ? AND deleted_at IS NULL", (company_id,),
        ).fetchone()
        if not row:
            raise ServiceError("COMPANY_NOT_FOUND", f"company {company_id} not found")
        before = _row_to_dict(row)
        conn.execute(
            "UPDATE companies SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, company_id),
        )
        audit.log(conn, ctx, action="company.deleted", object_type="company",
                  object_id=company_id, before=before)
        webhooks.enqueue(conn, "company.deleted", {"company_id": company_id})
    return {"id": company_id, "deleted_at": now}
