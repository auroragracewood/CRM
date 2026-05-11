"""Reports service. A catalog of named, pre-built queries that produce useful
CRM intelligence without an LLM.

Each report returns a JSON-ish dict with `name`, `description`, optional
`columns` (header order for tabular output), and `rows`/`values` (the actual
data). Reports stay deterministic and small enough to render in a single
page or stream to CSV via /api/reports/{name}.csv.

Reports are CALLABLES, not table rows — no `reports` table. New reports
become new functions in this module.
"""
import time
from typing import Optional

from ..context import ServiceContext
from ..db import db
from .contacts import ServiceError


DAY = 24 * 3600


def _ok(name: str, description: str, columns: list[str], rows: list,
        **extra) -> dict:
    return {"name": name, "description": description,
            "columns": columns, "rows": rows, **extra}


# ---------- individual reports ----------

def dormant_high_value(ctx: ServiceContext, *,
                       opportunity_min: int = 70,
                       days_silent: int = 60,
                       limit: int = 50) -> dict:
    """Contacts with high opportunity score who have gone silent.
    The classic 'reach out before it goes cold' query."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    now = int(time.time())
    threshold = now - days_silent * DAY
    with db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.full_name, c.email, cs.score AS opportunity,
                      (SELECT MAX(occurred_at) FROM interactions WHERE contact_id=c.id) AS last_at
                 FROM contacts c
                 JOIN contact_scores cs
                   ON cs.contact_id = c.id AND cs.score_type='opportunity'
                WHERE c.deleted_at IS NULL
                  AND cs.score >= ?
                  AND (SELECT MAX(occurred_at) FROM interactions WHERE contact_id=c.id) < ?
                ORDER BY cs.score DESC
                LIMIT ?""",
            (opportunity_min, threshold, limit),
        ).fetchall()
    out = []
    for r in rows:
        days_ago = (now - r["last_at"]) // DAY if r["last_at"] else None
        out.append({"id": r["id"], "full_name": r["full_name"],
                    "email": r["email"], "opportunity": r["opportunity"],
                    "days_since_last_interaction": days_ago})
    return _ok(
        "dormant_high_value",
        f"High-opportunity contacts ({opportunity_min}+) silent for {days_silent}+ days",
        ["id", "full_name", "email", "opportunity", "days_since_last_interaction"],
        out,
    )


def top_intent_now(ctx: ServiceContext, *, limit: int = 25) -> dict:
    """Contacts with the highest intent score right now."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.full_name, c.email, cs.score AS intent
                 FROM contacts c
                 JOIN contact_scores cs
                   ON cs.contact_id = c.id AND cs.score_type='intent'
                WHERE c.deleted_at IS NULL
                ORDER BY cs.score DESC, c.id DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    return _ok(
        "top_intent_now",
        "Contacts ranked by current intent score",
        ["id", "full_name", "email", "intent"],
        [dict(r) for r in rows],
    )


def pipeline_velocity(ctx: ServiceContext) -> dict:
    """Average days deals spend at each stage of each pipeline.

    Approximation: uses stage_id changes recorded in deals.updated_at minus
    the prior stage's updated_at. v2 simplification: just shows average
    age-since-created for open deals per stage."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    now = int(time.time())
    with db() as conn:
        rows = conn.execute(
            """SELECT p.id AS pipeline_id, p.name AS pipeline,
                      ps.id AS stage_id, ps.name AS stage, ps.position,
                      COUNT(d.id) AS open_deals,
                      ROUND(AVG((? - d.created_at) / 86400.0), 1) AS avg_age_days
                 FROM pipelines p
                 JOIN pipeline_stages ps ON ps.pipeline_id = p.id
                 LEFT JOIN deals d
                        ON d.pipeline_id = p.id AND d.stage_id = ps.id AND d.status='open'
                WHERE p.archived = 0
                GROUP BY p.id, ps.id
                ORDER BY p.id, ps.position""",
            (now,),
        ).fetchall()
    return _ok(
        "pipeline_velocity",
        "Open deal count + average age in days per pipeline stage",
        ["pipeline_id", "pipeline", "stage_id", "stage", "position",
         "open_deals", "avg_age_days"],
        [dict(r) for r in rows],
    )


