"""Example plug-in: rule-based fit score using tag heuristics.

Overrides the default neutral 'fit' score (50) when an enabled plug-in is
registered. Demonstrates the contract:

  - NAME / VERSION / DESCRIPTION module constants are required for
    registration in the plug-ins table.
  - Any callable matching a KNOWN_HOOKS name participates automatically.

This plug-in implements `compute_fit_score(ctx, contact_id)` and uses tag
patterns to compute an ICP (Ideal Customer Profile) score. Customize the
patterns to match your own business — that's what plug-ins are for.
"""

NAME = "example-fit-score"
VERSION = "0.1.0"
DESCRIPTION = "Compute fit score from contact tag patterns (example plug-in)."

# Tag patterns and their fit contribution. Positive = better fit; negative = worse.
_PATTERNS = {
    "icp-match":         +30,
    "ideal-customer":    +30,
    "decision-maker":    +15,
    "enterprise":        +10,
    "smb":               +5,
    "warm-intro":        +10,
    "self-employed":     -5,
    "wrong-fit":         -25,
    "out-of-icp":        -25,
    "too-small":         -15,
    "do-not-contact":    -50,
}


def compute_fit_score(ctx, contact_id: int):
    """Return (score, evidence) or None to fall back to the default."""
    from backend.db import db
    with db() as conn:
        rows = conn.execute(
            """SELECT t.name FROM tags t
                 JOIN contact_tags ct ON ct.tag_id = t.id
                WHERE ct.contact_id = ?""",
            (contact_id,),
        ).fetchall()
    tags = {r[0].lower() for r in rows}

    score = 50  # neutral baseline, matches default fit
    evidence = []
    for pattern, delta in _PATTERNS.items():
        if pattern in tags:
            score += delta
            evidence.append({"reason": f"tag {pattern!r}", "delta": delta})

    if not evidence:
        # No matching tags — let the default neutral score apply.
        return None

    score = max(0, min(100, score))
    evidence.append({"reason": "via example-fit-score plug-in", "delta": 0})
    return score, evidence


def on_contact_created(ctx, contact: dict, conn):
    """Side-effect hook: every new contact gets a 'new-contact' tag attached
    automatically by this plug-in. Demonstrates a write-side hook."""
    import time as _t
    name = "new-contact"
    row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row:
        tag_id = row[0]
    else:
        conn.execute("INSERT INTO tags (name, scope, created_at) VALUES (?,?,?)",
                     (name, "contact", int(_t.time())))
        tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT OR IGNORE INTO contact_tags (contact_id, tag_id, added_at, added_by)
           VALUES (?,?,?,?)""",
        (contact["id"], tag_id, int(_t.time()), ctx.user_id),
    )
