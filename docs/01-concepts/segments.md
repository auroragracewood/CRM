# Concept · Segments

> Named groups of contacts — either hand-picked (static) or auto-built
> from a rule expression that re-evaluates on demand (dynamic). The CRM's
> answer to "give me the people who match this set of conditions."

## Context

Every operational question in a CRM eventually becomes "show me the
contacts where X". Marketing wants "everyone who consented to email
in the last 90 days." Sales wants "everyone with an open deal in
stage 'proposal'." Customer success wants "everyone whose subscription
expires in the next 30 days AND has logged no interactions in 14."

Writing these as ad-hoc SQL is slow, error-prone, and unreusable.
Saving them in a sidecar spreadsheet means they drift out of sync.
Buying a dedicated tool means leaving the CRM.

Segments are the canonical "group of contacts that matches a rule"
primitive, stored in the CRM, addressable by name, queryable by every
surface, refreshable on demand.

## Understanding

Two tables:

```
segments
  id              INTEGER PK
  name            TEXT
  slug            TEXT UNIQUE       ('high-intent-q2')
  type            TEXT              'static' | 'dynamic'
  rules_json      TEXT              for dynamic: the rule tree (JSON)
  description     TEXT
  last_evaluated_at  INTEGER
  member_count    INTEGER           denormalized for fast list rendering
  created_at      INTEGER
  updated_at      INTEGER

segment_members
  segment_id  INTEGER  → segments(id) ON DELETE CASCADE
  contact_id  INTEGER  → contacts(id) ON DELETE CASCADE
  PRIMARY KEY (segment_id, contact_id)
```

Service: `backend/services/segments.py`. API:

```python
segments.create_static(ctx, name, slug, contact_ids)     -> segment
segments.create_dynamic(ctx, name, slug, rules)          -> segment
segments.evaluate(ctx, segment_id)                        -> {added, removed, total}
segments.list_(ctx)                                       -> list of segments
segments.get(ctx, segment_id)                             -> segment
segments.list_members(ctx, segment_id, limit, offset)     -> {items, total}
segments.delete(ctx, segment_id)                          -> {ok}
```

### Static segments

Caller passes a list of contact IDs. The service writes
`segment_members` rows. Membership changes only via explicit
`segments.add_member` / `segments.remove_member` calls. Useful for
hand-curated lists, frozen exports, "VIP customers for this
campaign."

### Dynamic segments

Caller passes a `rules` dict that describes a filter tree:

```json
{
  "and": [
    {"score":   {"intent":   ">=70"}},
    {"score":   {"fit":      ">=50"}},
    {"consent": {"channel":  "email", "status": "granted"}},
    {"not":     {"tag": {"name": "do-not-contact"}}},
    {"or": [
      {"interaction": {"type": "meeting", "within_days": 30}},
      {"deal": {"status": "open"}}
    ]}
  ]
}
```

The evaluator translates the rule tree into a SQL query against
contacts (joined with scores, consent, tags, interactions, deals as
needed), runs it, and rewrites `segment_members` with the result.

Membership is recomputed only when `segments.evaluate` is called —
NOT automatically on every relevant write. That's a tunable choice
(see Fine-tuning).

## Reason

**Why two segment types?**

- **Static** is needed for cases where the membership is the point —
  "the people I personally invited to the launch." Drift would be a
  bug, not a feature.
- **Dynamic** is needed for everything else. The rule is the durable
  artifact; membership is derived. Drift is the feature.

**Why JSON rules and not a DSL like 'intent >= 70 AND consent = granted'?**

- JSON is parseable in every language; we don't need a parser.
- JSON serializes cleanly into REST/MCP/CLI without escaping
  headaches.
- The UI can render the rule as a structured editor (add condition,
  AND, OR, NOT) without parsing a text expression.
- A future LLM that builds rules from natural language outputs JSON
  more reliably than DSL strings.

**Why pull (evaluate-on-demand) and not push (live)?**

- Live segments mean every write must re-check every dynamic segment
  it might affect — quadratic complexity at write time.
- Pull means segment evaluation is its own operation: fast when you
  need it, free when you don't, observable in audit.
