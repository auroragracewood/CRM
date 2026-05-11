# Recipe · Agent workflows

> Patterns for AI agents driving the CRM through MCP/REST. These are
> the proven shapes — not a list of every possible thing an agent
> could do.

## Context

An "AI agent" here means an external program (Claude Code, an OpenAI-
function-call loop, a custom orchestrator) that calls the CRM's MCP
or REST surface. The CRM is the tool, not the brain. The agent has
goals; the CRM has functions.

Three patterns recur across most agent workflows:

1. **Read-decide-write** — agent reads state, picks an action, writes.
2. **Idempotent batch** — agent processes a list, may retry, must not
   double-write.
3. **Cross-entity composition** — agent stitches contact + interaction
   + deal + task into one logical operation.

This recipe shows clean implementations of each, with the trade-offs
explicit.

## Pattern 1 — Read-decide-write

### Use case

Agent is asked: "Find the contact 'Maya Sato', see if we've talked in
the last 30 days, and if not, schedule a check-in task for the
account owner."

### Naive (broken) version

```python
results = find_contacts(q="Maya Sato")
maya = results["items"][0]
last = get_timeline(contact_id=maya["id"], limit=1)["items"]
if not last or (NOW - last[0]["occurred_at"]) > 30 * 86400:
    create_task(title="Check in with Maya", contact_id=maya["id"], ...)
```

What's broken:
- `find_contacts` is ambiguous; "Maya Sato" might match many people.
- No idempotency key on `create_task` — re-running creates duplicates.
- The check uses `NOW` from the agent, not the CRM, which can drift.
- No request_id stamping — debugging the chain later is hard.

### Robust version

```python
REQ = f"checkin-maya-{int(time.time())}"

results = find_contacts(q="maya@blueriver.media", limit=1)   # by EMAIL
items = results["items"]
if not items:
    return {"ok": False, "reason": "contact_not_found"}
maya = items[0]

# Check recency on CRM's clock by asking for interactions since cutoff
cutoff = int(time.time()) - 30 * 86400
recent = get_timeline(contact_id=maya["id"], limit=1)["items"]
already_active = recent and recent[0]["occurred_at"] >= cutoff
if already_active:
    return {"ok": True, "reason": "already_active",
            "last_interaction_id": recent[0]["id"]}

task = create_task(
    title="Check in with Maya",
    contact_id=maya["id"],
    priority="normal",
    due_date=int(time.time()) + 3 * 86400,
    idempotency_key=REQ,   # ← key
)
return {"ok": True, "task_id": task["task"]["id"], "request_id": REQ}
```

Improvements:
- Lookup by email (deterministic).
- Idempotency key — re-running returns the same task, no duplicates.
- Explicit reasoning in the return value (the agent's caller can see
  what was decided and why).
- request_id stamped consistently.

## Pattern 2 — Idempotent batch

### Use case

Agent has a list of 50 contacts to enrich (e.g., from a CSV upload
not run through the importer). For each: find or create, then attach
a tag.

### Robust version

```python
RUN_ID = f"enrich-batch-{date.today().isoformat()}"

for i, row in enumerate(rows):
    key = f"{RUN_ID}-{row['email']}"
    try:
        existing = find_contacts(q=row["email"], limit=1)["items"]
        if existing:
            contact = existing[0]
        else:
            contact = create_contact(
                name=row["name"], email=row["email"],
                idempotency_key=key,
            )["contact"]

        tag = create_tag(name=row["category"], scope="contact",
                         idempotency_key=f"{key}-tag")
        # create_tag returns existing tag if name exists (idempotent service)
        tag_contact(contact_id=contact["id"],
                    tag_id=tag.get("tag", tag).get("id"))
    except ServiceError as e:
        # Log + continue; do NOT abort the batch
        log_error(row, e)
```

The agent NEVER catches `IDEMPOTENT_REPLAY` — it's a success signal
(the server is returning a cached result). The agent stops only on
unexpected errors.

For very large batches, the agent should report progress every N
items and write a row to a small `agent_runs` table (or to its own
log) so the operator can resume.

## Pattern 3 — Cross-entity composition

