# Concept · Portals

> Self-service URLs that let external contacts see a curated view of
> their own data inside the CRM, without logging in.

## Context

Most CRMs treat external contacts as objects, not subjects. The
contact is a row a salesperson edits, not a person the CRM speaks to.
When the relationship requires interaction — sharing a proposal,
collecting a signature, letting a client see their open tickets — the
salesperson has to copy data out to a separate tool (DocuSign, a
Notion page, an email thread) which then drifts out of sync with
the CRM.

Portals close that loop. A salesperson issues a portal token for a
contact. The token resolves to a URL like
`https://crm.example.com/portal/abc123…`. The contact opens it (no
sign-in) and sees a page tailored to them: their profile, their open
deals, their non-private notes, their activity. Optionally, write
access for specific actions (sign a doc, leave feedback, request a
meeting).

Portals are how the CRM stays the source of truth when external
people need to see what's about them.

## Understanding

One table:

```
portal_tokens
  id              INTEGER PK
  token           TEXT UNIQUE       random URL-safe secret
  contact_id      INTEGER  → contacts(id) ON DELETE CASCADE
  scope           TEXT              'client' | 'applicant' | 'sponsor' | 'member' | ...
  label           TEXT              human-readable why it was issued
  expires_at      INTEGER           unix seconds; NULL = never
  revoked_at      INTEGER           NULL until revoked
  last_used_at    INTEGER           first/last hit
  created_at      INTEGER
  created_by      INTEGER  → users(id)
```

One service: `backend/services/portals.py`.

```python
portals.issue(ctx, contact_id, scope, label=None, expires_in_days=30)
portals.lookup(token) -> {contact, scope, scope_data}   # NO ctx; public
portals.revoke(ctx, token_id)
portals.list_for_contact(ctx, contact_id)
```

One public route: `GET /portal/{token}` in `backend/main.py`. The
route:

1. Calls `portals.lookup(token)`. Raises if not found, expired, or
   revoked. Updates `last_used_at`.
2. Renders `ui/portal.html` with a scope-specific data dict.
3. Audits the hit (`portal_token.used` action on first use; ignored
   thereafter to avoid audit-spam from page reloads — controllable
   via `PORTAL_AUDIT_EVERY_HIT=1`).

## Reason

**Why token URLs and not "magic links to a real login"?**

- **No password to forget.** External contacts may interact with the
  CRM once a quarter. Asking them to remember a password loses every
  time.
- **Friction-free.** The point of a portal is for the contact to
  see/do one thing right now. Add a sign-in form and half won't
  bother.
- **Bounded exposure.** A portal token has scope (e.g.,
  `scope="client"` sees deals + non-private notes), an expiry, and a
  revoke. It's not a full account.
- **No account proliferation.** External contacts aren't users.
  Treating them as such bloats the users table with rarely-used
  accounts.

**Why scope-driven views?**

A portal for an applicant should look different from one for a
sponsor. The scope decides:

- Which sections appear on the portal page.
- Which write actions are available (e.g., "upload a doc" vs "leave
  feedback").
- Which notes are visible (always `visibility != 'private'`; some
  scopes also hide `team` notes).
- Whether the portal page surfaces deal status (clients yes, applicants
  no).

The scope set is intentionally small (4-6 entries). Most teams use 2.

**Why audit the first hit and not every page load?**

A real human refreshes a portal page 20 times in a session. 20 audit
rows per session per contact would drown out the actual interesting
events. We audit the FIRST hit (the contact opened the portal at
all — informative) and ignore subsequent ones unless
`PORTAL_AUDIT_EVERY_HIT` is set. Important POST actions inside the
portal are always audited.

## Result

What portals give you:

- A frictionless way to share a contact's view of their CRM record
  with that contact.
- Scoped, expirable, revokable access without bloating the users
  table.
- An audit signal when the contact engages (`portal_token.used` row
  with timestamp).
- A natural home for self-service writes: client feedback, doc
  uploads, meeting requests, signatures.
- A real-world stand-in for "we'll send you a PDF" — and far harder
  to lose track of.

## Use case 1 — sharing a deal with a client

Sara, a salesperson, is closing a deal with Acme. She wants Acme's
buyer Greg to see the proposal + timeline + her notes (excluding
internal-only ones).

1. On Greg's contact page → Portal access section → Issue token.
   Scope=`client`, expires=60 days, label="Acme proposal Q2".
2. The CRM returns the URL:
   `https://crm.example.com/portal/aHc8B1...`. Audit row.
3. Sara emails Greg the link.
4. Greg opens it. The portal page shows Greg's profile (read-only),
   his open deals, the team-visible notes Sara left, and a "Reply
   to Sara" form. Audit row: `portal_token.used`.
5. Greg uses the reply form. The portal route writes an `interaction`
   (`type=note_system`, `source=portal`) to the timeline. Audit row.
6. Sara sees Greg's reply in the contact timeline alongside her own
   notes.

When the deal closes (or expires), Sara revokes the token. The next
hit on that URL gets a "this portal is no longer active" page.

## Use case 2 — applicant scope for a grant

Aurora runs an artist grant. Applicants get a portal showing only
their application status (no other applicants, no internal scoring).

1. Programmatically (via API) for every applicant:
   ```bash
   curl -X POST http://localhost:8000/api/contacts/{id}/portal-tokens \
     -H "Authorization: Bearer <key>" \
     -d '{"scope":"applicant","label":"Grant 2026 Q4","expires_in_days":120}'
   ```
2. Email the URL to each applicant.
3. The applicant portal renders a scope-`applicant` view: profile,
   application status, latest official communications, and a
   "withdraw" button.
4. The "withdraw" button POSTs to a portal-scoped endpoint that calls
   `applications.withdraw(ctx_for_portal, application_id)`. The
   `ctx_for_portal` is a system context with the contact's identity
   attached as `acting_on_behalf_of` — so audit tells you the right
   person did it, but the system mediated.

## Operations

### Issuing

UI: contact page → Portal access card → New token.

REST:
```bash
curl -X POST http://localhost:8000/api/contacts/{id}/portal-tokens \
  -H "Authorization: Bearer <key>" \
  -d '{"scope":"client","label":"Acme proposal","expires_in_days":60}'
# returns {"token":"...","url":"https://.../portal/..."}
```

CLI:
```bash
python -m agent_surface.cli portal issue \
  --contact-id 5 --scope client --label "Acme proposal" --expires-in-days 60
```

MCP: `issue_portal_token(contact_id=5, scope="client", ...)`.

### Listing

UI: contact page → Portal access card.

REST: `GET /api/contacts/{id}/portal-tokens`.

CLI: `portal list --contact-id 5`.

### Revoking

UI: Portal access card → Revoke button per token.

REST: `POST /api/portal-tokens/{id}/revoke`.

CLI: `portal revoke --id 12`.

Revoke is irreversible. The token row stays (audit trail), but
`revoked_at` is set; `portals.lookup` raises.

### Token format

Tokens are 32-byte URL-safe base64 strings, generated by
`secrets.token_urlsafe(32)`. ~256 bits of entropy. Not guessable.

### URL hardening

The portal route is rate-limited by token at the FastAPI middleware
layer (default: 60 hits/min/token). Beyond that, requests get 429.
Tune via `PORTAL_RATE_LIMIT_PER_MIN`.

For tokens issued in volume (e.g., 5000 applicants), pre-warm the
DB by indexing the `token` column (already PRIMARY KEY-ish via
UNIQUE).

## Fine-tuning

### Per-scope view templates

Add scopes by editing:

1. `backend/services/portals.py:_RENDER_BY_SCOPE` map — maps scope
   string to a render function.
2. `ui/portal.html` — main template; usually scope-specific blocks
   are conditional `{% if scope == "..." %}` chunks.
3. The `scope` enum in the migration if you want DB-level
   validation (optional).

### Portal-side writes

By default, portals are read-mostly. To enable a write action:

1. Add a route `POST /portal/{token}/<action>` in `main.py`.
2. The handler calls `portals.lookup(token)` to identify the contact
   and verify scope.
3. Calls a service function with a ctx like
   `ServiceContext(user_id=PORTAL_USER, surface="portal", scope="write",
                    acting_on_behalf_of=contact_id)`.
4. The service writes; audit rows include the contact as the actor.

### Portal-specific consent

A portal hit can trigger an implicit consent record (e.g., the
contact opened a portal we sent → arguably they consented to that
channel). Customize via plug-in:

```python
def on_portal_used(ctx, token, conn):
    contact_id = ctx.acting_on_behalf_of
    consent.record(ctx, contact_id, channel="email",
                   status="granted", source="portal_first_use",
                   conn=conn)
```

(`on_portal_used` is a planned hook — add to `KNOWN_HOOKS` when you
need it.)

### Custom expiry policies

By scope:

```python
_DEFAULT_EXPIRY_DAYS_BY_SCOPE = {
    "client":     90,
    "applicant":  120,
    "sponsor":    365,
    "member":     None,    # never expire by default
}
```

`portals.issue` reads this when `expires_in_days` isn't passed.

## Maximizing potential

1. **Portal page as a real product surface.** Style it like part of
   your brand, not "an admin link." A polished portal makes the CRM
   feel like a Stripe / Linear-quality system to outside contacts.

2. **Portal analytics.** Beyond first-hit audit, track portal page
   views via `audit_log` rows (with `PORTAL_AUDIT_EVERY_HIT=1`).
   You get "did this prospect actually read the proposal?" as data.

3. **Per-contact portal customization.** Each contact can have a
   `portal_preferences_json` field on their row — color, layout
   choices, default-language. The portal route reads it.

4. **Multi-token portals.** A single contact may have multiple active
   portal tokens (proposal, contract, support). Each renders its
   scope; revoke independently.

5. **QR code generation.** Generate a QR for each token. Tape it to
   a doc or invitation. Easy in-person discovery without forwarding
   URLs.

6. **Portal events as inbound.** When the contact submits the
   portal's reply form, treat it as an inbound event (same
   `inbound_events` table). Triggers consent, scoring, plug-ins.