- Pull is the simpler invariant: "membership reflects the last
  evaluation."
- A cron job re-evaluates segments nightly; on-demand evaluation is
  always available for fresh results.

**Why `member_count` denormalized?**

Listing segments needs counts. Counting on the fly is one extra
SELECT per row. Caching the count on the segment row and updating it
in `evaluate` is two orders of magnitude faster for big lists.

## Result

What segments give you:

- A reusable definition of "people who match condition X" by name and
  slug.
- A members list any surface can read.
- A rule expression that's serializable, versionable, editable.
- Plug-in hooks for `on_segment_evaluated` and `on_segment_member_
  changed` (planned hook list) — react when contacts enter or leave.
- A canonical thing to point campaigns / exports / reports at:
  "send this email to segment `q4-high-intent-and-consented`."

## Use case — building a "dormant high-value" segment

Spec: "Contacts with `opportunity >= 60` who have logged NO
interactions in the last 45 days, and aren't in the do-not-contact
set."

```bash
python -m agent_surface.cli segment create-dynamic \
  --name "Dormant High-Value" \
  --slug "dormant-high-value" \
  --rules '{
    "and": [
      {"score": {"opportunity": ">=60"}},
      {"interaction": {"type": "any", "within_days_not": 45}},
      {"not": {"tag": {"name": "do-not-contact"}}}
    ]
  }'
```

Then:

```bash
python -m agent_surface.cli segment evaluate --id 3
# {"added": 18, "removed": 2, "total": 18}
```

The segment now has 18 members. The home dashboard's widget
"Dormant high-value" pulls from this segment.

A week later, a salesperson finally calls one of those 18 contacts
and logs the call. The contact's last interaction is now today —
they no longer match `interaction.within_days_not: 45`. But until
`segments.evaluate` runs again, they're still in the segment. The
nightly cron job removes them. Or someone can re-evaluate from the
UI / CLI immediately.

## Operations

### Creating

UI: Segments page → New dynamic. The form renders a rule editor; save
produces the JSON.

REST:
```bash
curl -X POST http://localhost:8000/api/segments \
  -H "Authorization: Bearer <key>" \
  -d '{"name":"...", "slug":"...", "type":"dynamic", "rules": {...}}'
```

CLI:
```bash
python -m agent_surface.cli segment create-dynamic --name ... --slug ... --rules '...'
```

### Evaluating

The first evaluation usually happens at creation time (if `rules`
were given). Subsequent evaluations require an explicit call.

- UI: Segments page → click "Re-evaluate" on a row.
- REST: `POST /api/segments/{id}/evaluate`.
- CLI: `segment evaluate --id N`.
- Cron: a job in `agent_surface/cron.py` re-evaluates ALL dynamic
  segments nightly.

Evaluation writes:
- An `audit_log` row `action='segment.evaluated'` with the delta
  (added, removed, total).
- A `webhook_events` row for each subscriber to the
  `segment.evaluated` event.

### Reading members

UI: Segments page → segment row → Members list.

REST: `GET /api/segments/{id}/members?limit=200&offset=0`.

CLI: `segment members --id N`.

MCP: `list_segment_members(segment_id=N, limit=200)`.

Members are returned with the full contact row (joined) so callers
don't need a follow-up fetch.

### Deleting

`segments.delete` cascades through `segment_members`. The audit row
records the segment before deletion. Recreating with the same slug
gets a new id and starts fresh.

## Fine-tuning

### Live-ish dynamic segments

If you need near-live membership without full live evaluation, hook
`segment.maybe_re_evaluate(segment_id)` calls from service mutations
that touch fields the rule depends on. This is a per-segment opt-in,
controlled by a `segments.live_evaluate` flag. Worth doing for the
3-5 segments your team relies on hourly; not worth for the other 50.

### Rule predicates

The built-in predicates in `segments.py`:

- `score.<type>` — `>=N`, `<=N`, `==N`, `BETWEEN`
- `consent.{channel, status}` — equality match
- `tag.name` — equality (or `tag.name_prefix`)
- `interaction.{type, within_days, within_days_not}` — recency
  windows
