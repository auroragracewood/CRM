# Concept · Search

> SQLite FTS5 full-text search across contacts, companies,
> interactions, and notes — kept in sync by triggers, served as one
> ranked result list, with private notes excluded by design.

## Context

A CRM that can't answer "find the conversation where someone mentioned
copper signage" is a CRM you can't trust to remember things. Salespeople
won't enter detail if they can't get it back; the CRM degrades into a
phonebook.

Naive solutions:

- `LIKE '%copper%'` across every text column. Slow at scale, can't
  rank by relevance, misspellings kill it.
- A separate search service (Elasticsearch, Meilisearch). Operational
  weight far exceeds the value at single-machine scale.
- A nightly cron that builds an inverted index. Hours-stale.

SQLite has FTS5 built in. It's a virtual-table extension that does
real BM25 ranking, stem-aware tokenization, and incremental indexing
via triggers. We use it for cross-entity search with a single ranked
output.

## Understanding

One FTS5 virtual table:

```
search_index(kind, ref, title, body,
             tokenize='porter unicode61')
```

Columns:
- `kind` — one of `contact`, `company`, `interaction`, `note`. Acts
  as a filter facet.
- `ref` — the source row's id. With `kind`, uniquely identifies the
  source.
- `title` — short text, weighted more in BM25.
- `body` — long text.

Nine triggers keep `search_index` in sync with `contacts`,
`companies`, `interactions`, `notes`:

```
contacts        INSERT/UPDATE/DELETE  ─ trigger ─►  search_index
companies       INSERT/UPDATE/DELETE  ─ trigger ─►  search_index
interactions    INSERT (immutable)    ─ trigger ─►  search_index
notes           INSERT/UPDATE/DELETE  ─ trigger ─►  search_index
                                                    (IF visibility != 'private')
```

Notes triggers include a `WHEN NEW.visibility != 'private'` guard.
Private notes are NEVER indexed, NEVER searchable, NEVER appear in
search results. They are visible only to the author and admins who
explicitly call `notes.reveal_private`.

One service: `backend/services/search.py`. API:

```python
search.run(ctx, q, *, kind=None, limit=20) -> {"results": [...]}
```

A result item:
```json
{
  "kind":  "interaction",
  "ref":   42,
  "title": "Coffee chat",
  "snippet": "...wants a 2m <mark>copper</mark> signage piece...",
  "rank":  -4.521,        // BM25 score; more negative = more relevant
  "parent_contact_id": 5,
  "parent_company_id": null
}
```

The service joins back to the source table to enrich
`parent_contact_id`/`parent_company_id` so callers can link to the
right page.

## Reason

**Why FTS5 and not LIKE?**

- BM25 ranking gives "best match first" out of the box.
- Porter stemming matches `copper`, `coppers`, `coppered`,
  `coppering`.
- Tokenization handles punctuation and case.
- Indexing happens at write time, not search time.
- At small/medium scale (millions of rows) FTS5 outperforms naive
  LIKE by orders of magnitude.

**Why one cross-entity table and not per-entity tables?**

- One table = one query for "search across everything", which is
  what users want 80% of the time.
- Filtering by `kind` is a fast equality predicate.
- Per-entity tables would require N queries + merging in the
  application — losing ranking coherence.

**Why triggers and not application-side index writes?**

- Triggers fire inside the same transaction as the data write.
  Index can never drift from data.
- Service code stays simple — no remembering to call
  `search.index_update`.
- A raw SQL INSERT (in a migration, in a debugging session)
  still updates the index. Belt-and-suspenders.

**Why exclude private notes from the index?**

