# Recipe · Dormant revival

> Find high-value contacts who've gone cold, prioritize them with
> scores + evidence, and run a structured re-engagement campaign.

## Context

Every CRM accumulates dormant relationships — contacts who used to
matter but haven't been touched in months. Letting them rot is a
business cost: they were warm once, and warmth decays slower than you
think with the right nudge.

The naive approach is "ask sales to call old contacts." It fails
because "old" isn't actionable; not all old contacts are valuable.
Combining the scoring service (which fit/opportunity scores were they
last at?) with segments (define "dormant high-value" precisely) and
tasks (give sales a concrete worklist) turns dormant revival from a
vague aspiration into a recurring play.

## Understanding

The pieces:

1. **Scoring** — `opportunity` and `fit` scores tell you who's still
   valuable.
2. **Segments** — define "dormant high-value" as: opportunity >=
   60 AND fit >= 50 AND no interactions in 45 days AND consented.
3. **Tasks** — auto-create a "reconnect" task per segment member,
   assigned to the contact's original owner.
4. **Portal tokens** — optionally issue a self-service URL to make
   the reconnection feel less cold.
5. **Audit + reports** — track success of the play over time.

## Result

A repeatable monthly (or weekly) revival pass that:

- Surfaces N high-value dormant contacts.
- Creates a structured worklist with due dates.
- Sends an optional re-engagement email/portal link.
- Measures how often the play actually re-opens a deal.

## Recipe — step by step

### 1. Create the segment

```bash
python -m agent_surface.cli segment create-dynamic \
  --name "Dormant high-value" \
  --slug "dormant-high-value" \
  --rules '{
    "and": [
      {"score": {"opportunity": ">=60"}},
      {"score": {"fit":         ">=50"}},
      {"interaction": {"type": "any", "within_days_not": 45}},
      {"consent": {"channel": "email", "status": "granted"}},
      {"not": {"tag": {"name": "do-not-contact"}}}
    ]
  }'
```

The `within_days_not: 45` predicate means "no qualifying interaction
in the last 45 days." Tune the threshold to your sales cycle.

### 2. Evaluate

```bash
python -m agent_surface.cli segment evaluate --slug dormant-high-value
# {"added": 18, "removed": 0, "total": 18}
```

### 3. (Optional) Pre-screen plug-in for opt-outs

Sometimes contacts have implicit "leave me alone" signals not captured
by `do_not_contact` (e.g., they recently churned). Write a plug-in:

```python
# agent_surface/plugins/dormant_pre_screen.py
NAME = "dormant_pre_screen"
VERSION = "0.1.0"
DESCRIPTION = "Excludes recently-churned contacts from revival pass"

def on_segment_evaluated(ctx, segment, members_before, members_after, conn):
    if segment["slug"] != "dormant-high-value":
        return
    for member in members_after:
        # remove if had a 'churn' interaction in last 6 months
        had_churn = conn.execute("""
            SELECT 1 FROM interactions
            WHERE contact_id = ?
              AND title LIKE '%churn%'
              AND occurred_at > strftime('%s','now','-180 days')
            LIMIT 1
        """, (member["contact_id"],)).fetchone()
        if had_churn:
            conn.execute(
                "DELETE FROM segment_members WHERE segment_id=? AND contact_id=?",
                (segment["id"], member["contact_id"]),
            )
```

(`on_segment_evaluated` is a planned hook; add to `KNOWN_HOOKS` when
implementing.)

### 4. Auto-create tasks for each member

A small CLI script for the monthly pass:

```bash
# scripts/dormant_revival_pass.sh
set -euo pipefail
ts=$(date +%Y-%m-%dT%H%M%S)
echo "Running dormant revival pass at $ts"

python -m agent_surface.cli segment evaluate --slug dormant-high-value

# Read members
members=$(python -m agent_surface.cli segment members --slug dormant-high-value --limit 200)

# Create tasks via REST per member
echo "$members" | jq -r '.items[].id' | while read contact_id; do
  curl -sX POST "$BASE/api/tasks" \
    -H "Authorization: Bearer $KEY" \
    -H "Idempotency-Key: dormant-revival-$ts-$contact_id" \
    -H "Content-Type: application/json" \
    -d "{
      \"title\":      \"Reconnect with dormant lead\",
      \"contact_id\": $contact_id,
      \"priority\":   \"normal\",
      \"due_date\":   $(date -d '+7 days' +%s)
    }"
done
```