- `deal.{status, stage_id, pipeline_id}` — equality
- `task.{status, priority, overdue}` — equality + boolean
- `field.<column>` — generic column equality on contacts
- `and` / `or` / `not` — combinators

Add new predicates by editing the rule-to-SQL translator in
`segments.py`. Each predicate is a small function that takes the
rule node and returns a `(sql_fragment, params)` tuple. Pure
function; easy to test.

### Segment evaluation performance

For very large contact bases (>100k):

- Add covering indexes on the columns rule predicates filter on.
  Most are already indexed (`contact_scores.score`,
  `consent.status`, `interactions.contact_id + occurred_at`); add
  ad-hoc ones for your common predicates.
- Materialize the most-evaluated segments (cache contact ids in the
  `segment_members` table; only re-evaluate when explicitly
  triggered).
- Run nightly evaluation off-peak.

### Member-change hooks

When `segments.evaluate` produces a non-empty delta:

- `on_segment_member_added(ctx, segment, contact, conn)` for each
  newly added contact.
- `on_segment_member_removed(ctx, segment, contact, conn)` for each
  removed.

Plug-ins can listen to these (the hook names exist in `KNOWN_HOOKS`
once you add them — they're a small extension to the v4 set). Use
them for "when someone enters segment X, kick off campaign Y" flows.

## Maximizing potential

1. **Segment-driven campaign engine.** Combine segments + plug-in
   member-change hooks + portal tokens + emails (via Resend or
   similar). When a contact enters "dormant-high-value", auto-issue
   a portal token and send the "we'd love to hear from you" email.
   Track outcomes by tagging.

2. **Score-driven segments.** "high intent AND high fit AND
   consented AND open deal" is a one-rule segment. Pin its widget
   to the dashboard. Sales has their priority list.

3. **Segments as A/B test cohorts.** Random-50% via a hash rule on
   `contacts.id` (e.g., `id_mod_2 = 0`). Treat one cohort, control
   the other. Track downstream behavior via `audit_log`.

4. **Composable segments.** Reference one segment from another's
   rule: `{"in_segment": {"slug": "consented-email"}}`. Build small
   reusable predicates and compose. (Not built yet — easy
   extension; would require a recursion limit + cycle detection.)

5. **Time-travel segments.** Evaluate a segment "as of date X" by
   adding a `as_of` parameter that filters joined facts on
   `ts < as_of`. Audit-log + interactions are append-only, so this
   is sound.

6. **Segment overlap analysis.** Build a report
   `segments_overlap` that, given segment A and B, returns
   `|A ∩ B|`, `|A \ B|`, `|B \ A|`. Useful for "did our high-intent
   segment also catch all our open-deal contacts?"

7. **Natural-language segment builder.** Wire a small LLM tool
   that turns "everyone in BC who consented to SMS and has no open
   tasks" into the rule JSON. The CRM doesn't care how the JSON
   was produced; rules are rules.

## Anti-patterns

- **Putting state in the rule.** Don't bake a date like
  `within_days: 45` and expect "45 days from now". The rule is
  re-evaluated against current state; the window slides correctly.
  If you want a frozen cohort, make it static.
- **Treating segment_members as a queue.** It's a membership set,
  not a worklist. If you want a worklist, use a tag or a tasks list.
- **Live evaluation everywhere.** Cheap on small DBs, expensive at
  scale. Default is pull; opt in to live where it matters.
- **Slug churn.** Slugs are referenced from cron jobs, plug-ins,
  external systems. Don't change a slug after it's used. Create a
  new segment, deprecate the old one.
- **Free-text rules.** Always store rules as the typed JSON tree.
  Don't accept a SQL fragment from the UI — that's a SQL injection
  vector.

## Where to look in code

- `backend/services/segments.py` — service + rule-to-SQL translator
- `migrations/0004_v2.sql` — segments + segment_members schema
- `agent_surface/cron.py` — nightly re-evaluation job
- `backend/services/reports.py` — segments power some report widgets

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
- [scoring.md](scoring.md)
- [segments.md](segments.md) **← you are here**
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