def conversion_funnel(ctx: ServiceContext, *, pipeline_id: Optional[int] = None) -> dict:
    """Count of deals at each stage + won/lost ratio. If pipeline_id is given,
    scopes to that pipeline."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    params: list = []
    where = ""
    if pipeline_id:
        where = " WHERE d.pipeline_id = ?"
        params.append(pipeline_id)
    with db() as conn:
        rows = conn.execute(
            f"""SELECT p.name AS pipeline, ps.position, ps.name AS stage,
                       ps.is_won, ps.is_lost,
                       COUNT(d.id) AS count
                  FROM pipeline_stages ps
                  JOIN pipelines p ON p.id = ps.pipeline_id
                  LEFT JOIN deals d ON d.stage_id = ps.id{where.replace(' WHERE ', ' AND ')}
                  {where if pipeline_id else ''}
                 GROUP BY ps.id
                 ORDER BY p.id, ps.position""",
            params,
        ).fetchall()
        totals = conn.execute(
            f"""SELECT
                   SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) AS won,
                   SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) AS lost,
                   COUNT(*) AS total
                  FROM deals{where}""",
            params,
        ).fetchone()
    won = totals["won"] or 0
    lost = totals["lost"] or 0
    total = totals["total"] or 0
    win_rate = round(won / (won + lost) * 100, 1) if (won + lost) else None
    return _ok(
        "conversion_funnel",
        "Deal count by pipeline stage; overall win rate",
        ["pipeline", "position", "stage", "is_won", "is_lost", "count"],
        [dict(r) for r in rows],
        totals={"won": won, "lost": lost, "total": total, "win_rate_pct": win_rate},
    )


def deal_pipeline_summary(ctx: ServiceContext) -> dict:
    """Open-deal value summed per pipeline, broken into stages."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT p.name AS pipeline, COUNT(d.id) AS open_deals,
                      COALESCE(SUM(d.value_cents), 0) AS total_value_cents,
                      ROUND(AVG(d.probability), 0) AS avg_probability
                 FROM pipelines p
                 LEFT JOIN deals d ON d.pipeline_id = p.id AND d.status='open'
                WHERE p.archived = 0
                GROUP BY p.id
                ORDER BY p.id""",
        ).fetchall()
    out = [dict(r) for r in rows]
    return _ok(
        "deal_pipeline_summary",
        "Open deals per pipeline: count, total value, average probability",
        ["pipeline", "open_deals", "total_value_cents", "avg_probability"],
        out,
    )


def lead_sources(ctx: ServiceContext, *, days: int = 30) -> dict:
    """New contact creation sources (form:slug, import, manual) over the last N days,
    inferred from `interactions.source` rows of type 'form_submission' or 'system'."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    threshold = int(time.time()) - days * DAY
    with db() as conn:
        rows = conn.execute(
            """SELECT COALESCE(i.source, 'manual') AS source, COUNT(DISTINCT i.contact_id) AS contacts
                 FROM interactions i
                WHERE i.occurred_at >= ?
                  AND i.contact_id IS NOT NULL
                GROUP BY i.source
                ORDER BY contacts DESC""",
            (threshold,),
        ).fetchall()
    return _ok(
        "lead_sources", f"New contacts grouped by source over last {days} days",
        ["source", "contacts"], [dict(r) for r in rows],
    )


def tag_distribution(ctx: ServiceContext) -> dict:
    """How many contacts + companies carry each tag."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    with db() as conn:
        rows = conn.execute(
            """SELECT t.name, t.scope,
                      (SELECT COUNT(*) FROM contact_tags ct WHERE ct.tag_id = t.id) AS contact_count,
                      (SELECT COUNT(*) FROM company_tags ct WHERE ct.tag_id = t.id) AS company_count
                 FROM tags t
                ORDER BY contact_count + company_count DESC, t.name""",
        ).fetchall()
    return _ok(
        "tag_distribution", "Contacts + companies tagged with each tag",
        ["name", "scope", "contact_count", "company_count"],
        [dict(r) for r in rows],
    )


def overdue_tasks(ctx: ServiceContext) -> dict:
    """All overdue tasks grouped by assignee (or 'unassigned')."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    now = int(time.time())
    with db() as conn:
        rows = conn.execute(
            """SELECT t.id, t.title, t.priority, t.due_date,
                      t.contact_id, t.company_id,
                      COALESCE(u.email, 'unassigned') AS assigned_email
                 FROM tasks t
                 LEFT JOIN users u ON u.id = t.assigned_to
                WHERE t.status IN ('open','in_progress')
                  AND t.due_date IS NOT NULL
                  AND t.due_date < ?
                ORDER BY t.due_date ASC""",
            (now,),
        ).fetchall()
    return _ok(
        "overdue_tasks", "Open tasks whose due_date is in the past",
        ["id", "title", "priority", "due_date",
         "contact_id", "company_id", "assigned_email"],
        [dict(r) for r in rows],
    )


def recent_form_submissions(ctx: ServiceContext, *, days: int = 7) -> dict:
    """Form submissions in the last N days, linked back to the form name + contact."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    threshold = int(time.time()) - days * DAY
    with db() as conn:
        rows = conn.execute(
            """SELECT fs.id, fs.created_at, fs.contact_id,
                      f.name AS form_name, f.slug AS form_slug,
                      c.full_name AS contact_name, c.email AS contact_email
                 FROM form_submissions fs
                 JOIN forms f ON f.id = fs.form_id
                 LEFT JOIN contacts c ON c.id = fs.contact_id
                WHERE fs.created_at >= ?
                ORDER BY fs.created_at DESC""",
            (threshold,),
        ).fetchall()
    return _ok(
        "recent_form_submissions",
        f"Form submissions in the last {days} days",
        ["id", "created_at", "contact_id", "form_name", "form_slug",
         "contact_name", "contact_email"],
        [dict(r) for r in rows],
    )


# ---------- catalog + dispatch ----------

CATALOG = {
    "dormant_high_value":      dormant_high_value,
    "top_intent_now":          top_intent_now,
    "pipeline_velocity":       pipeline_velocity,
    "conversion_funnel":       conversion_funnel,
    "deal_pipeline_summary":   deal_pipeline_summary,
    "lead_sources":            lead_sources,
    "tag_distribution":        tag_distribution,
    "overdue_tasks":           overdue_tasks,
    "recent_form_submissions": recent_form_submissions,
}


def list_reports() -> list[dict]:
    """Lightweight listing of available reports + their docstring as description."""
    out = []
    for name, fn in CATALOG.items():
        doc = (fn.__doc__ or "").strip().split("\n", 1)[0]
        out.append({"name": name, "description": doc})
    return out


def run(ctx: ServiceContext, name: str, **params) -> dict:
    """Dispatch by name. `params` are passed through as keyword arguments."""
    if not ctx.can_read():
        raise ServiceError("FORBIDDEN", "ctx.scope does not allow reads")
    fn = CATALOG.get(name)
    if not fn:
        raise ServiceError("REPORT_NOT_FOUND",
                           f"unknown report {name!r}; expected one of {list(CATALOG)}")
    return fn(ctx, **params)
