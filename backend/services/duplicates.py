"""Duplicate detection service.

Finds groups of potential duplicate contacts via four strategies:

  1. Email collision   — including soft-deleted contacts (active-active is
                          already enforced by the partial unique index)
  2. Phone match       — normalized to digits-only (length >= 7 to ignore noise)
  3. Same full_name    — exact match, case-insensitive, both active
  4. Name + company    — same first OR last name + same company_id

Returns a list of `groups`. Each group is one suspected-duplicate cluster.
Resolution (merge) is a separate operation; this service only *finds*.

Costly query for large databases; intended for batch use (admin clicks
"find duplicates" or a cron job runs nightly). Limits caller via `max_groups`.
"""
import re
from typing import Iterable, Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError, _row_to_dict


_DIGIT_RE = re.compile(r"\D+")


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    d = _DIGIT_RE.sub("", phone)
    return d if len(d) >= 7 else None


def _attach_contact_rows(conn, ids: Iterable[int]) -> list[dict]:
    ids = list(ids)
    if not ids:
        return []
    qmarks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, full_name, email, phone, company_id, deleted_at FROM contacts WHERE id IN ({qmarks})",
        ids,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find(ctx: ServiceContext, *,
         strategies: Optional[list[str]] = None,
         max_groups: int = 200) -> dict:
    """Find potential duplicate contact groups. Returns
    {"groups": [{"strategy": ..., "key": ..., "contacts": [...]}, ...],
     "total_groups": int, "limit": int}"""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    strategies = strategies or ["email", "phone", "name", "name_company"]
    groups: list[dict] = []

    with db() as conn:
        # 1) email collisions across active+deleted
        if "email" in strategies:
            rows = conn.execute(
                "SELECT email, GROUP_CONCAT(id) FROM contacts "
                "WHERE email IS NOT NULL AND email != '' "
                "GROUP BY email HAVING COUNT(*) > 1 LIMIT ?",
                (max_groups,),
            ).fetchall()
            for email, ids_csv in rows:
                ids = [int(x) for x in ids_csv.split(",")]
                groups.append({
                    "strategy": "email",
                    "key": email,
                    "contacts": _attach_contact_rows(conn, ids),
                })

        # 2) phone matches (post-normalization). Done in Python because SQLite's
        #    REPLACE doesn't strip all non-digit chars in one expression.
        if "phone" in strategies and len(groups) < max_groups:
            phone_rows = conn.execute(
                "SELECT id, phone FROM contacts WHERE phone IS NOT NULL AND phone != ''",
            ).fetchall()
            buckets: dict[str, list[int]] = {}
            for r in phone_rows:
                norm = _normalize_phone(r["phone"])
                if norm:
                    buckets.setdefault(norm, []).append(r["id"])
            for norm, ids in buckets.items():
                if len(ids) < 2:
                    continue
                groups.append({
                    "strategy": "phone",
                    "key": norm,
                    "contacts": _attach_contact_rows(conn, ids),
                })
                if len(groups) >= max_groups:
                    break

        # 3) exact same full_name (case-insensitive), active only
        if "name" in strategies and len(groups) < max_groups:
            rows = conn.execute(
                "SELECT LOWER(full_name), GROUP_CONCAT(id) FROM contacts "
                "WHERE full_name IS NOT NULL AND TRIM(full_name) != '' AND deleted_at IS NULL "
                "GROUP BY LOWER(full_name) HAVING COUNT(*) > 1 LIMIT ?",
                (max_groups - len(groups),),
            ).fetchall()
            for name, ids_csv in rows:
                ids = [int(x) for x in ids_csv.split(",")]
                groups.append({
                    "strategy": "name",
                    "key": name,
                    "contacts": _attach_contact_rows(conn, ids),
                })

        # 4) same first/last name + same company
        if "name_company" in strategies and len(groups) < max_groups:
            rows = conn.execute(
                """SELECT
                       LOWER(COALESCE(first_name,'')) || '|' ||
                       LOWER(COALESCE(last_name,''))  || '|' ||
                       COALESCE(company_id,0) AS k,
                       GROUP_CONCAT(id)
                   FROM contacts
                  WHERE company_id IS NOT NULL
                    AND (TRIM(COALESCE(first_name,'')) != '' OR TRIM(COALESCE(last_name,'')) != '')
                    AND deleted_at IS NULL
                  GROUP BY k HAVING COUNT(*) > 1 LIMIT ?""",
                (max_groups - len(groups),),
            ).fetchall()
            for key, ids_csv in rows:
                ids = [int(x) for x in ids_csv.split(",")]
                groups.append({
                    "strategy": "name_company",
                    "key": key,
                    "contacts": _attach_contact_rows(conn, ids),
                })

        # log a single audit row for the scan itself
        audit.log(conn, ctx, action="duplicates.scanned",
                  object_type="duplicates", object_id=None,
                  after={"strategies": strategies, "found_groups": len(groups)})

    return {"groups": groups, "total_groups": len(groups), "limit": max_groups}


