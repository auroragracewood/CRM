"""Rule-based scoring service. No LLM, no statistical model — every score has
explicit rules and every score row carries the evidence that produced it.

Five score types persisted to contact_scores:
  - relationship_strength : how warm is the relationship today (0-100)
  - intent                : how likely to engage / buy soon (0-100)
  - fit                   : ICP match (0-100, neutral 50 if no rules yet)
  - risk                  : danger signs to act on (0-100, HIGHER = more risk)
  - opportunity           : composite (0-100)

Each rule returns a delta and a reason string. The service sums deltas, clamps
to [0, 100], and writes the evidence list to contact_scores.evidence_json.
This satisfies the architectural promise: "every prediction shows evidence."
"""
import json
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .. import audit
from .contacts import ServiceError


SCORE_TYPES = ("relationship_strength", "intent", "fit", "risk", "opportunity")
DAY = 24 * 3600


def _q(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def _signals_for(conn, contact_id: int) -> dict:
    """Pull the raw signals every score derives from. One query batch, not five."""
    now = int(time.time())
    last_inter = _q(conn,
        "SELECT MAX(occurred_at) FROM interactions WHERE contact_id=?",
        (contact_id,))[0]
    days_since_last = (now - last_inter) // DAY if last_inter else None

    cnt_90 = _q(conn,
        "SELECT COUNT(*) FROM interactions WHERE contact_id=? AND occurred_at >= ?",
        (contact_id, now - 90*DAY))[0]
    cnt_7 = _q(conn,
        "SELECT COUNT(*) FROM interactions WHERE contact_id=? AND occurred_at >= ?",
        (contact_id, now - 7*DAY))[0]
    page_views_7 = _q(conn,
        "SELECT COUNT(*) FROM interactions WHERE contact_id=? AND type='page_view' "
        "AND occurred_at >= ?",
        (contact_id, now - 7*DAY))[0]
    form_subs_30 = _q(conn,
        "SELECT COUNT(*) FROM interactions WHERE contact_id=? AND type='form_submission' "
        "AND occurred_at >= ?",
        (contact_id, now - 30*DAY))[0]

    open_deals = _q(conn,
        "SELECT COUNT(*) FROM deals WHERE contact_id=? AND status='open'",
        (contact_id,))[0]
    won_deals = _q(conn,
        "SELECT COUNT(*) FROM deals WHERE contact_id=? AND status='won'",
        (contact_id,))[0]
    stalled_deals = _q(conn,
        "SELECT COUNT(*) FROM deals WHERE contact_id=? AND status='open' "
        "AND expected_close IS NOT NULL AND expected_close < ?",
        (contact_id, now))[0]
    overdue_tasks = _q(conn,
        "SELECT COUNT(*) FROM tasks WHERE contact_id=? "
        "AND status IN ('open','in_progress') "
        "AND due_date IS NOT NULL AND due_date < ?",
        (contact_id, now))[0]

    tag_rows = conn.execute(
        "SELECT t.name FROM tags t JOIN contact_tags ct ON ct.tag_id = t.id "
        "WHERE ct.contact_id = ?",
        (contact_id,),
    ).fetchall()
    tags = [r[0].lower() for r in tag_rows]

    consent_rows = conn.execute(
        "SELECT channel, status FROM consent WHERE contact_id = ?",
        (contact_id,),
    ).fetchall()
    consent = {r["channel"]: r["status"] for r in consent_rows}

    return {
        "now": now,
        "days_since_last_interaction": days_since_last,
        "interactions_last_90": cnt_90,
        "interactions_last_7": cnt_7,
        "page_views_last_7": page_views_7,
        "form_submissions_last_30": form_subs_30,
        "open_deals": open_deals,
        "won_deals": won_deals,
        "stalled_deals": stalled_deals,
        "overdue_tasks": overdue_tasks,
        "tags": tags,
        "consent": consent,
    }


def _clamp(n: int) -> int:
    return max(0, min(100, int(n)))


# ---------- per-score rule sets ----------

def _score_relationship_strength(sig: dict) -> tuple[int, list[dict]]:
    evidence = []
    total = 50  # neutral baseline

    d = sig["days_since_last_interaction"]
    if d is None:
        evidence.append({"reason": "no interactions on record", "delta": -25})
        total -= 25
    elif d <= 7:
        evidence.append({"reason": f"interaction {d} days ago", "delta": 25})
        total += 25
    elif d <= 30:
        evidence.append({"reason": f"interaction {d} days ago", "delta": 15})
        total += 15
    elif d <= 90:
        evidence.append({"reason": f"interaction {d} days ago", "delta": 5})
        total += 5
    elif d <= 180:
        evidence.append({"reason": f"silent for {d} days", "delta": -10})
        total -= 10
    else:
        evidence.append({"reason": f"dormant for {d} days", "delta": -20})
        total -= 20

    if sig["interactions_last_90"] >= 5:
        evidence.append({"reason": f"{sig['interactions_last_90']} interactions in last 90 days", "delta": 15})
        total += 15
    elif sig["interactions_last_90"] >= 2:
        evidence.append({"reason": f"{sig['interactions_last_90']} interactions in last 90 days", "delta": 8})
        total += 8

    if sig["won_deals"] > 0:
        delta = min(30, sig["won_deals"] * 10)
        evidence.append({"reason": f"{sig['won_deals']} won deal(s)", "delta": delta})
        total += delta

    if sig["consent"].get("email") == "granted":
        evidence.append({"reason": "email marketing consent granted", "delta": 10})
        total += 10

    relationship_tags = {"partner", "vip", "collaborator"}
    matched = [t for t in sig["tags"] if t in relationship_tags]
    if matched:
        evidence.append({"reason": f"tags: {matched}", "delta": 10})
        total += 10

    if "do-not-contact" in sig["tags"] or "blocked" in sig["tags"]:
        evidence.append({"reason": "do-not-contact tag", "delta": -50})
        total -= 50

    return _clamp(total), evidence


def _score_intent(sig: dict) -> tuple[int, list[dict]]:
    evidence = []
    total = 30  # neutral-low baseline (most contacts are not actively buying)

    if sig["form_submissions_last_30"] > 0:
        evidence.append({"reason": f"{sig['form_submissions_last_30']} form submission(s) in last 30 days", "delta": 30})
        total += 30

    if sig["page_views_last_7"] > 0:
        delta = min(30, sig["page_views_last_7"] * 10)
        evidence.append({"reason": f"{sig['page_views_last_7']} page view(s) in last 7 days", "delta": delta})
        total += delta

    if sig["open_deals"] > 0:
        evidence.append({"reason": f"{sig['open_deals']} open deal(s) on record", "delta": 20})
        total += 20

    d = sig["days_since_last_interaction"]
    if d is not None and d <= 7:
        evidence.append({"reason": f"interaction {d} days ago", "delta": 15})
        total += 15
    elif d is not None and d > 60:
        evidence.append({"reason": f"no interaction in {d} days", "delta": -15})
        total -= 15

    if "high-intent" in sig["tags"]:
        evidence.append({"reason": "high-intent tag", "delta": 15})
        total += 15

    return _clamp(total), evidence


def _score_fit(sig: dict) -> tuple[int, list[dict]]:
    """v2 default: neutral (50). ICP rules become a plug-in (v3) — once a custom
    fit_rules.py plug-in is registered, it can override this default."""
    evidence = [{"reason": "no ICP rules configured; default neutral", "delta": 0}]
    total = 50

    if "ideal-customer" in sig["tags"] or "icp-match" in sig["tags"]:
        evidence.append({"reason": "icp-match tag", "delta": 30})
        total += 30
    if "wrong-fit" in sig["tags"] or "out-of-icp" in sig["tags"]:
        evidence.append({"reason": "wrong-fit tag", "delta": -30})
        total -= 30
    return _clamp(total), evidence


def _score_risk(sig: dict) -> tuple[int, list[dict]]:
    evidence = []
    total = 10  # low baseline: not every contact is risky

    if sig["stalled_deals"] > 0:
        evidence.append({"reason": f"{sig['stalled_deals']} open deal(s) past expected close", "delta": 25})
        total += 25

    d = sig["days_since_last_interaction"]
    if d is not None and d > 60 and sig["interactions_last_90"] > 0:
        evidence.append({"reason": f"engagement dropped: silent {d} days", "delta": 20})
        total += 20

    if sig["consent"].get("email") == "withdrawn":
        evidence.append({"reason": "email consent withdrawn", "delta": 15})
        total += 15

    if sig["overdue_tasks"] > 0:
        delta = min(20, sig["overdue_tasks"] * 5)
        evidence.append({"reason": f"{sig['overdue_tasks']} overdue task(s) on this contact", "delta": delta})
        total += delta

    if "at-risk" in sig["tags"]:
        evidence.append({"reason": "at-risk tag", "delta": 20})
        total += 20

    return _clamp(total), evidence


def _score_opportunity(intent: int, relationship: int, fit: int, risk: int) -> tuple[int, list[dict]]:
    """Composite: weighted average that DECREASES with risk."""
    score = int(round(intent * 0.4 + relationship * 0.25 + fit * 0.20 + (100 - risk) * 0.15))
    evidence = [
        {"reason": "intent contribution (40%)", "delta": int(intent * 0.4)},
        {"reason": "relationship contribution (25%)", "delta": int(relationship * 0.25)},
        {"reason": "fit contribution (20%)", "delta": int(fit * 0.20)},
        {"reason": "inverse-risk contribution (15%)", "delta": int((100 - risk) * 0.15)},
    ]
    return _clamp(score), evidence


# ---------- public service surface ----------

def compute_for_contact(ctx: ServiceContext, contact_id: int, *,
                        persist: bool = True) -> dict:
    """Compute all five scores for a contact. Returns a dict of {type: {score, evidence}}.
    If persist=True, writes them to contact_scores."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM contacts WHERE id = ? AND deleted_at IS NULL", (contact_id,),
        ).fetchone()
        if not existing:
            raise ServiceError("CONTACT_NOT_FOUND", f"contact {contact_id} not found")

        sig = _signals_for(conn, contact_id)
        rel_score, rel_ev   = _score_relationship_strength(sig)
        int_score, int_ev   = _score_intent(sig)
        fit_score, fit_ev   = _score_fit(sig)
        risk_score, risk_ev = _score_risk(sig)
        opp_score, opp_ev   = _score_opportunity(int_score, rel_score, fit_score, risk_score)

        results = {
            "relationship_strength": {"score": rel_score, "evidence": rel_ev},
            "intent":                {"score": int_score, "evidence": int_ev},
            "fit":                   {"score": fit_score, "evidence": fit_ev},
            "risk":                  {"score": risk_score, "evidence": risk_ev},
            "opportunity":           {"score": opp_score, "evidence": opp_ev},
        }

        if persist:
            now = int(time.time())
            for stype, data in results.items():
                conn.execute(
                    """INSERT INTO contact_scores
                         (contact_id, score_type, score, evidence_json, computed_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(contact_id, score_type) DO UPDATE SET
                         score=excluded.score,
                         evidence_json=excluded.evidence_json,
                         computed_at=excluded.computed_at""",
                    (contact_id, stype, data["score"], json.dumps(data["evidence"]), now),
                )
            audit.log(conn, ctx, action="scoring.computed",
                      object_type="contact", object_id=contact_id,
                      after={"scores": {k: v["score"] for k, v in results.items()},
                             "signals": {k: v for k, v in sig.items() if k != "consent"}})

    return {"contact_id": contact_id, "computed_at": int(time.time()), **results}


def compute_for_all(ctx: ServiceContext, *, limit: Optional[int] = None) -> dict:
    """Batch: recompute scores for every active contact. Intended for nightly cron."""
    if not ctx.is_admin():
        raise ServiceError("FORBIDDEN", "batch scoring requires admin scope")
    with db() as conn:
        sql = "SELECT id FROM contacts WHERE deleted_at IS NULL ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        ids = [r[0] for r in conn.execute(sql)]
    for cid in ids:
        compute_for_contact(ctx, cid, persist=True)
    return {"computed": len(ids)}


def get_scores(ctx: ServiceContext, contact_id: int) -> dict:
    """Return persisted scores for a contact, with evidence parsed."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            "SELECT score_type, score, evidence_json, computed_at "
            "FROM contact_scores WHERE contact_id = ?",
            (contact_id,),
        ).fetchall()
    out = {}
    for r in rows:
        out[r["score_type"]] = {
            "score": r["score"],
            "evidence": json.loads(r["evidence_json"] or "[]"),
            "computed_at": r["computed_at"],
        }
    return {"contact_id": contact_id, "scores": out}


def list_top(ctx: ServiceContext, score_type: str, *,
             limit: int = 20, min_score: Optional[int] = None) -> list[dict]:
    """List contacts ranked by a single score type. Useful for `dormant_high_value` etc."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    if score_type not in SCORE_TYPES:
        raise ServiceError("VALIDATION_ERROR",
                           f"score_type must be one of {SCORE_TYPES}")
    where = ["cs.score_type = ?"]
    params = [score_type]
    if min_score is not None:
        where.append("cs.score >= ?")
        params.append(int(min_score))
    where.append("c.deleted_at IS NULL")
    limit = max(1, min(int(limit), 500))
    params.append(limit)
    with db() as conn:
        rows = conn.execute(
            f"""SELECT c.id, c.full_name, c.email, cs.score, cs.computed_at
                  FROM contact_scores cs
                  JOIN contacts c ON c.id = cs.contact_id
                 WHERE {" AND ".join(where)}
                 ORDER BY cs.score DESC, c.id DESC
                 LIMIT ?""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
