# Connectors

Adapters for ingesting data from outside the CRM (CSV files, webhook receivers, third-party APIs, mail clients, etc.). At v0 this folder is intentionally empty.

When implemented (v1+), a connector will:
- Live as `agent_surface/connectors/<name>.py`
- Expose an entry point: `def ingest(args) -> dict`
- Call only the service layer (`backend.services.*`) — never raw SQL
- Be invokable via CLI: `python -m agent_surface.cli connector run <name> [args]`

Do not add real connector code here until the contract is in `docs/connectors.md`.
