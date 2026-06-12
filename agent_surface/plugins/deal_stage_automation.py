"""Deal stage automation — fires whenever a deal moves between pipeline stages.

The single highest-value moment in any sales pipeline is the transition.
Stale deals sit; *moving* deals demand attention. This plug-in turns every
stage change into one of three responses:

  - WON:   creates a "kickoff/thank-you" task, logs a system interaction
           on the contact (if any) recording the win.
  - LOST:  creates a "postmortem: why did we lose this?" task, logs a
           system interaction on the contact (if any).
  - OPEN -> OPEN:  logs a system interaction noting the stage transition
           (no task — tasks would create noise mid-pipeline).

All writes use the connection passed by the framework, so the plug-in's
side effects commit atomically with the deal update itself. If the deal
update rolls back, so do these writes.

Demonstrates:
  - A hook (`on_deal_stage_changed`) not exercised by any other plug-in.
  - The full 5-arg hook signature (ctx, deal, from_stage, to_stage, conn).
  - Cross-service mutation inside a hook (tasks + interactions tables).
  - Stage-classification by reading pipeline_stages flags.
"""
import json
import time


NAME = "deal-stage-automation"
VERSION = "0.1.0"
DESCRIPTION = ("Fires on every deal stage change. Creates kickoff tasks on "
               "wins, postmortem tasks on losses, and logs a system "
               "interaction on every transition.")


def _stage_meta(conn, stage_id):
    """Return (name, is_won, is_lost) for a stage_id, or (None, 0, 0) if missing."""
    if not stage_id:
        return (None, 0, 0)
    row = conn.execute(
        "SELECT name, is_won, is_lost FROM pipeline_stages WHERE id = ?",
        (stage_id,),
    ).fetchone()
    if not row:
        return (None, 0, 0)
    return (row[0], int(row[1] or 0), int(row[2] or 0))


def _create_task(conn, ctx, *, title, deal_id, contact_id, company_id,
                 priority, due_in_days, source_tag):
    """Direct INSERT into tasks — uses the hook's conn so it commits with
    the triggering deal update."""
    now = int(time.time())
    due = now + (due_in_days * 86400)
    conn.execute(
        """INSERT INTO tasks
             (title, description, status, priority, due_date,
              deal_id, contact_id, company_id, assigned_to,
              created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            title,
            f"Auto-created by plug-in {NAME!r} ({source_tag}).",
            "open", priority, due,
            deal_id, contact_id, company_id,
            ctx.user_id,
            ctx.user_id, now, now,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _log_system_interaction(conn, ctx, *, contact_id, title, metadata):
    """Direct INSERT into interactions table — same atomic-commit reasoning."""
    if not contact_id:
        return None
    now = int(time.time())
    conn.execute(
        """INSERT INTO interactions
             (contact_id, type, channel, title, body, metadata_json,
              source, occurred_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            contact_id, "system", "plugin",
            title, None,
            json.dumps(metadata),
            f"plugin:{NAME}", now, now,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def on_deal_stage_changed(ctx, deal, from_stage_id, to_stage_id, conn):
    """The whole point. Fires from backend/services/deals.py inside the
    update() transaction. `deal` is the after-state."""
    from_name, _, _ = _stage_meta(conn, from_stage_id)
    to_name, is_won, is_lost = _stage_meta(conn, to_stage_id)
    transition = f"{from_name or '?'} -> {to_name or '?'}"

    deal_id = deal.get("id")
    contact_id = deal.get("contact_id")
    company_id = deal.get("company_id")
    deal_title = (deal.get("title") or "(untitled)").strip()

    base_meta = {
        "plugin": NAME,
        "deal_id": deal_id,
        "from_stage_id": from_stage_id,
        "to_stage_id": to_stage_id,
        "transition": transition,
    }

    if is_won:
        task_id = _create_task(
            conn, ctx,
            title=f"Kickoff + thank-you: {deal_title}",
            deal_id=deal_id, contact_id=contact_id, company_id=company_id,
            priority="high", due_in_days=1, source_tag="won",
        )
        _log_system_interaction(
            conn, ctx, contact_id=contact_id,
            title=f"Deal won: {deal_title} ({transition})",
            metadata={**base_meta, "outcome": "won", "task_id": task_id},
        )
    elif is_lost:
        task_id = _create_task(
            conn, ctx,
            title=f"Postmortem: {deal_title}",
            deal_id=deal_id, contact_id=contact_id, company_id=company_id,
            priority="normal", due_in_days=7, source_tag="lost",
        )
        _log_system_interaction(
            conn, ctx, contact_id=contact_id,
            title=f"Deal lost: {deal_title} ({transition})",
            metadata={**base_meta, "outcome": "lost", "task_id": task_id},
        )
    else:
        # Open-to-open transition: log only, no task — pipeline mid-flight
        # shouldn't generate task noise.
        _log_system_interaction(
            conn, ctx, contact_id=contact_id,
            title=f"Deal advanced: {deal_title} ({transition})",
            metadata={**base_meta, "outcome": "advanced"},
        )