Private notes are "things I want to remember but can't share." If
they're searchable, they're not really private — the very act of
searching them across a shared interface leaks the existence of the
content (even if the content itself isn't shown in results). Excluding
them from the index is the only honest privacy guarantee.

**Why `porter unicode61` and not `trigram`?**

- `porter` gives stemming — `copper` and `coppers` match same.
- `unicode61` handles non-ASCII reasonably well.
- Trigram gives substring match (finding `cop` inside `copper`) at
  cost of larger index, no ranking, no stemming. Not worth it for our
  scale.

## Result

What search gives you:

- One endpoint, one query, results across four entity types.
- Ranking that surfaces relevant matches first.
- Snippets with highlighted match terms (via FTS5's `snippet()`
  function).
- A `kind` filter for "only contacts" / "only interactions" / etc.
- A privacy guarantee on private notes that's enforced by SQL, not
  by application code (so a future agent that bypasses the service
  layer still can't search them).
- An index that's always fresh because it's transactional.

## Use case 1 — global search bar

The UI's top-right search bar POSTs `?q=copper+signage` to
`/search`. The handler calls `search.run(ctx, q="copper signage",
limit=20)` and renders results grouped by `kind`:

> **Interactions (3)**
> - Coffee chat (Maya Sato) — "...wants a 2m **copper signage** piece..."
> - Site visit (Greg Johnson) — "...feature wall, **copper** finish..."
>
> **Contacts (1)**
> - Greg Johnson — title "Owner" at Hammer Build Co.
>
> **Notes (1)**
> - Maya — "...send him **copper** portfolio photos..."

One click jumps to the source contact / company / interaction with
the search term anchored.

## Use case 2 — search-driven segments

A dynamic segment with rule
`{"search": {"q": "copper", "kind": "interaction"}}` returns every
contact whose timeline contains the word "copper". Combined with
`and`/`or`, you get "contacts with copper-related interactions AND
high intent AND consented".

The segments service translates `search` predicates into a JOIN against
`search_index`. (This is an opt-in predicate, added on top of the
default segment grammar — see [segments](segments.md) Fine-tuning.)

## Use case 3 — agent natural-language lookup

An MCP-driven agent receives a natural query: "find the contact who
mentioned a 2m signage piece." The agent calls
`find_contacts(q="2m signage")` MCP tool, which translates to
`search.run` and returns Greg Johnson with the matching interaction
as evidence. No special LLM-embedding-search infrastructure needed
— FTS5 is enough at this scale.

## Operations

### Searching

UI: top-right global search bar, or `/search` page for full results.

REST: `GET /api/search?q=...&kind=interaction&limit=20`.

CLI: `python -m agent_surface.cli search --q "..." --limit 20`.

MCP: `find_contacts(q="...")` (kind=contact),
`find_companies(q="...")`, or call a generic `search` tool if
exposed.

### Reindexing

In normal operation, the triggers keep the index fresh. A reindex is
needed only if:

- You import data via raw SQL bypassing the application (the
  triggers still fire, so this is rare).
- You change the FTS5 schema (rare; usually just adding `kind`
  variants).

```bash
python -m agent_surface.cli search reindex
# rebuilds search_index from contacts, companies, interactions, notes
```

Reindex is an O(N) operation. For 100k rows expect ~10-30 seconds.
Safe while the server is running (it holds the WAL writer lock briefly
per chunk).

### Checking index health

```sql
SELECT (SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL) AS c_rows,
       (SELECT COUNT(*) FROM search_index WHERE kind='contact')  AS c_idx;
```

If those numbers differ significantly, the index is out of sync —
run a reindex.

### Disabling search

In rare deployments (very low-resource boxes), you can drop the
search service. Set `SEARCH_ENABLED=0`. The UI's search bar disappears;
the API returns 503 for `/api/search`; segments with `search`
predicates raise validation errors. Triggers still run — disabling
search at runtime doesn't drop the index, just hides it.

## Fine-tuning

### Stop words

FTS5's default stop word set is none. Common English words like
"the", "a", "of" are indexed and matched. For most CRMs that's
fine. If you find queries are dominated by stop word matches, add a
custom token filter:

```sql
CREATE VIRTUAL TABLE search_index USING fts5(
   kind, ref, title, body,
   tokenize='porter unicode61 remove_diacritics 2',
   content=''
);
```

The `remove_diacritics 2` option strips accents so `cafe` matches
`café`.

### Ranking weights

BM25 in FTS5 lets you weight columns:

```sql
SELECT *, bm25(search_index, 5.0, 1.0) AS rank
FROM search_index
WHERE search_index MATCH ?
ORDER BY rank;
```

Here `title` gets 5x weight vs `body`. Adjust by editing the SQL in
`search.py`. We default to (3.0, 1.0) which surfaces matches in
titles ahead of long bodies.

### Snippet length

FTS5's `snippet()` function takes start/end markers and a max
length:

```sql
SELECT snippet(search_index, 3, '<mark>', '</mark>', '...', 32)
```

The `3` is the column index (the snippet comes from `body`, the 4th
column). 32 is the token count. Tune to taste.

### Adding new entities to search

To index a new entity (e.g., `deals.notes`):

1. In the migration that creates the entity, add three triggers
   (INSERT/UPDATE/DELETE) that maintain rows in `search_index` with
   `kind='deal'`.
2. Add `'deal'` to the `kind` enum check (if you have one).
3. Update `services/search.py` to recognize the new `kind` and join
   back to the deals table for parent ids.

### Multi-language

If your contact base is multi-lingual, `porter` only stems English.
Two options:

- Stick with `unicode61` only (no stemming). Loses recall but works
  across languages.
- Use a language column on each source row + per-language FTS
  indexes. More work but more accurate.

## Maximizing potential

1. **Search-driven dashboards.** Top-N most-discussed topics last
   week: query `search_index` grouped by token frequency in
   interactions logged in the last 7 days. (Approximate — FTS5
   doesn't expose per-token stats easily, but you can compute via
   tokenizer call.)

2. **Search as agent grounding.** Before an agent answers "who's the
   right contact for X?", it runs a search and includes top hits in
   its prompt. The agent's answer is now grounded in the CRM's
   actual data, not its training memory.

3. **Saved searches as queries.** A "saved view" can be just
   `{q: "copper", kind: "interaction"}`. Bookmark, share, embed.

4. **Search heat-map.** Track which terms users search most often.
   If 30% of searches are for project names, surface those as
   first-class tags or a project entity. Search analytics inform
   product decisions.

5. **Trigram fallback for typos.** For very short queries (3-4
   chars), the porter stemmer doesn't help. Add a fallback path: if
   FTS5 returns < 3 results, also run a trigram-LIKE match. Merge,
   deduplicate, return.

6. **Vector search as ANOTHER plug-in.** When you need semantic
   search ("contacts interested in our new product line"), add a
   plug-in that maintains embeddings in a separate table. Use FTS5
   for keyword recall, embeddings for semantic recall, merge for
   reranking. Best of both.

7. **Search-driven scoring signal.** A contact whose interactions
   match a "high-intent vocabulary" (words like "pricing",
   "deadline", "ready to start") gets an intent score boost. Implement
   as a scoring rule that queries `search_index` for the contact's
   interactions.

## Anti-patterns

- **Indexing private notes "just for admin use".** Don't. The
  guarantee is structural, not operational. If admins need
  private-note search, build a separate admin-only path with
  explicit consent and audit.
- **Using LIKE for partial matches and skipping FTS5.** Slower,
  worse ranking, no stemming. Use FTS5 with full tokens and trust
  the porter stemmer.
- **Putting the entire `interactions.metadata_json` into the
  index.** JSON keys aren't useful matches; the values you actually
  care about (title, body, channel) are already indexed. JSON pollutes
  the index with structural tokens.
- **Reindexing on a schedule "just to be safe".** The triggers are
  the contract. If the index drifts, find the bug, don't paper over
  it with rebuilds.

## Where to look in code

- `backend/services/search.py` — service + result enrichment
- `migrations/0003_*.sql` — FTS5 virtual table + 9 triggers
- `backend/main.py:1172` — `/search` UI route

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
- [portals.md](portals.md)
- [inbound.md](inbound.md)
- [search.md](search.md) **← you are here**

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
