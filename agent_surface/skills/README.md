# Skills

Markdown files in this directory describe CRM operations to an
external AI agent harness (Claude Code, OpenClaw, Codex, custom
orchestrators). Each skill is self-contained — an agent reading the
markdown alone should be able to invoke the right tool with the
right arguments.

For the meta-guide on writing more skills, see
[docs/06-development/writing-a-skill.md](../../docs/06-development/writing-a-skill.md).

## Catalog (v4.1)

### Contacts
- [create-contact.md](create-contact.md)
- [find-contact.md](find-contact.md)
- [update-contact.md](update-contact.md)
- [delete-contact.md](delete-contact.md)
- [tag-contact.md](tag-contact.md)

### Companies
- [create-company.md](create-company.md)

### Activity
- [log-interaction.md](log-interaction.md)
- [add-note.md](add-note.md)
- [record-consent.md](record-consent.md)

### Pipelines + deals
- [create-pipeline.md](create-pipeline.md)
- [create-deal.md](create-deal.md)
- [move-deal-stage.md](move-deal-stage.md)

### Tasks
- [create-task.md](create-task.md)
- [complete-task.md](complete-task.md)

### Forms + inbound
- [build-form.md](build-form.md)
- [register-inbound-endpoint.md](register-inbound-endpoint.md)

### Scoring + segments + reports
- [score-contact.md](score-contact.md)
- [evaluate-segment.md](evaluate-segment.md)
- [run-report.md](run-report.md)

### Portals
- [issue-portal-token.md](issue-portal-token.md)

### Bulk + maintenance
- [import-csv.md](import-csv.md)
- [export-csv.md](export-csv.md)
- [merge-duplicates.md](merge-duplicates.md)
- [backup-database.md](backup-database.md)

## File format

Each skill starts with YAML frontmatter:

```yaml
---
verb: create
noun: deal
canonical_transport: rest
mcp_tool: create_deal
cli: deal create
rest: POST /api/deals
required_scope: write
related: ["move-deal-stage", "find-deal"]
---
```

The body explains: when to use, when NOT to use, how to invoke via
MCP / REST / CLI, required arguments, error codes, and tips.

## Naming convention

Files are `<verb>-<noun>.md`. Lowercase, hyphenated. Action-shaped.
Predictable filenames let agents guess paths before listing the
directory.
