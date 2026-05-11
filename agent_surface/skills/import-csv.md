---
verb: import
noun: csv
canonical_transport: cli
mcp_tool: (none — CLI only)
cli: import
rest: (none — CLI only)
required_scope: admin (CLI runs as the resolved user; effectively local admin)
related: ["export-csv"]
---

# Import a CSV

Bulk-imports contacts or companies from a CSV file. CLI-only by
design — large multipart uploads aren't a fit for the REST API.

## Required fields
- `--kind` — `contacts` or `companies`
- `--path` — local path to the CSV

## Optional fields
- `--on-duplicate` — `skip` (default) | `update` | `merge`
- `--dry-run` — validate without writing
- `--chunk-size` — rows per transaction (default 500)

## Example

```bash
python -m agent_surface.cli import \
  --kind contacts \
  --path leads.csv \
  --on-duplicate skip \
  --dry-run                 # validate first

python -m agent_surface.cli import \
  --kind contacts \
  --path leads.csv \
  --on-duplicate skip       # for real
```

## CSV format

UTF-8 (with or without BOM). Header row required. Columns map by
header name to service-layer fields.

```
full_name,email,phone,title,company_domain,location,source
Maya Sato,maya@blueriver.media,+1604-555-0188,Marketing Director,blueriver.media,Toronto,conference-2026
...
```

Unknown columns are ignored. Missing columns are treated as null.

## Dedup modes

| mode | meaning |
|------|---------|
| `skip` | Existing email/slug → row ignored |
| `update` | Existing entity's fields overwritten from CSV |
| `merge` | Call duplicates service to merge timeline, tags, notes |

## Per-row error reporting

Errors don't abort the whole import; the bad rows are reported:

```json
{
  "errors": [
    {"row": 17, "email": "invalid@", "code": "VALIDATION_ERROR",
     "message": "..."},
    {"row": 42, "email": "maya@blueriver.media", "code": "CONTACT_EMAIL_EXISTS",
     "message": "..."}
  ]
}
```

Fix the CSV and re-run with `--on-duplicate skip` to skip the
already-imported ones.

## Plug-in side effects

Each created/updated contact fires `on_contact_created` /
`on_contact_updated` per row. For 10k-row imports with LLM plug-ins,
disable expensive plug-ins first.

## See also

For a full walkthrough including dry-run, large-import advice, and
round-tripping with Excel: [docs/02-guides/import-export.md](../../docs/02-guides/import-export.md).
