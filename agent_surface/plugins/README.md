# Plugins

Drop-in Python modules that extend the CRM's service layer. At v0 this folder is intentionally empty — only the directory + this README exists. Real plugin loading lands in v3 once the surface is stable enough to commit to a contract.

When implemented, a plugin will:
- Live as `agent_surface/plugins/<name>.py`
- Register hooks into service-layer lifecycle (`on_contact_created`, etc.)
- Be loaded by the FastAPI startup hook
- Run in-process (no IPC) with the same SQLite handle the rest of the app uses

Do not add real plugin code here until the contract is in `docs/plugins.md`.