(Idempotency key prevents duplicate tasks on re-run.)

### 5. (Optional) Issue portal tokens

For the highest-value contacts, send a personalized self-service link:

```bash
echo "$members" | jq -r '.items[] | select(.scores.opportunity >= 80) | .id' \
| while read contact_id; do
  curl -sX POST "$BASE/api/contacts/$contact_id/portal-tokens" \
    -H "Authorization: Bearer $KEY" \
    -d '{"scope":"client","label":"Dormant revival 2026 Q2","expires_in_days":30}'
done
```

### 6. (Optional) Email outreach plug-in

A plug-in `on_task_created` that, when a task with title="Reconnect"
is created and source=cron, drafts a templated email via Resend.

### 7. Monthly report

```bash
python -m agent_surface.cli report run --name dormant_revival_pass_outcomes \
  --params '{"since_days": 90}'
```

(You'll need to add this report to `services/reports.py`.) It returns:

- members in the segment at pass time
- tasks created
- tasks completed
- new interactions on those contacts within 30 days post-pass
- new deals opened
- estimated revenue impact

## Operations

### Cadence

- Weekly: small, focused pass on top 5-10 dormant high-value contacts.
- Monthly: full pass, larger volume, comprehensive task creation.
- Quarterly: review pass outcomes; tune segment threshold + scoring
  rules.

### Avoiding burn

- Cap tasks per pass: don't drop 200 tasks on one salesperson. Sort
  by `opportunity` desc and limit to top N per assignee.
- Track `dormant_revival_count` as a per-contact custom field; skip
  if attempted in the last 6 months.
- Respect explicit "talk to me later" interactions — a plug-in can
  detect "ping me in Q3" and exclude until Q3.

### Measuring success

The audit log captures every action. The interesting question is
"after the revival task was created, did anything happen?" Query:

```sql
SELECT
  t.id AS task_id, t.contact_id, t.created_at, t.completed_at,
  (SELECT MAX(i.occurred_at) FROM interactions i
   WHERE i.contact_id = t.contact_id AND i.occurred_at > t.created_at) AS next_interaction
FROM tasks t
WHERE t.title LIKE '%dormant%'
ORDER BY t.created_at DESC LIMIT 100;
```

Success rate = % of tasks with a `next_interaction` within 30 days
of `created_at`.

## Fine-tuning

### Score thresholds

Run the pass with `opportunity >= 60`. After 3 months of data, check
which conversions came from the upper vs lower half of that range.
Maybe `>= 70` captures 90% of the value with 50% of the effort.

### Time windows

`within_days_not: 45` is the dormancy threshold. Industries vary:
- Fast-cycle B2C: 30 days
- Standard B2B: 45-60 days
- Long-cycle (enterprise, public sector): 120+ days

Pick yours; revisit annually.

### Per-stage dormancy

For contacts with open deals, dormancy means "no activity on the
deal." Add to the rule:
```json
{"or": [
  {"deal": {"status": "open"}, "interaction_on_deal": {"within_days_not": 14}},
  {"deal": {"status": null},   "interaction": {"within_days_not": 45}}
]}
```

(`interaction_on_deal` is an extended predicate — add to the
segments rule grammar in `segments.py`.)

### A/B test the outreach

Half the segment gets a portal token + email. Half just gets a task
for sales to call. Compare 30-day re-engagement rates. The CRM's
audit log gives you the data.

### LLM-generated talking points

When sales opens a revival task, the plug-in
`on_task_assigned_to_user` fetches the contact's last 5
interactions and asks Claude "given this history, write 3 personalized
opening lines to re-engage." Surface in the task description.

## Maximizing potential

1. **Multi-stage revival cadence.** First task: "send personal
   note." If no reply in 14d → second task: "follow up." If no reply
   in 30d → "soft archive" tag. Each step is a plug-in reacting to
   task status changes.

2. **Score-driven prioritization.** Sort the revival worklist by
   `opportunity * recency_of_drop` — contacts whose scores were
   highest before going dark get attention first.

3. **Cross-team revival.** Mix sales + customer-success — some
   "dormant" contacts are existing clients gone quiet, who CS
   should own. The segment rule can branch on tag or owner.

4. **Revival outcomes as scoring signal.** Add a scoring rule that
   bumps `opportunity` if a revival task succeeded. The model
   learns which dormant contacts respond to revival.

5. **Embed revival in onboarding.** Every new salesperson gets a
   "dormant high-value" segment as their warm-up list — the existing
   contacts most likely to convert with attention.

6. **Annual report on dormant value recaptured.** "We re-engaged
   N contacts, opened M deals worth $X, of which $Y closed." Shows
   the CRM's compounding value.

## Anti-patterns

- **One mega-pass per quarter.** Sales gets overwhelmed and skips
  half. Smaller, more frequent passes win.
- **Generic outreach.** "Hey, just checking in" emails get
  ignored. Use the audit log + interactions to find a specific hook
  ("you asked about copper signage last May — here's a new piece we
  made").
- **Treating consent.granted as forever.** GDPR/CASL/etc. require
  active consent. Some installs have a `consent.expires_at` field
  (extension) — respect it.
- **Skipping consent check.** ALWAYS gate revival on
  `consent.status='granted'` for the channel. Cold outreach to
  withdrawn contacts is a fine in some jurisdictions.
- **Not measuring outcomes.** If you can't show the pass works, it
  becomes the first thing cut when sales is busy.

## Where to look in code

- `backend/services/segments.py` — dynamic segment rules
- `backend/services/tasks.py` — task creation
- `backend/services/scoring.py` — opportunity/fit rules
- `backend/services/reports.py` — `dormant_high_value` widget

## Wiki map

Reading any single file in this wiki should make you aware of every
other. This is the full map.

**Repo root**
- [README.md](../../README.md) — human entry point
- [AGENTS.md](../../AGENTS.md) — AI agent operating contract
- [CLAUDE.md](../../CLAUDE.md) — Claude Code project conventions
- [SCHEMATICS.md](../../SCHEMATICS.md) — ASCII architecture diagrams
- [Blueprint.md](../../Blueprint.md) — product spec
- [prompt.md](../../prompt.md) — build-from-scratch prompt

**Wiki root** (`docs/`)
- [README.md](../README.md) — wiki index
- [00-start-here.md](../00-start-here.md) — 10-minute orientation

**01 — Concepts** (`docs/01-concepts/`) — why each piece exists
- [service-layer.md](../01-concepts/service-layer.md)
- [service-context.md](../01-concepts/service-context.md)
- [audit-and-webhooks.md](../01-concepts/audit-and-webhooks.md)
- [plugins.md](../01-concepts/plugins.md)
- [scoring.md](../01-concepts/scoring.md)
- [segments.md](../01-concepts/segments.md)
- [portals.md](../01-concepts/portals.md)
- [inbound.md](../01-concepts/inbound.md)
- [search.md](../01-concepts/search.md)

**02 — Guides** (`docs/02-guides/`) — step-by-step how-tos
- [install.md](../02-guides/install.md)
- [first-contact.md](../02-guides/first-contact.md)
- [your-first-pipeline.md](../02-guides/your-first-pipeline.md)
- [import-export.md](../02-guides/import-export.md)
- [deploying.md](../02-guides/deploying.md)

**03 — Reference** (`docs/03-reference/`) — exhaustive lookup
- [data-model.md](../03-reference/data-model.md)
- [api.md](../03-reference/api.md)
- [cli.md](../03-reference/cli.md)
- [mcp.md](../03-reference/mcp.md)
- [plugins.md](../03-reference/plugins.md)
- [webhooks.md](../03-reference/webhooks.md)
- [errors.md](../03-reference/errors.md)

**04 — Recipes** (`docs/04-recipes/`) — end-to-end workflows
- [lead-intake.md](lead-intake.md)
- [dormant-revival.md](dormant-revival.md) **← you are here**
- [agent-workflows.md](agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
