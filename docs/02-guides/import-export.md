# Guide · Import + export

> Move bulk data into and out of the CRM via CSV. Covers the standard
> "we got a leads list from a conference" workflow and the equally
> important "give me everything we know about X for a client review."

## Context

CRMs live or die by the data they hold. The honest tax of running
any CRM is "how do you get data IN?" and "how do you get it OUT?"
This CRM treats both as first-class operations:

- **Import** via `python -m agent_surface.cli import` — chunked
  inserts through the service layer (full audit, validation,
  uniqueness, deduplication options).
- **Export** via `GET /api/export/{kind}.csv` and
  `crm-cli export ...` — joins entities so the CSV is useful
  immediately, not a sparse table requiring joins.

## Understanding

### Import

The import service (`backend/services/imports.py`) handles CSVs for:
- `contacts`
- `companies`

It reads the file, maps columns by header name to service-layer
fields, calls `contacts.create` / `companies.create` per row in
chunks of 500 inside a transaction. Each chunk commits or rolls back
atomically — a malformed row in a chunk aborts the chunk, not the
whole import.

Dedup behavior is controlled by `--on-duplicate`:
- `skip` — existing email/slug → row ignored
- `update` — existing record's fields overwritten from the CSV
- `merge` — call the duplicates service to merge timeline, tags,
  notes into the existing record

Every imported row produces a `audit_log` entry tagged
`surface='cli', action='contact.created'` (or `.updated`). Plug-ins
fire as if each row had been a manual API call.

### Export

Export endpoints (`backend/api.py:709+`) join useful fields and
stream the result. Supported kinds:

- `contacts.csv` — contact with company name resolved, tag list as
  semicolon-separated, primary consent state.
- `companies.csv` — company with contact count, latest interaction
  timestamp.
- `deals.csv` — deal with contact + company names, pipeline + stage
  names, value, status.
- `tasks.csv` — task with parent context (contact/company/deal),
  assignee email.
- `interactions.csv` — interaction with contact + company names.

The CSVs are UTF-8 with a BOM (so Excel opens them cleanly).

There's also `GET /api/reports/{name}.csv` — emits the report's
result as CSV. Useful for canned views like "pipeline_overview" or
"top_intent_now".

## Reason

**Why CSV and not JSON for import/export?**

- Every salesperson, marketer, and analyst speaks Excel.
- CSV is the lowest common denominator.
- The structured fields the CRM cares about (name, email, etc.) fit
  flat columns. JSON-nested data (custom fields, interests) goes
  into single columns as JSON strings.

For programmatic transfer between systems, prefer REST/MCP — they
preserve structure better. CSVs are for humans + spreadsheet tools.

**Why no API endpoint for import?**

A REST POST that uploads a large file invites timeout grief and
forces multipart parsing into the API layer. The CLI is the right
tool — it has direct DB access, can stream the file, and avoids
the HTTP-vs-batch impedance mismatch.

If you really need network import, write a small adapter that reads
a remote file, parses rows, and calls REST `POST /api/contacts` per
row with batching. That gives you exactly the right control.

**Why join-enriched CSVs and not raw rows?**

A CSV with `company_id=42` is useless out of the box. A CSV with
`company_name="Acme Roastery"` is immediately legible. The cost is
an N+1 join on export, which is fine at CRM-scale (<1M contacts).

**Why `--on-duplicate` choices?**

Real imports always include some overlap with existing data. Forcing
the user to pre-clean is impractical. The three modes cover the
common intents: ignore (default safe), overwrite (trust the new
data), merge (combine carefully).

## Result

After this guide you can:

- Bring a 10,000-row CSV into the CRM in under a minute.
- Export any entity to a usable spreadsheet for sharing or analysis.
- Decide the right dedup strategy for your situation.
- Reason about what side-effects (audit, webhook, plug-in) fire
  during bulk operations.

## Use case 1 — importing a leads list

You have `leads.csv`:

```
full_name,email,phone,title,company_domain,location,source
Maya Sato,maya@blueriver.media,+1604-555-0188,Marketing Director,blueriver.media,Toronto,conference-2026
Greg Johnson,greg@hammerbuild.example,+1778-555-0233,Owner,hammerbuild.example,Abbotsford,conference-2026
...
```