7. **Portals as embedded widgets.** Iframe the portal page into a
   partner's website. The partner's branding outside; the CRM-
   sourced truth inside. Useful for white-label deployments.

## Anti-patterns

- **Long-lived tokens with no expiry.** A 5-year-old token is a
  forgotten security hole. Default expiry should be measured in
  weeks/months, not years.
- **Embedding tokens in publicly-shared URLs.** A portal token is
  effectively a password. Don't put it in a tweet, a public deck,
  or an indexable page.
- **Sharing one token among many people.** "Forwarded the link to
  the team" — each forwarded recipient gets the same view as the
  intended contact. Issue separate tokens per recipient.
- **Showing private notes in any portal scope.** Always filter
  `WHERE visibility != 'private'`.
- **Making portal-side writes bypass the service layer.** Same rule
  as everywhere else — go through services.

## Where to look in code

- `backend/services/portals.py` — issue/lookup/revoke
- `backend/main.py:1692` — `GET /portal/{token}` route
- `ui/portal.html` — render template
- `migrations/0005_v3.sql` — `portal_tokens` schema

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
- [service-layer.md](service-layer.md)
- [service-context.md](service-context.md)
- [audit-and-webhooks.md](audit-and-webhooks.md)
- [plugins.md](plugins.md)
- [scoring.md](scoring.md)
- [segments.md](segments.md)
- [portals.md](portals.md) **← you are here**
- [inbound.md](inbound.md)
- [search.md](search.md)

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
