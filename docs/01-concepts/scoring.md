# Concept · Scoring

> Rule-based contact scores with full evidence trails. No black-box ML.
> Every number you see on a contact's profile comes from rules you can
> read, audit, modify, and disable.

## Context

Salespeople and ops teams ask the CRM the same set of questions a
hundred times a week:

- "Who's hot right now?" (intent)
- "Who fits our ICP?" (fit)
- "Who's about to slip away?" (risk)
- "Who's worth a real outbound push?" (opportunity)
- "Who do we actually know well?" (relationship_strength)

The naive answer is a single "lead score" — one number per contact.
The honest answer is five numbers, because those five questions don't
correlate. A contact can fit perfectly (great industry, senior title)
but have zero intent (no recent engagement). Another can have high
intent (replied yesterday) but bad fit (wrong industry).

Most CRMs solve scoring with opaque ML models or hidden formulas.
Both fail the same way: when the number is "wrong", no one can explain
why. Salespeople stop trusting it. Ops can't tune it without
re-training. Compliance can't audit it.

Our scoring is rule-based, evidence-tracked, and plug-in extensible.
Every score on every contact comes with a list of `{reason, delta}`
items that produced it. You read the evidence; you change the rules;
the next score reflects the change.

## Understanding

One table:

```
contact_scores
  contact_id    INTEGER  → contacts(id)
  score_type    TEXT     'relationship_strength' | 'intent' | 'fit'
                          | 'risk' | 'opportunity'
  score         INTEGER  0..100
  evidence_json TEXT     JSON array: [{reason, delta}, ...]
  computed_at   INTEGER  unix seconds
  PRIMARY KEY (contact_id, score_type)
```

One service: `backend/services/scoring.py`. Five built-in rule sets,
one per score_type. The scoring service exposes:

```python
scoring.compute_for_contact(ctx, contact_id) -> dict[score_type] = score
scoring.compute_one(ctx, contact_id, score_type) -> int
scoring.recompute_all(ctx) -> {"updated": N}     # admin scope
scoring.top(ctx, score_type, limit) -> list[contact]
```

A score computation:

1. Loads the contact, its company, its recent interactions, its
   consent state, its tags, its open deals/tasks.
2. Walks the rule set for that score_type, accumulating
   `{reason, delta}` items as it goes.
3. Sums deltas, clamps to 0..100.
4. UPSERTs into `contact_scores` with the rule-derived score AND the
   full evidence array.

The fifth score type (`fit`) is special: it ALSO calls every plug-in
that implements `compute_fit_score(ctx, contact, conn) -> dict` and
folds plug-in contributions into the final score with their declared
`weight`. This is the one place a customer-specific (or LLM-driven)
plug-in can directly affect scoring — without modifying core.

## Reason

**Why rule-based and not ML?**

- **Auditability.** A salesperson disputes a score. You SELECT the
  evidence, point at the rules, end the conversation in 30 seconds.
  ML would require re-running the model, exporting feature
  attributions, hoping the explanation is faithful.
- **Tunability.** Marketing wants intent to weigh recency more.
  You change one constant in the rules. Done. With ML you'd re-label,
  re-train, re-validate.
- **Cold-start.** ML scoring is useless until you have thousands of
  labeled examples. Rules work on row 1.
- **Compliance.** "How did you decide this person is a low-fit lead?"
  is answerable with rules; harder with ML.
- **No vendor lock-in on a model.** No re-training drift. No
  surprise behavior changes.

**Why FIVE scores and not one?**

A single number conflates "should I prioritize this lead?" with
"do they like me?" with "are they likely to buy?". They're different
signals. Combining them hides information. Five scores let the UI
sort by any dimension AND let ops build composite metrics (e.g.,
"opportunity AND not at-risk") as needed.

**Why is `fit` the only plug-in-extensible one?**

Fit is the most install-specific signal. Every company defines "fits
us" differently. Intent/risk/opportunity are based on signals (recency,
engagement, deal stage) that work the same across installs. Fit
needs your industry definition, your ICP, your custom triggers.
Plug-ins are the right seam.

## Result

What scoring gives you:

- **Five comparable numbers per contact**, persisted, with timestamps.
- **An evidence trail per score** — every reason and delta, viewable
  on the contact page via the "why?" expand.
- **Top-N queries** for each score type, fast (indexed).
- **Sorted lists in segments** — dynamic segments can filter on
  scores, so "intent > 70 AND fit > 50" is a one-liner.
- **A plug-in extension point** for fit, so customer-specific
  signals or LLM judgments can enrich scores without forking.