```bash
python -m agent_surface.cli import \
  --kind contacts \
  --path leads.csv \
  --on-duplicate skip
```

Output:

```json
{
  "ok": true,
  "kind": "contacts",
  "total": 312,
  "created": 287,
  "skipped_existing": 25,
  "errors": []
}
```

Verify:

```bash
python -m agent_surface.cli contact list --q conference-2026 --limit 5
```

The `source` column from your CSV landed in `contacts.source`. The
`company_domain` was resolved against `companies.domain` — if a
matching company existed, the contact's `company_id` was filled; if
not, a new company was created (with `source='import'`). Inspect
recent companies if curious.

### Re-running with update mode

If the original CSV had stale phone numbers and you have a corrected
file:

```bash
python -m agent_surface.cli import \
  --kind contacts \
  --path leads-corrected.csv \
  --on-duplicate update
```

Existing contacts with matching email get their fields overwritten.
Audit log records each update with full before/after JSON.

### What plug-ins see

The auto-tag-from-interactions plug-in does NOT fire on imports
(there are no interactions logged). But `on_contact_created` plug-ins
DO fire — 287 times if you imported 287 new contacts.

For large imports, this can cascade. If you have a plug-in that
hits an external API per contact, an import of 10k rows triggers
10k API calls. Either:

- Disable the plug-in temporarily during the import.
- Pass `--skip-plugins` (if supported by your import; planned
  feature).
- Write the plug-in defensively to detect bulk context (e.g., skip
  when `ctx.surface == 'cli' and ctx.request_id.startswith('bulk-')`).

## Use case 2 — exporting contacts for a sales review

```bash
curl -sH "Authorization: Bearer $KEY" \
  "http://localhost:8000/api/export/contacts.csv" > contacts.csv

# or via CLI
python -m agent_surface.cli export --kind contacts --out contacts.csv
```

Opens in Excel/Google Sheets with columns:

```
id, full_name, email, phone, title, company_name, location,
preferred_channel, source, tags, intent, fit, opportunity,
created_at, last_interaction_at
```

Tags are joined as semicolon-separated. Scores come from
`contact_scores`. `last_interaction_at` is the max `interactions.
occurred_at` for that contact.

### Filtering exports

The export endpoint accepts query params:

```
GET /api/export/contacts.csv?q=copper
GET /api/export/contacts.csv?company_id=3
GET /api/export/contacts.csv?tag=vip
```

Combinations apply as AND. Same params as the contacts list
endpoint.

### Exporting reports

```bash
curl -sH "Authorization: Bearer $KEY" \
  "http://localhost:8000/api/reports/pipeline_overview.csv" > pipeline.csv
```

The report's structured result is flattened into a CSV. Custom
reports just need to return a list-of-dicts; the CSV serializer
handles the rest.

## Operations

### Validating an import dry-run

```bash
python -m agent_surface.cli import \
  --kind contacts --path leads.csv --dry-run
```

Reads the file, validates every row, reports what WOULD happen, but
writes nothing. Use this on every import before doing it for real.

### Per-row error reporting

```json
{
  "errors": [
    {"row": 17, "email": "invalid@", "code": "VALIDATION_ERROR",
     "message": "email is not a valid address"},
    {"row": 42, "email": "maya@blueriver.media", "code": "CONTACT_EMAIL_EXISTS",
     "message": "Another active contact already has email..."}
  ]
}
```

Fix the CSV, rerun. The created-already rows will skip cleanly under
`--on-duplicate skip`.

### Large imports

For 100k+ rows:

- Run during off-hours; the import holds the writer lock during each
  chunk commit.
- Disable expensive plug-ins (LLM calls, Slack notifications).
- Use `--chunk-size 1000` (if supported) to amortize transaction
  overhead.
- Monitor `crm.db` size growth; WAL can balloon temporarily.

### Exporting all entities at once

A backup script:

```bash
KEY="..."
for kind in contacts companies deals tasks interactions; do
  curl -sH "Authorization: Bearer $KEY" \
    "http://localhost:8000/api/export/$kind.csv" > exports/$kind.csv
done
```

For "complete data dump including audit, schema versions, etc."
see [05-operations/backup-restore](../05-operations/backup-restore.md).

### Round-tripping

You CAN export → modify in Excel → re-import with `--on-duplicate
update`. Caveats:

- Spreadsheet auto-formatting destroys phone numbers (`+1 604-555-0188`
  becomes `1604555188` in scientific notation). Quote columns as
  text.
- Excel saves UTF-8 with BOM, which the importer handles.
- IDs in the CSV are advisory only — the importer matches on email
  (contacts) or slug (companies), not id.

## Fine-tuning

### Column mapping

By default the importer maps CSV columns to service fields by
identical name. To handle non-standard headers:

```bash
python -m agent_surface.cli import \
  --kind contacts --path leads.csv \
  --map '{"name":"full_name","work_email":"email"}'
```

(Wire `--map` into the parser if it isn't there yet.)

### Custom fields

The `custom_fields_json` column on contacts/companies takes any JSON.
The importer accepts:

```csv
full_name,email,custom_fields_json
Maya Sato,maya@..., "{""industry_subniche"":""indie media""}"
```

Note the doubled quotes — CSV-escaped JSON.

### Required vs optional

Contacts: at least one of full_name/first_name/last_name/email.
Companies: name is required.

Rows missing the requirements error with `VALIDATION_ERROR`. The row
is reported; the import continues.

### Encoding

- Input: UTF-8 (with or without BOM). Other encodings: convert
  first via `iconv`.
- Output: UTF-8 with BOM (Excel-friendly). Override with
  `?encoding=utf-8-nobom` if you're piping to a Unix-y tool.

### Streaming large exports

The export endpoints stream (the CSV is generated row-by-row, not
loaded into memory). A 1M-row export uses ~10 MB RAM regardless of
result size. The client can stream-process incrementally.

### Importing tags + consent

Currently the importer handles `contacts` and `companies`. Tags and
consent are derivable from columns:

- `tags` CSV column: semicolon-separated tag names. Importer auto-
  creates missing tags and attaches them.
- `consent_email`, `consent_phone`, `consent_sms` columns: values
  `granted`, `withdrawn`, or empty. Importer creates consent rows
  with `source='import'`.

(Wire these in `imports.py` if your version doesn't have them; the
shape is well-defined.)

## Maximizing potential

1. **Treat exports as your warehouse pipe.** Schedule a nightly
   export → load into BigQuery / Snowflake / DuckDB. The CRM stays
   small and operational; analytics happen on the warehouse.

2. **Use exports for client reports.** "Here's everything we know
   about your portfolio of accounts" as a single CSV is a more
   credible artifact than a slide deck.

3. **Use imports as the migration path FROM a competing CRM.**
   Export from HubSpot/Salesforce → map columns → import here. The
   service-layer enforces validation, so bad data is rejected at the
   door.

4. **Round-trip cohorts.** Export "high-intent contacts" → enrich
   externally (e.g., Clearbit) → import back with
   `--on-duplicate update`. The CRM stays in sync without an
   integration.

5. **CI-driven imports.** Have your marketing site dump form
   submissions into `s3://crm-import-staging/` daily. A cron job
   on the CRM host downloads and runs `crm-cli import`. Hours of
   wiring saved.

6. **Differential exports.** `?since=2026-05-01` returns only
   contacts changed after that date. Smaller files; incremental
   pipelines.

## Anti-patterns

- **Importing without `--dry-run` first.** Always. A typo in a
  column name silently drops half your data.
- **`--on-duplicate update` for newly purchased lead lists.** They
  often overwrite hand-curated fields with garbage. Use `skip` by
  default; review what skipped; fix those manually.
- **Exporting `crm.db` and "importing" it via sqlite tools.** That
  bypasses the service layer entirely. Use logical export (CSV /
  REST) for data transfer; use the SQLite backup API only for
  full-system backups within the SAME install.
- **Hand-editing the CSV header to match.** Use `--map` and keep
  the original CSV untouched. Now you can re-run the import
  reproducibly.
- **Importing via copy-paste in the UI.** There's no UI import
  page by design (would be a footgun for large files). CLI only.

## Where to look in code

- `backend/services/imports.py` — chunked import + dedup logic
- `backend/api.py:709` — export endpoints
- `agent_surface/cli.py:447-481` — CLI import/export commands

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
- [install.md](install.md)
- [first-contact.md](first-contact.md)
- [your-first-pipeline.md](your-first-pipeline.md)
- [import-export.md](import-export.md) **← you are here**
- [deploying.md](deploying.md)

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
