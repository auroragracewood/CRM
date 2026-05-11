# Skills

Markdown files in this directory describe CRM operations to an external AI agent harness (Claude Code, OpenClaw, Codex, Hermes, etc.). Each skill is self-contained: an agent reading the markdown alone should be able to invoke the right tool with the right arguments.

## File format

Each skill starts with YAML frontmatter:

```
---
name: short-kebab-name
description: One-line summary of what this skill does and when to use it.
---
```

The body explains: when to use, when NOT to use, how to invoke via MCP / REST / CLI, required arguments, error codes, and any tips.

## Available skills (v0)

- `create-contact.md` — Add a new person record
- `find-contact.md` — Search contacts by name/email
- `log-interaction.md` — Record a timeline event (email, call, meeting, etc.)
- `add-note.md` — Add a visibility-scoped human note
- `tag-contact.md` — Apply a tag to a contact

More land as features ship in v1+.
