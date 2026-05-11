# Prompts

Reusable prompt templates for external AI agent harnesses. **The CRM never loads, executes, or depends on these at runtime.** They are reference material for agents (or humans configuring agents) — nothing here is LLM glue inside the CRM itself.

A future agent harness might read this directory to bootstrap a "CRM operator" persona, or to define standardized message-drafting templates that get rendered with contact data the agent fetched separately.

At v0 the directory is empty. The architectural promise — "no AI inside the CRM" — is enforced by this folder being inert.
