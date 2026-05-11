---
verb: export
noun: csv
canonical_transport: rest
mcp_tool: (none — REST or CLI)
cli: export
rest: GET /api/export/{kind}.csv
required_scope: read
related: ["import-csv"]
---

# Export to CSV

Exports an entity or report as a CSV file. Join-enriched (e.g.,
contacts include resolved company name + tags + scores).

## Required fields
- `kind` — entity to export

## Supported kinds

| kind | columns |
|------|---------|
| `contacts` | full_name, email, phone, title, company_name, location, source, tags, intent, fit, opportunity, ... |
| `companies` | name, domain, industry, location, contact_count, last_interaction_at |
| `deals` | title, contact_name, company_name, pipeline_name, stage_name, value, status, ... |
| `tasks` | title, assignee, contact_name, deal_title, priority, status, due_date |
| `interactions` | type, title, contact_name, company_name, channel, occurred_at |

## Optional query params (REST)

- `q` — search query
- `company_id`, `tag`, etc. — filter as on the list endpoint
- `since=YYYY-MM-DD` — only rows changed since this date (when supported)

## Example (REST)

```bash
curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/export/contacts.csv?tag=vip" > vip-contacts.csv

curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/export/deals.csv?pipeline_id=1" > q4-deals.csv
```

## Example (CLI)

```bash
python -m agent_surface.cli export --kind contacts --out contacts.csv
python -m agent_surface.cli export --kind deals --out deals.csv
```

## Encoding

UTF-8 with BOM by default (Excel-friendly). Override with
`?encoding=utf-8-nobom` when piping to Unix tools.

## Reports as CSV

```bash
curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/reports/pipeline_overview.csv" > pipeline.csv
```

Any report in the catalog can be exported as CSV by appending
`.csv` to the path. See [run-report](run-report.md).

## Streaming

Exports stream — the CSV is generated row-by-row, not loaded into
memory. A 1M-row export uses ~10 MB RAM on the server regardless of
output size.

## Common errors

| code | meaning |
|------|---------|
| `FORBIDDEN` | scope insufficient |
| `VALIDATION_ERROR` | unknown kind |

## Audit

Exports are reads — no audit row. If you need to track who exported
what, add a per-export audit row via a plug-in listening to
`on_export_run` (planned hook).
