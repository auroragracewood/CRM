---
verb: score
noun: contact
canonical_transport: rest
mcp_tool: score_contact
cli: score contact
rest: POST /api/contacts/{id}/score
required_scope: write
related: ["run-report"]
---

# Recompute scores for one contact

Recomputes all five score types for one contact and persists them
into `contact_scores` with evidence. Use to force-refresh after a
big change (manual interaction, tag attach, plug-in change).

## Score types

| score_type | summary |
|------------|---------|
| `relationship_strength` | how well we know the contact |
| `intent` | how engaged they are right now |
| `fit` | how well they match our ICP (plug-in extensible) |
| `risk` | likelihood of disengagement |
| `opportunity` | overall priority score |

## Required fields
- contact id

## Example (REST)

```bash
curl -sX POST $BASE/api/contacts/5/score \
  -H "Authorization: Bearer $KEY"
# {"ok":true,"scores":{"intent":78,"fit":62,"opportunity":71,
#                      "relationship_strength":40,"risk":18}}
```

## Example (CLI)

```bash
python -m agent_surface.cli score contact --id 5
```

## Reading without recomputing

```bash
curl -sH "Authorization: Bearer $KEY" $BASE/api/contacts/5/scores
# returns persisted scores + evidence
```

The UI's contact page renders `evidence_json` with a "why?" expand
per row.

## Common errors

| code | meaning |
|------|---------|
| `CONTACT_NOT_FOUND` | id missing or soft-deleted |
| `FORBIDDEN` | scope insufficient |

## Audit + webhooks

- Audit: `action="score.recomputed"` (one row per contact)
- No webhook for routine recomputes (would flood subscribers)
- Plug-in hook: `compute_fit_score(ctx, contact, conn) -> dict`
  fires during recompute; plug-ins contribute to the `fit` score

## When to manually recompute

- Right after writing many interactions for the contact in a session.
- After bulk consent / tag changes.
- After tuning scoring rules (then call `recompute-all` for everyone).
- When a plug-in's `compute_fit_score` was just installed/changed.

## Bulk recompute (admin only)

```bash
curl -sX POST $BASE/api/scoring/recompute-all \
  -H "Authorization: Bearer $ADMIN_KEY"
```

Takes seconds-to-minutes depending on contact count.
