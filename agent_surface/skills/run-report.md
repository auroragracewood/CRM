---
verb: run
noun: report
canonical_transport: rest
mcp_tool: run_report
cli: report run
rest: GET /api/reports/{name}
required_scope: read
related: ["evaluate-segment"]
---

# Run a report

Runs a pre-built report function from `services/reports.py:CATALOG`
and returns the result as JSON. Add `.csv` to the path for a CSV
response.

## Required fields
- `name` (string; from the catalog)

## Optional fields
- `params` (dict) — report-specific parameters

## Available reports (default catalog)

| name | summary |
|------|---------|
| `pipeline_overview` | per-pipeline counts + value totals |
| `task_load` | open tasks per assignee, with overdue counts |
| `top_intent_now` | top N contacts by intent score |
| `dormant_high_value` | top N dormant high-opportunity contacts |
| `consent_coverage` | percentage of contacts with consent recorded per channel |
| `recent_activity` | interactions logged in the last N days |
| `won_lost_summary` | won/lost deals in a window with values |

## Example (REST)

```bash
curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/reports/pipeline_overview"
# {"ok":true,"rows":[...]}

curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/reports/recent_activity?days=14"

curl -sH "Authorization: Bearer $KEY" \
  "$BASE/api/reports/recent_activity.csv?days=14" > activity.csv
```

## Example (CLI)

```bash
python -m agent_surface.cli report list
python -m agent_surface.cli report run --name pipeline_overview
python -m agent_surface.cli report run --name recent_activity \
  --params '{"days":14}'
```

## Common errors

| code | meaning |
|------|---------|
| `REPORT_NOT_FOUND` | name not in catalog |
| `REPORT_PARAMS_INVALID` | bad params for the chosen report |

## Audit + webhooks

Reports are reads — no audit row, no webhook.

## Adding new reports

Reports are pure functions in `services/reports.py`. Add an entry to
`CATALOG` with `{"name", "fn", "params"}`. New reports appear in
`/api/reports` and the UI's Reports page immediately.

See [docs/06-development/adding-an-entity.md](../../docs/06-development/adding-an-entity.md)
for the workflow when a new entity also needs reports.