- **Predictability** — a contact with the same state today and
  tomorrow has the same score (unless rules change). No model drift.

## Use case — how the dashboard's "Top intent right now" widget works

1. The widget renders the `top_intent_now` report.
2. The report calls `scoring.top(ctx, score_type='intent', limit=5)`.
3. The function runs:
   ```sql
   SELECT c.*, cs.score, cs.evidence_json
   FROM contacts c
   JOIN contact_scores cs
     ON cs.contact_id = c.id AND cs.score_type = 'intent'
   WHERE c.deleted_at IS NULL
   ORDER BY cs.score DESC, cs.computed_at DESC
   LIMIT 5;
   ```
4. The dashboard renders each row's full_name, score, and a "why?"
   expander that reveals the evidence array.

When you click "why?" on, say, "Maya Sato — intent 78":

> +30 — Logged interaction within last 7 days
> +20 — Two-way email thread active
> +15 — Title senior (Director)
> +10 — In Q4 nurture segment
> +3  — Last interaction body contains 'budget'

That's the evidence. Visible. Changeable.

## Operations

### Recomputing

Scores are recomputed:

- Synchronously when a contact-relevant event fires (the service
  layer calls `scoring.maybe_recompute(...)` after interactions,
  consent changes, tag attaches, deal stage moves).
- Manually for one contact:
  ```bash
  python -m agent_surface.cli score contact --id 5
  ```
- In bulk (admin only — can be slow):
  ```bash
  python -m agent_surface.cli score recompute-all
  ```
  or
  ```bash
  curl -X POST http://localhost:8000/api/scoring/recompute-all \
       -H "Authorization: Bearer <admin-key>"
  ```

A cron job in `agent_surface/cron.py` runs `recompute_all` nightly
to catch contacts whose freshness has decayed since their last
trigger event.

### Reading

- UI: contact profile → "Scores" card → click "why?" on any row.
- REST: `GET /api/contacts/{id}/scores`.
- CLI: `score get --id 5`.
- MCP: `get_scores(contact_id=5)`.
- Top-N: REST `GET /api/scoring/top?score_type=intent&limit=10` or
  CLI `score top --score-type intent --limit 10`.

### Tuning rules

Rules live in `backend/services/scoring.py` as small functions:

```python
def _intent_rules(contact, recent, deals, conn):
    ev = []
    last = _last_interaction_ts(contact, recent)
    age_days = (int(time.time()) - last) / 86400 if last else 9999
    if age_days < 3:    ev.append({"reason": "Recent interaction (≤3d)",  "delta": +30})
    elif age_days < 14: ev.append({"reason": "Recent interaction (≤14d)", "delta": +15})
    elif age_days > 60: ev.append({"reason": "Dormant (>60d)",            "delta": -20})
    ...
    return ev
```

Tune the constants in place. Restart the server. New scores reflect
the change immediately on next recompute. Existing scores are stale
until recomputed.

Mark every change with a comment so a future you can see the rationale:

```python
elif age_days < 14: ev.append({"reason": "Recent interaction (≤14d)", "delta": +15})
# tightened from +20 → +15 on 2026-04-12 after sales team feedback
# that the score was rewarding too many stale leads
```

### Plug-in `compute_fit_score` contributions

Plug-ins implementing `compute_fit_score` return:

```python
{
  "score":   <int 0..100>,    # this plug-in's component
  "weight":  <float 0..1>,    # how much it counts
  "evidence": [{"reason": "...", "delta": +N}, ...]
}
```

Scoring takes the weighted average across all enabled plug-ins +
the core rule set. Evidence arrays are concatenated and stored.

Inspect what each plug-in contributed:

```sql
SELECT evidence_json FROM contact_scores
WHERE contact_id=5 AND score_type='fit';
```

You'll see lines like `{"reason": "[plugin:industry_match] Industry
matches ICP", "delta": +20}` — the plug-in's name is prefixed for
attribution.

## Fine-tuning

### Per-rule kill switch

If a rule is producing bad results, comment it out and recompute.
Or wrap it in a feature flag:

```python
if FEATURES.get("intent_use_email_velocity", True):
    ev.extend(_email_velocity_rules(...))
```

The flag becomes a config knob you can flip without redeploying.

### Score caps

The default cap is 0..100. For some teams a logarithmic feel is
more useful (most contacts in the 20-50 range, with rare hits in
80+). Apply a transformation in `scoring.compute_one`:

```python
return max(0, min(100, int(round(50 + 20 * math.log10(raw / 50 + 1)))))
```