### Use case

Agent is asked: "Onboard Acme. Create the company, add the primary
contact, log an intro meeting, open a deal, and assign three tasks."

### Robust version

```python
REQ = f"onboard-{client_slug}-{date.today().isoformat()}"

# Idempotency: re-running this whole flow yields the same artifacts
def k(suffix):  return f"{REQ}-{suffix}"

company = create_company(
    name="Acme Roastery", domain="acme.coffee",
    industry="food & beverage",
    idempotency_key=k("company"),
)["company"]

contact = create_contact(
    name="Sara Patel", email="sara@acme.coffee",
    title="Brand Manager", company_id=company["id"],
    idempotency_key=k("contact"),
)["contact"]

log_interaction(
    type="meeting", contact_id=contact["id"],
    title="Intro meeting", body="...",
    idempotency_key=k("intro"),
)

# Suppose pipeline 1 stage_ids = [1..6] (sales template)
deal = create_deal(
    title="Acme cafe rebrand", pipeline_id=1, stage_id=2,
    contact_id=contact["id"], company_id=company["id"],
    value_cents=1_800_000, currency="cad", probability=40,
    idempotency_key=k("deal"),
)["deal"]

for title, days in [
    ("Send proposal draft", 3),
    ("Prepare mood board",  7),
    ("Follow up call",     14),
]:
    create_task(
        title=title, contact_id=contact["id"], deal_id=deal["id"],
        priority="normal", due_date=int(time.time()) + days * 86400,
        idempotency_key=k(f"task-{title}"),
    )

return {
    "ok": True, "request_id": REQ,
    "company_id": company["id"], "contact_id": contact["id"],
    "deal_id": deal["id"],
}
```

The full chain has one request_id. Reading `audit_log WHERE request_id
= '<REQ>'` returns the entire onboarding in order. Re-running is a
no-op (everything is idempotent). Failure mid-way leaves a partial
state — but the agent's next run picks up where it left off thanks to
the keys.

## Operations

### Choosing transport

- **REST** for stateless agents that run in the cloud and operate
  remotely. Good for high-volume batch jobs.
- **MCP** for agents that have local access to the box (Claude Code,
  Cursor, custom orchestrators). Lower latency, no key juggling.
- **CLI** for human-in-the-loop. You're not really an "agent" if a
  human is running the commands; but the CLI is the right interface
  during incident response.

Mixing is fine — an MCP agent can prepare a payload then shell out
to CLI for the actual write if it wants the audit row tagged
`surface=cli`.

### Auth

- REST: bearer key. Generate a per-agent key. Revoke when the agent
  is rotated.
- MCP: process-level identity. Set `CRM_AS_EMAIL=agent@local`. Audit
  attribute everything to that user.
- CLI: same.

### Error handling

Always distinguish:

- `VALIDATION_ERROR` — your payload is wrong; fix and retry.
- `FORBIDDEN` — your scope is wrong; escalate or quit.
- `*_NOT_FOUND` — the object doesn't exist; check your inputs.
- `*_EXISTS` — uniqueness collision; either use the existing object
  or change your payload.
- `INTERNAL_ERROR` — bug in the CRM; surface to operator; don't
  retry.
- `IDEMPOTENT_REPLAY` — success; the server is just returning a
  cached answer.

### Rate limits

The CRM has per-key rate limits (default: 1000 writes/hour, 10000
reads/hour). Agents that batch 1000+ ops need to either:
- Spread across multiple keys, OR
- Pause when hitting 429, OR
- Use the `imports` service via CLI (no rate limit; designed for
  bulk).

## Fine-tuning

### Pre-flight validation

Before a heavy operation, call cheap reads to verify your assumptions:

```python
# Confirm the pipeline + stages exist
pipeline = get_pipeline(pipeline_id=1)
if not pipeline.get("pipeline"):
    raise RuntimeError("Pipeline 1 not found; check setup")

stage_ids = [s["id"] for s in pipeline["pipeline"]["stages"]]
if 2 not in stage_ids:
    raise RuntimeError("Stage 2 missing")
```

### Caching at the agent

Within one agent invocation, cache lookups:

```python
_company_cache = {}
def _resolve_company(domain):
    if domain in _company_cache: return _company_cache[domain]
    res = find_companies(q=domain, limit=1)["items"]
    co = res[0] if res else create_company(name=domain, domain=domain)["company"]
    _company_cache[domain] = co
    return co
```

Don't cache across runs unless you've thought about TTL.

### Stamping every call

Build a small wrapper:

```python
class Crm:
    def __init__(self, key, base, run_id):
        self.key, self.base, self.run_id = key, base, run_id

    def post(self, path, body, idem=None):
        h = {"Authorization": f"Bearer {self.key}",
             "X-Request-Id": self.run_id,
             "Content-Type": "application/json"}
        if idem: h["Idempotency-Key"] = idem
        return requests.post(f"{self.base}{path}", json=body, headers=h, timeout=10)
```

Every call from the agent now carries request_id + auth + idem.

### Defensive scope

Issue keys with the narrowest scope the agent needs. An agent that
only reads gets `scope=read`. The audit log shows your agents'
permissions explicitly.

## Maximizing potential

1. **Agents that explain.** After each operation, the agent fetches
   the audit row for its `request_id` and includes a summary in its
   response: "I created 3 audit rows for this onboarding: company,
   contact, deal." Builds operator trust.

2. **Agents that propose-then-apply.** First call with
   `dry_run=true` (a flag many services can support) — returns "what
   would happen." Operator confirms; second call commits. Lower
   risk for high-stakes operations.

3. **Agents that escalate.** When `VALIDATION_ERROR` or `FORBIDDEN`
   hits, the agent doesn't silently retry — it writes a row to a
   `agent_escalations` table (or DMs Slack) with the failing
   payload + request_id. Humans handle the hard ones.

4. **Agents that learn from audit.** Periodically read recent audit
   rows for `surface='ui'` to see what humans are doing manually.
   Propose automation candidates.

5. **Multi-agent orchestration.** One agent reads inbox emails and
   creates contact + interaction. Another scores contacts. A third
   runs revival passes. Each agent has its own narrow scope; they
   coordinate via the CRM's data, not a side channel.

6. **Agents that compose plug-ins.** A plug-in can call services
   that fire other plug-ins. An agent kicks off the chain with one
   call; the CRM cascades. The agent doesn't need to know all the
   downstream effects.

## Anti-patterns

- **Polling.** Don't `find_contacts` every second to see if
  something changed. Subscribe to webhooks instead, or use audit_log
  as your stream (poll by id, not by repeated scans).
- **No request_id.** You'll regret it the first time you have to
  debug.
- **No idempotency.** Retry is normal; double-write is a bug.
- **Hardcoding ids.** Stage ids, pipeline ids, tag ids may differ
  per install. Look them up by name/slug; only USE them inside the
  current run.
- **Mixing agent identity with user identity.** Each agent gets its
  own user record (and its own API key). Don't use the founder's
  admin key for automation — revoke + audit gets messy.
- **Treating the CRM as eventually-consistent.** It isn't. After
  the service call returns, the change is durable AND visible to
  every subsequent call. No "wait 30 seconds for replication."

## Where to look in code

- `backend/api.py` — REST contract every agent sees
- `agent_surface/mcp_server.py` — MCP tools
- `backend/services/contacts.py` etc. — what the agent ultimately
  invokes
- `backend/audit.py` — what gets logged so you can debug

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
- [dormant-revival.md](dormant-revival.md)
- [agent-workflows.md](agent-workflows.md) **← you are here**

**05 — Operations** (`docs/05-operations/`)
- [backup-restore.md](../05-operations/backup-restore.md)
- [migrations.md](../05-operations/migrations.md)

**06 — Development** (`docs/06-development/`)
- [adding-an-entity.md](../06-development/adding-an-entity.md)
- [writing-a-plugin.md](../06-development/writing-a-plugin.md)
- [writing-a-skill.md](../06-development/writing-a-skill.md)

**07 — Troubleshooting** (`docs/07-troubleshooting/`)
- [error-codes.md](../07-troubleshooting/error-codes.md)