def merge(ctx: ServiceContext, *, keep_id: int, merge_ids: list[int]) -> dict:
    """Merge `merge_ids` INTO `keep_id`. Re-parents interactions, notes, tags,
    consent, deals, tasks to the kept contact, then soft-deletes the merged ones.

    The contacts being merged-from become unrecoverable as separate identities,
    but their data lives on under the kept contact and the audit log preserves
    the merge event. Soft-delete frees their emails for future use.
    """
    if not ctx.can_write():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow writes")
    if keep_id in merge_ids:
        raise ServiceError("VALIDATION_ERROR", "keep_id cannot also be in merge_ids")
    if not merge_ids:
        raise ServiceError("VALIDATION_ERROR", "merge_ids cannot be empty")
    import time
    now = int(time.time())
    with db() as conn:
        keep_row = conn.execute(
            "SELECT id FROM contacts WHERE id=? AND deleted_at IS NULL", (keep_id,),
        ).fetchone()
        if not keep_row:
            raise ServiceError("CONTACT_NOT_FOUND",
                               f"keep contact {keep_id} not found or already deleted")
        merge_rows = conn.execute(
            f"SELECT id, email FROM contacts WHERE id IN ({','.join('?' * len(merge_ids))})",
            merge_ids,
        ).fetchall()
        if len(merge_rows) != len(merge_ids):
            raise ServiceError("CONTACT_NOT_FOUND", "one or more merge_ids not found")

        before_summary = {
            "keep_id": keep_id,
            "merge_ids": merge_ids,
            "emails": [r["email"] for r in merge_rows],
        }

        for mid in merge_ids:
            conn.execute("UPDATE interactions SET contact_id=? WHERE contact_id=?", (keep_id, mid))
            conn.execute("UPDATE notes        SET contact_id=? WHERE contact_id=?", (keep_id, mid))
            conn.execute("UPDATE form_submissions SET contact_id=? WHERE contact_id=?", (keep_id, mid))
            conn.execute(
                """UPDATE OR IGNORE consent SET contact_id=? WHERE contact_id=?""",
                (keep_id, mid),
            )
            # contact_tags: insert-or-ignore each tag onto keep_id, then drop the merged tags
            conn.execute(
                """INSERT OR IGNORE INTO contact_tags (contact_id, tag_id, added_at, added_by)
                   SELECT ?, tag_id, added_at, added_by FROM contact_tags WHERE contact_id=?""",
                (keep_id, mid),
            )
            conn.execute("DELETE FROM contact_tags WHERE contact_id=?", (mid,))
            conn.execute("UPDATE deals SET contact_id=? WHERE contact_id=?", (keep_id, mid))
            conn.execute("UPDATE tasks SET contact_id=? WHERE contact_id=?", (keep_id, mid))
            # finally, soft-delete the merged record
            conn.execute(
                "UPDATE contacts SET deleted_at=?, updated_at=? WHERE id=?",
                (now, now, mid),
            )

        # System interaction documenting the merge on the kept contact
        import json
        conn.execute(
            """INSERT INTO interactions
                 (contact_id, type, channel, title, body, metadata_json,
                  source, occurred_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (keep_id, "system", None,
             f"Merged {len(merge_ids)} duplicate contact(s)",
             None,
             json.dumps({"keep_id": keep_id, "merged_ids": merge_ids}),
             f"duplicates.merge:{ctx.surface}",
             now, now),
        )
        audit.log(conn, ctx, action="contact.merged",
                  object_type="contact", object_id=keep_id,
                  before=before_summary,
                  after={"keep_id": keep_id, "merged_count": len(merge_ids)})
        # webhook so external systems can react (notion sync, etc.)
        from .. import webhooks
        webhooks.enqueue(conn, "contact.merged",
                         {"keep_id": keep_id, "merged_ids": merge_ids})

    return {"keep_id": keep_id, "merged_ids": merge_ids,
            "merged_count": len(merge_ids)}