Document the transformation in the score's `evidence_json` as the
last entry, so it's auditable.

### Recency decay

The simplest tune: scale every recency-based delta by an
exponential decay function. Define once, reuse everywhere:

```python
def _decay(days_ago, halflife_days):
    return 0.5 ** (days_ago / halflife_days)

ev.append({"reason": "Interaction recency", "delta": int(30 * _decay(age_days, 14))})
```

### Score freshness invalidation

`scoring.maybe_recompute(ctx, contact_id, reason)` is called from
service hooks. To skip recompute for cheap actions (a tag attach that
the rules don't care about), check the rules' input dependencies:

```python
SCORE_DEPENDS_ON = {
    "intent":  {"interaction", "deal", "task"},
    "fit":     {"contact", "company", "tag"},
    "risk":    {"interaction", "consent", "task"},
    ...
}
```

Skip recompute when the reason isn't in the score's dependency set.

### Plug-in weight tuning

If a plug-in is dominating fit scores, lower its `weight`. If it's
being drowned, raise it. Weights are floats; the scoring service
normalizes by sum-of-weights, so you can think of them as relative.

## Maximizing potential

1. **Score-aware segments.** Build a dynamic segment "high-intent
   high-fit" with rule
   `{"and":[{"score":{"intent":">=70"}},{"score":{"fit":">=60"}}]}`.
   Hook a nurture cadence plug-in to fire when a contact ENTERS this
   segment. Now your CRM ships "right person at right time"
   prioritization out of the box.

2. **Evidence-driven email drafts.** When a salesperson opens a
   contact, surface the top-3 evidence items per score as suggested
   talking points. "They opened your last email" beats "they have
   high intent."

3. **A/B test scoring rules.** Run two rule sets in parallel, write
   both to `contact_scores` with different `score_type` namespaces
   (`intent_v1`, `intent_v2`). Compare conversion rates over a
   quarter. Pick the winner.

4. **LLM-as-fit-judge.** Write a plug-in that, for contacts with
   sparse info, calls Claude with the contact's interactions and asks
   for a 0-100 fit score + 3 reasons. Cache. Return as
   `compute_fit_score`. Now your CRM has LLM-driven scoring without
   becoming an LLM product.

5. **Per-team rules.** Sales and customer-success care about
   different scores. Add a `team_id` to the rule set and let teams
   tune their own rules. Stored in a `scoring_rules` table; loaded
   on startup; tunable from the UI.

6. **Score histograms.** Build a report showing the distribution of
   each score across all contacts. If 80% of contacts have intent <
   20, your rules are too pessimistic. If 80% have intent > 80, too
   generous. Calibrate.

7. **Decay tier badges.** Contacts whose `intent` score has dropped
   30+ points since last week get a "cooling" badge. Surface in a
   "lost momentum" dashboard widget. Sales acts on it before the
   prospect goes fully cold.

## Anti-patterns

- **Hiding score logic.** Never compute a score without storing
  evidence. The "why?" expand is what makes the score trustworthy.
- **Mixing scores.** Don't add intent + fit and call it
  "opportunity" — that's lossy. Opportunity is its own score with
  its own rules.
- **Per-contact custom rules.** Resist requests like "Bump Maya's
  score by 20 because she's special." That's data, not logic — store
  it as a tag the rules can read.
- **Caching scores forever.** Recompute on the events the rules
  depend on. A score that hasn't updated in 30 days probably reflects
  stale state.
- **Adding scores to attributes nobody looks at.** Each score_type
  has UI cost (a card on the profile, a column in lists). Don't add
  a sixth score unless three different teams ask for it.

## Where to look in code

- `backend/services/scoring.py` — rule sets + plug-in aggregation
- `agent_surface/plugins/example_fit_score.py` — `compute_fit_score`
  example
- `migrations/0004_v2.sql` — `contact_scores` schema
- `backend/services/reports.py` — uses scoring in
  `top_intent_now`, `dormant_high_value` reports

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
- [service-layer.md](service-layer.md)
- [service-context.md](service-context.md)
- [audit-and-webhooks.md](audit-and-webhooks.md)
- [plugins.md](plugins.md)
- [scoring.md](scoring.md) **← you are here**
- [segments.md](segments.md)
- [portals.md](portals.md)
- [inbound.md](inbound.md)
- [search.md](search.md)

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
- [lead-intake.md](../04-recipes/lead-intake.md)
- [dormant-revival.md](../04-recipes/dormant-revival.md)
- [agent-workflows.md](../04-recipes/agent-workflows.md)

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
