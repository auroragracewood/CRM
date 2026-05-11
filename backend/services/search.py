"""Global search service. Queries the FTS5 `search_index` virtual table that
covers contacts, companies, interactions, and (non-private) notes.

The FTS5 index is populated by triggers in migration 0003 and stays in sync
automatically. Private notes are NEVER in the index (the note insert trigger
guards on `visibility != 'private'`), so search results can be safely shown
to anyone with read scope.

The query is tokenized and each token is wrapped in quotes for FTS5 MATCH,
which neutralizes special characters like `*`, `(`, `)`, `^`, and reserved
words like `AND`, `OR`, `NOT`. The user's intent is always "match these terms
anywhere", not "use FTS5 advanced syntax."
"""
import re
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .contacts import ServiceError, _row_to_dict


_VALID_KINDS = {"contact", "company", "interaction", "note"}
_TOKEN_RE = re.compile(r"[\w\-@.]+", re.UNICODE)


def _build_match(q: str) -> str:
    """Quote-escape each token so FTS5 MATCH treats them literally."""
    tokens = _TOKEN_RE.findall(q or "")
    if not tokens:
        return ""
    # Wrap each token in double quotes (FTS5 phrase delimiter). Backslash-escape
    # any embedded double quote.
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search(
    ctx: ServiceContext,
    q: str,
    *,
    kinds: Optional[list[str]] = None,
    limit: int = 50,
) -> dict:
    """Cross-entity FTS5 search. Returns one bucket per kind, plus a flat list."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    limit = max(1, min(int(limit), 200))
    match_q = _build_match(q)
    if not match_q:
        return {"query": q, "items": [], "buckets": {k: [] for k in _VALID_KINDS}, "total": 0}

    where_kind = ""
    params: list = [match_q]
    if kinds:
        ks = [k for k in kinds if k in _VALID_KINDS]
        if ks:
            where_kind = " AND kind IN (" + ",".join("?" * len(ks)) + ")"
            params += ks
    params.append(limit)

    with db() as conn:
        rows = conn.execute(
            f"""SELECT kind, ref,
                       snippet(search_index, 2, '<mark>', '</mark>', '…', 10) AS title_snip,
                       snippet(search_index, 3, '<mark>', '</mark>', '…', 16) AS body_snip,
                       rank
                  FROM search_index
                 WHERE search_index MATCH ?
                   {where_kind}
                 ORDER BY rank
                 LIMIT ?""",
            params,
        ).fetchall()

        items = []
        buckets: dict[str, list[dict]] = {k: [] for k in _VALID_KINDS}
        for r in rows:
            kind = r["kind"]; ref = r["ref"]
            base = {"kind": kind, "ref": ref,
                    "title": r["title_snip"], "body": r["body_snip"]}
            # Hydrate each hit with a stable display label + a navigable URL.
            if kind == "contact":
                c = conn.execute(
                    "SELECT id, full_name, email FROM contacts WHERE id=? AND deleted_at IS NULL",
                    (ref,),
                ).fetchone()
                if not c: continue
                base["label"] = c["full_name"] or c["email"] or f"#{c['id']}"
                base["url"] = f"/contacts/{c['id']}"
            elif kind == "company":
                c = conn.execute(
                    "SELECT id, name FROM companies WHERE id=? AND deleted_at IS NULL", (ref,),
                ).fetchone()
                if not c: continue
                base["label"] = c["name"]
                base["url"] = f"/companies/{c['id']}"
            elif kind == "interaction":
                i = conn.execute(
                    "SELECT id, contact_id, company_id, type, occurred_at FROM interactions WHERE id=?",
                    (ref,),
                ).fetchone()
                if not i: continue
                base["label"] = f"{i['type']} interaction"
                base["url"] = (f"/contacts/{i['contact_id']}" if i["contact_id"]
                               else f"/companies/{i['company_id']}")
                base["occurred_at"] = i["occurred_at"]
            elif kind == "note":
                n = conn.execute(
                    "SELECT id, contact_id, company_id, visibility FROM notes WHERE id=?",
                    (ref,),
                ).fetchone()
                if not n or n["visibility"] == "private": continue
                base["label"] = f"note ({n['visibility']})"
                base["url"] = (f"/contacts/{n['contact_id']}" if n["contact_id"]
                               else f"/companies/{n['company_id']}")
            else:
                continue
            buckets[kind].append(base)
            items.append(base)

    return {"query": q, "items": items, "buckets": buckets, "total": len(items)}
